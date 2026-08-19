from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base

if TYPE_CHECKING:
    from app.models.chat import Chat
    from app.models.chat_message import ChatMessage, ChatSummary
    from app.models.document import Document
    from app.models.report import Report
    from app.models.usage_log import UsageLog


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    # Optional profile picture as a Base64 data URL (data:image/...;base64,...).
    avatar_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    role: Mapped[str] = mapped_column(
        String(20), default="user", server_default="user", nullable=False
    )
    # Blocked accounts keep their data but cannot authenticate or use the API
    # (enforced server-side in security.get_current_user_id / auth.login).
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )
    # Soft-deleted accounts are hidden from the admin list but their rows (and
    # the documents/chats/reports they reference) must survive for referential
    # integrity, so real deletion never happens for a user.
    is_deleted: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Last successful login / authenticated request (refreshed at most every 5
    # minutes to bound the per-request write load).
    last_active_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    documents: Mapped[list["Document"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    usage_logs: Mapped[list["UsageLog"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    chat_messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    chat_summary: Mapped["ChatSummary | None"] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False
    )
    chats: Mapped[list["Chat"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    reports_made: Mapped[list["Report"]] = relationship(
        back_populates="reporter",
        foreign_keys="Report.reporter_id",
        cascade="all, delete-orphan",
    )
    reports_received: Mapped[list["Report"]] = relationship(
        back_populates="reported_user",
        foreign_keys="Report.reported_user_id",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email!r}>"
