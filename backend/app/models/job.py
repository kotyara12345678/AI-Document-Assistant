from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, JSON, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base

if TYPE_CHECKING:
    from app.models.user import User


class Job(Base):
    """Persistent background job. Survives backend/worker restarts."""

    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    chat_id: Mapped[int | None] = mapped_column(
        ForeignKey("chats.id", ondelete="SET NULL"), index=True, nullable=True
    )
    type: Mapped[str] = mapped_column(Text, nullable=False, default="agent")
    status: Mapped[str] = mapped_column(Text, nullable=False, default="queued", index=True)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    cancel_requested: Mapped[bool] = mapped_column(default=False, server_default="false", nullable=False)

    user: Mapped["User"] = relationship(back_populates="jobs")

    def __repr__(self) -> str:
        return f"<Job id={self.id} type={self.type!r} status={self.status!r}>"
