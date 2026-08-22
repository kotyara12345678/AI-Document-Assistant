"""background jobs and notifications

Adds ``jobs`` and ``notifications`` tables for persistent background task
execution. Jobs survive backend/worker restarts; notifications allow offline
users to see completed task results.

Revision ID: 0013_background_jobs
Revises: 0012_user_avatar
Create Date: 2026-08-22

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0013_background_jobs"
down_revision: Union[str, None] = "0012_user_avatar"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "chat_id",
            sa.Integer(),
            sa.ForeignKey("chats.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column("type", sa.Text(), nullable=False, server_default="agent"),
        sa.Column("status", sa.Text(), nullable=False, server_default="queued", index=True),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "cancel_requested",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )

    op.create_table(
        "notifications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "job_id",
            sa.Integer(),
            sa.ForeignKey("jobs.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column(
            "is_read",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("notifications")
    op.drop_table("jobs")
