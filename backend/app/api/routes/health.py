from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.vector.client import get_qdrant_client

router = APIRouter(tags=["health"])


@router.get("/health")
def health_check(db: Session = Depends(get_db)) -> dict:
    db_status = "ok"
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        db_status = "unavailable"

    qdrant_status = "ok"
    try:
        get_qdrant_client().get_collections()
    except Exception:
        qdrant_status = "unavailable"

    return {
        "status": "ok" if db_status == "ok" and qdrant_status == "ok" else "degraded",
        "database": db_status,
        "qdrant": qdrant_status,
    }
