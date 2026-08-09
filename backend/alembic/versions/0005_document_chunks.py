"""document chunks table for PostgreSQL FTS (keyword search)

Revision ID: 0005_document_chunks
Revises: 0004_chats
Create Date: 2026-08-09

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005_document_chunks"
down_revision: Union[str, None] = "0004_chats"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

FTS_CONFIG = "russian"


def upgrade() -> None:
    op.create_table(
        "document_chunks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("document_id", "chunk_index", name="uq_document_chunks_document_chunk"),
    )
    op.create_index("ix_document_chunks_document_id", "document_chunks", ["document_id"])
    op.create_index(
        "ix_document_chunks_tsv",
        "document_chunks",
        [sa.text(f"to_tsvector('{FTS_CONFIG}', text)")],
        postgresql_using="gin",
    )

    # Backfill chunk rows for documents that already exist (so keyword search
    # works immediately without a full re-upload).
    conn = op.get_bind()
    from app.services.chunking import chunk_text

    documents = conn.execute(sa.text("SELECT id, content FROM documents")).fetchall()
    for doc_id, content in documents:
        chunks = chunk_text(content or "")
        if not chunks:
            continue
        conn.execute(
            sa.text(
                "INSERT INTO document_chunks (document_id, chunk_index, text) "
                "VALUES (:document_id, :chunk_index, :text)"
            ),
            [
                {"document_id": doc_id, "chunk_index": chunk.index, "text": chunk.text}
                for chunk in chunks
            ],
        )


def downgrade() -> None:
    op.drop_index("ix_document_chunks_tsv", table_name="document_chunks")
    op.drop_index("ix_document_chunks_document_id", table_name="document_chunks")
    op.drop_table("document_chunks")
