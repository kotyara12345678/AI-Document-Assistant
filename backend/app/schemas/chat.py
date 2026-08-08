from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    document_id: int | None = Field(default=None, description="Limit the answer to a single document.")
    question: str = Field(min_length=1, max_length=2000)


class SourceRef(BaseModel):
    document_id: int
    page: int | None = None
    snippet: str = ""


class ChatResponse(BaseModel):
    answer: str
    sources: list[SourceRef] = []
