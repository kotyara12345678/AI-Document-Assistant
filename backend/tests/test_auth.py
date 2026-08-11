"""Tests for registration, login, JWT auth and strict user data isolation.

All cross-user checks rely on two accounts created on the same application; a
request's identity is whatever token it presents, so the assertions prove that
nobody reads somebody else's documents, chats, sources or search results.
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.auth import AuthResponse, UserOut

API_PREFIX = "/api"
PWD = "test-pass-123"


def _upload(client: TestClient, filename: str, content: bytes):
    return client.post(f"{API_PREFIX}/documents/upload", files={"file": (filename, content)})


def _register(client: TestClient, email: str | None = None) -> dict:
    email = email or f"b{uuid.uuid4().hex[:8]}@example.com"
    resp = client.post(
        f"{API_PREFIX}/auth/register",
        json={"email": email, "password": PWD, "password_confirm": PWD},
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    return {"email": data["user"]["email"], "user_id": data["user"]["id"], "token": data["access_token"]}


# --- registration ---


def test_register_returns_token_and_user(client: TestClient):
    email = f"reg{uuid.uuid4().hex[:8]}@example.com"
    resp = client.post(
        f"{API_PREFIX}/auth/register",
        json={"email": email.upper(), "password": PWD, "password_confirm": PWD},
    )
    assert resp.status_code == 201, resp.text
    data = AuthResponse.model_validate(resp.json())
    assert data.token_type == "bearer"
    assert data.access_token
    assert data.user.email == email.lower()
    assert data.user.id > 0


def test_register_duplicate_email_conflict(client):
    info = _register(client)
    resp = client.post(
        f"{API_PREFIX}/auth/register",
        json={"email": info["email"].upper(), "password": PWD, "password_confirm": PWD},
    )
    assert resp.status_code == 409, resp.text


def test_register_passwords_mismatch(client):
    resp = client.post(
        f"{API_PREFIX}/auth/register",
        json={"email": "x@example.com", "password": PWD, "password_confirm": "different-pass"},
    )
    assert resp.status_code == 422, resp.text


def test_register_short_password_rejected(client):
    resp = client.post(
        f"{API_PREFIX}/auth/register",
        json={"email": "x@example.com", "password": "short", "password_confirm": "short"},
    )
    assert resp.status_code == 422, resp.text


def test_register_invalid_email_rejected(client):
    resp = client.post(
        f"{API_PREFIX}/auth/register",
        json={"email": "not-an-email", "password": PWD, "password_confirm": PWD},
    )
    assert resp.status_code == 422, resp.text


# --- login ---


def test_login_success(client):
    info = _register(client)
    resp = client.post(
        f"{API_PREFIX}/auth/login",
        json={"email": info["email"].upper(), "password": PWD},
    )
    assert resp.status_code == 200, resp.text
    data = AuthResponse.model_validate(resp.json())
    assert data.access_token
    assert data.user.id == info["user_id"]
    assert data.user.email == info["email"]


def test_login_wrong_password(client):
    info = _register(client)
    resp = client.post(
        f"{API_PREFIX}/auth/login",
        json={"email": info["email"], "password": "wrong-pass"},
    )
    assert resp.status_code == 401, resp.text


def test_login_unknown_email(client):
    resp = client.post(
        f"{API_PREFIX}/auth/login",
        json={"email": f"nobody{uuid.uuid4().hex[:6]}@example.com", "password": PWD},
    )
    assert resp.status_code == 401, resp.text


# --- /me and token enforcement ---


def test_me_with_token_returns_user(client, identity):
    resp = client.get(f"{API_PREFIX}/auth/me")
    assert resp.status_code == 200, resp.text
    user = UserOut.model_validate(resp.json())
    assert user.id == identity.user_id
    assert user.email == identity.email


def test_public_endpoints_require_token():
    with TestClient(app) as plain:
        assert plain.get(f"{API_PREFIX}/auth/me").status_code == 401
        assert plain.get(f"{API_PREFIX}/documents").status_code == 401
        assert plain.get(f"{API_PREFIX}/chats").status_code == 401


def test_garbage_and_bad_scheme_tokens_rejected():
    with TestClient(app) as client:
        client.headers.update({"Authorization": "Bearer not.a.jwt"})
        assert client.get(f"{API_PREFIX}/auth/me").status_code == 401
        client.headers.update({"Authorization": "ApiKey whatever"})
        assert client.get(f"{API_PREFIX}/documents").status_code == 401


# --- isolation between two users ---


def test_documents_are_isolated_between_users(client):
    marker = f"ISOD{uuid.uuid4().hex[:6]}"
    text = (f"Конфиденциальный отчёт {marker}. Бюджет 7777777 рублей. ") * 30
    upload = _upload(client, f"secret_{marker}.txt", text.encode("utf-8"))
    assert upload.status_code == 201, upload.text
    doc_id = upload.json()[0]["id"]

    own = client.get(f"{API_PREFIX}/documents").json()
    assert any(d["id"] == doc_id for d in own)

    other = _register(client)
    b_headers = {"Authorization": f"Bearer {other['token']}"}

    # B cannot see, read, reindex or delete A's document.
    other_docs = client.get(f"{API_PREFIX}/documents", headers=b_headers).json()
    assert all(d["id"] != doc_id for d in other_docs)
    assert client.get(f"{API_PREFIX}/documents/{doc_id}/content", headers=b_headers).status_code == 404
    assert client.post(f"{API_PREFIX}/documents/{doc_id}/index", headers=b_headers).status_code == 404
    assert client.delete(f"{API_PREFIX}/documents/{doc_id}", headers=b_headers).status_code == 404

    # B's semantic search must not surface A's chunk.
    results = client.post(
        f"{API_PREFIX}/search",
        headers=b_headers,
        json={"query": f"конфиденциальный {marker}", "limit": 5},
    ).json()["results"]
    assert all(r["document_id"] != doc_id for r in results)


def test_search_is_scoped_to_a_users_vectors(client):
    marker = f"SCPV{uuid.uuid4().hex[:6]}"
    text = (f"Секрет проекта {marker}. Ставка 3000 рублей в час. ") * 30
    upload = _upload(client, "scoped.txt", text.encode("utf-8"))
    assert upload.status_code == 201, upload.text
    doc_id = upload.json()[0]["id"]

    own = client.post(f"{API_PREFIX}/search", json={"query": f"ставка {marker}", "limit": 5}).json()
    assert any(r["document_id"] == doc_id for r in own["results"])

    other = _register(client)
    b_headers = {"Authorization": f"Bearer {other['token']}"}
    other_results = client.post(
        f"{API_PREFIX}/search",
        headers=b_headers,
        json={"query": f"ставка {marker}", "limit": 5},
    ).json()
    assert all(r["document_id"] != doc_id for r in other_results["results"])


def test_chat_retrieval_and_sources_are_scoped(client, register_user, monkeypatch):
    marker = f"CHTS{uuid.uuid4().hex[:6]}"
    text = (f"База знаний только для владельца {marker}. Шифр 4242. ") * 20
    upload = _upload(client, "owner.txt", text.encode("utf-8"))
    assert upload.status_code == 201, upload.text
    doc_id = upload.json()[0]["id"]

    calls = []

    def fake(prompt, system_instruction=None, client=None, history=None, summary=None):
        calls.append(prompt)
        return "Ответ владельцу."

    monkeypatch.setattr("app.services.gemini.generate_answer", fake)

    a_resp = client.post(f"{API_PREFIX}/chat", json={"question": f"что за шифр {marker}"})
    assert a_resp.status_code == 200, a_resp.text
    assert a_resp.json()["sources"], "Owner must get sources for their own data"
    assert calls and marker in calls[0]

    # B has no such data: retrieval yields nothing -> honest answer, no LLM call.
    calls.clear()
    other = _register(client)
    b_headers = {"Authorization": f"Bearer {other['token']}"}
    b_resp = client.post(
        f"{API_PREFIX}/chat",
        headers=b_headers,
        json={"question": f"что за шифр {marker}"},
    )
    assert b_resp.status_code == 200, b_resp.text
    assert b_resp.json()["sources"] == []

    # B must not answer from A's document even when passing its id.
    b_doc = client.post(
        f"{API_PREFIX}/chat",
        headers=b_headers,
        json={"question": f"что за шифр {marker}", "document_id": doc_id},
    )
    assert b_doc.status_code == 200, b_doc.text
    assert b_doc.json()["sources"] == []


def test_chats_are_isolated_between_users(client):
    created = client.post(f"{API_PREFIX}/chats", json={"title": "мой чат"}).json()
    chat_id = created["id"]

    other = _register(client)
    b_headers = {"Authorization": f"Bearer {other['token']}"}

    other_chats = client.get(f"{API_PREFIX}/chats", headers=b_headers).json()
    assert all(c["id"] != chat_id for c in other_chats)

    assert client.get(f"{API_PREFIX}/chats/{chat_id}/messages", headers=b_headers).status_code == 404
    assert client.delete(f"{API_PREFIX}/chats/{chat_id}", headers=b_headers).status_code == 404