"""user avatar_url column

Adds an optional ``avatar_url`` to ``users``. It stores the user's profile
picture as a Base64 data URL (e.g. ``data:image/png;base64,...``) so no
object storage is needed in the current stack.

Revision ID: 0012_user_avatar
Revises: 0011_user_moderation_report
Create Date: 2026-08-18

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0012_user_avatar"
down_revision: Union[str, None] = "0011_user_moderation_report"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("avatar_url", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "avatar_url")