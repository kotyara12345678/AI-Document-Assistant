"""User reports (moderation complaints).

A minimal, self-contained report model per the moderation spec:

    Report
    - id
    - reporter_id      (who complained)
    - reported_user_id (against whom)
    - reason           (short category/code)
    - description      (optional free text)
    - status           (pending | reviewed | rejected | action_taken)
    - created_at
    - resolved_at      (when a moderator closed it)
    - resolved_by      (which staff member closed it)

Reports are deliberately independent of documents/chats so a user's report
history survives document or chat deletion. Deleting a user row cascades its
reports (``ondelete=CASCADE``), but the app only ever soft-deletes users, so
report history in practice is never lost.
"""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base

if TYPE_CHECKING:
    from app.models.user import User

# Lifecycle statuses of a report.
REPORT_STATUS_PENDING = "pending"
REPORT_STATUS_REVIEWED = "reviewed"
REPORT_STATUS_REJECTED = "rejected"
REPORT_STATUS_ACTION_TAKEN = "action_taken"

# Reports that still demand moderation attention (are counted on the user row).
ACTIVE_REPORT_STATUSES = (REPORT_STATUS_PENDING, REPORT_STATUS_REVIEWED)
# Closed statuses: moderation reached a final decision.
FINAL_REPORT_STATUSES = (REPORT_STATUS_REJECTED, REPORT_STATUS_ACTION_TAKEN)


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    reporter_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    reported_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    reason: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), default=REPORT_STATUS_PENDING, server_default=REPORT_STATUS_PENDING, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    reporter: Mapped["User"] = relationship(
        back_populates="reports_made",
        foreign_keys=[reporter_id],
    )
    reported_user: Mapped["User"] = relationship(
        back_populates="reports_received",
        foreign_keys=[reported_user_id],
    )
    resolver: Mapped["User | None"] = relationship(
        foreign_keys=[resolved_by],
    )

    def __repr__(self) -> str:
        return f"<Report id={self.id} reporter={self.reporter_id} reported={self.reported_user_id} status={self.status!r}>"