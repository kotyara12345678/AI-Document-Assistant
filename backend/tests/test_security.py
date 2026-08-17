"""Security-focused tests: rate limiting, JWT tampering, upload hardening and
cross-user isolation for the newer multi-document chat filter.

The rate-limiter state is shared within a test process, so the throttling
tests reset it first and shrink the configured limits to keep them fast.
"""

import uuid

import jwt
from fastapi.testclient import TestClient

from app.core.ratelimit import throttle

API_PREFIX = "/api"
PWD = "test-pass-123"


def _reset_throttle() -> None:
    throttle.reset()


def _register_new(client: TestClient) -> dict:
    resp = client.post(
        f"{API_PREFIX}/auth/register",
        json={
            "email": f"sec{uuid.uuid4().hex[:8]}@example.com",
            "password": PWD,
            "password_confirm": PWD,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# --- rate limiting -----------------------------------------------------------


def test_login_brute_force_is_throttled(client, monkeypatch):
    """Repeated failed logins on one account eventually return HTTP 429."""
    _reset_throttle()
    monkeypatch.setattr("app.api.routes.auth.FAILED_LOGIN_LIMIT", 3)

    registered = _register_new(client)
    email = registered["user"]["email"]

    statuses = []
    for _ in range(6):
        resp = client.post(
            f"{API_PREFIX}/auth/login",
            json={"email": email, "password": "wrong-password"},
        )
        statuses.append(resp.status_code)

    assert statuses[:3] == [401, 401, 401], statuses
    assert statuses[-1] == 429, f"Brute force must be throttled: {statuses}"


def test_register_burst_is_throttled(client, monkeypatch):
    """Mass account creation from one IP is throttled (burst guard)."""
    _reset_throttle()
    monkeypatch.setattr("app.api.routes.auth.AUTH_BURST_LIMIT", 3)

    statuses = [_register_new(client), _register_new(client), _register_new(client)]
    blocked = client.post(
        f"{API_PREFIX}/auth/register",
        json={
            "email": f"burst{uuid.uuid4().hex[:8]}@example.com",
            "password": PWD,
            "password_confirm": PWD,
        },
    )
    assert blocked.status_code == 429, blocked.text
    assert statuses, "three registrations must have succeeded before the burst guard"


def test_consecutive_successful_logins_are_not_blocked(client):
    """The failed-attempt throttle must never count successful logins."""
    _reset_throttle()
    registered = _register_new(client)
    email = registered["user"]["email"]

    for _ in range(2):
        resp = client.post(
            f"{API_PREFIX}/auth/login",
            json={"email": email, "password": PWD},
        )
        assert resp.status_code == 200, resp.text


def test_chat_rate_limit_per_user(client, monkeypatch):
    """Per-user chat burst guard kicks in without breaking normal use."""
    from app.services import gemini

    def fake(prompt, system_instruction=None, client=None, history=None, summary=None):
        return "Ответ."

    monkeypatch.setattr(gemini, "generate_answer", fake)
    _reset_throttle()
    monkeypatch.setattr("app.api.routes.chat.CHAT_BURST_LIMIT", 3)

    statuses = []
    for _ in range(4):
        resp = client.post(
            f"{API_PREFIX}/chat",
            json={"question": f"вопрос {uuid.uuid4().hex[:4]}"},
        )
        statuses.append(resp.status_code)

    assert statuses[:3] == [200, 200, 200], statuses
    assert statuses[-1] == 429, f"Chat cost must be bound: {statuses}"


# --- JWT hardening -----------------------------------------------------------


def test_unknown_invalid_and_tampered_tokens_rejected(client):
    """Malformed/foreign-signed tokens must never authenticate."""
    forged = jwt.encode({"sub": "1"}, "totally-other-secret", algorithm="HS256")
    resp = client.get(
        f"{API_PREFIX}/auth/me",
        headers={"Authorization": f"Bearer {forged}"},
    )
    assert resp.status_code == 401, resp.text

    # Signed with the real secret but missing the `sub` claim: must still 401.
    from app.core.config import settings

    no_sub = jwt.encode({"foo": "bar"}, settings.JWT_SECRET, algorithm="HS256")
    resp = client.get(
        f"{API_PREFIX}/auth/me",
        headers={"Authorization": f"Bearer {no_sub}"},
    )
    assert resp.status_code == 401, resp.text


# --- upload hardening --------------------------------------------------------


def test_upload_content_type_mismatch_rejected(client):
    """A file whose magic bytes do not match its extension is rejected."""
    resp = client.post(
        f"{API_PREFIX}/documents/upload",
        files={"file": ("fake.pdf", b"PK\x03\x04 not a real docx")},
    )
    assert resp.status_code == 400, resp.text


def test_upload_oversize_rejected_without_creating_document(client, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "MAX_UPLOAD_SIZE_MB", 1)
    oversized = b"x" * (2 * 1024 * 1024)

    resp = client.post(
        f"{API_PREFIX}/documents/upload",
        files={"file": ("big.txt", oversized)},
    )
    assert resp.status_code == 413, resp.text

    docs = client.get(f"{API_PREFIX}/documents").json()
    assert all(d["original_filename"] != "big.txt" for d in docs)


def test_upload_filename_is_sanitized(client):
    """Client-chosen names lose path separators and stay basenames."""
    resp = client.post(
        f"{API_PREFIX}/documents/upload",
        files={"file": ("../../evil.txt", b"safe content")},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()[0]["original_filename"] == "evil.txt"


# --- upload hardening --------------------------------------------------------


def test_upload_forbidden_extension_rejected(client):
    """An executable must never pass the extension allow-list."""
    resp = client.post(
        f"{API_PREFIX}/documents/upload",
        files={"file": ("payload.exe", b"MZ\x90\x00 not really an exe")},
    )
    assert resp.status_code == 400, resp.text
    assert "Allowed" in resp.json()["detail"]


def test_upload_too_many_files_rejected(client, monkeypatch):
    """More files than MAX_UPLOAD_FILES in one request is rejected."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "MAX_UPLOAD_FILES", 2)
    files = [
        ("file", ("a.txt", b"aaa")),
        ("file", ("b.txt", b"bbb")),
        ("file", ("c.txt", b"ccc")),
    ]
    resp = client.post(f"{API_PREFIX}/documents/upload", files=files)
    assert resp.status_code == 400, resp.text
    assert "Maximum is 2" in resp.json()["detail"]

    docs = client.get(f"{API_PREFIX}/documents").json()
    assert docs == [], "no documents may be persisted from a rejected batch"


def test_upload_multiple_files_within_limit(client):
    """A batch within MAX_UPLOAD_FILES uploads every file in one request."""
    files = [
        ("file", ("alpha.txt", b"first file content")),
        ("file", ("beta.txt", b"second file content")),
    ]
    resp = client.post(f"{API_PREFIX}/documents/upload", files=files)
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert len(data) == 2
    assert {d["original_filename"] for d in data} == {"alpha.txt", "beta.txt"}
    assert len({d["id"] for d in data}) == 2


def test_upload_rejected_batch_persists_nothing(client, monkeypatch):
    """A batch with one bad file must not persist the other files."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "MAX_UPLOAD_FILES", 5)
    files = [
        ("file", ("good.txt", b"good content")),
        ("file", ("virus.exe", b"MZ...")),
    ]
    resp = client.post(f"{API_PREFIX}/documents/upload", files=files)
    assert resp.status_code == 400, resp.text

    docs = client.get(f"{API_PREFIX}/documents").json()
    assert all(d["original_filename"] != "good.txt" for d in docs)


# --- cross-user isolation (multi-document filter) ----------------------------


def test_chat_with_foreign_document_ids_scoped(client, monkeypatch):
    """User B must not retrieve chunks from user A even via document_ids."""
    from app.services import gemini

    marker = f"SECM{uuid.uuid4().hex[:6]}"
    text = (f"Секретный протокол {marker}. Код доступа 9999. ") * 20
    upload = client.post(
        f"{API_PREFIX}/documents/upload",
        files={"file": ("secret.txt", text.encode("utf-8"))},
    )
    assert upload.status_code == 201, upload.text
    doc_id = upload.json()[0]["id"]

    def fake(prompt, system_instruction=None, client=None, history=None, summary=None):
        return "Ответ."

    monkeypatch.setattr(gemini, "generate_answer", fake)

    other = _register_new(client)
    b_headers = {"Authorization": f"Bearer {other['access_token']}"}

    resp = client.post(
        f"{API_PREFIX}/chat",
        headers=b_headers,
        json={"question": f"что за протокол {marker}", "document_ids": [doc_id]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["sources"] == [], (
        "B must not receive A's chunks even when passing A's document_ids"
    )