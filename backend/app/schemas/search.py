from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    limit: int = Field(default=5, ge=1, le=50)


class SearchResultItem(BaseModel):
    document_id: int
    filename: str
    chunk_index: int
    text: str
    score: float


class SearchResponse(BaseModel):
    query: str
    results: list[SearchResultItem]
