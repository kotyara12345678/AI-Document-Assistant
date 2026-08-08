from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration loaded from environment / .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    APP_NAME: str = "AI Document Assistant"
    APP_VERSION: str = "0.1.0"
    API_PREFIX: str = "/api"
    DEBUG: bool = False

    # --- Database (PostgreSQL via SQLAlchemy) ---
    DATABASE_URL: str = "postgresql+psycopg2://docassistant:docassistant@db:5432/docassistant"
    DB_ECHO: bool = False

    # --- Vector database (Qdrant) ---
    QDRANT_URL: str = "http://qdrant:6333"
    QDRANT_API_KEY: str | None = None
    QDRANT_COLLECTION: str = "document_chunks"
    QDRANT_VECTOR_SIZE: int = 768

    # --- File uploads ---
    UPLOAD_DIR: str = "/data/uploads"
    MAX_UPLOAD_SIZE_MB: int = 50
    ALLOWED_EXTENSIONS: list[str] = ["pdf", "docx"]

    # --- CORS ---
    CORS_ORIGINS: list[str] = ["http://localhost:5173", "http://localhost:3000"]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
