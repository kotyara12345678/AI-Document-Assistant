"""chat history: chats table + chat_id scoping

Revision ID: 0004_chats
Revises: 0003_chat_history
Create Date: 2026-08-09

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004_chats"
down_revision: Union[str, None] = "0003_chat_history"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "chats",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_chats_user_id", "chats", ["user_id"])

    # Add nullable chat_id first so we can backfill existing rows.
    op.add_column("chat_messages", sa.Column("chat_id", sa.Integer(), nullable=True))
    op.add_column("chat_summaries", sa.Column("chat_id", sa.Integer(), nullable=True))

    # Move every existing message/summary into a per-user default chat.
    conn = op.get_bind()
    conn.execute(
        sa.text(
            """
            INSERT INTO chats (user_id, title, created_at, updated_at)
            SELECT DISTINCT user_id, 'Новый чат', now(), now()
            FROM (
                SELECT user_id FROM chat_messages
                UNION
                SELECT user_id FROM chat_summaries
            ) u
            """
        )
    )
    conn.execute(
        sa.text(
            """
            UPDATE chat_messages m
            SET chat_id = c.id
            FROM chats c
            WHERE c.user_id = m.user_id
            """
        )
    )
    conn.execute(
        sa.text(
            """
            UPDATE chat_summaries s
            SET chat_id = c.id
            FROM chats c
            WHERE c.user_id = s.user_id
            """
        )
    )

    op.alter_column("chat_messages", "chat_id", nullable=False)
    op.alter_column("chat_summaries", "chat_id", nullable=False)

    op.create_foreign_key(
        "fk_chat_messages_chat_id", "chat_messages", "chats", ["chat_id"], ["id"], ondelete="CASCADE"
    )
    op.create_foreign_key(
        "fk_chat_summaries_chat_id", "chat_summaries", "chats", ["chat_id"], ["id"], ondelete="CASCADE"
    )

    op.create_index("ix_chat_messages_chat_id", "chat_messages", ["chat_id"])

    # One summary per chat (was one per user).
    op.drop_index("ix_chat_summaries_user_id", table_name="chat_summaries")
    op.create_index("ix_chat_summaries_chat_id", "chat_summaries", ["chat_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_chat_summaries_chat_id", table_name="chat_summaries")
    op.create_index("ix_chat_summaries_user_id", "chat_summaries", ["user_id"])
    op.drop_index("ix_chat_messages_chat_id", table_name="chat_messages")
    op.drop_constraint("fk_chat_summaries_chat_id", "chat_summaries", type_="foreignkey")
    op.drop_constraint("fk_chat_messages_chat_id", "chat_messages", type_="foreignkey")
    op.drop_column("chat_summaries", "chat_id")
    op.drop_column("chat_messages", "chat_id")
    op.drop_index("ix_chats_user_id", table_name="chats")
    op.drop_table("chats")
