from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.security import get_current_user_id
from app.database.session import get_db
from app.models.chat import Chat
from app.models.chat_message import ChatMessage
from app.schemas.chat import ChatCreate, ChatOut, ChatUpdate, MessageOut
from app.services.chat import DEFAULT_CHAT_TITLE

router = APIRouter()


def _get_chat(db: Session, chat_id: int, user_id: int) -> Chat:
    chat = db.query(Chat).filter(Chat.id == chat_id, Chat.user_id == user_id).first()
    if chat is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat not found",
        )
    return chat


@router.get("", response_model=list[ChatOut])
def list_chats(
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> list[Chat]:
    return (
        db.query(Chat)
        .filter(Chat.user_id == user_id)
        .order_by(Chat.updated_at.desc(), Chat.id.desc())
        .all()
    )


@router.post("", response_model=ChatOut, status_code=status.HTTP_201_CREATED)
def create_chat(
    payload: ChatCreate,
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> Chat:
    chat = Chat(
        user_id=user_id,
        title=(payload.title or "").strip() or DEFAULT_CHAT_TITLE,
    )
    db.add(chat)
    db.commit()
    db.refresh(chat)
    return chat


@router.get("/{chat_id}/messages", response_model=list[MessageOut])
def chat_messages(
    chat_id: int,
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> list[ChatMessage]:
    _get_chat(db, chat_id, user_id)
    return (
        db.query(ChatMessage)
        .filter(ChatMessage.chat_id == chat_id)
        .order_by(ChatMessage.id.asc())
        .all()
    )


@router.patch("/{chat_id}", response_model=ChatOut)
def rename_chat(
    chat_id: int,
    payload: ChatUpdate,
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> Chat:
    chat = _get_chat(db, chat_id, user_id)
    chat.title = payload.title.strip() or DEFAULT_CHAT_TITLE
    db.commit()
    db.refresh(chat)
    return chat


@router.delete("/{chat_id}", status_code=status.HTTP_200_OK)
def delete_chat(
    chat_id: int,
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> dict:
    chat = _get_chat(db, chat_id, user_id)
    db.delete(chat)
    db.commit()
    return {"deleted": chat_id, "status": "ok"}
