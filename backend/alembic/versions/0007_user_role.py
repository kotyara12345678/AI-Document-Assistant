"""add user role column

Adds the ``role`` column to ``users`` (default ``"user"``) and promotes the
demo account to ``"admin"`` so the built-in demo login can use the protected
/admin interface out of the box.

Revision ID: 0007_user_role
Revises: 0006_auth_seed_demo
Create Date: 2026-08-10

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007_user_role"
down_revision: Union[str, None] = "0006_auth_seed_demo"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users", sa.Column("role", sa.String(length=20), nullable=False, server_default="user")
    )
    op.execute("UPDATE users SET role = 'admin' WHERE email = 'demo@example.com'")


def downgrade() -> None:
    op.drop_column("users", "role")