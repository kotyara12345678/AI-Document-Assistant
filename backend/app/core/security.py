"""Security helpers: password hashing and JWT-based authentication.

Passwords are hashed with bcrypt (never stored in plaintext). API access is
granted via signed short-lived JWT bearer tokens; every protected endpoint
resolves the current user id from the token through ``get_current_user_id``.
"""

import logging
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.config import settings
from app.database.session import get_db
from app.models.user import User

logger = logging.getLogger("app.security")

# bcrypt ignores everything past the first 72 bytes; hashing a longer password
# would yield a hash that matches a truncated prefix, so refuse up front.
BCRYPT_MAX_BYTES = 72


def _enforce_jwt_secret() -> None:
    """Refuse to start a production app with a forgeable JWT secret."""
    if settings.JWT_SECRET == "dev-secret-change-me" and settings.ENVIRONMENT == "production":
        raise RuntimeError(
            "JWT_SECRET is still set to the insecure development default. Set a "
            "strong, unique JWT_SECRET (and ENVIRONMENT=production) before deploying."
        )


_enforce_jwt_secret()

_bearer_scheme = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    encoded = password.encode("utf-8")
    if len(encoded) > BCRYPT_MAX_BYTES:
        raise ValueError(
            f"Password must not exceed {BCRYPT_MAX_BYTES} bytes in UTF-8 encoding"
        )
    return bcrypt.hashpw(encoded, bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        # Malformed hash (e.g. from the pre-auth placeholder) can never match.
        return False


def create_access_token(user_id: int) -> str:
    """Sign a JWT whose ``sub`` claim holds the user id."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + timedelta(minutes=settings.JWT_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> int | None:
    """Return the user id encoded in a valid token, or None if invalid/expired."""
    try:
        payload = jwt.decode(
            token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM]
        )
    except jwt.PyJWTError:
        return None
    sub = payload.get("sub")
    if sub is None:
        return None
    try:
        return int(sub)
    except (TypeError, ValueError):
        return None


def get_current_user_id(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> int:
    """FastAPI dependency: resolve the authenticated user id from the JWT.

    Every user-scoped endpoint depends on this so the request always runs with
    the identity carried by the bearer token. Missing, malformed or expired
    tokens (and unknown users) yield HTTP 401. Accounts that were blocked
    (``is_active``) or soft-deleted (``is_deleted``) are rejected with 403 even
    though their token is still cryptographically valid.
    """
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user_id = decode_access_token(credentials.credentials)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if user.is_deleted or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is inactive or deleted",
        )
    _touch_last_active(user)
    return user_id


def _touch_last_active(user: User) -> None:
    """Refresh the account's activity marker, at most once every 5 minutes.

    Runs inside the request session so the write is committed together with the
    request's own work; the 5-minute throttle keeps it off the hot per-request
    path of long-lived sessions.
    """
    now = datetime.now(timezone.utc)
    if user.last_active_at is None or now - user.last_active_at > timedelta(minutes=5):
        user.last_active_at = now
        Session.object_session(user).commit()


ADMIN_ROLE = "admin"
MODERATOR_ROLE = "moderator"
USER_ROLE = "user"
# Roles that may perform moderation: work with reports and block users.
STAFF_ROLES = (ADMIN_ROLE, MODERATOR_ROLE)


def get_current_admin(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    """FastAPI dependency: require the caller to be an authenticated admin."""
    user_id = get_current_user_id(credentials, db)
    user = db.get(User, user_id)
    if user is None or user.role != ADMIN_ROLE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin role required",
        )
    return user


def get_current_moderator(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    """FastAPI dependency: require admin or moderator.

    Covers the moderation surface (user list for moderation context, blocking,
    reports). Role promotions/demotions and user deletion stay what
    ``get_current_admin`` protects — a moderator can never escalate.
    """
    user_id = get_current_user_id(credentials, db)
    user = db.get(User, user_id)
    if user is None or user.role not in STAFF_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Moderator role required",
        )
    return user