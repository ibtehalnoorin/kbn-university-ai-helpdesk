"""Concurrent-user smoke test against a running server.

Each simulated visitor has its own anonymous id and session, asks two questions and
then reads the transcript back. Checks: no 5xx, every answer belongs to the right
session, and each visitor's transcript contains exactly their own messages.

Rate limits are per client address, so start the server under test with them raised:
  KBN_RATE_LIMIT_PER_MINUTE=1000 KBN_RATE_LIMIT_SESSIONS_PER_HOUR=1000 \
  KBN_DAILY_LLM_BUDGET=100000 python backend/run_server.py

Usage:  python backend/scripts/load_smoke.py --base-url http://127.0.0.1:8000 --users 20
Exit code 0 when all checks pass. 429/503 busy replies are counted, not failures.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import httpx

QUESTIONS = ("What is {n} + {n}?", "Name one planet. (visitor {n})")


def visitor(base_url: str, n: int, timeout: float) -> dict:
    result = {"n": n, "latencies": [], "busy": 0, "errors": [], "ok": 0}
    headers = {"X-Client-Id": uuid.uuid4().hex}
    with httpx.Client(base_url=base_url, headers=headers, timeout=timeout) as client:
        created = client.post("/sessions", json={})
        if created.status_code != 201:
            result["errors"].append(f"create session -> {created.status_code}")
            return result
        sid = created.json()["id"]
        asked = []
        for template in QUESTIONS:
            question = template.format(n=n)
            started = time.perf_counter()
            reply = client.post("/chat", json={"session_id": sid, "message": question})
            result["latencies"].append(time.perf_counter() - started)
            if reply.status_code in (429, 503):
                result["busy"] += 1
                continue
            if reply.status_code != 200:
                result["errors"].append(f"chat -> {reply.status_code} {reply.text[:120]}")
                continue
            body = reply.json()
            if body["session_id"] != sid or body["user_message"]["content"] != question:
                result["errors"].append("answer belongs to a different session")
                continue
            asked.append(question)
            result["ok"] += 1
        detail = client.get(f"/sessions/{sid}")
        if detail.status_code != 200:
            result["errors"].append(f"read transcript -> {detail.status_code}")
        else:
            users = [m["content"] for m in detail.json()["messages"] if m["role"] == "user"]
            if users != asked:
                result["errors"].append(f"transcript mismatch: {users} != {asked}")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--users", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=60)
    args = parser.parse_args()

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.users) as pool:
        results = list(pool.map(lambda n: visitor(args.base_url.rstrip("/"), n, args.timeout), range(args.users)))
    wall = time.perf_counter() - started

    latencies = sorted(x for r in results for x in r["latencies"])
    errors = [e for r in results for e in r["errors"]]
    p95 = latencies[int(0.95 * (len(latencies) - 1))] if latencies else float("nan")
    print(f"visitors={args.users} wall={wall:.1f}s answered={sum(r['ok'] for r in results)} "
          f"busy(429/503)={sum(r['busy'] for r in results)} errors={len(errors)}")
    if latencies:
        print(f"latency median={statistics.median(latencies):.2f}s p95={p95:.2f}s max={latencies[-1]:.2f}s")
    for error in errors[:10]:
        print("ERROR:", error)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
