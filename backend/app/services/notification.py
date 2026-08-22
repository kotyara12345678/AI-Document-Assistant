"""Notification service: create and manage user notifications."""

import logging

from sqlalchemy.orm import Session

from app.models.notification import Notification

logger = logging.getLogger("app.notifications")


def create_notification(
    db: Session,
    *,
    user_id: int,
    job_id: int | None = None,
    title: str,
    body: str | None = None,
) -> Notification:
    """Persist a notification for a user."""
    notif = Notification(
        user_id=user_id,
        job_id=job_id,
        title=title,
        body=body,
    )
    db.add(notif)
    db.commit()
    db.refresh(notif)
    logger.info("Notification %s created for user %s: %s", notif.id, user_id, title)
    return notif


def get_user_notifications(
    db: Session,
    user_id: int,
    *,
    unread_only: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> list[Notification]:
    """List notifications for a user, newest first."""
    query = db.query(Notification).filter(Notification.user_id == user_id)
    if unread_only:
        query = query.filter(Notification.is_read == False)  # noqa: E712
    return query.order_by(Notification.created_at.desc()).offset(offset).limit(limit).all()


def get_unread_count(db: Session, user_id: int) -> int:
    """Count unread notifications for a user."""
    return (
        db.query(Notification.id)
        .filter(Notification.user_id == user_id, Notification.is_read == False)  # noqa: E712
        .count()
    )


def mark_read(db: Session, notification_id: int, user_id: int) -> Notification | None:
    """Mark a single notification as read. Returns None if not found."""
    notif = (
        db.query(Notification)
        .filter(Notification.id == notification_id, Notification.user_id == user_id)
        .first()
    )
    if notif is None:
        return None
    notif.is_read = True
    db.commit()
    db.refresh(notif)
    return notif


def mark_all_read(db: Session, user_id: int) -> int:
    """Mark all unread notifications as read. Returns count updated."""
    count = (
        db.query(Notification)
        .filter(Notification.user_id == user_id, Notification.is_read == False)  # noqa: E712
        .update({Notification.is_read: True}, synchronize_session=False)
    )
    db.commit()
    return count
