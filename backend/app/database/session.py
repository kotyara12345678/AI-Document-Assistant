from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

# Sized pool for the request concurrency this service sees (fastapi sync routes
# + background agent tasks). pool_pre_ping avoids handing out connections that
# die after a Postgres restart; pool_recycle bounds how long a connection may
# live behind a stateful proxy / LB.
# NOTE: upload/edit keep their request session open while the (slow) embedding
# runs, so each concurrent upload occupies a pooled connection for seconds. The
# audited load was 10/25/50 simultaneous uploads; 10+20 connections timed out
# at 50, so the pool is sized for that ceiling while staying safe against the
# default PostgreSQL max_connections (100).
engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    pool_size=20,
    max_overflow=50,
    pool_recycle=3600,
    pool_timeout=30,
    echo=settings.DB_ECHO,
)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
