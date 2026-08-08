"""Security helpers.

Authentication is intentionally NOT implemented yet. The user ID dependency
(`get_current_user_id`) is a placeholder that will be replaced with real
JWT-based auth in a later iteration.
"""

import bcrypt


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(plain_password.encode("utf-8"), password_hash.encode("utf-8"))


def get_current_user_id() -> int:
    """Placeholder: returns a fixed user until authentication is implemented."""
    return 1
