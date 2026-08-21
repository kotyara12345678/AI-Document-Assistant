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

    # Deployment environment. "production" enables extra safety checks (e.g.
    # refusing to start with the default JWT_SECRET); anything else (the
    # default "development") keeps the app usable for local runs and tests.
    ENVIRONMENT: str = "development"

    # --- Database (PostgreSQL via SQLAlchemy) ---
    DATABASE_URL: str = "postgresql+psycopg2://docassistant:docassistant@db:5432/docassistant"
    DB_ECHO: bool = False

    # --- Vector database (Qdrant) ---
    QDRANT_URL: str = "http://qdrant:6333"
    QDRANT_API_KEY: str | None = None
    QDRANT_COLLECTION: str = "document_chunks"
    # Per-request timeout (seconds) and automatic retry budget for Qdrant calls
    # so a slow Qdrant does not stall every RAG turn indefinitely.
    QDRANT_TIMEOUT: float = 10.0
    QDRANT_RETRIES: int = 2

    # --- Embeddings ---
    EMBEDDING_MODEL: str = "sentence-transformers/all-MiniLM-L6-v2"
    # Vector dimension is derived automatically from the model, not hardcoded.

    # --- Chunking ---
    # Sized for all-MiniLM-L6-v2's 256-token context (≈180 words for RU/EN):
    # a larger chunk would be silently truncated by the model on encode, so the
    # tail of every chunk would never influence its vector.
    CHUNK_SIZE: int = 180
    CHUNK_OVERLAP: int = 30

    # --- File uploads ---
    UPLOAD_DIR: str = "/data/uploads"
    MAX_UPLOAD_SIZE_MB: int = 50
    MAX_UPLOAD_FILES: int = 5
    ALLOWED_EXTENSIONS: list[str] = ["pdf", "txt", "docx", "md", "odt"]
    # Cap on the total UNCOMPRESSED size of container-format uploads (DOCX and
    # ODT are both ZIP packs). A 50 MB file could pack many times that, so this
    # stops zip bombs from ballooning memory during text extraction.
    ZIP_UNCOMPRESSED_MAX_MB: int = 512
    # Cap on extracted text kept per document. Pathological files (or one big
    # text) cannot grow the content column, chunking memory or embedding time
    # without bound; extractions beyond this are truncated.
    MAX_EXTRACTED_CHARS: int = 5_000_000

    # --- CORS ---
    CORS_ORIGINS: list[str] = ["http://localhost:5173", "http://localhost:3000"]

    # --- Auth (JWT) ---
    # Secret used to sign access tokens (HS256). MUST be overridden via the
    # JWT_SECRET environment variable in real deployments; the value below is
    # a development default only.
    JWT_SECRET: str = "dev-secret-change-me"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days

    # --- Admin access ---
    # Users with these (lowercased) emails are granted the "admin" role at
    # startup. Access to /api/admin endpoints is enforced server-side by the
    # role field — never by the UI alone.
    ADMIN_EMAILS: list[str] = ["demo@example.com"]

    # --- LLM (GigaChat, OAuth 2.0 client-credentials + OpenAI-compatible API) ---
    # Authorization: "Basic base64(GIGACHAT_CLIENT_ID:GIGACHAT_CLIENT_SECRET)"
    GIGACHAT_CLIENT_ID: str | None = None
    GIGACHAT_CLIENT_SECRET: str | None = None
    GIGACHAT_SCOPE: str = "GIGACHAT_API_PERS"
    GIGACHAT_BASE_URL: str = "https://gigachat.devices.sberbank.ru/api/v1"
    # OAuth token endpoint. Kept configurable so CI/E2E can point the client at
    # a local mock LLM instead of the real GigaChat API.
    GIGACHAT_AUTH_URL: str = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
    # Supported: GigaChat-Lite, GigaChat-Pro, GigaChat-Max, GigaChat-Ultra, GigaChat
    GIGACHAT_MODEL: str = "GigaChat-Max"
    GIGACHAT_TEMPERATURE: float = 0.2
    GIGACHAT_MAX_TOKENS: int = 2048
    GIGACHAT_TIMEOUT: float = 60.0
    # Read timeout is deliberately larger than the connect/write timeout: large
    # structured PDF-edit prompts make GigaChat take a long time to *produce*
    # the answer, but the connection itself should still fail fast.
    GIGACHAT_READ_TIMEOUT: float = 300.0
    # OAuth access token TTL: refresh every 30 minutes.
    GIGACHAT_TOKEN_TTL_SECONDS: int = 1800
    CHAT_TOP_K: int = 5

    # --- Agent layer (function calling over the existing retrieval pipeline) ---
    # How many ranked chunks a search_documents tool call returns to the model.
    AGENT_TOP_K: int = 3
    # Maximum number of tool-call rounds in one agent turn (bounds LLM cost).
    # High enough for the search -> read -> create chain plus a retry after a
    # hallucinated document id.
    AGENT_MAX_TOOL_ROUNDS: int = 5
    # Max characters of document text a read_document tool call returns per
    # call. Longer documents are read in windows via the tool's `offset` arg
    # instead of flooding the LLM context.
    AGENT_READ_MAX_CHARS: int = 8000
    # Bounds for documents the agent may create. The LLM only produces a
    # structured DocumentSpec; these caps are enforced by Pydantic validation
    # before any file is rendered, so an oversized spec can never blow up the
    # generator or the user's storage.
    AGENT_DOCUMENT_MAX_CHARS: int = 60_000
    AGENT_DOCUMENT_MAX_SECTIONS: int = 50
    AGENT_DOCUMENT_MAX_PARAGRAPHS: int = 200
    AGENT_DOCUMENT_MAX_LINE_CHARS: int = 2_000
    AGENT_DOCUMENT_MAX_LIST_ITEMS: int = 200
    AGENT_DOCUMENT_MAX_TABLE_ROWS: int = 100
    AGENT_DOCUMENT_DEFAULT_TITLE: str = "document"

    # --- Multi-stage document generation pipeline ---
    # When the LLM requests pipeline mode (pipeline=True in create_document),
    # the backend generates a large document in stages: outline → section-by-
    # section generation → assembly → consistency check. This bypasses the
    # single-response token limit (GIGACHAT_MAX_TOKENS) for large documents.
    DOCUMENT_PIPELINE_ENABLED: bool = True
    # Max tokens for outline/section generation calls (larger than agent default).
    DOCUMENT_PIPELINE_MAX_TOKENS: int = 4096
    # Max sections the pipeline will generate.
    DOCUMENT_PIPELINE_MAX_SECTIONS: int = 30
    # Max retries per section generation call.
    DOCUMENT_PIPELINE_SECTION_RETRIES: int = 2
    # Timeout (seconds) for a single section generation call.
    DOCUMENT_PIPELINE_SECTION_TIMEOUT: float = 120.0
    # Max total retries across all sections before aborting.
    DOCUMENT_PIPELINE_MAX_TOTAL_RETRIES: int = 10
    # Consistency check: after assembly, ask LLM to review for contradictions.
    DOCUMENT_PIPELINE_CONSISTENCY_CHECK: bool = True

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

    # --- Metadata-aware RAG ---
    # Before retrieval the LLM is asked (cheap, bounded) whether the question
    # actually needs document metadata (upload date, file name/size/type). When
    # it does not, ONLY chunk text reaches the model — no metadata headers are
    # injected. When it does, only the requested (available) fields are added,
    # and a named document can be used to pre-filter retrieval.
    CHAT_METADATA_CLASSIFIER_ENABLED: bool = True
    CHAT_METADATA_CLASSIFIER_INSTRUCTION: str = (
        "You decide whether a user question needs DOCUMENT METADATA to be "
        "answered. Allowed metadata fields, with their meaning: "
        "original_filename (name of the document file), "
        "file_type (pdf/txt/docx), "
        "file_size (size in bytes, integer), "
        "content_length (length of extracted text in chars, integer), "
        "created_at (upload date and time, ISO 8601). "
        "Page numbers, authors, or any other fields are NOT available and "
        "must never be requested. "
        "A question needs metadata only when the answer actually depends on "
        "those fields: e.g. 'когда загружен документ' (created_at), 'сколько "
        "весит файл' (file_size), 'что это за файл' (original_filename, "
        "file_type). "
        "Content questions, explanations, summaries and 'на какой странице' "
        "type questions need NO metadata (page info does not exist). "
        "Set target_filename ONLY when the user explicitly asks to search "
        "within one named document and retrieval should be limited to it; "
        "otherwise null. Use the exact file name when given. "
        "Respond with ONLY a JSON object of the form: "
        '{"needs_metadata": bool, "fields": [<subset of allowed fields>], '
        '"target_filename": <string or null>}. No other text.'
    )

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
