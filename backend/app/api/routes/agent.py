from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.ratelimit import AGENT_BURST_LIMIT, AGENT_BURST_WINDOW, throttle
from app.core.security import get_current_user_id
from app.database.session import get_db
from app.schemas.agent import AgentRequest, AgentResponse
from app.services.agent import agent_service

router = APIRouter()


@router.post("", response_model=AgentResponse)
def run_agent(
    request: AgentRequest,
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> AgentResponse:
    # Same LLM cost guard as /api/chat: a per-user burst window stops runaway
    # request loops well before GigaChat is hit.
    if not throttle.allow(f"agent:{user_id}", AGENT_BURST_LIMIT, AGENT_BURST_WINDOW):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests, please slow down",
        )
    return agent_service.run_agent(request, user_id=user_id, db=db)
