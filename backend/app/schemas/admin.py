from datetime import datetime

from pydantic import BaseModel, ConfigDict

# All admin statistics are aggregate counts only. They intentionally never
# include document content (app/models/document.py content), chat transcripts
# or any kind of credential/token value.


class ServiceStatus(BaseModel):
    model_config = ConfigDict(use_enum_values=False)

    database: str
    qdrant: str
    status: str


class UserStats(BaseModel):
    total: int
    admins: int
    new_last_24h: int


class DocumentStats(BaseModel):
    total: int
    chunks: int
    total_content_chars: int
    new_last_24h: int


class ChatStats(BaseModel):
    total: int
    messages: int
    new_last_24h: int


class RequestStats(BaseModel):
    api_total: int
    llm_requests: int
    average_latency_ms: float


class TokenStats(BaseModel):
    total_tokens_used: int


class ErrorEntry(BaseModel):
    timestamp: datetime
    status: int
    path: str


class ErrorStats(BaseModel):
    total: int
    status_buckets: dict[str, int]
    recent: list[ErrorEntry]


class AdminOverview(BaseModel):
    services: ServiceStatus
    users: UserStats
    documents: DocumentStats
    chats: ChatStats
    requests: RequestStats
    tokens: TokenStats
    errors: ErrorStats
    generated_at: datetime