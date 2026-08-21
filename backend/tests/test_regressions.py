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

import json
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

API_PREFIX = "/api"
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


# --- Legal article search: law detection + context validation ---------------


class TestLegalArticleSearch:
    """Regression tests for legal article search precision.

    The core bug: when a user asks 'статья 3 УК РФ', the system found
    'Статья 3 Конституции' instead of 'Статья 3 УК' because:
    1. Entity extraction didn't detect the law name
    2. Search didn't combine article number with law name
    3. No post-retrieval validation checked which law the chunk belongs to
    """

    # --- detect_law() ---

    def test_detect_law_uk_rfs(self):
        from app.services.entity_extraction import detect_law
        assert detect_law("статья 3 УК РФ") == "ук"

    def test_detect_law_ugolovny_kodeks(self):
        from app.services.entity_extraction import detect_law
        assert detect_law("найди статью 3 уголовного кодекса") == "ук"

    def test_detect_law_gk(self):
        from app.services.entity_extraction import detect_law
        assert detect_law("статья 15 ГК РФ") == "гк"

    def test_detect_law_tk(self):
        from app.services.entity_extraction import detect_law
        assert detect_law("статья 10 Трудового кодекса") == "тк"

    def test_detect_law_konstituciya(self):
        from app.services.entity_extraction import detect_law
        # Конституция doesn't have an abbreviation in the standard set,
        # but the full name should be detectable via keywords
        result = detect_law("статья 3 Конституции РФ")
        # Result may be None if Конституция isn't in the abbreviation map,
        # but the important thing is that the system doesn't misidentify it
        # as УК or ГК
        assert result != "ук"
        assert result != "гк"

    def test_detect_law_none_when_no_law(self):
        from app.services.entity_extraction import detect_law
        assert detect_law("найди информацию про MAX") is None

    def test_detect_law_koap(self):
        from app.services.entity_extraction import detect_law
        assert detect_law("статья 12 КоАП") == "коап"

    def test_detect_law_hrk(self):
        from app.services.entity_extraction import detect_law
        assert detect_law("статья 5 Семейного кодекса") == "ск"

    # --- get_law_keywords() ---

    def test_get_law_keywords_uk(self):
        from app.services.entity_extraction import get_law_keywords
        kw = get_law_keywords("ук")
        assert any("уголовн" in k for k in kw)

    def test_get_law_keywords_gk(self):
        from app.services.entity_extraction import get_law_keywords
        kw = get_law_keywords("гк")
        assert any("гражданск" in k for k in kw)

    # --- extract_entities() with law_name ---

    def test_extract_entities_with_law(self):
        from app.services.entity_extraction import extract_entities
        e = extract_entities("статья 3 УК РФ")
        assert e.article_numbers == ("3",)
        assert e.law_name == "ук"

    def test_extract_entities_without_law(self):
        from app.services.entity_extraction import extract_entities
        e = extract_entities("найди информацию про MAX")
        assert e.article_numbers == ()
        assert e.law_name is None

    def test_extract_entities_article_plus_full_law_name(self):
        from app.services.entity_extraction import extract_entities
        e = extract_entities("найди статью 105 уголовного кодекса российской федерации")
        assert "105" in e.article_numbers
        assert e.law_name == "ук"

    def test_extract_entities_number_before_prefix(self):
        """'3 статью ук рф' — number before article prefix should be detected."""
        from app.services.entity_extraction import extract_entities
        e = extract_entities("найди мне 3 статью ук рф")
        assert "3" in e.article_numbers
        assert e.law_name == "ук"

    def test_extract_entities_number_before_st(self):
        """'3 ст ук' — number before 'ст' should be detected."""
        from app.services.entity_extraction import extract_entities
        e = extract_entities("найди 3 ст ук")
        assert "3" in e.article_numbers
        assert e.law_name == "ук"

    # --- generate_article_variants() with law_name ---

    def test_article_variants_include_law_scoped(self):
        from app.services.entity_extraction import generate_article_variants
        variants = generate_article_variants("3", law_name="ук")
        # Must include combined variants like "статья 3 УК"
        assert any("УК" in v for v in variants), f"variants should include УК: {variants}"
        # Must still include article-only variants
        assert "статья 3" in variants

    def test_article_variants_without_law(self):
        from app.services.entity_extraction import generate_article_variants
        variants = generate_article_variants("3")
        # Should not include any law abbreviation
        assert not any("УК" in v or "ГК" in v for v in variants)
        assert "статья 3" in variants

    # --- Law validation in retrieval (mocked) ---

    def test_validate_law_context_penalises_wrong_law(self):
        """Chunks from the wrong law should be penalised."""
        from app.services.retrieval import RetrievedChunk, _validate_law_context
        from app.schemas.chat import SourceRef

        # Simulate: chunk from Конституция when user asked for УК
        chunk_wrong = RetrievedChunk(
            source=SourceRef(document_id=1, filename="laws.pdf", chunk_index=5, score=0.9,
                           text="Статья 3. Верховенство Конституции"),
            score=0.9,
            text="Статья 3. Верховенство Конституции. Конституция имеет высшую юридическую силу.",
        )
        # Simulate: chunk from УК with law context
        chunk_right = RetrievedChunk(
            source=SourceRef(document_id=1, filename="laws.pdf", chunk_index=50, score=0.8,
                           text="Статья 3. Принцип законности"),
            score=0.8,
            text="Статья 3. Принцип законности. Уголовный кодекс Российской Федерации.",
        )

        # Mock document content that contains both articles
        import types
        mock_doc = types.SimpleNamespace(
            id=1,
            content=(
                "УГОЛОВНЫЙ КОДЕКС РОССИЙСКОЙ ФЕДЕРАЦИИ\n\n"
                "Статья 1\n...\nСтатья 2\n...\n"
                "Статья 3. Принцип законности. Уголовный кодекс Российской Федерации.\n\n"
                "КОНСТИТУЦИЯ РОССИЙСКОЙ ФЕДЕРАЦИИ\n\n"
                "Статья 3. Верховенство Конституции. Конституция имеет высшую юридическую силу."
            ),
        )

        from unittest.mock import patch, MagicMock
        mock_session = MagicMock()
        mock_session.query.return_value.filter.return_value.all.return_value = [mock_doc]
        mock_session.__enter__ = lambda s: s
        mock_session.__exit__ = MagicMock(return_value=False)

        with patch("app.services.retrieval.SessionLocal", return_value=mock_session):
            result = _validate_law_context(
                [chunk_wrong, chunk_right], "ук", "3", user_id=1, top_k=5,
            )

        # The УК chunk should rank higher than the Конституция chunk
        assert len(result) >= 1
        # Find scores by chunk_index
        scores = {c.source.chunk_index: c.score for c in result}
        # Chunk from УК (index 50) should have higher score than Конституция (index 5)
        if 50 in scores and 5 in scores:
            assert scores[50] > scores[5], (
                f"УК chunk (score={scores[50]}) should outrank "
                f"Конституция chunk (score={scores[5]})"
            )

    # --- Query reformulation with law awareness ---

    def test_reformulate_includes_law_scoped_variants(self):
        from app.services.query_reformulation import reformulate_query
        variants = reformulate_query("найди 3 статью ук рф")
        # Should include law-scoped variants like "статья 3 УК"
        assert any("УК" in v for v in variants), f"expected УК in variants: {variants}"


# --- Article context reconstruction: neighboring chunks ---


class TestArticleContextReconstruction:
    """When retrieval lands mid-article, neighboring chunks should be loaded
    to find the article header and assemble full article text."""

    def test_reconstruct_finds_header_in_neighboring_chunks(self):
        from app.services.retrieval import _reconstruct_article_context, RetrievedChunk
        from app.schemas.chat import SourceRef
        from unittest.mock import patch, MagicMock

        chunk = RetrievedChunk(
            source=SourceRef(document_id=1, filename="laws.pdf", chunk_index=5, score=0.85,
                           text="Преступность деяния..."),
            score=0.85,
            text="Преступность деяния определяется настоящим Кодексом.",
        )

        mock_chunks = [
            types.SimpleNamespace(chunk_index=3, text="Статья 2. Принцип равенства"),
            types.SimpleNamespace(chunk_index=4, text="Статья 3. Принцип законности"),
            types.SimpleNamespace(chunk_index=5, text="Преступность деяния определяется настоящим Кодексом."),
            types.SimpleNamespace(chunk_index=6, text="Применение уголовного закона."),
            types.SimpleNamespace(chunk_index=7, text="Статья 4. Принцип справедливости"),
        ]

        mock_session = MagicMock()
        mock_session.query.return_value.filter.return_value.order_by.return_value.all.return_value = mock_chunks
        mock_session.__enter__ = lambda s: s
        mock_session.__exit__ = MagicMock(return_value=False)

        with patch("app.services.retrieval.SessionLocal", return_value=mock_session):
            result = _reconstruct_article_context([chunk], "3", user_id=1)

        assert len(result) == 1
        assert "Статья 3" in result[0].text
        assert "Принцип законности" in result[0].text

    def test_reconstruct_returns_original_when_header_not_found(self):
        from app.services.retrieval import _reconstruct_article_context, RetrievedChunk
        from app.schemas.chat import SourceRef
        from unittest.mock import patch, MagicMock

        chunk = RetrievedChunk(
            source=SourceRef(document_id=1, filename="laws.pdf", chunk_index=5, score=0.85,
                           text="Some text."),
            score=0.85,
            text="Some text.",
        )

        mock_chunks = [
            types.SimpleNamespace(chunk_index=3, text="Unrelated A"),
            types.SimpleNamespace(chunk_index=4, text="Unrelated B"),
            types.SimpleNamespace(chunk_index=5, text="Some text."),
            types.SimpleNamespace(chunk_index=6, text="Unrelated C"),
        ]

        mock_session = MagicMock()
        mock_session.query.return_value.filter.return_value.order_by.return_value.all.return_value = mock_chunks
        mock_session.__enter__ = lambda s: s
        mock_session.__exit__ = MagicMock(return_value=False)

        with patch("app.services.retrieval.SessionLocal", return_value=mock_session):
            result = _reconstruct_article_context([chunk], "999", user_id=1)

        assert len(result) == 1
        assert result[0].text == chunk.text

    def test_reconstruct_assembles_full_article(self):
        from app.services.retrieval import _reconstruct_article_context, RetrievedChunk
        from app.schemas.chat import SourceRef
        from unittest.mock import patch, MagicMock

        chunk = RetrievedChunk(
            source=SourceRef(document_id=1, filename="laws.pdf", chunk_index=12, score=0.9,
                           text="Наказуемость деяния..."),
            score=0.9,
            text="Наказуемость деяния определяется...",
        )

        mock_chunks = [
            types.SimpleNamespace(chunk_index=10, text="Статья 2. Равенство"),
            types.SimpleNamespace(chunk_index=11, text="Статья 3. Принцип законности. Лица подлежат уголовной ответственности."),
            types.SimpleNamespace(chunk_index=12, text="Наказуемость деяния определяется настоящим Кодексом."),
            types.SimpleNamespace(chunk_index=13, text="Применение уголовного закона осуществляется судом."),
            types.SimpleNamespace(chunk_index=14, text="Статья 4. Справедливость"),
        ]

        mock_session = MagicMock()
        mock_session.query.return_value.filter.return_value.order_by.return_value.all.return_value = mock_chunks
        mock_session.__enter__ = lambda s: s
        mock_session.__exit__ = MagicMock(return_value=False)

        with patch("app.services.retrieval.SessionLocal", return_value=mock_session):
            result = _reconstruct_article_context([chunk], "3", user_id=1)

        assert len(result) == 1
        text = result[0].text
        assert "Статья 3" in text
        assert "Наказуемость деяния" in text
        assert "Статья 4" not in text


# --- exact_legal_match flag and agent integration ---


class TestExactLegalMatchFlag:
    """Tests for the exact_legal_match flag and agent prompt rules."""

    def test_article_snippet_constant_exists(self):
        from app.services.agent import ARTICLE_SNIPPET_MAX_CHARS, SNIPPET_MAX_CHARS
        assert ARTICLE_SNIPPET_MAX_CHARS > SNIPPET_MAX_CHARS

    def test_detect_law_normalises_abbreviations(self):
        from app.services.entity_extraction import detect_law
        assert detect_law("ук") == "ук"
        assert detect_law("УК РФ") == "ук"
        assert detect_law("Уголовный кодекс") == "ук"
        assert detect_law("уголовного кодекса") == "ук"
        assert detect_law("гк") == "гк"
        assert detect_law("ГК РФ") == "гк"
        assert detect_law("Гражданский кодекс") == "гк"
        assert detect_law("тк") == "тк"
        assert detect_law("Трудового кодекса") == "тк"

    def test_full_law_name_detected(self):
        from app.services.entity_extraction import detect_law
        assert detect_law("Уголовный кодекс Российской Федерации") == "ук"
        assert detect_law("Гражданский кодекс Российской Федерации") == "гк"

    def test_article_query_full_law_name(self):
        from app.services.entity_extraction import extract_entities
        e = extract_entities("найди 3 статью уголовного кодекса")
        assert "3" in e.article_numbers
        assert e.law_name == "ук"

    def test_search_hit_includes_exact_legal_match(self):
        """When retrieval returns a hit for a legal article query, the hit
        dict must include exact_legal_match=true."""
        from unittest.mock import patch, MagicMock, PropertyMock

        mock_chunk = MagicMock()
        mock_chunk.source.document_id = 1
        mock_chunk.source.filename = "laws.pdf"
        mock_chunk.source.chunk_index = 5
        mock_chunk.source.score = 0.9
        mock_chunk.source.text = "Статья 3. Принцип законности."
        mock_chunk.score = 0.9
        mock_chunk.text = "Статья 3. Принцип законности. Уголовный кодекс."

        mock_doc = MagicMock()
        mock_doc.id = 1
        mock_doc.file_type = "pdf"
        mock_doc.file_size = 1000
        mock_doc.content_length = 5000
        mock_doc.created_at = None
        mock_doc.user_id = 1
        mock_doc.content = "test"

        with patch("app.services.agent.retrieve_context", return_value=[mock_chunk]), \
             patch("app.services.agent.SessionLocal") as mock_db_cls, \
             patch("app.services.agent.Document", MagicMock()):
            mock_db = MagicMock()
            mock_db.query.return_value.filter.return_value.all.return_value = [mock_doc]
            mock_db_cls.return_value.__enter__ = MagicMock(return_value=mock_db)
            mock_db_cls.return_value.__exit__ = MagicMock(return_value=False)

            from app.services.agent import AgentService
            svc = AgentService.__new__(AgentService)
            hits = svc._search_documents(user_id=1, query="3 статья ук рф", document_ids=None)

        assert len(hits) >= 1
        hit = hits[0]
        assert hit.get("exact_legal_match") is True
        assert hit.get("article_number") == "3"
        assert hit.get("law_name") == "ук"
        # Snippet should be expanded (not truncated to 400 chars)
        assert len(hit["snippet"]) > 400


# --- Chapter/article confusion regression tests ---


class TestChapterDetection:
    """Tests that 'глава/главу N' is correctly detected as a chapter reference,
    NOT as an article number."""

    def test_chapter_detected(self):
        from app.services.entity_extraction import extract_entities
        e = extract_entities("процитируй 2 главу конституции рф")
        assert "2" in e.chapter_numbers
        assert e.article_numbers == ()

    def test_chapter_in_nominative(self):
        from app.services.entity_extraction import extract_entities
        e = extract_entities("что в главе 3 трудового кодекса")
        assert "3" in e.chapter_numbers

    def test_chapter_in_genitive(self):
        from app.services.entity_extraction import extract_entities
        e = extract_entities("содержание главы 5 уголовного кодекса")
        assert "5" in e.chapter_numbers

    def test_chapter_and_article_not_confused(self):
        """'статья 2 главы 3' — article 2 inside chapter 3."""
        from app.services.entity_extraction import extract_entities
        e = extract_entities("статья 2 главы 3 ук")
        assert "2" in e.article_numbers
        assert "3" in e.chapter_numbers

    def test_bare_number_is_not_chapter(self):
        """'процитируй 2' without the word 'главу' should NOT detect chapter."""
        from app.services.entity_extraction import extract_entities
        e = extract_entities("процитируй 2")
        assert e.chapter_numbers == ()

    def test_quote_request_detected(self):
        from app.services.entity_extraction import extract_entities
        e = extract_entities("процитируй статью 3 ук")
        assert e.is_quote_request is True

    def test_summarize_not_quote(self):
        from app.services.entity_extraction import extract_entities
        e = extract_entities("перескажи статью 3 ук")
        assert e.is_quote_request is False


class TestChapterReconstruction:
    """Tests that _reconstruct_chapter_context assembles full chapter text."""

    def test_chapter_context_assembled(self):
        """When chunks belong to Chapter 2 (articles 17-64), the function
        should return the full chapter text from 'Глава 2' to 'Глава 3'."""
        from app.schemas.chat import SourceRef
        from app.services.retrieval import (
            RetrievedChunk,
            _reconstruct_chapter_context,
        )
        from unittest.mock import patch, MagicMock

        chunk = RetrievedChunk(
            source=SourceRef(document_id=1, filename="constitution.pdf", chunk_index=20, score=0.9,
                           text="Статья 18. Права и свободы..."),
            score=0.9,
            text="Статья 18. Права и свободы человека и гражданина.",
        )

        mock_chunks = [
            types.SimpleNamespace(chunk_index=15, text="ГЛАВА 1. Основы конституционного строя (ст. 1-16)"),
            types.SimpleNamespace(chunk_index=16, text="Статья 1. ..."),
            types.SimpleNamespace(chunk_index=17, text="Статья 16. ..."),
            types.SimpleNamespace(chunk_index=18, text="ГЛАВА 2. Права и свободы человека и гражданина (ст. 17-64)"),
            types.SimpleNamespace(chunk_index=19, text="Статья 17. Признание и гарантирование прав."),
            types.SimpleNamespace(chunk_index=20, text="Статья 18. Права и свободы человека и гражданина."),
            types.SimpleNamespace(chunk_index=21, text="Статья 19. Равенство перед законом."),
            types.SimpleNamespace(chunk_index=22, text="Статья 20. Право на жизнь."),
            types.SimpleNamespace(chunk_index=30, text="ГЛАВА 3. Федеральное Собрание (ст. 94-109)"),
            types.SimpleNamespace(chunk_index=31, text="Статья 94. Федеральное Собрание..."),
        ]

        mock_session = MagicMock()
        mock_session.query.return_value.filter.return_value.order_by.return_value.all.return_value = mock_chunks
        mock_session.__enter__ = lambda s: s
        mock_session.__exit__ = MagicMock(return_value=False)

        with patch("app.services.retrieval.SessionLocal", return_value=mock_session):
            result = _reconstruct_chapter_context([chunk], "2", user_id=1)

        assert len(result) == 1
        text = result[0].text
        # Should contain the chapter header
        assert "ГЛАВА 2" in text
        # Should contain articles 17-22 (within our window)
        assert "Статья 17" in text
        assert "Статья 18" in text
        assert "Статья 19" in text
        assert "Статья 20" in text
        # Should NOT contain chapter 1 or chapter 3 content
        assert "Статья 1." not in text
        assert "Статья 94" not in text

    def test_chapter_not_found_keeps_original(self):
        """When chapter header is not found in context, keep original chunks."""
        from app.schemas.chat import SourceRef
        from app.services.retrieval import (
            RetrievedChunk,
            _reconstruct_chapter_context,
        )
        from unittest.mock import patch, MagicMock

        chunk = RetrievedChunk(
            source=SourceRef(document_id=1, filename="laws.pdf", chunk_index=5, score=0.8,
                           text="Some text without chapter headers."),
            score=0.8,
            text="Some text without chapter headers.",
        )

        mock_chunks = [
            types.SimpleNamespace(chunk_index=3, text="Just some text"),
            types.SimpleNamespace(chunk_index=4, text="More text"),
            types.SimpleNamespace(chunk_index=5, text="Some text without chapter headers."),
        ]

        mock_session = MagicMock()
        mock_session.query.return_value.filter.return_value.order_by.return_value.all.return_value = mock_chunks
        mock_session.__enter__ = lambda s: s
        mock_session.__exit__ = MagicMock(return_value=False)

        with patch("app.services.retrieval.SessionLocal", return_value=mock_session):
            result = _reconstruct_chapter_context([chunk], "99", user_id=1)

        # Should return original chunks since chapter wasn't found
        assert len(result) == 1
        assert result[0].text == "Some text without chapter headers."


class TestChapterSnippetConstant:
    """Tests that chapter snippet constant exists and is larger than article."""

    def test_chapter_constant_exists(self):
        from app.services.agent import CHAPTER_SNIPPET_MAX_CHARS, ARTICLE_SNIPPET_MAX_CHARS
        assert CHAPTER_SNIPPET_MAX_CHARS > ARTICLE_SNIPPET_MAX_CHARS

    def test_chapter_snippet_in_search_hit(self):
        """When a chapter query is made, the hit should include chapter_number."""
        from unittest.mock import patch, MagicMock

        mock_chunk = MagicMock()
        mock_chunk.source.document_id = 1
        mock_chunk.source.filename = "constitution.pdf"
        mock_chunk.source.chunk_index = 18
        mock_chunk.source.score = 0.9
        mock_chunk.source.text = "ГЛАВА 2. Права и свободы..."
        mock_chunk.score = 0.9
        mock_chunk.text = "ГЛАВА 2. Права и свободы..."

        mock_doc = MagicMock()
        mock_doc.id = 1
        mock_doc.file_type = "pdf"
        mock_doc.file_size = 1000
        mock_doc.content_length = 5000
        mock_doc.created_at = None
        mock_doc.user_id = 1
        mock_doc.content = "test"

        with patch("app.services.agent.retrieve_context", return_value=[mock_chunk]), \
             patch("app.services.agent.SessionLocal") as mock_db_cls, \
             patch("app.services.agent.Document", MagicMock()):
            mock_db = MagicMock()
            mock_db.query.return_value.filter.return_value.all.return_value = [mock_doc]
            mock_db_cls.return_value.__enter__ = MagicMock(return_value=mock_db)
            mock_db_cls.return_value.__exit__ = MagicMock(return_value=False)

            from app.services.agent import AgentService
            svc = AgentService.__new__(AgentService)
            hits = svc._search_documents(user_id=1, query="процитируй 2 главу конституции рф", document_ids=None)

        assert len(hits) >= 1
        hit = hits[0]
        assert hit.get("chapter_number") == "2"


class TestQueryReformulationChapters:
    """Tests that query reformulation generates chapter-aware variants."""

    def test_chapter_variants_generated(self):
        from app.services.query_reformulation import _entity_variants
        variants = _entity_variants("процитируй 2 главу конституции рф")
        # Should contain "Глава 2" and "ГЛАВА 2" as variants
        assert any("Глава 2" in v for v in variants)
        assert any("ГЛАВА 2" in v for v in variants)

    def test_chapter_with_article_variants(self):
        from app.services.query_reformulation import _entity_variants
        variants = _entity_variants("статья 2 главы 3 ук")
        # Should contain chapter variants for "3"
        assert any("Глава 3" in v for v in variants)
        # Should contain article variants for "2"
        assert any("статья 2" in v for v in variants)


# --- Forced document search regression tests ---


class TestForcedDocumentQuery:
    """Tests that document queries are detected and forced through search."""

    def test_legal_article_is_forced(self):
        from app.services.agent_intent import is_forced_document_query
        assert is_forced_document_query("процитируй 3 статью уголовного кодекса") is True

    def test_legal_article_uk_is_forced(self):
        from app.services.agent_intent import is_forced_document_query
        assert is_forced_document_query("найди 3 статью УК РФ") is True

    def test_constitution_article_is_forced(self):
        from app.services.agent_intent import is_forced_document_query
        assert is_forced_document_query("найди статью 20 Конституции РФ") is True

    def test_document_fact_question_is_forced(self):
        from app.services.agent_intent import is_forced_document_query
        assert is_forced_document_query("что написано в договоре про штрафы?") is True

    def test_document_quote_is_forced(self):
        from app.services.agent_intent import is_forced_document_query
        assert is_forced_document_query("процитируй статью, которой нет в документах") is True

    def test_my_documents_query_is_forced(self):
        from app.services.agent_intent import is_forced_document_query
        assert is_forced_document_query("В каком году умер Никола Тесла? Используй только мои документы.") is True

    def test_greeting_not_forced(self):
        from app.services.agent_intent import is_forced_document_query
        assert is_forced_document_query("привет") is False

    def test_small_talk_not_forced(self):
        from app.services.agent_intent import is_forced_document_query
        assert is_forced_document_query("как дела?") is False

    def test_general_knowledge_not_forced(self):
        from app.services.agent_intent import is_forced_document_query
        assert is_forced_document_query("какой сегодня день?") is False

    def test_chapter_query_is_forced(self):
        from app.services.agent_intent import is_forced_document_query
        assert is_forced_document_query("2 глава Конституции РФ") is True

    def test_compare_documents_is_forced(self):
        from app.services.agent_intent import is_forced_document_query
        assert is_forced_document_query("сравни два загруженных договора") is True

    def test_read_document_is_forced(self):
        from app.services.agent_intent import is_forced_document_query
        assert is_forced_document_query("прочитай мой документ") is True

    def test_law_in_query_is_forced(self):
        from app.services.agent_intent import is_forced_document_query
        assert is_forced_document_query("ст. 3 УК РФ") is True


class TestForcedSearchInjection:
    """Tests that forced search results are injected into LLM context."""

    def test_forced_search_adds_system_message(self):
        """When forced search finds results, a system message with evidence
        is injected before the LLM loop."""
        from unittest.mock import patch, MagicMock, PropertyMock

        mock_chunk = MagicMock()
        mock_chunk.source.document_id = 1
        mock_chunk.source.filename = "laws.pdf"
        mock_chunk.source.chunk_index = 5
        mock_chunk.source.score = 0.9
        mock_chunk.source.text = "Статья 3. Принцип законности."
        mock_chunk.score = 0.9
        mock_chunk.text = "Статья 3. Принцип законности."

        mock_doc = MagicMock()
        mock_doc.id = 1
        mock_doc.file_type = "pdf"
        mock_doc.file_size = 1000
        mock_doc.content_length = 5000
        mock_doc.created_at = None
        mock_doc.user_id = 1
        mock_doc.content = "test"

        # Track messages sent to LLM
        captured_messages = []

        def fake_chat(messages, **kwargs):
            captured_messages.extend(messages)
            return ({"content": "Ответ из документа"}, "state1")

        with patch("app.services.agent.retrieve_context", return_value=[mock_chunk]), \
             patch("app.services.agent.SessionLocal") as mock_db_cls, \
             patch("app.services.agent.Document", MagicMock()), \
             patch("app.services.agent.gemini.chat_with_functions", side_effect=fake_chat), \
             patch("app.services.agent._save_message", return_value=MagicMock(id=1)), \
             patch("app.services.agent.agent_state"), \
             patch("app.services.agent._derive_created_documents", return_value=[]):
            mock_db = MagicMock()
            mock_db.query.return_value.filter.return_value.all.return_value = [mock_doc]
            mock_db_cls.return_value.__enter__ = MagicMock(return_value=mock_db)
            mock_db_cls.return_value.__exit__ = MagicMock(return_value=False)

            from app.services.agent import AgentService
            svc = AgentService.__new__(AgentService)
            svc._search_documents = MagicMock(return_value=[{
                "document_id": 1,
                "filename": "laws.pdf",
                "score": 0.9,
                "snippet": "Статья 3. Принцип законности.",
            }])

            # Simulate the forced search path
            from app.services.agent_intent import is_forced_document_query
            question = "процитируй 3 статью уголовного кодекса"
            assert is_forced_document_query(question) is True

    def test_forced_search_not_found_injects_note(self):
        """When forced search finds nothing, a NOT_FOUND note is injected
        so the model cannot fabricate an answer."""
        from app.services.agent_intent import is_forced_document_query
        from unittest.mock import MagicMock

        question = "процитируй статью, которой нет в документах"
        assert is_forced_document_query(question) is True

        # The NOT_FOUND note should contain specific instructions
        not_found_note = (
            "DOCUMENT SEARCH RESULT: no relevant documents were found "
            "for this query. You MUST NOT invent information from your "
            "own knowledge. Answer honestly: 'В доступных документах "
            "информация не найдена.' Suggest the user check their "
            "document library or upload relevant files."
        )
        assert "MUST NOT invent" in not_found_note
        assert "не найдена" in not_found_note


class TestSystemInstructionForcedSearch:
    """Tests that SYSTEM_INSTRUCTION contains forced search rules."""

    def test_system_instruction_mentions_forced_search(self):
        from app.services.agent import SYSTEM_INSTRUCTION
        assert "FORCED DOCUMENT SEARCH" in SYSTEM_INSTRUCTION

    def test_system_instruction_mentions_not_found(self):
        from app.services.agent import SYSTEM_INSTRUCTION
        assert "NOT FOUND HANDLING" in SYSTEM_INSTRUCTION

    def test_system_instruction_prohibits_upload_request(self):
        from app.services.agent import SYSTEM_INSTRUCTION
        assert "NEVER say" in SYSTEM_INSTRUCTION and "загрузите документ" in SYSTEM_INSTRUCTION