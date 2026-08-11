"""Admin panel tests.

Covers server-side role enforcement of /api/admin/stats plus the guarantee
that aggregate statistics never leak document or chat content:
  1. an authenticated admin can read the stats;
  2. a regular user is rejected with 403;
  3. an unauthenticated caller is rejected with 401;
  4. registration/login and the ordinary user endpoints keep working.

The ``client`` fixture registers a plain "user" role account and attaches its
token; admins are created by flipping the role directly in the database.
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.database.session import SessionLocal
from app.main import app
from app.models.user import User

ADMIN_API = "/api/admin/stats"
PWD = "test-pass-123"


def _set_role(user_id: int, role: str) -> None:
    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        assert user is not None
        user.role = role
        db.commit()
    finally:
        db.close()


def _register_on(target_client: TestClient, email: str | None = None) -> dict:
    email = email or f"adm{uuid.uuid4().hex[:10]}@example.com"
    resp = target_client.post(
        "/api/auth/register",
        json={"email": email, "password": PWD, "password_confirm": PWD},
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    return {
        "user_id": data["user"]["id"],
        "token": data["access_token"],
        "email": data["user"]["email"],
    }


def test_admin_can_read_aggregate_stats(client, identity):
    """An authenticated admin gets the full stats payload (aggregates only)."""
    _set_role(identity.user_id, "admin")
    resp = client.get(ADMIN_API)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    for key in ("services", "users", "documents", "chats", "requests", "tokens", "errors", "generated_at"):
        assert key in body, f"missing section {key}"
    assert body["users"]["total"] >= 1
    assert isinstance(body["tokens"]["total_tokens_used"], int)
    assert body["services"]["database"] == "ok"


def test_admin_stats_never_leak_document_or_chat_content(client, identity, monkeypatch):
    """Uploads/chats exist but their raw content never appears in the stats."""
    from app.services import gemini

    _set_role(identity.user_id, "admin")

    marker = f"TOPSECRET{uuid.uuid4().hex[:8]}"
    upload = client.post(
        "/api/documents/upload",
        files={"file": ("confidential.txt", marker.encode("utf-8"))},
    )
    assert upload.status_code == 201, upload.text

    chat = client.post("/api/chats", json={"title": None})
    assert chat.status_code == 201, chat.text
    chat_id = chat.json()["id"]

    def fake(prompt, system_instruction=None, client=None, history=None, summary=None):
        return "Ответ."

    monkeypatch.setattr(gemini, "generate_answer", fake)
    ask = client.post("/api/chat", json={"chat_id": chat_id, "question": f"что значит {marker}?"})
    assert ask.status_code == 200, ask.text

    resp = client.get(ADMIN_API)
    assert resp.status_code == 200, resp.text
    assert marker not in resp.text, "admin stats must not contain document/chat content"
    body = resp.json()
    assert body["documents"]["total"] == 1
    assert body["chats"]["messages"] == 2  # one user question + one assistant reply
    assert body["requests"]["llm_requests"] == 1


def test_regular_user_gets_403(client):
    """A normal (non-admin) authenticated user must be rejected by the API."""
    resp = client.get(ADMIN_API)
    assert resp.status_code == 403, resp.text
    assert "role" in resp.json().get("detail", "").lower()


def test_unauthenticated_gets_401():
    """No bearer token at all -> HTTP 401, never 403 or 200."""
    with TestClient(app) as c:
        resp = c.get(ADMIN_API)
    assert resp.status_code == 401, resp.text


def test_register_login_and_user_endpoints_still_work(client):
    """Existing auth flow and ordinary user endpoints are unaffected."""
    me = client.get("/api/auth/me")
    assert me.status_code == 200, me.text
    assert me.json()["role"] == "user"

    docs = client.get("/api/documents")
    assert docs.status_code == 200, docs.text

    with TestClient(app) as c:
        email = f"flow{uuid.uuid4().hex[:8]}@example.com"
        reg = c.post(
            "/api/auth/register",
            json={"email": email, "password": PWD, "password_confirm": PWD},
        )
        assert reg.status_code == 201, reg.text
        assert reg.json()["user"]["role"] == "user"

        log = c.post("/api/auth/login", json={"email": email, "password": PWD})
        assert log.status_code == 200, log.text


def test_admin_with_unknown_token_gets_401():
    """A valid-looking but unknown admin id must not pass (401, not 403)."""
    with TestClient(app) as c:
        resp = c.get(
            ADMIN_API,
            headers={"Authorization": "Bearer definitely-not-a-jwt"},
        )
    assert resp.status_code == 401, resp.text