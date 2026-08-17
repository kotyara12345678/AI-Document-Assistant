"""Admin/moderation user management.

Server-side role enforcement is the only thing that matters: the frontend only
hides the panel, it never decides access. Permission model:

* ``admin``     — full user management: list/search, role assignment
                  (user/moderator/admin), block/unblock, soft-delete, reports.
* ``moderator`` — moderation surface only: list/search (for context), block of
                  *regular users* and report review. Cannot change roles, block
                  staff or delete accounts.
* ``user``      — nothing here (403).

Soft delete (``is_deleted``) is used instead of physical deletion: documents,
chats, usage logs and reports hold FK references to the user row, so removing
the row would either fail or cascade away moderation history.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.security import (
    MODERATOR_ROLE,
    STAFF_ROLES,
    get_current_admin,
    get_current_moderator,
)
from app.database.session import get_db
from app.models.report import ACTIVE_REPORT_STATUSES, Report
from app.models.user import User
from app.schemas.admin_users import (
    AdminReportList,
    AdminUserList,
    AdminUserOut,
    UserRoleUpdate,
    UserStatusUpdate,
)
from app.schemas.reports import ReportOut

router = APIRouter()


def _live_user_or_404(db: Session, user_id: int) -> User:
    """Fetch a non-deleted user row, or 404 (deleted users act as gone)."""
    user = db.get(User, user_id)
    if user is None or user.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


def _pending_report_counts(db: Session, user_ids: list[int]) -> dict[int, int]:
    """Map user_id -> number of active (pending/reviewed) reports they have."""
    if not user_ids:
        return {}
    rows = (
        db.query(Report.reported_user_id, func.count(Report.id))
        .filter(
            Report.reported_user_id.in_(user_ids),
            Report.status.in_(ACTIVE_REPORT_STATUSES),
        )
        .group_by(Report.reported_user_id)
        .all()
    )
    return {reported: count for reported, count in rows}


def _user_to_out(db: Session, user: User, counts: dict[int, int]) -> AdminUserOut:
    return AdminUserOut(
        id=user.id,
        email=user.email,
        role=user.role,
        created_at=user.created_at,
        last_active_at=user.last_active_at,
        is_active=user.is_active,
        is_deleted=user.is_deleted,
        reports_active=counts.get(user.id, 0),
    )


def _report_to_out(db: Session, report: Report) -> ReportOut:
    reporter = db.get(User, report.reporter_id)
    resolver = db.get(User, report.resolved_by) if report.resolved_by is not None else None
    return ReportOut(
        id=report.id,
        reporter_email=reporter.email if reporter else "—",
        reported_user_id=report.reported_user_id,
        reason=report.reason,
        description=report.description,
        status=report.status,
        created_at=report.created_at,
        resolved_at=report.resolved_at,
        resolved_by_email=resolver.email if resolver else None,
    )


@router.get("/users", response_model=AdminUserList, tags=["admin"])
def admin_users_list(
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    search: str | None = Query(default=None, max_length=100),
    staff: User = Depends(get_current_moderator),
    db: Session = Depends(get_db),
) -> AdminUserList:
    """Paginated list of registered (non-deleted) users with moderation counts.

    ``search`` matches a substring of the email (case-insensitive). Only
    enough rows for one page are loaded — no full-table scan into memory.
    """
    query = db.query(User).filter(User.is_deleted.is_(False))

    term = (search or "").strip().lower()
    if term:
        query = query.filter(func.lower(User.email).contains(term))

    total = query.with_entities(func.count(User.id)).scalar() or 0
    users = (
        query.order_by(User.id.desc())
        .offset((page - 1) * limit)
        .limit(limit)
        .all()
    )

    counts = _pending_report_counts(db, [u.id for u in users])
    return AdminUserList(
        items=[_user_to_out(db, u, counts) for u in users],
        total=total,
        page=page,
        limit=limit,
    )


@router.get("/users/{user_id}", response_model=AdminUserOut, tags=["admin"])
def admin_user_get(
    user_id: int,
    staff: User = Depends(get_current_moderator),
    db: Session = Depends(get_db),
) -> AdminUserOut:
    user = _live_user_or_404(db, user_id)
    return _user_to_out(db, user, _pending_report_counts(db, [user.id]))


@router.patch("/users/{user_id}/role", response_model=AdminUserOut, tags=["admin"])
def admin_user_role(
    user_id: int,
    payload: UserRoleUpdate,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> AdminUserOut:
    """Assign user/moderator/admin. Admin-only — moderators cannot escalate."""
    user = _live_user_or_404(db, user_id)
    if user.id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot change your own role",
        )
    user.role = payload.role
    db.commit()
    db.refresh(user)
    return _user_to_out(db, user, _pending_report_counts(db, [user.id]))


@router.patch("/users/{user_id}/status", response_model=AdminUserOut, tags=["admin"])
def admin_user_status(
    user_id: int,
    payload: UserStatusUpdate,
    staff: User = Depends(get_current_moderator),
    db: Session = Depends(get_db),
) -> AdminUserOut:
    """Block (is_active=False) / unblock a user.

    Moderators may moderate regular users only; staff accounts (admins and
    moderators) can only be toggled by an admin. Nobody may block themselves.
    """
    user = _live_user_or_404(db, user_id)
    if user.id == staff.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot change your own account status",
        )
    if user.role in STAFF_ROLES and staff.role == MODERATOR_ROLE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Moderators cannot block or unblock staff accounts",
        )
    user.is_active = payload.is_active
    db.commit()
    db.refresh(user)
    return _user_to_out(db, user, _pending_report_counts(db, [user.id]))


@router.delete("/users/{user_id}", tags=["admin"])
def admin_user_delete(
    user_id: int,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> dict:
    """Soft-delete a user: no FK chains are broken, no data is destroyed.

    The row is marked deleted (and deactivated) so it disappears from the user
    list and can no longer authenticate, while documents, chats, usage logs and
    reports keep valid references.
    """
    user = _live_user_or_404(db, user_id)
    if user.id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot delete your own account",
        )
    user.is_deleted = True
    user.is_active = False
    user.deleted_at = datetime.now(timezone.utc)
    db.commit()
    return {"deleted": True, "user_id": user.id}


@router.get("/users/{user_id}/reports", response_model=AdminReportList, tags=["admin"])
def admin_user_reports(
    user_id: int,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    staff: User = Depends(get_current_moderator),
    db: Session = Depends(get_db),
) -> AdminReportList:
    _live_user_or_404(db, user_id)

    base = db.query(Report).filter(Report.reported_user_id == user_id)
    total = base.with_entities(func.count(Report.id)).scalar() or 0
    reports = (
        base.order_by(Report.created_at.desc())
        .offset((page - 1) * limit)
        .limit(limit)
        .all()
    )
    return AdminReportList(
        items=[_report_to_out(db, r) for r in reports],
        total=total,
        page=page,
        limit=limit,
    )