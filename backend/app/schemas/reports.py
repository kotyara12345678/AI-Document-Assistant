from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.report import (
    REPORT_STATUS_PENDING,
    REPORT_STATUS_REVIEWED,
    REPORT_STATUS_REJECTED,
    REPORT_STATUS_ACTION_TAKEN,
)

REPORT_REASON_MIN_LEN = 2
REPORT_REASON_MAX_LEN = 100
REPORT_DESCRIPTION_MAX_LEN = 2000


class ReportCreate(BaseModel):
    """Minimal user-facing payload to file a complaint about another user."""

    reported_user_id: int = Field(ge=1)
    reason: str = Field(min_length=REPORT_REASON_MIN_LEN, max_length=REPORT_REASON_MAX_LEN)
    description: str | None = Field(default=None, max_length=REPORT_DESCRIPTION_MAX_LEN)


class ReportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    reporter_email: str
    reported_user_id: int
    reason: str
    description: str | None
    status: str = REPORT_STATUS_PENDING
    created_at: datetime
    resolved_at: datetime | None
    resolved_by_email: str | None


# Advertised in OpenAPI so the report lifecycle (pending/reviewed/rejected/
# action_taken) is discoverable without hardcoding statuses in every schema.
REPORT_STATUSES = [
    REPORT_STATUS_PENDING,
    REPORT_STATUS_REVIEWED,
    REPORT_STATUS_REJECTED,
    REPORT_STATUS_ACTION_TAKEN,
]