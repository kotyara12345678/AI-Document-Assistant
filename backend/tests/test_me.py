"""Tests for the personal account: /api/me/stats, /api/me DELETE,
/api/auth/change-password and cross-user isolation of those aggregates."""

import uuid

from fastapi.testclient import TestClient

from app.main import app
from app.schemas.me import MeStats

API_PREFIX = "/api"
PWD = "test-pass-123"
NEW_PWD = "new-pass-456"


def _register(client: TestClient, email: str | None = None) -> dict:
    email = email or f"m{uuid.uuid4().hex[:8]}@example.com"
    resp = client.post(
        f"{API_PREFIX}/auth/register",
        json={"email": email, "password": PWD, "password_confirm": PWD},
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    return {"email": data["user"]["email"], "user_id": data["user"]["id"], "token": data["access_token"]}


def _upload(client: TestClient, filename: str, content: bytes):
    return client.post(f"{API_PREFIX}/documents/upload", files={"file": (filename, content)})


# --- /api/me/stats ---


def test_me_stats_empty_profile(client, identity):
    resp = client.get(f"{API_PREFIX}/me/stats")
    assert resp.status_code == 200, resp.text
    stats = MeStats.model_validate(resp.json())
    assert stats.user.id == identity.user_id
    assert stats.user.email == identity.email
    assert stats.documents_total == 0
    assert stats.chats_total == 0
    assert stats.messages_total == 0
    assert stats.tokens_used == 0
    assert stats.last_active_at is not None


def test_me_stats_reflects_own_data(client):
    marker = f"MEST{uuid.uuid4().hex[:6]}"
    text = (f"Личный документ {marker}. Сумма 12345 рублей. ") * 30
    upload = _upload(client, "own.txt", text.encode("utf-8"))
    assert upload.status_code == 201, upload.text

    chat = client.post(f"{API_PREFIX}/chats", json={"title": "мой чат"})
    assert chat.status_code == 201, chat.text

    resp = client.get(f"{API_PREFIX}/me/stats")
    assert resp.status_code == 200, resp.text
    stats = MeStats.model_validate(resp.json())
    assert stats.documents_total == 1
    assert stats.chats_total == 1
    assert stats.messages_total == 0


def test_me_stats_are_isolated_between_users(client):
    marker = f"MEIS{uuid.uuid4().hex[:6]}"
    text = (f"Приватный отчёт {marker}. Цена 9999 рублей. ") * 30
    assert _upload(client, "private.txt", text.encode("utf-8")).status_code == 201

    other = _register(client)
    b_headers = {"Authorization": f"Bearer {other['token']}"}
    resp = client.get(f"{API_PREFIX}/me/stats", headers=b_headers)
    assert resp.status_code == 200, resp.text
    stats = MeStats.model_validate(resp.json())
    assert stats.user.id == other["user_id"]
    assert stats.documents_total == 0
    assert stats.chats_total == 0


def test_me_stats_require_auth():
    with TestClient(app) as plain:
        assert plain.get(f"{API_PREFIX}/me/stats").status_code == 401


# --- /api/auth/change-password ---


def test_change_password_success(client, identity):
    resp = client.post(
        f"{API_PREFIX}/auth/change-password",
        json={
            "current_password": PWD,
            "new_password": NEW_PWD,
            "password_confirm": NEW_PWD,
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["changed"] is True

    # The old password no longer works, the new one does.
    old = client.post(
        f"{API_PREFIX}/auth/login",
        json={"email": identity.email, "password": PWD},
    )
    assert old.status_code == 401, old.text
    new = client.post(
        f"{API_PREFIX}/auth/login",
        json={"email": identity.email, "password": NEW_PWD},
    )
    assert new.status_code == 200, new.text


def test_change_password_wrong_current(client):
    resp = client.post(
        f"{API_PREFIX}/auth/change-password",
        json={
            "current_password": "wrong-current",
            "new_password": NEW_PWD,
            "password_confirm": NEW_PWD,
        },
    )
    assert resp.status_code == 400, resp.text


def test_change_password_mismatch_rejected(client):
    resp = client.post(
        f"{API_PREFIX}/auth/change-password",
        json={
            "current_password": PWD,
            "new_password": NEW_PWD,
            "password_confirm": "different-pass",
        },
    )
    assert resp.status_code == 422, resp.text


# --- DELETE /api/me ---


def test_delete_me_soft_deletes_account(client, identity):
    resp = client.delete(f"{API_PREFIX}/me")
    assert resp.status_code == 200, resp.text
    assert resp.json()["deleted"] is True

    # The old token no longer grants access, and login is refused too.
    assert client.get(f"{API_PREFIX}/me/stats").status_code == 403
    assert client.get(f"{API_PREFIX}/auth/me").status_code == 403
    login = client.post(
        f"{API_PREFIX}/auth/login",
        json={"email": identity.email, "password": PWD},
    )
    assert login.status_code == 403
