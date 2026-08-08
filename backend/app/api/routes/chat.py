from fastapi import APIRouter

from app.schemas.chat import ChatRequest, ChatResponse
from app.services import chat as chat_service

router = APIRouter()


@router.post("", response_model=ChatResponse)
def ask_chat(request: ChatRequest) -> ChatResponse:
    return chat_service.answer_question(request)
