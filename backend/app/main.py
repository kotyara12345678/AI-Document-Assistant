from contextlib import asynccontextmanager
import logging
import time

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import (
    admin,
    agent,
    auth,
    chat,
    chats,
    documents,
    health,
    ready,
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


def _promote_admin_emails() -> None:
    """Grant the "admin" role to every user listed in ADMIN_EMAILS (idempotent).

    Kept deliberately small: startup declaration of who is an administrator,
    driven by configuration rather than a second authorization system.
    """
    emails = [e.strip().lower() for e in settings.ADMIN_EMAILS if e and e.strip()]
    if not emails:
        return
    db = SessionLocal()
    try:
        updated = (
            db.query(User)
            .filter(User.email.in_(emails), User.role != ADMIN_ROLE)
            .update({User.role: ADMIN_ROLE}, synchronize_session=False)
        )
        if updated:
            db.commit()
            logger.info("Promoted %s user(s) to admin role", updated)
    except Exception:
        db.rollback()
        logger.exception("Failed to promote admin emails")
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Make sure designated admins exist before serving requests.
    _promote_admin_emails()

    # Make sure the Qdrant collection exists before serving requests.
    try:
        vector_client.ensure_collection()
    except Exception:
        logger.exception("Failed to ensure Qdrant collection at startup")

    # Re-index documents that lost their vectors (e.g. after a collection wipe).
    try:
        reindexed = indexing_service.reindex_missing_documents()
        if reindexed:
            logger.info("Startup re-index: %s document(s) written to Qdrant", reindexed)
    except Exception:
        logger.exception("Startup re-index failed")

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
