from datetime import datetime

from pydantic import BaseModel, Field


class JobCreateRequest(BaseModel):
    """Request body for submitting a background job."""
    question: str = Field(min_length=1, max_length=2000)
    chat_id: int | None = None
    context_document_ids: list[int] | None = None
    document_id: int | None = None
    document_ids: list[int] | None = None


class JobResponse(BaseModel):
    id: int
    type: str
    status: str
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    result: dict | None = None
    error: str | None = None
    chat_id: int | None = None


class JobListResponse(BaseModel):
    jobs: list[JobResponse]
    total: int


class NotificationResponse(BaseModel):
    id: int
    job_id: int | None = None
    title: str
    body: str | None = None
    is_read: bool
    created_at: datetime


class NotificationListResponse(BaseModel):
    notifications: list[NotificationResponse]
    unread_count: int
    total: int
