"""Health and readiness probes.

``/health`` is a liveness probe (always answers 200 with per-dependency
status), while ``/api/ready`` answers 503 until PostgreSQL and Qdrant are
reachable — this is the endpoint deployment wait loops rely on.
"""

from fastapi.testclient import TestClient

from app.database.session import get_db
from app.main import app


def test_health_reports_ok(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["database"] == "ok"
    assert data["qdrant"] == "ok"
    assert data["status"] == "ok"


def test_ready_reports_ready(client):
    resp = client.get("/api/ready")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ready"
    assert data["database"] == "ok"
    assert data["qdrant"] == "ok"


def test_ready_degrades_when_database_unreachable():
    """Readiness must answer 503 (not 200) while a dependency is down."""

    # get_db is lazy: a session is created even when the DB is down, and the
    # first failure surfaces on db.execute(). Model that exact path so the
    # endpoint's own try/except is what degrades the probe to 503.
    class BrokenSession:
        def execute(self, *args, **kwargs):
            raise RuntimeError("db down")

        def close(self):
            pass

    app.dependency_overrides[get_db] = lambda: BrokenSession()
    try:
        with TestClient(app) as c:
            resp = c.get("/api/ready")
            assert resp.status_code == 503
            data = resp.json()
            assert data["database"] == "unavailable"
            assert data["status"] == "unavailable"
    finally:
        app.dependency_overrides.clear()


def test_ready_degrades_when_qdrant_unreachable(client, monkeypatch):
    """The probe must not 200 when Qdrant is unreachable either."""
    from app.api.routes import ready as ready_route

    monkeypatch.setattr(
        ready_route,
        "get_qdrant_client",
        lambda: (_ for _ in ()).throw(RuntimeError("qdrant down")),
    )

    resp = client.get("/api/ready")
    assert resp.status_code == 503
    data = resp.json()
    assert data["qdrant"] == "unavailable"
