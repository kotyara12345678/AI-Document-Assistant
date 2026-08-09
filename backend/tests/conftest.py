"""Shared pytest fixtures: authenticated API client + DB/Qdrant isolation.

Every test that needs the API is run as a fresh registered user: the
`client` fixture registers a unique account via POST /api/auth/register and
attaches its bearer token as the client-wide default Authorization header.
`user_id` exposes the id of that current user for service-level calls.
"""

import types
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app

TEST_PASSWORD = "test-pass-123"


def _register_user(
    target_client: TestClient,
    email: str | None = None,
    password: str = TEST_PASSWORD,
) -> dict:
    email = email or f"u{uuid.uuid4().hex[:10]}@example.com"
    resp = target_client.post(
        "/api/auth/register",
        json={
            "email": email,
            "password": password,
            "password_confirm": password,
        },
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    return {
        "email": data["user"]["email"],
        "password": password,
        "user_id": data["user"]["id"],
        "token": data["access_token"],
    }


@pytest.fixture()
def register_user() -> callable:
    """Factory fixture: register an arbitrary fresh user on a given client."""
    return _register_user


@pytest.fixture()
def identity() -> types.SimpleNamespace:
    """Mutable holder shared by `client`/`user_id` (avoids attrs on httpx)."""
    return types.SimpleNamespace(user_id=None, token=None, email=None)


@pytest.fixture()
def client(identity: types.SimpleNamespace):
    with TestClient(app) as c:
        info = _register_user(c)
        identity.email = info["email"]
        identity.user_id = info["user_id"]
        identity.token = info["token"]
        c.headers.update({"Authorization": f"Bearer {identity.token}"})
        yield c


@pytest.fixture()
def user_id(client: TestClient, identity: types.SimpleNamespace) -> int:
    """id of the currently authenticated test user (forces `client` setup)."""
    return identity.user_id


@pytest.fixture(autouse=True)
def _clean_db():
    """Isolate each test from user/chat/document rows left by previous runs."""
    from app.database.session import SessionLocal
    from app.models.chat import Chat
    from app.models.chat_message import ChatMessage, ChatSummary
    from app.models.document import Document
    from app.models.user import User

    db = SessionLocal()
    try:
        db.query(ChatSummary).delete()
        db.query(ChatMessage).delete()
        db.query(Chat).delete()
        db.query(Document).delete()
        db.query(User).delete()
        db.commit()
    finally:
        db.close()
    yield
    db = SessionLocal()
    try:
        db.query(ChatSummary).delete()
        db.query(ChatMessage).delete()
        db.query(Chat).delete()
        db.query(Document).delete()
        db.query(User).delete()
        db.commit()
    finally:
        db.close()


@pytest.fixture(autouse=True)
def _clean_qdrant():
    from app.core.config import settings
    from app.vector.client import get_qdrant_client

    qclient = get_qdrant_client()
    try:
        qclient.delete_collection(settings.QDRANT_COLLECTION)
    except Exception:
        pass
    yield
    try:
        qclient.delete_collection(settings.QDRANT_COLLECTION)
    except Exception:
        pass