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

    # --- Embeddings ---
    EMBEDDING_MODEL: str = "sentence-transformers/all-MiniLM-L6-v2"
    # Vector dimension is derived automatically from the model, not hardcoded.

    # --- Chunking ---
    CHUNK_SIZE: int = 600
    CHUNK_OVERLAP: int = 100

    # --- File uploads ---
    UPLOAD_DIR: str = "/data/uploads"
    MAX_UPLOAD_SIZE_MB: int = 50
    ALLOWED_EXTENSIONS: list[str] = ["pdf", "txt", "docx"]

    # --- CORS ---
    CORS_ORIGINS: list[str] = ["http://localhost:5173", "http://localhost:3000"]

    # --- Auth (JWT) ---
    # Secret used to sign access tokens (HS256). MUST be overridden via the
    # JWT_SECRET environment variable in real deployments; the value below is
    # a development default only.
    JWT_SECRET: str = "dev-secret-change-me"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days

    # --- LLM (GigaChat, OAuth 2.0 client-credentials + OpenAI-compatible API) ---
    # Authorization: "Basic base64(GIGACHAT_CLIENT_ID:GIGACHAT_CLIENT_SECRET)"
    GIGACHAT_CLIENT_ID: str | None = None
    GIGACHAT_CLIENT_SECRET: str | None = None
    GIGACHAT_SCOPE: str = "GIGACHAT_API_PERS"
    GIGACHAT_BASE_URL: str = "https://gigachat.devices.sberbank.ru/api/v1"
    # Supported: GigaChat-Lite, GigaChat-Pro, GigaChat-Max, GigaChat-Ultra, GigaChat
    GIGACHAT_MODEL: str = "GigaChat-Max"
    GIGACHAT_TEMPERATURE: float = 0.2
    GIGACHAT_MAX_TOKENS: int = 2048
    GIGACHAT_TIMEOUT: float = 60.0
    # OAuth access token TTL: refresh every 30 minutes.
    GIGACHAT_TOKEN_TTL_SECONDS: int = 1800
    CHAT_TOP_K: int = 5

    # --- Reranker (cross-encoder re-ranking of hybrid candidates) ---
    # When enabled, hybrid retrieval first fetches RERANKER_CANDIDATES chunks
    # and the reranker re-orders them before the final top_k are sent to the
    # LLM. Disabled by default so the exported scores keep the hybrid scale.
    RERANKER_ENABLED: bool = False
    RERANKER_MODEL: str = "BAAI/bge-reranker-v2-m3"
    RERANKER_DEVICE: str = "cpu"
    # Size of the candidate pool fetched before re-ranking (>= CHAT_TOP_K).
    RERANKER_CANDIDATES: int = 30
    # Chunks are truncated to this many characters for re-ranking (cost control).
    RERANKER_MAX_CHARS: int = 1000

    # --- Chat history context (saved in PostgreSQL, sent to GigaChat) ---
    # Number of most recent messages sent verbatim to the LLM on each turn.
    CHAT_HISTORY_MESSAGES: int = 6
    # When the stored history grows beyond this many messages, older turns are
    # collapsed into a rolling summary so the request stays token-cheap.
    CHAT_SUMMARY_THRESHOLD: int = 20
    # Summary prompt controls how the rolling history summary is produced.
    CHAT_SUMMARY_INSTRUCTION: str = (
        "Summarize the following conversation so far into a concise paragraph "
        "that captures the user's intent, key facts and open questions. "
        "Keep it in the same language as the conversation. "
        "Preserve names, numbers and decisions verbatim where relevant."
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
