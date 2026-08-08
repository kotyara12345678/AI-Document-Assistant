"""add document metadata and content columns

Revision ID: 0002_document_metadata
Revises: 0001_initial
Create Date: 2026-08-08

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_document_metadata"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("original_filename", sa.String(length=255), nullable=True))
    op.add_column("documents", sa.Column("file_type", sa.String(length=10), nullable=True))
    op.add_column("documents", sa.Column("file_size", sa.BigInteger(), nullable=True))
    op.add_column("documents", sa.Column("content", sa.Text(), nullable=True))

    op.execute("UPDATE documents SET original_filename = filename WHERE original_filename IS NULL")
    op.execute("UPDATE documents SET file_type = lower(split_part(filename, '.', cardinality(string_to_array(filename, '.')))) WHERE file_type IS NULL")
    op.execute("UPDATE documents SET file_size = 0 WHERE file_size IS NULL")
    op.execute("UPDATE documents SET content = '' WHERE content IS NULL")

    op.alter_column("documents", "original_filename", nullable=False)
    op.alter_column("documents", "file_type", nullable=False)
    op.alter_column("documents", "file_size", nullable=False)
    op.alter_column("documents", "content", nullable=False)


def downgrade() -> None:
    op.drop_column("documents", "content")
    op.drop_column("documents", "file_size")
    op.drop_column("documents", "file_type")
    op.drop_column("documents", "original_filename")
