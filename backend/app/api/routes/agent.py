import json
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.ratelimit import AGENT_BURST_LIMIT, AGENT_BURST_WINDOW, throttle
from app.core.security import get_current_user_id
from app.database.session import get_db
from app.models.document import Document
from app.schemas.agent import AgentRequest, AgentResponse
from app.services.agent import agent_service

router = APIRouter()

logger = logging.getLogger("app.agent")


def _require_owned_documents(db: Session, user_id: int, ids: list[int] | None) -> None:
    """Reject requests that attach documents the user does not own (404)."""
    if not ids:
        return
    owned = {
        row[0]
        for row in db.query(Document.id)
        .filter(Document.id.in_(ids), Document.user_id == user_id)
        .all()
    }
    missing = [i for i in ids if i not in owned]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Один или несколько прикреплённых документов недоступны",
        )


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
    _require_owned_documents(db, user_id, request.context_document_ids)
    return agent_service.run_agent(request, user_id=user_id, db=db)


@router.post("/stream")
def run_agent_stream(
    request: AgentRequest,
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    """Realtime agent endpoint.

    Streams ``text/event-stream`` SSE frames, one ``data: <json>`` per agent
    event (``agent_step`` running/completed/error, ``document_created``,
    ``final``). No chain-of-thought or internal prompts are ever sent. The
    non-streaming ``POST /api/agent`` remains available for backward compat.
    """
    if not throttle.allow(f"agent:{user_id}", AGENT_BURST_LIMIT, AGENT_BURST_WINDOW):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests, please slow down",
        )
    _require_owned_documents(db, user_id, request.context_document_ids)

    def event_generator():
        try:
            for event in agent_service.run_agent_stream(request, user_id=user_id, db=db):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception:
            # A crash after the stream started can no longer change the HTTP
            # status code; emit a final SSE error frame so the client never
            # sees a silently truncated stream.
            logger.exception("Agent stream failed after it started")
            yield (
                "data: "
                + json.dumps(
                    {
                        "type": "agent_step",
                        "step_id": "error",
                        "status": "error",
                        "tool": None,
                        "message": "Сервер не смог завершить задачу, попробуйте ещё раз",
                    },
                    ensure_ascii=False,
                )
                + "\n\n"
            )

    return StreamingResponse(event_generator(), media_type="text/event-stream")
