"""User-facing report submission.

Minimal moderation input: an authenticated regular user can file a complaint
about another user. Resolution/status handling happens in the admin/moderation
surface (admin_users.py); this route only creates pending reports.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.security import get_current_user_id
from app.database.session import get_db
from app.models.report import (
    REPORT_STATUS_PENDING,
    Report,
)
from app.models.user import User
from app.schemas.reports import ReportCreate, ReportOut

router = APIRouter()


@router.post("", response_model=ReportOut, status_code=status.HTTP_201_CREATED)
def create_report(
    payload: ReportCreate,
    actor_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> ReportOut:
    """File a complaint against another user (cannot target yourself)."""
    actor = db.get(User, actor_id)
    if actor is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    if payload.reported_user_id == actor.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot report yourself",
        )

    target = db.get(User, payload.reported_user_id)
    if target is None or target.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Reported user not found",
        )

    report = Report(
        reporter_id=actor.id,
        reported_user_id=target.id,
        reason=payload.reason.strip(),
        description=(payload.description or "").strip() or None,
        status=REPORT_STATUS_PENDING,
    )
    db.add(report)
    db.commit()
    db.refresh(report)

    return ReportOut(
        id=report.id,
        reporter_email=actor.email,
        reported_user_id=report.reported_user_id,
        reason=report.reason,
        description=report.description,
        status=report.status,
        created_at=report.created_at,
        resolved_at=None,
        resolved_by_email=None,
    )