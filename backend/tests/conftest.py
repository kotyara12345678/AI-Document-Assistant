"""Shared pytest fixtures: authenticated API client + DB/Qdrant isolation.

Every test that needs the API is run as a fresh registered user: the
`client` fixture registers a unique account via POST /api/auth/register and
attaches its bearer token as the client-wide default Authorization header.
`user_id` exposes the id of that current user for service-level calls.

IMPORTANT — isolation from real data:
Before anything else, pytest is pointed at a DEDICATED test database and a
DEDICATED Qdrant collection. The cleanup fixtures (``_clean_db``,
``_clean_qdrant``) run against those test resources only, so running the
suite can never delete, reset or corrupt real user accounts, documents,
chats or vectors.
"""

import os
from pathlib import Path


def _test_environment_overrides() -> None:
    """Switch the app to throw-away test storage before it is imported.

    Must run before ``app.main`` is imported (see the imports below). The
    database name becomes ``<main>_test`` and Qdrant uses a separate
    collection. Both can be overridden explicitly for unusual setups.
    """
    main_url = os.environ.get(
        "DATABASE_URL",
        "postgresql+psycopg2://docassistant:docassistant@db:5432/docassistant",
    )
    test_db = os.environ.get("TEST_DATABASE_NAME", "docassistant_test")
    os.environ["DATABASE_URL"] = f"{main_url.rsplit('/', 1)[0]}/{test_db}"
    os.environ["QDRANT_COLLECTION"] = os.environ.get(
        "TEST_QDRANT_COLLECTION", "document_chunks_test"
    )


_test_environment_overrides()

import types  # noqa: E402
import uuid  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

TEST_PASSWORD = "test-pass-123"


@pytest.fixture(scope="session", autouse=True)
def _prepare_test_database():
    """Create the dedicated test database and apply migrations to it (once).

    Runs in its own PostgreSQL server connection (the ``postgres`` admin db)
    with autocommit, so ``CREATE DATABASE`` is allowed. Real data is never
    touched: the app already points at the test database at this point.
    """
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text

    from app.core.config import settings

    server_url = f"{settings.DATABASE_URL.rsplit('/', 1)[0]}/postgres"
    engine = create_engine(server_url, isolation_level="AUTOCOMMIT")
    dbname = settings.DATABASE_URL.rsplit("/", 1)[-1]
    try:
        with engine.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": dbname},
            ).scalar()
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{dbname}"'))
    finally:
        engine.dispose()

    alembic_ini = Path(__file__).resolve().parents[1] / "alembic.ini"
    config = Config(str(alembic_ini))
    command.upgrade(config, "head")


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
    from app.core.ratelimit import throttle
    from app.database.session import SessionLocal
    from app.models.agent_session import AgentSession
    from app.models.chat import Chat
    from app.models.chat_message import ChatMessage, ChatSummary
    from app.models.document import Document
    from app.models.report import Report
    from app.models.user import User

    # Auth throttling is keyed by the client IP, which every TestClient call
    # reports as "testclient"; without a reset the whole suite shares one
    # budget and bursts of registers start failing with 429.
    throttle.reset()
    db = SessionLocal()
    try:
        db.query(ChatSummary).delete()
        db.query(ChatMessage).delete()
        db.query(AgentSession).delete()
        db.query(Chat).delete()
        db.query(Document).delete()
        db.query(Report).delete()
        db.query(User).delete()
        db.commit()
    finally:
        db.close()
    yield
    db = SessionLocal()
    try:
        db.query(ChatSummary).delete()
        db.query(ChatMessage).delete()
        db.query(AgentSession).delete()
        db.query(Chat).delete()
        db.query(Document).delete()
        db.query(Report).delete()
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