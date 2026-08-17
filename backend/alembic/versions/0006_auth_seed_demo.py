"""seed demo user with a valid bcrypt password hash

The pre-auth placeholder left the demo account (email demo@example.com) with
the plaintext password_hash 'demo'. Real authentication can no longer accept
that, so this migration turns the demo account into a normal bcrypt hashed
one (password: "demo") or creates it when missing.

Revision ID: 0006_auth_seed_demo
Revises: 0005_document_chunks
Create Date: 2026-08-09
"""
import os
from typing import Sequence, Union

import bcrypt
import sqlalchemy as sa
from alembic import op

revision: str = "0006_auth_seed_demo"
down_revision: Union[str, None] = "0005_document_chunks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DEMO_EMAIL = "demo@example.com"
DEMO_PASSWORD = "demo"

# Setting SEED_DEMO_ADMIN=0 skips creating/promoting the demo account on a
# FRESH database. The account has a well-known password and must never appear
# in a production deployment; for an existing database this migration is a
# no-op and the account can be demoted via ADMIN_EMAILS at app startup.
SEED_DEMO_ADMIN = os.environ.get("SEED_DEMO_ADMIN", "1") != "0"


def upgrade() -> None:
    conn = op.get_bind()

    def _lookup() -> sa.engine.Row | None:
        return conn.execute(
            sa.text("SELECT id FROM users WHERE email = :email"),
            {"email": DEMO_EMAIL},
        ).fetchone()

    row = _lookup()
    if row is not None:
        # Fix the invalid placeholder hash, keep the row's identity intact so
        # existing documents/chats owned by that user stay reachable.
        password_hash = bcrypt.hashpw(
            DEMO_PASSWORD.encode("utf-8"), bcrypt.gensalt()
        ).decode("utf-8")
        conn.execute(
            sa.text("UPDATE users SET password_hash = :hash WHERE email = :email"),
            {"email": DEMO_EMAIL, "hash": password_hash},
        )
        return

    # No demo account yet: this is a fresh database. Create it only when the
    # operator asked for the seeded demo login.
    if not SEED_DEMO_ADMIN:
        return
    password_hash = bcrypt.hashpw(
        DEMO_PASSWORD.encode("utf-8"), bcrypt.gensalt()
    ).decode("utf-8")
    conn.execute(
        sa.text("INSERT INTO users (email, password_hash) VALUES (:email, :hash)"),
        {"email": DEMO_EMAIL, "hash": password_hash},
    )


def downgrade() -> None:
    # The demo account may pre-date this migration; do not remove it.
    pass