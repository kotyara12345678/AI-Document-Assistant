# Background Jobs Report

## What Changed

Added a persistent background job queue to ADA, enabling the agent to execute
long-running tasks (large translations, PDF edits, document generation)
**autonomously on the server** — even if the user closes the browser, refreshes
the page, or loses internet connectivity.

## Files Changed

### New Files
| File | Purpose |
|------|---------|
| `app/models/job.py` | Job ORM model (status, payload, result, timestamps) |
| `app/models/notification.py` | Notification ORM model (title, body, is_read) |
| `app/services/job.py` | Job lifecycle: create, claim (`FOR UPDATE SKIP LOCKED`), complete, fail, cancel, crash recovery |
| `app/services/notification.py` | Notification CRUD: create, list, mark read, unread count |
| `app/schemas/job.py` | Pydantic schemas for Job/Notification API |
| `app/api/routes/jobs.py` | API: `GET /jobs`, `GET /jobs/{id}`, `POST /jobs/{id}/cancel`, `POST /jobs` |
| `app/api/routes/notifications.py` | API: `GET /notifications`, `POST /notifications/{id}/read`, `POST /notifications/read-all`, `GET /notifications/stream` (SSE) |
| `app/worker.py` | Standalone worker process: polls DB, executes jobs, sends notifications |
| `alembic/versions/0013_background_jobs.py` | Migration: creates `jobs` and `notifications` tables |
| `tests/test_background_jobs.py` | 30+ tests covering lifecycle, recovery, ownership, concurrency, API |

### Modified Files
| File | Change |
|------|--------|
| `app/models/user.py` | Added `jobs` and `notifications` relationships |
| `app/models/__init__.py` | Registered Job and Notification models |
| `app/main.py` | Registered `/jobs` and `/notifications` routers |
| `app/core/config.py` | Added worker config settings (concurrency, intervals, limits) |
| `deploy/docker-compose.production.yml` | Added `worker` service |
| `deploy/docker-compose.staging.yml` | Added `worker` service |
| `tests/conftest.py` | Added Job/Notification cleanup to `_clean_db` fixture |

## How It Works

### Architecture
```
Browser → POST /api/jobs → creates Job row in PostgreSQL
                          → returns {id, status: "queued"}

Worker (separate container) → polls PostgreSQL
                            → FOR UPDATE SKIP LOCKED (safe for multiple workers)
                            → runs full agent loop (search, read, create, edit)
                            → saves result to job.result
                            → creates notification row

Browser → GET /api/notifications → sees completed job notification
         GET /api/jobs/{id}      → gets full result
```

### Job Lifecycle
```
queued → running → completed
                 → failed
                 → cancelled
```

### Crash Recovery
On worker startup, all jobs stuck in `running` longer than
`JOB_STALE_TIMEOUT_SECONDS` (default: 10 minutes) are reset to `queued`
and will be re-executed by the next available worker.

### Concurrent Worker Safety
Uses PostgreSQL `FOR UPDATE SKIP LOCKED` to ensure multiple workers never
claim the same job. FIFO ordering guarantees fairness.

### Notification Flow
1. Worker completes a job → creates a `Notification` row
2. If user is online, `GET /notifications/stream` (SSE) pushes the event
3. If user is offline, notification waits in the DB
4. On next page load, frontend fetches `GET /notifications` and shows unread

## Configuration

| Setting | Default | Description |
|---------|---------|-------------|
| `WORKER_CONCURRENCY` | 1 | Max concurrent jobs per worker |
| `JOB_POLL_INTERVAL` | 2.0s | Seconds between polling cycles |
| `JOB_STALE_TIMEOUT_SECONDS` | 600 | Seconds before a running job is considered stale |
| `MAX_QUEUED_JOBS_PER_USER` | 5 | Max queued jobs per user |
| `MAX_RUNNING_JOBS_PER_USER` | 2 | Max running jobs per user |

## API Endpoints

### Jobs
- `POST /api/jobs` — submit a background job
- `GET /api/jobs` — list user's jobs
- `GET /api/jobs/{id}` — get job detail
- `POST /api/jobs/{id}/cancel` — cancel a queued/running job

### Notifications
- `GET /api/notifications` — list notifications (with `?unread_only=true`)
- `POST /api/notifications/{id}/read` — mark one as read
- `POST /api/notifications/read-all` — mark all as read
- `GET /api/notifications/stream` — SSE stream (5 min timeout, reconnects)

## Running Tests

```bash
docker compose run --rm --entrypoint python backend -m pytest tests/test_background_jobs.py -v
```

## Known Limitations

1. **Single active job per agent request**: The API accepts `POST /api/jobs`
   but the agent loop is the same synchronous `run_agent_stream` running in
   the worker. Multiple concurrent jobs from the same user are limited by
   `MAX_RUNNING_JOBS_PER_USER`.

2. **SSE stream timeout**: The notification SSE stream times out after 5
   minutes. Clients must reconnect. This is intentional to prevent
   unbounded connection holding.

3. **No Redis**: All coordination happens through PostgreSQL. For very high
   throughput, a Redis-based job queue would be more efficient, but
   PostgreSQL with `FOR UPDATE SKIP LOCKED` is sufficient for single-digit
   workers.

4. **Cooperative cancellation**: Running jobs check `cancel_requested`
   between LLM rounds, not mid-LLM-call. A long-running GigaChat request
   cannot be interrupted until it returns.

5. **Worker shares the same Docker image as backend**: The worker uses the
   same container image but runs `python -m app.worker` instead of uvicorn.
   This means worker dependencies are bundled with the backend image.
