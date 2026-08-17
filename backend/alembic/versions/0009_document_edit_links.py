"""link generated/edited documents to chats and source files

Generated and edited files are stored as rows in the existing ``documents``
table (so they reuse storage, the download endpoint and RAG unchanged). To
survive a page reload they must be linked to the chat/message that produced
them, and an edited file must remember its immutable original.

Revision ID: 0009_document_edit_links
Revises: 0008_agent_session
Create Date: 2026-08-13

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009_document_edit_links"
down_revision: Union[str, None] = "0008_agent_session"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column(
            "chat_id",
            sa.Integer(),
            sa.ForeignKey("chats.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_documents_chat_id", "documents", ["chat_id"])

    op.add_column(
        "documents",
        sa.Column(
            "source_file_id",
            sa.Integer(),
            sa.ForeignKey("documents.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_documents_source_file_id", "documents", ["source_file_id"])

    op.add_column(
        "chat_messages",
        sa.Column(
            "document_id",
            sa.Integer(),
            sa.ForeignKey("documents.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_chat_messages_document_id", "chat_messages", ["document_id"])


def downgrade() -> None:
    op.drop_index("ix_chat_messages_document_id", "chat_messages")
    op.drop_column("chat_messages", "document_id")
    op.drop_index("ix_documents_source_file_id", "documents")
    op.drop_column("documents", "source_file_id")
    op.drop_index("ix_documents_chat_id", "documents")
    op.drop_column("documents", "chat_id")
