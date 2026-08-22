"""Push subscription service: register, list, and remove browser push subscriptions."""

import json
import logging

from sqlalchemy.orm import Session

from app.models.push_subscription import PushSubscription

logger = logging.getLogger("app.push")


def register_subscription(
    db: Session,
    *,
    user_id: int,
    endpoint: str,
    p256dh: str,
    auth: str,
) -> PushSubscription:
    """Register or update a browser push subscription."""
    existing = (
        db.query(PushSubscription)
        .filter(PushSubscription.user_id == user_id, PushSubscription.endpoint == endpoint)
        .first()
    )
    if existing:
        existing.p256dh = p256dh
        existing.auth = auth
        db.commit()
        db.refresh(existing)
        logger.info("Push subscription updated for user %s", user_id)
        return existing

    sub = PushSubscription(user_id=user_id, endpoint=endpoint, p256dh=p256dh, auth=auth)
    db.add(sub)
    db.commit()
    db.refresh(sub)
    logger.info("Push subscription registered for user %s (endpoint=%s…)", user_id, endpoint[:60])
    return sub


def get_user_subscriptions(db: Session, user_id: int) -> list[PushSubscription]:
    """Return all push subscriptions for a user."""
    return (
        db.query(PushSubscription)
        .filter(PushSubscription.user_id == user_id)
        .all()
    )


def remove_subscription(db: Session, *, user_id: int, endpoint: str) -> bool:
    """Remove a push subscription by endpoint. Returns True if one was deleted."""
    count = (
        db.query(PushSubscription)
        .filter(PushSubscription.user_id == user_id, PushSubscription.endpoint == endpoint)
        .delete()
    )
    if count:
        db.commit()
        logger.info("Push subscription removed for user %s", user_id)
    return count > 0


def remove_stale_subscriptions(db: Session, user_id: int) -> int:
    """Remove subscriptions whose endpoint returns 404/410 (stale).
    This is called externally when a push attempt fails with Gone."""
    # For now just return 0; actual cleanup happens on push failure in worker.
    return 0
