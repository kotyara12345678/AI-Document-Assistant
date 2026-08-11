from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.ratelimit import CHAT_BURST_LIMIT, CHAT_BURST_WINDOW, throttle
from app.core.security import get_current_user_id
from app.database.session import get_db
from app.schemas.chat import ChatRequest, ChatResponse
from app.services import chat as chat_service

router = APIRouter()


@router.post("", response_model=ChatResponse)
def ask_chat(
    request: ChatRequest,
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> ChatResponse:
    # Keep LLM spend bounded: a per-user burst window stops runaway request
    # loops well before the LLM is hit (cheap, in-memory, no middleware).
    if not throttle.allow(f"chat:{user_id}", CHAT_BURST_LIMIT, CHAT_BURST_WINDOW):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests, please slow down",
        )
    return chat_service.answer_question(request, user_id=user_id, db=db)
