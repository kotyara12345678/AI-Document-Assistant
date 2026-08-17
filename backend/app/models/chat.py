from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base

if TYPE_CHECKING:
    from app.models.chat_message import ChatMessage, ChatSummary
    from app.models.document import Document
    from app.models.user import User


class Chat(Base):
    """A chat conversation. Holds its own message history and rolling summary."""

    __tablename__ = "chats"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="Новый чат")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="chats")
    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="chat", cascade="all, delete-orphan"
    )
    summary: Mapped["ChatSummary | None"] = relationship(
        back_populates="chat", cascade="all, delete-orphan", uselist=False
    )
    # Generated/edited documents attached to this chat (informational link).
    documents: Mapped[list["Document"]] = relationship(back_populates="chat")

    def __repr__(self) -> str:
        return f"<Chat id={self.id} user_id={self.user_id} title={self.title!r}>"
