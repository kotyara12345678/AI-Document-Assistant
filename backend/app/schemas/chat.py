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
    context_document_ids: list[int] | None = Field(
        default=None,
        description=(
            "Documents the user explicitly attached as context for this turn "
            "(e.g. by double-clicking them in the UI). Takes precedence over "
            "document_ids/document_id and over RAG retrieval."
        ),
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


class ChatUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=255)


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
    # Links the message to a file it produced/edited so the file card can be
    # restored from the database after a page reload (never stored in JS state).
    document_id: int | None = None
    # Documents explicitly attached as context for this turn (UI chips).
    context_document_ids: list[int] | None = None
