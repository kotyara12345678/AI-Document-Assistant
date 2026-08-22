"""Push subscription API endpoints.

POST   /push/subscribe    — register a browser push subscription
POST   /push/unsubscribe  — remove a push subscription
GET    /push/key          — return the VAPID public key
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import get_current_user_id
from app.database.session import get_db
from app.services import push as push_service

logger = logging.getLogger("app.api.push")

router = APIRouter()


class SubscribeRequest(BaseModel):
    endpoint: str
    p256dh: str
    auth: str


class UnsubscribeRequest(BaseModel):
    endpoint: str


@router.get("/key")
def vapid_key():
    """Return the VAPID public key so the frontend can subscribe."""
    if not settings.VAPID_PUBLIC_KEY:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Web push not configured",
        )
    return {"public_key": settings.VAPID_PUBLIC_KEY}


@router.post("/subscribe", status_code=status.HTTP_201_CREATED)
def subscribe(
    request: SubscribeRequest,
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    push_service.register_subscription(
        db,
        user_id=user_id,
        endpoint=request.endpoint,
        p256dh=request.p256dh,
        auth=request.auth,
    )
    return {"status": "ok"}


@router.post("/unsubscribe")
def unsubscribe(
    request: UnsubscribeRequest,
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    push_service.remove_subscription(
        db,
        user_id=user_id,
        endpoint=request.endpoint,
    )
    return {"status": "ok"}
