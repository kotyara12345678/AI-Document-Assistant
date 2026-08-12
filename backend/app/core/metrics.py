"""In-memory request/error metrics for the admin dashboard.

Records only status codes, request paths and durations per request — never
bodies, headers, query strings or user data. That keeps the aggregated stats
free of passwords, JWTs, GigaChat credentials or private document/chat
content. State is process-local (like the rate limiter) and resets on
restart, which is fine for an at-a-glance admin panel.
"""

import threading
from collections import deque
from datetime import datetime, timezone
from typing import Deque

RECENT_ERRORS_LIMIT = 50


class RequestMetrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._total: int = 0
        self._by_status: dict[int, int] = {}
        self._latency_sum_ms: float = 0.0
        self._recent_errors: Deque[dict] = deque(maxlen=RECENT_ERRORS_LIMIT)

    def record(self, status: int, path: str, duration_ms: float) -> None:
        """Record one finished request. ``path`` is the URL path only."""
        with self._lock:
            self._total += 1
            self._by_status[status] = self._by_status.get(status, 0) + 1
            self._latency_sum_ms += duration_ms
            if 400 <= status < 600:
                self._recent_errors.append(
                    {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "status": status,
                        "path": path,
                    }
                )

    def snapshot(self) -> dict:
        with self._lock:
            buckets = {
                "2xx": sum(v for k, v in self._by_status.items() if 200 <= k < 300),
                "3xx": sum(v for k, v in self._by_status.items() if 300 <= k < 400),
                "4xx": sum(v for k, v in self._by_status.items() if 400 <= k < 500),
                "5xx": sum(v for k, v in self._by_status.items() if k >= 500),
            }
            return {
                "total": self._total,
                "by_status": dict(self._by_status),
                "status_buckets": buckets,
                "error_total": buckets["4xx"] + buckets["5xx"],
                "average_latency_ms": (
                    round(self._latency_sum_ms / self._total, 2) if self._total else 0.0
                ),
                "recent_errors": list(self._recent_errors),
            }

    def reset(self) -> None:
        """Drop all recorded metrics (used by tests for deterministic counts)."""
        with self._lock:
            self._total = 0
            self._by_status = {}
            self._latency_sum_ms = 0.0
            self._recent_errors.clear()


# Single shared instance used by the metrics middleware and the admin router.
metrics = RequestMetrics()