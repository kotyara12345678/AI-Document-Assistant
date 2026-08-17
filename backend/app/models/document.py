from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base

if TYPE_CHECKING:
    from app.models.chat import Chat
    from app.models.chat_message import ChatMessage
    from app.models.document_chunk import DocumentChunk
    from app.models.user import User


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_type: Mapped[str] = mapped_column(String(10), nullable=False)
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    filepath: Mapped[str] = mapped_column(String(1024), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # Chat this generated/edited file belongs to (informational link; the file
    # itself survives chat deletion, so the FK is nullable + SET NULL).
    chat_id: Mapped[int | None] = mapped_column(
        ForeignKey("chats.id", ondelete="SET NULL"), index=True, nullable=True
    )
    # When this file is the result of editing another file, points at the
    # immutable original (a copy is always made; the original is never touched).
    source_file_id: Mapped[int | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), index=True, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="documents")
    chunks: Mapped[list["DocumentChunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )
    chat: Mapped["Chat | None"] = relationship(back_populates="documents")
    source_file: Mapped["Document | None"] = relationship(
        "Document",
        remote_side=[id],
        back_populates="derived_files",
    )
    derived_files: Mapped[list["Document"]] = relationship(
        "Document",
        remote_side=[source_file_id],
        back_populates="source_file",
    )
    message: Mapped["ChatMessage | None"] = relationship(
        back_populates="document", uselist=False
    )

    @property
    def content_length(self) -> int:
        return len(self.content)

    def __repr__(self) -> str:
        return f"<Document id={self.id} filename={self.original_filename!r}>"
