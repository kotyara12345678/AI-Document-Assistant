"""Readiness probe for orchestrators and deploy smoke tests.

Unlike the always-200 ``/health`` (liveness), ``/api/ready`` answers 503 until
the dependencies the app actually needs (PostgreSQL and Qdrant) are reachable.
Docker healthchecks and deployment wait loops use this so a container is only
"healthy" when it can serve real requests.
"""

from fastapi import APIRouter, Depends, Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.vector.client import get_qdrant_client

router = APIRouter(tags=["health"])


@router.get("/ready")
def readiness_check(response: Response, db: Session = Depends(get_db)) -> dict:
    database = "ok"
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        database = "unavailable"

    qdrant = "ok"
    try:
        get_qdrant_client().get_collections()
    except Exception:
        qdrant = "unavailable"

    ready = database == "ok" and qdrant == "ok"
    if not ready:
        response.status_code = 503
    return {
        "status": "ready" if ready else "unavailable",
        "database": database,
        "qdrant": qdrant,
    }
