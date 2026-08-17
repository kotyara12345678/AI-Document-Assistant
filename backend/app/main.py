from contextlib import asynccontextmanager
import logging
import threading
import time

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import (
    admin,
    admin_users,
    agent,
    auth,
    chat,
    chats,
    documents,
    health,
    ready,
    reports,
    search,
)
from app.core.config import settings
from app.core.metrics import metrics
from app.core.security import ADMIN_ROLE
from app.database.session import SessionLocal
from app.models.user import User
from app.services import indexing as indexing_service
from app.vector import client as vector_client

logger = logging.getLogger("app.main")

# Demo account seeded by the alembic migrations 0006/0007 with a well-known
# password; this is the one account that is a real backdoor unless it is either
# listed in ADMIN_EMAILS or explicitly demoted.
SEEDED_DEMO_EMAIL = "demo@example.com"


def _sync_admin_emails() -> None:
    """Enforce that the admin role is configuration-driven.

    Promotes every user listed in ADMIN_EMAILS and demotes the seeded demo
    account when it is NOT listed, so removing it from ADMIN_EMAILS closes the
    known-credentials backdoor without hand-editing the database. Idempotent
    and kept deliberately small; it does not touch roles of other users.
    """
    emails = [e.strip().lower() for e in settings.ADMIN_EMAILS if e and e.strip()]
    db = SessionLocal()
    try:
        if emails:
            updated = (
                db.query(User)
                .filter(User.email.in_(emails), User.role != ADMIN_ROLE)
                .update({User.role: ADMIN_ROLE}, synchronize_session=False)
            )
            if updated:
                logger.info("Promoted %s user(s) to admin role", updated)
        if SEEDED_DEMO_EMAIL not in emails:
            demoted = (
                db.query(User)
                .filter(User.email == SEEDED_DEMO_EMAIL, User.role == ADMIN_ROLE)
                .update({User.role: "user"}, synchronize_session=False)
            )
            if demoted:
                logger.warning(
                    "Demo account %s is not in ADMIN_EMAILS; demoted from admin",
                    SEEDED_DEMO_EMAIL,
                )
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Failed to sync admin emails")
    finally:
        db.close()


def _reindex_missing_documents_background() -> None:
    """Re-vectorize documents that lost their Qdrant points (best-effort).

    Runs in a daemon thread so a slow re-index (embedding model loads per
    document) never blocks the app from serving requests.
    """
    def _run() -> None:
        try:
            reindexed = indexing_service.reindex_missing_documents()
            if reindexed:
                logger.info("Startup re-index: %s document(s) written to Qdrant", reindexed)
        except Exception:
            logger.exception("Startup re-index failed")

    threading.Thread(
        target=_run,
        name="startup-reindex",
        daemon=True,
    ).start()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Make sure designated admins exist before serving requests.
    _sync_admin_emails()

    # Make sure the Qdrant collection exists before serving requests.
    try:
        vector_client.ensure_collection()
    except Exception:
        logger.exception("Failed to ensure Qdrant collection at startup")

    # Re-index documents that lost their vectors (e.g. after a collection wipe)
    # in the background; do not delay the first request.
    _reindex_missing_documents_background()

    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        debug=settings.DEBUG,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(ready.router, prefix=f"{settings.API_PREFIX}", tags=["health"])
    app.include_router(auth.router, prefix=f"{settings.API_PREFIX}/auth", tags=["auth"])
    app.include_router(documents.router, prefix=f"{settings.API_PREFIX}/documents", tags=["documents"])
    app.include_router(chat.router, prefix=f"{settings.API_PREFIX}/chat", tags=["chat"])
    app.include_router(chats.router, prefix=f"{settings.API_PREFIX}/chats", tags=["chats"])
    app.include_router(search.router, prefix=f"{settings.API_PREFIX}/search", tags=["search"])
    app.include_router(admin.router, prefix=f"{settings.API_PREFIX}/admin", tags=["admin"])
    app.include_router(admin_users.router, prefix=f"{settings.API_PREFIX}/admin", tags=["admin"])
    app.include_router(reports.router, prefix=f"{settings.API_PREFIX}/reports", tags=["reports"])
    app.include_router(agent.router, prefix=f"{settings.API_PREFIX}/agent", tags=["agent"])

    @app.middleware("http")
    async def _metrics_middleware(request, call_next):
        # Records status + path + duration only; never request bodies, query
        # strings or headers, so passwords/tokens/credentials cannot leak in.
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            metrics.record(500, request.url.path, (time.perf_counter() - start) * 1000.0)
            raise
        metrics.record(response.status_code, request.url.path, (time.perf_counter() - start) * 1000.0)
        return response

    @app.get("/")
    def root() -> dict:
        return {"service": settings.APP_NAME, "version": settings.APP_VERSION}

    return app


app = create_app()
