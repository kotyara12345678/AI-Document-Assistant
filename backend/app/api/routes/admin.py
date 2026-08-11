"""Admin-only endpoints serving aggregated, content-free statistics.

Every route here requires the authenticated ``admin`` role via
``get_current_admin`` (server-side; the frontend hiding is cosmetic only).
Responses contain aggregate counts — never document contents, chat
transcripts, passwords, JWTs or provider credentials.
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.metrics import metrics
from app.core.security import ADMIN_ROLE, get_current_admin
from app.database.session import get_db
from app.models.chat import Chat
from app.models.chat_message import ChatMessage
from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from app.models.usage_log import UsageLog
from app.models.user import User
from app.schemas.admin import AdminOverview
from app.vector.client import get_qdrant_client

router = APIRouter()


def _services_status(db: Session) -> dict:
    database_status = "ok"
    try:
        db.execute(func.now())
    except Exception:
        database_status = "unavailable"

    qdrant_status = "ok"
    try:
        get_qdrant_client().get_collections()
    except Exception:
        qdrant_status = "unavailable"

    return {
        "database": database_status,
        "qdrant": qdrant_status,
        "status": "ok" if database_status == "ok" and qdrant_status == "ok" else "degraded",
    }


@router.get("/stats", response_model=AdminOverview, tags=["admin"])
def admin_stats(
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> AdminOverview:
    """Aggregate platform statistics for administrators (no user content)."""
    now = datetime.now(timezone.utc)
    day_ago = now - timedelta(hours=24)

    users_total = db.query(User).count()
    users_new_24h = db.query(User).filter(User.created_at >= day_ago).count()
    admins_total = db.query(User).filter(User.role == ADMIN_ROLE).count()

    documents_total = db.query(Document).count()
    documents_new_24h = db.query(Document).filter(Document.created_at >= day_ago).count()
    content_chars = db.query(func.coalesce(func.sum(func.length(Document.content)), 0)).scalar()
    chunks_total = db.query(DocumentChunk).count()

    chats_total = db.query(Chat).count()
    chats_new_24h = db.query(Chat).filter(Chat.created_at >= day_ago).count()
    messages_total = db.query(ChatMessage).count()
    user_requests = db.query(ChatMessage).filter(ChatMessage.role == "user").count()

    tokens_used = db.query(func.coalesce(func.sum(UsageLog.tokens_used), 0)).scalar()

    request_metrics = metrics.snapshot()

    return AdminOverview(
        services=_services_status(db),
        users={"total": users_total, "admins": admins_total, "new_last_24h": users_new_24h},
        documents={
            "total": documents_total,
            "chunks": chunks_total,
            "total_content_chars": content_chars,
            "new_last_24h": documents_new_24h,
        },
        chats={
            "total": chats_total,
            "messages": messages_total,
            "new_last_24h": chats_new_24h,
        },
        requests={
            "api_total": request_metrics["total"],
            "llm_requests": user_requests,
            "average_latency_ms": request_metrics["average_latency_ms"],
        },
        tokens={"total_tokens_used": tokens_used},
        errors={
            "total": request_metrics["error_total"],
            "status_buckets": request_metrics["status_buckets"],
            "recent": request_metrics["recent_errors"],
        },
        generated_at=now,
    )