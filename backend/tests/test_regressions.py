"""Regression tests for bugs found during the quality audit.

Each test maps to a concrete defect that was fixed:

- bcrypt silently truncates passwords past 72 bytes -> registration must 422.
- concurrent registration of the same email raced the pre-check SELECT and
  crashed with an unhandled IntegrityError -> one 201 and one 409.
- Qdrant rejected non-UUID string point ids ("3855:0") -> point ids are
  deterministic UUIDs and re-indexing never duplicates points.
- RateLimiter._hits grew without bound under key floods -> capped.
- A multi-file upload partially persisted earlier files when a later file
  failed content checks -> the whole batch is validated before persisting.
- The seeded demo account was an unconditional admin backdoor in fresh DBs ->
  gated by SEED_DEMO_ADMIN and demoted at startup when not in ADMIN_EMAILS.
- JWT_SECRET default was accepted even in production -> refused at startup.
"""

import threading
import types
import uuid

import pytest
from fastapi import HTTPException, status

from app.api.routes import auth as auth_route
from app.core import ratelimit
from app.core import security
from app.database.session import SessionLocal
from app.core.config import settings
from app.main import SEEDED_DEMO_EMAIL, _sync_admin_emails
from app.models.user import User
from app.schemas.auth import RegisterRequest
from app.vector import client as vector_client
from app.vector.client import _point_id

PWD = "test-pass-123"


# --- auth registration hardening ---------------------------------------------


def test_register_rejects_password_longer_than_bcrypt_72_bytes(client):
    """>72 UTF-8 bytes must be rejected up front (bcrypt truncates silently)."""
    email = f"long{uuid.uuid4().hex[:8]}@example.com"
    too_long_ascii = "x" * 73
    resp = client.post(
        "/api/auth/register",
        json={"email": email, "password": too_long_ascii, "password_confirm": too_long_ascii},
    )
    assert resp.status_code == 422, resp.text

    email2 = f"cry{uuid.uuid4().hex[:8]}@example.com"
    too_long_utf8 = "яжи" * 25  # 3 bytes per char -> 75 bytes
    resp = client.post(
        "/api/auth/register",
        json={"email": email2, "password": too_long_utf8, "password_confirm": too_long_utf8},
    )
    assert resp.status_code == 422, resp.text


def test_register_accepts_password_up_to_72_bytes(client):
    """Exactly 72 UTF-8 bytes is the bcrypt boundary and must still succeed."""
    email = f"edge{uuid.uuid4().hex[:8]}@example.com"
    edge = "я" * 36  # 36 * 2 bytes = 72 bytes
    resp = client.post(
        "/api/auth/register",
        json={"email": email, "password": edge, "password_confirm": edge},
    )
    assert resp.status_code == 201, resp.text


def test_concurrent_register_same_email_yields_201_and_409():
    """Two threads racing the same email must never crash (TOCTOU fix)."""
    email = f"race{uuid.uuid4().hex[:8]}@example.com"

    class _FakeRequest:
        client = types.SimpleNamespace(host="10.0.0.1")

    outcomes: list = []
    barrier = threading.Barrier(2)

    def _do() -> None:
        db = SessionLocal()
        try:
            barrier.wait()
            auth_route.register(
                RegisterRequest(email=email, password=PWD, password_confirm=PWD),
                _FakeRequest(),
                db,
            )
            outcomes.append(201)
        except HTTPException as exc:
            outcomes.append(exc.status_code)
        finally:
            db.close()

    threads = [threading.Thread(target=_do) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(outcomes) == [201, 409], outcomes


# --- Qdrant point ids ---------------------------------------------------------


def test_point_id_is_a_valid_qdrant_uuid():
    """Point ids must be unsigned ints or UUIDs; the old "1:0" format 400'd."""
    pid = _point_id({"document_id": 3855, "chunk_index": 0})
    assert uuid.UUID(pid), f"point id must parse as a UUID, got {pid!r}"
    assert pid == _point_id({"document_id": 3855, "chunk_index": 0}), "must be deterministic"


def test_reindexing_a_document_does_not_duplicate_points(client):
    """Upserting the same document again replaces points instead of appending."""
    marker = f"REIDX{uuid.uuid4().hex[:6]}"
    text = (f"Повторная индексация не создаёт дубликатов {marker}. Данные по роботам. ") * 30
    upload = client.post(
        "/api/documents/upload",
        files={"file": ("reindex.txt", text.encode("utf-8"))},
    )
    assert upload.status_code == 201, upload.text
    doc_id = upload.json()[0]["id"]

    first = vector_client.document_vector_count(doc_id)
    assert first > 0, "upload must index its vectors"

    reindex = client.post(f"/api/documents/{doc_id}/index")
    assert reindex.status_code == 200, reindex.text

    assert vector_client.document_vector_count(doc_id) == first, (
        "stale vectors deleted before re-indexing must prevent duplicates"
    )


# --- rate limiter memory bound -------------------------------------------------


def test_rate_limiter_caps_key_growth(monkeypatch):
    """A flood of unique keys must bound _hits memory instead of growing forever."""
    monkeypatch.setattr(ratelimit, "MAX_KEYS", 3)
    limiter = ratelimit.RateLimiter()
    for i in range(5):
        assert limiter.allow(f"key-{i}", limit=100, window_seconds=60)
    assert len(limiter._hits) <= 3, len(limiter._hits)


# --- batch upload atomicity ----------------------------------------------------


def test_batch_upload_with_content_failure_persists_nothing(client, monkeypatch):
    """A later file that passes extension checks but fails content checks must
    not leave earlier files of the same request persisted."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "MAX_UPLOAD_FILES", 5)
    files = [
        ("file", ("good.txt", b"would be saved only on full-batch success")),
        ("file", ("broken.txt", b"")),
    ]
    resp = client.post("/api/documents/upload", files=files)
    assert resp.status_code == status.HTTP_400_BAD_REQUEST, resp.text
    assert "empty" in resp.json()["detail"].lower()

    docs = client.get("/api/documents").json()
    assert all(d["original_filename"] != "good.txt" for d in docs), (
        "earlier valid files must be rolled back with the failing one"
    )


# --- demo admin backdoor -------------------------------------------------------


def test_seeded_demo_account_is_demoted_and_deactivated_when_not_listed(monkeypatch):
    """Removing demo@example.com from ADMIN_EMAILS revokes admin AND login.

    The account ships with a publicly known password, so when it is not an
    explicitly listed admin it must also be deactivated — otherwise anyone
    could log into a live account with the well-known credentials.
    """
    db = SessionLocal()
    try:
        db.add(User(email=SEEDED_DEMO_EMAIL, password_hash=security.hash_password(PWD), role="admin"))
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(settings, "ADMIN_EMAILS", [])
    _sync_admin_emails()

    db = SessionLocal()
    try:
        demo = db.query(User).filter(User.email == SEEDED_DEMO_EMAIL).one()
        assert demo.role == "user", "demo account must be demoted when not listed"
        assert demo.is_active is False, "demo account must be deactivated when not listed"
    finally:
        db.close()


def test_seeded_demo_account_stays_admin_when_listed(monkeypatch):
    """Explicitly listing demo@example.com in ADMIN_EMAILS keeps it usable."""
    db = SessionLocal()
    try:
        db.add(User(email=SEEDED_DEMO_EMAIL, password_hash=security.hash_password(PWD), role="user"))
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(settings, "ADMIN_EMAILS", ["demo@example.com"])
    _sync_admin_emails()

    db = SessionLocal()
    try:
        demo = db.query(User).filter(User.email == SEEDED_DEMO_EMAIL).one()
        assert demo.role == "admin"
        assert demo.is_active is True, "listed demo account must stay active"
    finally:
        db.close()


# --- JWT secret production gate -----------------------------------------------


def test_jwt_secret_default_rejected_in_production(monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "JWT_SECRET", "dev-secret-change-me")
    with pytest.raises(RuntimeError):
        security._enforce_jwt_secret()


def test_jwt_secret_ok_when_overridden_in_production(monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "JWT_SECRET", "a-really-strong-32-byte-minimum-secret-here")
    security._enforce_jwt_secret()


def test_jwt_secret_default_allowed_outside_production(monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "JWT_SECRET", "dev-secret-change-me")
    security._enforce_jwt_secret()


# --- article-number search: reformulation must include digit tokens ----------


def test_reformulate_includes_digit_tokens_for_article_search():
    """'найди статью 3 ук рф' must produce a variant containing just '3' so
    that FTS can match 'Статья 3' in legal documents."""
    from app.services.query_reformulation import reformulate_query

    variants = reformulate_query("найди статью 3 ук рф")
    assert "3" in variants, f"digit '3' must be a variant, got {variants}"


def test_reformulate_includes_digit_for_multi_digit_article():
    from app.services.query_reformulation import reformulate_query

    variants = reformulate_query("найди статью 105 ук рф")
    assert "105" in variants, f"digit '105' must be a variant, got {variants}"


# --- anti-fabrication: list/search results must not be sanitized -----------


def test_sanitize_preserves_list_response_without_create(client, monkeypatch):
    """'список всех файлов' triggers a list_documents tool; the model then
    says 'файлы готовы к просмотру'. The word 'готовы' must NOT be treated as
    a fabricated creation claim when no create_document was called."""
    from app.services.agent import agent_service
    from app.schemas.agent import AgentRequest

    list_msg = {
        "role": "assistant",
        "content": None,
        "function_call": {"name": "list_documents", "arguments": {}},
    }
    list_result = json.dumps(
        [
            {"document_id": 1, "filename": "doc1.txt"},
            {"document_id": 2, "filename": "doc2.txt"},
        ]
    )
    model_answer = "Ваши файлы готовы к просмотру: doc1.txt, doc2.txt."

    calls = []

    def fake(messages, functions=None, function_call="auto", functions_state_id=None, client=None, usage_hook=None):
        calls.append(messages)
        if not calls or len(calls) == 1:
            return (list_msg, "s")
        return ({"content": model_answer}, None)

    monkeypatch.setattr("app.services.gemini.chat_with_functions", fake)

    resp = client.post(f"{API_PREFIX}/agent", json={"question": "список всех файлов"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    # The answer must be the model's response, NOT "ничего не было создано".
    assert "ничего не было создано" not in data["answer"]
    assert "готовы" in data["answer"] or "файлы" in data["answer"]


def test_sanitize_preserves_search_result_response(client, monkeypatch):
    """After a search_documents call, the model says 'документ доступен'.
    'доступен' must NOT trigger the "nothing was created" replacement."""
    from app.services.agent import agent_service
    from app.schemas.agent import AgentRequest

    search_msg = {
        "role": "assistant",
        "content": None,
        "function_call": {"name": "search_documents", "arguments": {"query": "зарплата"}},
    }
    search_result = json.dumps([{"document_id": 1, "filename": "salary.txt", "score": 0.9, "text": "Зарплата 50000"}])
    model_answer = "Документ доступен: в файле salary.txt зарплата 50000 рублей."

    calls = []

    def fake(messages, functions=None, function_call="auto", functions_state_id=None, client=None, usage_hook=None):
        calls.append(messages)
        if not calls or len(calls) == 1:
            return (search_msg, "s")
        return ({"content": model_answer}, None)

    monkeypatch.setattr("app.services.gemini.chat_with_functions", fake)

    resp = client.post(f"{API_PREFIX}/agent", json={"question": "какая зарплата?"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "ничего не было создано" not in data["answer"]
    assert "50000" in data["answer"]


# --- trailing number safety net: search queries must not trigger create/edit -


def test_execute_tool_rejects_create_for_search_question(client, monkeypatch, user_id):
    """_execute_tool must reject create_document when the question starts with
    a search verb and has no creation verb (trailing number is data, not a
    directive)."""
    from app.services.agent import agent_service
    from app.schemas.agent import AgentRequest

    create_msg = {
        "role": "assistant",
        "content": None,
        "function_call": {
            "name": "create_document",
            "arguments": {
                "document_spec": {"title": "ИНН", "blocks": []},
                "output_format": "docx",
            },
        },
    }
    model_answer = "Документ создан."

    calls = []

    def fake(messages, functions=None, function_call="auto", functions_state_id=None, client=None, usage_hook=None):
        calls.append(messages)
        if not calls or len(calls) == 1:
            return (create_msg, "s")
        return ({"content": model_answer}, None)

    monkeypatch.setattr("app.services.gemini.chat_with_functions", fake)

    resp = client.post(f"{API_PREFIX}/agent", json={"question": "найди инн алексея 4"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    # The safety net must have blocked the create_document call.
    create_calls = [c for c in data["tool_calls"] if c["name"] == "create_document"]
    assert create_calls == [], f"create_document must be blocked for search query, got {create_calls}"
    assert data["created_documents"] == []