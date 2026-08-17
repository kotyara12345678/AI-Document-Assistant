from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.ratelimit import (
    AUTH_BURST_LIMIT,
    AUTH_BURST_WINDOW,
    FAILED_LOGIN_LIMIT,
    FAILED_LOGIN_WINDOW,
    throttle,
)
from app.core.security import (
    create_access_token,
    get_current_user_id,
    hash_password,
    verify_password,
)
from app.database.session import get_db
from app.models.user import User
from app.schemas.auth import AuthResponse, LoginRequest, RegisterRequest, UserOut

router = APIRouter()


def _rate_limited(detail: str = "Too many attempts, please try again later") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=detail,
    )


def _client_ip(request: Request) -> str:
    return request.client.host if request.client is not None else "unknown"


def _auth_response(user: User) -> AuthResponse:
    return AuthResponse(
        access_token=create_access_token(user.id),
        token_type="bearer",
        user=UserOut.model_validate(user),
    )


def _find_user_by_email(db: Session, email: str) -> User | None:
    normalized = email.strip().lower()
    return db.query(User).filter(func.lower(User.email) == normalized).first()


@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
def register(
    payload: RegisterRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> AuthResponse:
    """Create an account and return an access token for immediate use."""
    if not throttle.allow(
        f"auth:{_client_ip(request)}", AUTH_BURST_LIMIT, AUTH_BURST_WINDOW
    ):
        raise _rate_limited()

    if _find_user_by_email(db, payload.email) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email is already registered",
        )

    user = User(
        email=payload.email.strip().lower(),
        password_hash=hash_password(payload.password),
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        # Two concurrent registers with the same email race past the SELECT
        # above; the unique index is the final arbiter. Report the same 409 so
        # callers see one consistent error.
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email is already registered",
        ) from None
    db.refresh(user)
    return _auth_response(user)


@router.post("/login", response_model=AuthResponse)
def login(
    payload: LoginRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> AuthResponse:
    """Exchange valid credentials for an access token."""
    if not throttle.allow(
        f"auth:{_client_ip(request)}", AUTH_BURST_LIMIT, AUTH_BURST_WINDOW
    ):
        raise _rate_limited()

    user = _find_user_by_email(db, payload.email)
    if user is None or not verify_password(payload.password, user.password_hash):
        # Count only the failed attempt, keyed by account+IP so that trying
        # many wrong passwords on one account is throttled while a successful
        # login never accumulates failures for that user.
        fail_key = f"fail:{user.id if user else payload.email}:{_client_ip(request)}"
        if not throttle.allow(fail_key, FAILED_LOGIN_LIMIT, FAILED_LOGIN_WINDOW):
            raise _rate_limited(
                "Too many failed login attempts for this account, try again later"
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )
    if user.is_deleted or not user.is_active:
        # Credentials are correct but the account was blocked / soft-deleted.
        # Resolving this only after a successful password check avoids letting
        # unauthenticated callers probe which accounts are moderated.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is inactive or deleted",
        )
    user.last_active_at = datetime.now(timezone.utc)
    db.commit()
    return _auth_response(user)


@router.get("/me", response_model=UserOut)
def me(
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> UserOut:
    """Return the currently authenticated user."""
    user = db.get(User, user_id)
    return UserOut.model_validate(user)