"""In-memory abuse protection for a single-instance deployment.

State lives in the API process (fine for one free-tier instance; use Redis if the
service is ever scaled to several replicas).
"""

from __future__ import annotations

import math
import threading
import time
from collections import defaultdict, deque

from starlette.requests import Request


class SlidingWindowLimiter:
    def __init__(self, limit: int, window_seconds: float) -> None:
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> deque[float]:
        hits = self._hits[key]
        while hits and hits[0] <= now - self.window:
            hits.popleft()
        if not hits:
            self._hits.pop(key, None)
            return self._hits[key]
        return hits

    def allow(self, key: str, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        with self._lock:
            hits = self._prune(key, now)
            if len(hits) >= self.limit:
                return False
            hits.append(now)
            return True

    def retry_after(self, key: str, now: float | None = None) -> int:
        now = time.time() if now is None else now
        with self._lock:
            hits = self._hits.get(key)
            if not hits:
                return 0
            return max(1, math.ceil(hits[0] + self.window - now))


class DailyBudget:
    """Caps LLM calls per UTC day so the free-tier quota cannot be drained."""

    def __init__(self, max_calls: int) -> None:
        self.max_calls = max_calls
        self._day = -1
        self._used = 0
        self._lock = threading.Lock()

    def try_consume(self, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        day = int(now // 86400)
        with self._lock:
            if day != self._day:
                self._day, self._used = day, 0
            if self._used >= self.max_calls:
                return False
            self._used += 1
            return True


def client_ip(request: Request, trusted_hops: int = 1) -> str:
    """The caller's address behind ``trusted_hops`` proxies that append to X-Forwarded-For.

    The entry the nearest trusted proxy appended is used (counting from the right);
    entries to its left can be forged by the client and are ignored.
    """
    socket_host = request.client.host if request.client else "unknown"
    forwarded = request.headers.get("x-forwarded-for")
    if trusted_hops <= 0 or not forwarded:
        return socket_host
    hops = [h.strip() for h in forwarded.split(",") if h.strip()]
    if len(hops) < trusted_hops:
        return socket_host
    return hops[-trusted_hops]
