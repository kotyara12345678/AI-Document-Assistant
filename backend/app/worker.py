"""Background job worker — standalone process that executes queued jobs.

Polls PostgreSQL for ``queued`` jobs using ``FOR UPDATE SKIP LOCKED``,
executes them via the agent service, and persists results + notifications.

Run as a separate Docker container alongside the backend:
    python -m app.worker

Or via the entrypoint:
    exec python -m app.worker
"""

import logging
import os
import signal
import sys
import time

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
    """Create a notification for the user about the completed/failed job."""
    db = SessionLocal()
    try:
        answer = result.get("answer", "")
        # Build a short title from the answer or job type.
        title = f"Задача завершена ({job.type})"
        body = answer[:500] if answer else "Задача выполнена."

        # If documents were created, mention them.
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
    except Exception:
        logger.exception("Failed to create notification for job %s", job.id)
    finally:
        db.close()


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
