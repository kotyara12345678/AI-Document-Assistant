"""push subscriptions for web push notifications

Adds ``push_subscriptions`` table to store browser push endpoint keys
for delivering native Web Push (VAPID) notifications.

Revision ID: 0014_push_subscriptions
Revises: 0013_background_jobs
Create Date: 2026-08-22

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0014_push_subscriptions"
down_revision: Union[str, None] = "0013_background_jobs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "push_subscriptions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            index=True,
            nullable=False,
        ),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("p256dh", sa.Text(), nullable=False),
        sa.Column("auth", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("user_id", "endpoint", name="uq_push_subscription_user_endpoint"),
    )


def downgrade() -> None:
    op.drop_table("push_subscriptions")
