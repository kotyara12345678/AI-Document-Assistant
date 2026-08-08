from fastapi import APIRouter, Depends

from app.core.security import get_current_user_id
from app.schemas.chat import ChatRequest, ChatResponse
from app.services import chat as chat_service

router = APIRouter()


@router.post("", response_model=ChatResponse)
def ask_chat(
    request: ChatRequest,
    user_id: int = Depends(get_current_user_id),
) -> ChatResponse:
    return chat_service.answer_question(request, user_id=user_id)
