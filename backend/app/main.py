from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import chat, documents, health
from app.core.config import settings


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        debug=settings.DEBUG,
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

    @app.get("/")
    def root() -> dict:
        return {"service": settings.APP_NAME, "version": settings.APP_VERSION}

    return app


app = create_app()
