from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.core.security import ADMIN_ROLE, MODERATOR_ROLE, USER_ROLE
from app.schemas.reports import ReportOut

# Role transitions are privilege escalation; only admins may set them and the
# set is fully enumerated so a frontend can never invent a new role.
AllowedRole = Literal[USER_ROLE, MODERATOR_ROLE, ADMIN_ROLE]


class UserRoleUpdate(BaseModel):
    role: AllowedRole


class UserStatusUpdate(BaseModel):
    is_active: bool


class AdminUserOut(BaseModel):
    """One row of the admin user list.

    Aggregate moderation data only: the account identity (email), role,
    account status, registration/activity timestamps and the count of *active*
    (pending/reviewed) reports. Never exposes password hashes or tokens.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    role: str = USER_ROLE
    created_at: datetime
    last_active_at: datetime | None
    is_active: bool
    is_deleted: bool
    reports_active: int = 0


class AdminUserList(BaseModel):
    items: list[AdminUserOut]
    total: int
    page: int
    limit: int


class AdminReportList(BaseModel):
    items: list[ReportOut]
    total: int
    page: int
    limit: int