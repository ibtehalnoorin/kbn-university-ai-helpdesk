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


class SharedWindowLimiter:
    """Fixed-window limiter whose counters live in a shared store (Postgres).

    Every server instance increments the same counter, so the limit holds when the
    platform runs several copies of the app. A fixed window can admit up to twice the
    limit across a window boundary; that is acceptable for abuse protection.
    """

    def __init__(self, store, limit: int, window_seconds: float, name: str) -> None:
        self.store, self.limit, self.window, self.name = store, limit, window_seconds, name

    def allow(self, key: str, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        bucket = int(now // self.window)
        used = self.store.incr_counter(f"{self.name}:{key}", bucket, int(self.window * 2))
        return used <= self.limit

    def retry_after(self, key: str, now: float | None = None) -> int:
        now = time.time() if now is None else now
        return max(1, math.ceil(self.window - (now % self.window)))


def check_limits(pairs, now: float | None = None) -> int | None:
    """Check several ``(limiter, key)`` pairs; return seconds to wait if any blocks, else None.

    When the limiters share one Postgres store the counters are bumped in a single round trip.
    """
    now = time.time() if now is None else now
    shared = [lim for lim, _ in pairs if isinstance(lim, SharedWindowLimiter)]
    if len(shared) == len(pairs) and len({id(lim.store) for lim in shared}) == 1 and hasattr(shared[0].store, "incr_counters"):
        items = [
            (f"{lim.name}:{key}", int(now // lim.window), int(lim.window * 2), 1) for lim, key in pairs
        ]
        used = shared[0].store.incr_counters(items)
        waits = [lim.retry_after(key, now) for (lim, key), n in zip(pairs, used) if n > lim.limit]
        return max(waits) if waits else None
    for limiter, key in pairs:
        if not limiter.allow(key, now):
            return limiter.retry_after(key, now)
    return None


class SharedDailyBudget:
    """Daily LLM budget (overall and per client key) shared by every server instance."""

    TTL = 2 * 86400

    def __init__(self, store, max_calls: int, per_key_max: int | None = None) -> None:
        self.store, self.max_calls, self.per_key_max = store, max_calls, per_key_max

    def _keys(self, key: str | None) -> list[str]:
        return ["budget:all"] + ([f"budget:key:{key}"] if key is not None and self.per_key_max is not None else [])

    def try_consume(self, now: float | None = None, key: str | None = None) -> bool:
        if self.max_calls <= 0:
            return False
        day = int((time.time() if now is None else now) // 86400)
        limits = {"budget:all": self.max_calls}
        if key is not None and self.per_key_max is not None:
            limits[f"budget:key:{key}"] = self.per_key_max
        names = self._keys(key)
        if hasattr(self.store, "incr_counters"):  # one round trip for all counters
            used = self.store.incr_counters([(n, day, self.TTL, 1) for n in names])
            if any(u > limits[n] for n, u in zip(names, used)):
                self.store.incr_counters([(n, day, self.TTL, -1) for n in names])
                return False
            return True
        taken: list[str] = []
        for name in names:
            taken.append(name)
            if self.store.incr_counter(name, day, self.TTL) > limits[name]:
                for undo in taken:  # over the cap: put back everything this call added
                    self.store.incr_counter(undo, day, self.TTL, amount=-1)
                return False
        return True

    def refund(self, key: str | None = None, now: float | None = None) -> None:
        day = int((time.time() if now is None else now) // 86400)
        names = self._keys(key)
        if hasattr(self.store, "incr_counters"):
            self.store.incr_counters([(n, day, self.TTL, -1) for n in names])
            return
        for name in names:
            self.store.incr_counter(name, day, self.TTL, amount=-1)


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
