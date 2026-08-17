"""attach explicit context documents to a chat message

When the user pins documents as context for a turn (UI chips / double-click),
the chosen document ids are stored on the message so the agent can prioritise
them over RAG and so the chips can be restored after a page reload.

Revision ID: 0010_message_context_docs
Revises: 0009_document_edit_links
Create Date: 2026-08-13

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010_message_context_docs"
down_revision: Union[str, None] = "0009_document_edit_links"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "chat_messages",
        sa.Column("context_document_ids", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("chat_messages", "context_document_ids")
