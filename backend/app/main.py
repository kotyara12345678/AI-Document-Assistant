from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import chat, documents, health, search
from app.core.config import settings
from app.services import indexing as indexing_service
from app.vector import client as vector_client

logger = logging.getLogger("app.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
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
    app.include_router(documents.router, prefix=f"{settings.API_PREFIX}/documents", tags=["documents"])
    app.include_router(chat.router, prefix=f"{settings.API_PREFIX}/chat", tags=["chat"])
    app.include_router(search.router, prefix=f"{settings.API_PREFIX}/search", tags=["search"])

    @app.get("/")
    def root() -> dict:
        return {"service": settings.APP_NAME, "version": settings.APP_VERSION}

    return app


app = create_app()
