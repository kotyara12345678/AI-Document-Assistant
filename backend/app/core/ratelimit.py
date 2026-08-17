"""Tiny in-memory fixed-window throttling for auth abuse & LLM cost control.

Deliberately minimal: per-key hit timestamps kept in a dict behind a lock.
Windows are checked lazily — expired entries are dropped only when a key is
touched — so there are no background timers, no global scans and no
measurable load. State survives only in the process that served the request
(no shared Redis); fine for a single-node deployment.

Thresholds live here as module constants so tests can shrink them via
``monkeypatch`` without a config surface change.
"""

import threading
import time

FAILED_LOGIN_LIMIT = 8
FAILED_LOGIN_WINDOW = 900  # seconds (15 min)

AUTH_BURST_LIMIT = 120
AUTH_BURST_WINDOW = 60  # seconds

CHAT_BURST_LIMIT = 30
CHAT_BURST_WINDOW = 60  # seconds

AGENT_BURST_LIMIT = 30
AGENT_BURST_WINDOW = 60  # seconds

# Upper bound on tracked keys. Keys are keyed by client IP/account, so without
# a cap a flood of requests from rotating IPs could grow _hits without limit.
# When the bound is crossed we first drop keys whose window has fully expired;
# if it is still over the cap the whole table is reset (much cheaper and safer
# than a slow global scan on every request).
MAX_KEYS = 10_000


class RateLimiter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._hits: dict[str, list[float]] = {}

    def allow(self, key: str, limit: int, window_seconds: int) -> bool:
        """Record one hit for ``key``; return True when still within the limit.

        The first ``limit`` hits in the window are allowed; anything after is
        refused and not counted further until old stamps age out.
        """
        now = time.monotonic()
        cutoff = now - window_seconds
        with self._lock:
            if len(self._hits) >= MAX_KEYS:
                self._prune_under_lock(cutoff)
            stamps = self._hits.get(key)
            if stamps is None:
                self._hits[key] = [now]
                return True
            alive = [t for t in stamps if t > cutoff]
            if len(alive) >= limit:
                self._hits[key] = alive
                return False
            alive.append(now)
            self._hits[key] = alive
            return True

    def _prune_under_lock(self, cutoff: float) -> None:
        """Drop entries whose timestamps are all older than ``cutoff``."""
        self._hits = {
            key: [t for t in stamps if t > cutoff]
            for key, stamps in self._hits.items()
            if any(t > cutoff for t in stamps)
        }
        if len(self._hits) >= MAX_KEYS:
            # Still saturated (attacker keeps each key alive): reset instead of
            # letting memory grow further for the rest of the process lifetime.
            self._hits.clear()

    def reset(self) -> None:
        """Drop all recorded hits (used by tests for deterministic counters)."""
        with self._lock:
            self._hits.clear()


# Single shared instance used by the routes that need throttling.
throttle = RateLimiter()