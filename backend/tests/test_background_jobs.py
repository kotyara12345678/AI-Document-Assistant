"""Tests for the persistent background job system.

Covers: job lifecycle, crash recovery, cancellation, ownership enforcement,
notification CRUD, concurrent worker safety, and API endpoint behaviour.
"""

import threading
from datetime import datetime, timedelta, timezone

import pytest

from app.database.session import SessionLocal
from app.models.job import Job
from app.services import job as job_service
from app.services import notification as notification_service


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_job(db, user_id, *, status="queued", payload=None):
    """Insert a job directly into the DB (bypass API)."""
    job = Job(
        user_id=user_id,
        type="agent",
        status=status,
        payload=payload or {"question": "test"},
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


# ===========================================================================
# 1. Job lifecycle (service layer)
# ===========================================================================


class TestJobLifecycle:
    def test_create_and_claim(self, user_id):
        db = SessionLocal()
        try:
            job = job_service.create_job(
                db, user_id=user_id, payload={"question": "hello"}
            )
            assert job.id is not None
            assert job.status == "queued"

            claimed = job_service.claim_next_job(db)
            assert claimed is not None
            assert claimed.id == job.id
            assert claimed.status == "running"
            assert claimed.started_at is not None

            job_service.complete_job(db, claimed, {"answer": "done"})
            assert claimed.status == "completed"
            assert claimed.result == {"answer": "done"}
            assert claimed.finished_at is not None
        finally:
            db.close()

    def test_claim_returns_none_when_empty(self):
        db = SessionLocal()
        try:
            claimed = job_service.claim_next_job(db)
            assert claimed is None
        finally:
            db.close()

    def test_claim_fIFO_order(self, user_id):
        db = SessionLocal()
        try:
            j1 = job_service.create_job(db, user_id=user_id, payload={"question": "first"})
            j2 = job_service.create_job(db, user_id=user_id, payload={"question": "second"})

            c1 = job_service.claim_next_job(db)
            assert c1.id == j1.id
            c2 = job_service.claim_next_job(db)
            assert c2.id == j2.id
        finally:
            db.close()

    def test_fail_job(self, user_id):
        db = SessionLocal()
        try:
            _create_job(db, user_id)
            claimed = job_service.claim_next_job(db)
            job_service.fail_job(db, claimed, "boom")
            assert claimed.status == "failed"
            assert claimed.error == "boom"
            assert claimed.finished_at is not None
        finally:
            db.close()

    def test_cancel_queued_job(self, user_id):
        db = SessionLocal()
        try:
            job = _create_job(db, user_id)
            cancelled = job_service.cancel_job(db, job.id, user_id)
            assert cancelled.status == "cancelled"
            assert cancelled.finished_at is not None
        finally:
            db.close()

    def test_cancel_running_job_sets_flag(self, user_id):
        db = SessionLocal()
        try:
            _create_job(db, user_id)
            claimed = job_service.claim_next_job(db)
            cancelled = job_service.cancel_job(db, claimed.id, user_id)
            assert cancelled.status == "running"
            assert cancelled.cancel_requested is True
        finally:
            db.close()

    def test_cancel_nonexistent_raises(self, user_id):
        db = SessionLocal()
        try:
            with pytest.raises(job_service._JobNotFoundError):
                job_service.cancel_job(db, 999999, user_id)
        finally:
            db.close()

    def test_cancel_already_finished_raises(self, user_id):
        db = SessionLocal()
        try:
            _create_job(db, user_id)
            claimed = job_service.claim_next_job(db)
            job_service.complete_job(db, claimed, {"answer": "ok"})
            with pytest.raises(job_service._JobAlreadyFinishedError):
                job_service.cancel_job(db, claimed.id, user_id)
        finally:
            db.close()

    def test_check_cancel_requested(self, user_id):
        db = SessionLocal()
        try:
            _create_job(db, user_id)
            claimed = job_service.claim_next_job(db)
            assert job_service.check_cancel_requested(db, claimed.id) is False
            job_service.cancel_job(db, claimed.id, user_id)
            assert job_service.check_cancel_requested(db, claimed.id) is True
        finally:
            db.close()


# ===========================================================================
# 2. Ownership enforcement
# ===========================================================================


class TestOwnership:
    def test_get_job_wrong_user(self, user_id):
        db = SessionLocal()
        try:
            job = _create_job(db, user_id)
            result = job_service.get_job(db, job.id, user_id + 999)
            assert result is None
        finally:
            db.close()

    def test_cancel_wrong_user(self, user_id):
        db = SessionLocal()
        try:
            job = _create_job(db, user_id)
            with pytest.raises(job_service._JobNotFoundError):
                job_service.cancel_job(db, job.id, user_id + 999)
        finally:
            db.close()

    def test_list_jobs_scoped_to_user(self, user_id):
        db = SessionLocal()
        try:
            _create_job(db, user_id)
            _create_job(db, user_id + 999)
            mine = job_service.get_user_jobs(db, user_id)
            assert len(mine) == 1
        finally:
            db.close()


# ===========================================================================
# 3. Queue limits
# ===========================================================================


class TestQueueLimits:
    def test_queued_limit_enforced(self, user_id):
        db = SessionLocal()
        try:
            from app.core.config import settings
            for _ in range(settings.MAX_QUEUED_JOBS_PER_USER):
                job_service.create_job(db, user_id=user_id)
            with pytest.raises(job_service._JobLimitError):
                job_service.create_job(db, user_id=user_id)
        finally:
            db.close()

    def test_running_limit_enforced(self, user_id):
        db = SessionLocal()
        try:
            from app.core.config import settings
            for _ in range(settings.MAX_RUNNING_JOBS_PER_USER):
                job_service.create_job(db, user_id=user_id)
                job_service.claim_next_job(db)
            with pytest.raises(job_service._JobLimitError):
                job_service.create_job(db, user_id=user_id)
        finally:
            db.close()


# ===========================================================================
# 4. Crash recovery
# ===========================================================================


class TestCrashRecovery:
    def test_stale_jobs_recovered(self, user_id):
        db = SessionLocal()
        try:
            _create_job(db, user_id)
            claimed = job_service.claim_next_job(db)
            # Backdate started_at to simulate a stale job.
            db.query(Job).filter(Job.id == claimed.id).update(
                {Job.started_at: datetime.now(timezone.utc) - timedelta(hours=1)}
            )
            db.commit()

            recovered = job_service.recover_stale_jobs(db)
            assert recovered == 1

            db.refresh(claimed)
            assert claimed.status == "queued"
            assert claimed.started_at is None
        finally:
            db.close()

    def test_recent_running_not_recovered(self, user_id):
        db = SessionLocal()
        try:
            _create_job(db, user_id)
            job_service.claim_next_job(db)
            # Just claimed — should NOT be stale.
            recovered = job_service.recover_stale_jobs(db)
            assert recovered == 0
        finally:
            db.close()


# ===========================================================================
# 5. Concurrent worker safety (FOR UPDATE SKIP LOCKED)
# ===========================================================================


class TestConcurrency:
    def test_two_workers_claim_different_jobs(self, user_id):
        db = SessionLocal()
        try:
            j1 = job_service.create_job(db, user_id=user_id)
            j2 = job_service.create_job(db, user_id=user_id)

            # Two separate connections simulating two workers.
            db2 = SessionLocal()
            try:
                c1 = job_service.claim_next_job(db)
                c2 = job_service.claim_next_job(db2)
                assert c1 is not None and c2 is not None
                assert c1.id != c2.id
                assert {c1.id, c2.id} == {j1.id, j2.id}
            finally:
                db2.close()
        finally:
            db.close()

    def test_parallel_claims_no_double_execution(self, user_id):
        """Simulate multiple workers racing to claim jobs."""
        db = SessionLocal()
        try:
            for _ in range(5):
                job_service.create_job(db, user_id=user_id)
            claimed_ids = []

            def claim_worker(worker_id):
                db_local = SessionLocal()
                try:
                    for _ in range(3):
                        j = job_service.claim_next_job(db_local)
                        if j:
                            claimed_ids.append((worker_id, j.id))
                            break
                finally:
                    db_local.close()

            threads = [threading.Thread(target=claim_worker, args=(i,)) for i in range(5)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            # Each job claimed at most once.
            all_job_ids = [jid for _, jid in claimed_ids]
            assert len(all_job_ids) == len(set(all_job_ids))
            assert len(all_job_ids) == 5
        finally:
            db.close()


# ===========================================================================
# 6. Notification service
# ===========================================================================


class TestNotifications:
    def test_create_and_read(self, user_id):
        db = SessionLocal()
        try:
            n = notification_service.create_notification(
                db, user_id=user_id, title="Test", body="Hello"
            )
            assert n.id is not None
            assert n.is_read is False

            notifs = notification_service.get_user_notifications(db, user_id)
            assert len(notifs) == 1

            unread = notification_service.get_unread_count(db, user_id)
            assert unread == 1

            notification_service.mark_read(db, n.id, user_id)
            unread = notification_service.get_unread_count(db, user_id)
            assert unread == 0
        finally:
            db.close()

    def test_mark_all_read(self, user_id):
        db = SessionLocal()
        try:
            for i in range(3):
                notification_service.create_notification(
                    db, user_id=user_id, title=f"n{i}"
                )
            assert notification_service.get_unread_count(db, user_id) == 3
            count = notification_service.mark_all_read(db, user_id)
            assert count == 3
            assert notification_service.get_unread_count(db, user_id) == 0
        finally:
            db.close()

    def test_notifications_scoped_to_user(self, user_id):
        db = SessionLocal()
        try:
            notification_service.create_notification(
                db, user_id=user_id, title="mine"
            )
            notification_service.create_notification(
                db, user_id=user_id + 999, title="theirs"
            )
            mine = notification_service.get_user_notifications(db, user_id)
            assert len(mine) == 1
            assert mine[0].title == "mine"
        finally:
            db.close()


# ===========================================================================
# 7. API endpoint tests
# ===========================================================================


class TestJobAPI:
    def test_create_job(self, client, user_id):
        resp = client.post(
            "/api/jobs",
            json={"question": "translate this document"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "queued"
        assert data["type"] == "agent"

    def test_list_jobs(self, client, user_id):
        client.post("/api/jobs", json={"question": "task 1"})
        client.post("/api/jobs", json={"question": "task 2"})
        resp = client.get("/api/jobs")
        assert resp.status_code == 200
        assert resp.json()["total"] == 2

    def test_get_job(self, client, user_id):
        create = client.post("/api/jobs", json={"question": "task"})
        job_id = create.json()["id"]
        resp = client.get(f"/api/jobs/{job_id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == job_id

    def test_get_job_not_found(self, client):
        resp = client.get("/api/jobs/999999")
        assert resp.status_code == 404

    def test_cancel_job(self, client, user_id):
        create = client.post("/api/jobs", json={"question": "task"})
        job_id = create.json()["id"]
        resp = client.post(f"/api/jobs/{job_id}/cancel")
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"

    def test_cancel_nonexistent(self, client):
        resp = client.post("/api/jobs/999999/cancel")
        assert resp.status_code == 404


class TestNotificationAPI:
    def test_list_notifications(self, client, user_id):
        db = SessionLocal()
        try:
            notification_service.create_notification(
                db, user_id=user_id, title="test notif"
            )
        finally:
            db.close()
        resp = client.get("/api/notifications")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        assert data["unread_count"] >= 1

    def test_mark_read(self, client, user_id):
        db = SessionLocal()
        try:
            n = notification_service.create_notification(
                db, user_id=user_id, title="read me"
            )
            n_id = n.id
        finally:
            db.close()
        resp = client.post(f"/api/notifications/{n_id}/read")
        assert resp.status_code == 200
        assert resp.json()["is_read"] is True

    def test_mark_read_not_found(self, client):
        resp = client.post("/api/notifications/999999/read")
        assert resp.status_code == 404

    def test_mark_all_read(self, client, user_id):
        db = SessionLocal()
        try:
            for i in range(3):
                notification_service.create_notification(
                    db, user_id=user_id, title=f"n{i}"
                )
        finally:
            db.close()
        resp = client.post("/api/notifications/read-all")
        assert resp.status_code == 200
        assert resp.json()["marked_read"] == 3


# ===========================================================================
# 8. Persistence after restart (simulate via new DB sessions)
# ===========================================================================


class TestPersistence:
    def test_job_survives_new_session(self, user_id):
        """Create a job, close the session, reopen — job should still exist."""
        db1 = SessionLocal()
        try:
            job = job_service.create_job(db1, user_id=user_id)
            job_id = job.id
        finally:
            db1.close()

        db2 = SessionLocal()
        try:
            found = job_service.get_job(db2, job_id, user_id)
            assert found is not None
            assert found.status == "queued"
        finally:
            db2.close()

    def test_notification_survives_new_session(self, user_id):
        db1 = SessionLocal()
        try:
            n = notification_service.create_notification(
                db1, user_id=user_id, title="persist"
            )
            n_id = n.id
        finally:
            db1.close()

        db2 = SessionLocal()
        try:
            notifs = notification_service.get_user_notifications(db2, user_id)
            assert any(n.id == n_id for n in notifs)
        finally:
            db2.close()
