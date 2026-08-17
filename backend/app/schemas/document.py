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
