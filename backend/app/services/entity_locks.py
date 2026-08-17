"""Tiny per-key lock registry for check-then-act critical sections.

Keeps one ``threading.Lock`` per key (document id, user id) so concurrent index
and delete of the SAME document, or first-chat creation for the SAME user, are
serialized without touching the rest of the system. There is no per-key
unlock/free: each entry is a few dozen bytes and there is at most one entry per
document/user actually touched during the process lifetime -- the same order as
the rows themselves, so unbounded growth is not a realistic concern. Verdict
of the concurrency audit: the two raced sections were index/delete of one
document and first-chat creation; both are fixed by holding this key lock.
"""

import threading

_lock = threading.Lock()
_registry: dict[int, threading.Lock] = {}


def lock_for(key: int) -> threading.Lock:
    """Return the single lock dedicated to ``key`` (created once)."""
    with _lock:
        lock = _registry.get(key)
        if lock is None:
            lock = threading.Lock()
            _registry[key] = lock
        return lock