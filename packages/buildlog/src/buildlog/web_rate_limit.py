"""Bounded local rate limiting for one application replica."""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque


class SlidingWindowRateLimiter:
    """Protect one replica; use a shared store when global limits are required."""

    def __init__(self, requests_per_minute: int) -> None:
        self._limit = requests_per_minute
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str, *, now: float | None = None) -> tuple[bool, int, int]:
        """Return allowed, remaining requests, and reset seconds."""
        if self._limit <= 0:
            return True, 2**31 - 1, 0
        current = time.monotonic() if now is None else now
        cutoff = current - 60
        with self._lock:
            events = self._events[key]
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= self._limit:
                reset = max(1, int(60 - (current - events[0])))
                return False, 0, reset
            events.append(current)
            return True, self._limit - len(events), 60
