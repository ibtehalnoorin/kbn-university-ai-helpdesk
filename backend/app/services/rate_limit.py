"""In-memory abuse protection for a single-instance deployment.

State lives in the API process (fine for one free-tier instance; use Redis if the
service is ever scaled to several replicas).
"""

from __future__ import annotations

import math
import threading
import time
from collections import defaultdict, deque
from contextvars import ContextVar

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


# The caller's address for the request being served; set by the chat route so the
# service layer can charge the LLM budget to the right visitor without new parameters.
current_client_key: ContextVar[str | None] = ContextVar("current_client_key", default=None)


class DailyBudget:
    """Caps LLM calls per UTC day (overall and per client key) to protect the free-tier quota."""

    def __init__(self, max_calls: int, per_key_max: int | None = None) -> None:
        self.max_calls = max_calls
        self.per_key_max = per_key_max
        self._day = -1
        self._used = 0
        self._per_key: dict[str, int] = defaultdict(int)
        self._lock = threading.Lock()

    def _roll(self, now: float) -> None:
        day = int(now // 86400)
        if day != self._day:
            self._day, self._used = day, 0
            self._per_key = defaultdict(int)

    def try_consume(self, now: float | None = None, key: str | None = None) -> bool:
        now = time.time() if now is None else now
        with self._lock:
            self._roll(now)
            if self._used >= self.max_calls:
                return False
            if key is not None and self.per_key_max is not None and self._per_key[key] >= self.per_key_max:
                return False
            self._used += 1
            if key is not None:
                self._per_key[key] += 1
            return True

    def refund(self, key: str | None = None) -> None:
        with self._lock:
            self._used = max(0, self._used - 1)
            if key is not None and self._per_key.get(key):
                self._per_key[key] -= 1


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
