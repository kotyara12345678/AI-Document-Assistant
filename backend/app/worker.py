"""Background job worker — standalone process that executes queued jobs.

Polls PostgreSQL for ``queued`` jobs using ``FOR UPDATE SKIP LOCKED``,
executes them via the agent service, and persists results + notifications.

Run as a separate Docker container alongside the backend:
    python -m app.worker

Or via the entrypoint:
    exec python -m app.worker
"""

import json
import logging
import os
import signal
import sys
import time

from pywebpush import WebPushException, webpush

# Ensure the backend root is on sys.path when running as a module.
_backend_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend_root not in sys.path:
    sys.path.insert(0, _backend_root)

from app.core.config import settings  # noqa: E402
from app.database.session import SessionLocal  # noqa: E402
from app.models.job import Job  # noqa: E402
from app.services import job as job_service  # noqa: E402
from app.services import notification as notification_service  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("app.worker")

_shutdown_requested = False


def _handle_signal(signum, frame):
    global _shutdown_requested
    logger.info("Received signal %s, shutting down gracefully...", signum)
    _shutdown_requested = True


def _execute_job(job: Job) -> dict:
    """Execute a background agent job and return the result dict.

    This runs the FULL agent pipeline (search, read, create, edit) inside
    the worker process. All results are persisted to PostgreSQL by the agent
    service itself. The returned dict is stored as ``job.result``.
    """
    from app.services.agent import AgentService
    from app.schemas.agent import AgentRequest

    payload = job.payload or {}
    agent = AgentService()

    request = AgentRequest(
        question=payload.get("question", ""),
        chat_id=job.chat_id,
        context_document_ids=payload.get("context_document_ids"),
        document_id=payload.get("document_id"),
        document_ids=payload.get("document_ids"),
    )

    # Run the full agent loop (non-streaming). The agent manages its own
    # DB session internally (SessionLocal inside run_agent_stream), creates
    # messages, persists state, etc. We collect the result from the sink dict.
    sink: dict = {}
    for _ in agent.run_agent_stream(request, user_id=job.user_id, sink=sink):
        pass  # drain the generator; all persistence happens inside

    return {
        "answer": sink.get("answer", ""),
        "chat_id": sink.get("chat_id", job.chat_id),
        "created_documents": sink.get("created_documents", []),
        "sources": sink.get("sources", []),
    }


def _notify_user(job: Job, result: dict) -> None:
    """Create a DB notification AND send web push to the user's devices."""
    db = SessionLocal()
    try:
        answer = result.get("answer", "")
        title = f"Задача завершена ({job.type})"
        body = answer[:500] if answer else "Задача выполнена."

        created = result.get("created_documents", [])
        if created:
            doc_names = [d.get("filename", "?") for d in created[:3]]
            body = f"Созданы документы: {', '.join(doc_names)}\n\n{body}"

        notification_service.create_notification(
            db,
            user_id=job.user_id,
            job_id=job.id,
            title=title,
            body=body,
        )

        _send_web_push(db, job.user_id, title, body)
    except Exception:
        logger.exception("Failed to create notification for job %s", job.id)
    finally:
        db.close()


def _send_web_push(db, user_id: int, title: str, body: str) -> None:
    """Dispatch a web push to all of the user's registered subscriptions."""
    if not settings.VAPID_PRIVATE_KEY:
        return

    from app.models.push_subscription import PushSubscription

    subs = (
        db.query(PushSubscription)
        .filter(PushSubscription.user_id == user_id)
        .all()
    )
    if not subs:
        return

    payload = json.dumps({"title": title, "body": body})

    for sub in subs:
        try:
            subscription_info = {
                "endpoint": sub.endpoint,
                "keys": {"p256dh": sub.p256dh, "auth": sub.auth},
            }
            webpush(
                subscription_info=subscription_info,
                data=payload,
                vapid_private_key=settings.VAPID_PRIVATE_KEY,
                vapid_claims={"sub": settings.VAPID_CLAIM_EMAIL},
            )
        except WebPushException as exc:
            status_code = getattr(exc, "response", None)
            status_code = getattr(status_code, "status_code", None) if status_code else None
            # 404 Gone / 410 Gone: subscription expired — remove it
            if status_code in (404, 410):
                logger.info("Removing stale push subscription %s for user %s", sub.id, user_id)
                db.query(PushSubscription).filter(PushSubscription.id == sub.id).delete()
                db.commit()
            else:
                logger.warning("Web push failed for subscription %s: %s", sub.id, exc)
        except Exception:
            logger.exception("Unexpected error sending web push to subscription %s", sub.id)


def run_worker():
    """Main worker loop."""
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    logger.info("Worker starting (concurrency=%s, poll_interval=%ss)",
                settings.WORKER_CONCURRENCY, settings.JOB_POLL_INTERVAL)

    db = SessionLocal()
    try:
        recovered = job_service.recover_stale_jobs(db)
        if recovered:
            logger.info("Recovered %d stale job(s) on startup", recovered)
    except Exception:
        logger.exception("Crash recovery failed")
    finally:
        db.close()

    while not _shutdown_requested:
        db = SessionLocal()
        try:
            job = job_service.claim_next_job(db)
            if job is None:
                db.close()
                time.sleep(settings.JOB_POLL_INTERVAL)
                continue

            logger.info("Processing job %s (type=%s, user=%s)", job.id, job.type, job.user_id)

            try:
                # Check cancellation before starting heavy work.
                if job_service.check_cancel_requested(db, job.id):
                    job_service.cancel_job(db, job.id, job.user_id)
                    logger.info("Job %s was cancelled before execution", job.id)
                    db.close()
                    continue

                result = _execute_job(job)

                # Check cancellation after execution (user may have cancelled during).
                if job_service.check_cancel_requested(db, job.id):
                    job_service.fail_job(db, job, "Cancelled by user")
                    logger.info("Job %s cancelled after execution", job.id)
                else:
                    job_service.complete_job(db, job, result)
                    _notify_user(job, result)
                    logger.info("Job %s completed successfully", job.id)

            except Exception as exc:
                logger.exception("Job %s failed", job.id)
                try:
                    db_new = SessionLocal()
                    try:
                        job_ref = db_new.get(Job, job.id)
                        if job_ref:
                            job_service.fail_job(db_new, job_ref, str(exc))
                            _notify_user(job_ref, {"answer": f"Ошибка: {exc}"})
                    finally:
                        db_new.close()
                except Exception:
                    logger.exception("Failed to record job failure")

        except Exception:
            logger.exception("Worker loop error")
        finally:
            db.close()

    logger.info("Worker shut down gracefully")


if __name__ == "__main__":
    run_worker()
