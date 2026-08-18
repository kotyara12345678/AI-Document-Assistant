"""User-facing personal account endpoints.

Everything here is scoped to the authenticated user only: aggregated usage
counts (never document contents or chat transcripts) plus self soft-delete,
which mirrors the admin soft-delete so referential integrity is preserved.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.security import get_current_user_id
from app.database.session import get_db
from app.models.chat import Chat
from app.models.chat_message import ChatMessage
from app.models.document import Document
from app.models.usage_log import UsageLog
from app.models.user import User
from app.schemas.auth import UserOut
from app.schemas.me import MeStats

router = APIRouter()


@router.get("/stats", response_model=MeStats)
def me_stats(
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> MeStats:
    """Aggregate usage statistics for the authenticated user only."""
    user = db.get(User, user_id)

    documents_total = db.query(Document).filter(Document.user_id == user_id).count()
    chats_total = db.query(Chat).filter(Chat.user_id == user_id).count()
    messages_total = (
        db.query(ChatMessage).filter(ChatMessage.user_id == user_id).count()
    )
    tokens_used = (
        db.query(func.coalesce(func.sum(UsageLog.tokens_used), 0))
        .filter(UsageLog.user_id == user_id)
        .scalar()
    )

    return MeStats(
        user=UserOut.model_validate(user),
        documents_total=documents_total,
        chats_total=chats_total,
        messages_total=messages_total,
        tokens_used=tokens_used,
        last_active_at=user.last_active_at,
    )


@router.delete("", status_code=200)
def delete_me(
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> dict:
    """Soft-delete your own account (the token becomes invalid right away).

    Mirrors admin soft-delete: the row is marked deleted and deactivated, so
    the account can no longer authenticate, while documents, chats, usage logs
    and reports keep valid references. No data is physically destroyed.
    """
    user = db.get(User, user_id)
    user.is_deleted = True
    user.is_active = False
    user.deleted_at = datetime.now(timezone.utc)
    db.commit()
    return {"deleted": True, "user_id": user_id}
