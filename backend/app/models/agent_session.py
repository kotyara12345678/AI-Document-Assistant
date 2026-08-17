from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class AgentSession(Base):
    """Per-chat structured agent memory.

    Holds the running task state and the document context the agent has built
    up while working in a chat. Kept deliberately small and explicit (no raw
    transcript dump) so the agent can restore "what it was doing" and "which
    documents it already found/read" between messages without re-sending the
    whole conversation.
    """

    __tablename__ = "agent_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    chat_id: Mapped[int] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"),
        index=True,
        unique=True,
        nullable=False,
    )
    state: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<AgentSession chat_id={self.chat_id} user_id={self.user_id}>"
