from datetime import datetime

from pydantic import BaseModel, ConfigDict


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    filename: str
    original_filename: str
    file_type: str
    file_size: int
    content_length: int
    created_at: datetime
    # Provenance links (informational): the chat that produced the file and the
    # immutable original it was edited from (null for plain uploads).
    chat_id: int | None = None
    source_file_id: int | None = None


class CompareRequest(BaseModel):
    left_id: int
    right_id: int


class CompareDocumentRef(BaseModel):
    id: int
    original_filename: str
    file_type: str
    content_length: int
    created_at: datetime | None = None
    source_file_id: int | None = None


class DiffOperation(BaseModel):
    kind: str
    left_start: int
    left_end: int
    right_start: int
    right_end: int


class CompareSummary(BaseModel):
    added_lines: int
    removed_lines: int
    changed_lines: int
    unchanged_lines: int


class CompareResponse(BaseModel):
    left: CompareDocumentRef
    right: CompareDocumentRef
    left_lines: list[str]
    right_lines: list[str]
    operations: list[DiffOperation]
    summary: CompareSummary
    equal: bool
    truncated: bool
    limit: int
