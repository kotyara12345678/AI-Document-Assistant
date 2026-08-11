from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ChatRequest(BaseModel):
    chat_id: int | None = Field(
        default=None,
        description="Chat to run this turn in. When omitted, the most recently used chat is used (or a new one is created).",
    )
    document_id: int | None = Field(default=None, description="Limit the answer to a single document.")
    document_ids: list[int] | None = Field(
        default=None,
        description="Limit the answer to several documents at once. Takes precedence over document_id.",
    )
    question: str = Field(min_length=1, max_length=2000)


class SourceRef(BaseModel):
    document_id: int
    filename: str = ""
    chunk_index: int = 0
    score: float = 0.0
    text: str = ""


class ChatResponse(BaseModel):
    chat_id: int
    answer: str
    sources: list[SourceRef] = []


class ChatCreate(BaseModel):
    title: str | None = Field(default=None, max_length=255)


class ChatOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    created_at: datetime
    updated_at: datetime


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    chat_id: int
    role: str
    content: str
    created_at: datetime
