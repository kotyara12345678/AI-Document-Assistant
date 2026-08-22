"""Notification API endpoints.

GET    /notifications              — list notifications
POST   /notifications/{id}/read   — mark as read
POST   /notifications/read-all     — mark all as read
GET    /notifications/stream       — SSE stream of new notifications
"""

import json
import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.security import get_current_user_id
from app.database.session import get_db
from app.schemas.job import NotificationListResponse, NotificationResponse
from app.services import notification as notification_service

logger = logging.getLogger("app.api.notifications")

router = APIRouter()


@router.get("", response_model=NotificationListResponse)
def list_notifications(
    unread_only: bool = Query(default=False),
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> NotificationListResponse:
    notifs = notification_service.get_user_notifications(db, user_id, unread_only=unread_only)
    unread = notification_service.get_unread_count(db, user_id)
    return NotificationListResponse(
        notifications=[
            NotificationResponse(
                id=n.id,
                job_id=n.job_id,
                title=n.title,
                body=n.body,
                is_read=n.is_read,
                created_at=n.created_at,
            )
            for n in notifs
        ],
        unread_count=unread,
        total=len(notifs),
    )


@router.post("/{notification_id}/read", response_model=NotificationResponse)
def mark_notification_read(
    notification_id: int,
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> NotificationResponse:
    notif = notification_service.mark_read(db, notification_id, user_id)
    if notif is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notification not found",
        )
    return NotificationResponse(
        id=notif.id,
        job_id=notif.job_id,
        title=notif.title,
        body=notif.body,
        is_read=notif.is_read,
        created_at=notif.created_at,
    )


@router.post("/read-all", status_code=status.HTTP_200_OK)
def mark_all_notifications_read(
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> dict:
    count = notification_service.mark_all_read(db, user_id)
    return {"marked_read": count}


@router.get("/stream")
def stream_notifications(
    user_id: int = Depends(get_current_user_id),
):
    """SSE stream that pushes new notifications to the client.

    Polls the database every 3 seconds. When a new notification arrives,
    it is sent as an SSE event and marked as read (so it won't be
    re-sent on reconnect). The stream terminates after 5 minutes of
    inactivity (client should reconnect).
    """
    from app.database.session import SessionLocal

    def event_generator():
        db = SessionLocal()
        last_id = 0
        try:
            # Start from the latest notification id so we don't re-send old ones.
            latest = (
                db.query(notification_service.Notification.id)
                .filter(notification_service.Notification.user_id == user_id)
                .order_by(notification_service.Notification.id.desc())
                .first()
            )
            if latest:
                last_id = latest[0]

            start = time.monotonic()
            while time.monotonic() - start < 300:  # 5 minute timeout
                notifs = (
                    db.query(notification_service.Notification)
                    .filter(
                        notification_service.Notification.user_id == user_id,
                        notification_service.Notification.id > last_id,
                    )
                    .order_by(notification_service.Notification.id.asc())
                    .all()
                )
                for n in notifs:
                    event = json.dumps(
                        {
                            "type": "notification",
                            "id": n.id,
                            "job_id": n.job_id,
                            "title": n.title,
                            "body": n.body,
                            "created_at": n.created_at.isoformat(),
                        },
                        ensure_ascii=False,
                    )
                    yield f"data: {event}\n\n"
                    # Mark as read so reconnection won't re-send
                    n.is_read = True
                    last_id = n.id
                db.commit()
                time.sleep(3)
        except GeneratorExit:
            pass
        finally:
            db.close()

    return StreamingResponse(event_generator(), media_type="text/event-stream")
