"""Live end-to-end verification against a running KBN University AI Helpdesk.

This script deliberately talks to the real HTTP API (no fakes, no monkeypatching)
so it exercises the real ChromaDB collection, the real grounding guards and the
real Qwen3:8B worker.

Examples
--------
# Fast checks only (hallucination guards + API contract)
python backend/scripts/verify_live.py --mode hallucinations

# All seven university questions (Qwen3:8B on CPU: allow roughly 20 min/question)
python backend/scripts/verify_live.py --mode all

# Resume an interrupted run
python backend/scripts/verify_live.py --mode valid --resume
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = PROJECT_ROOT / "backend" / "reports" / "live_verification.json"

FALLBACK = "I couldn't find verified information about this in the available KBN University records."


# --------------------------------------------------------------------- cases
VALID_CASES: list[dict[str, Any]] = [
    {
        "id": "vc_identity",
        "question": "Who is the Vice Chancellor of KBN University?",
        "must_match_all": [
            [r"ali raza moosvi"],
            [r"vice[- ]chancellor"],
        ],
    },
    {
        "id": "vc_email",
        "question": "What is the email address of the Vice Chancellor of KBN University?",
        "must_match_all": [
            [r"vc@kbn\.university"],
        ],
    },
    {
        "id": "faculties",
        "question": "Which faculties and departments does KBN University have?",
        "must_match_all": [
            [r"medical sciences"],
            [r"engineering"],
            [r"faculty"],
        ],
    },
    {
        "id": "courses",
        "question": "What courses does KBN University offer?",
        "must_match_all": [
            [r"mbbs"],
            [r"engineering"],
        ],
    },
    {
        "id": "location_contact",
        "question": "Where is KBN University located?",
        "must_match_all": [
            [r"kalaburagi", r"gulbarga"],
        ],
    },
    {
        "id": "established_ranking",
        "question": "When was KBN University established?",
        "must_match_all": [
            [r"2018"],
        ],
    },
    {
        "id": "official_contact",
        "question": "What is the university's official contact information?",
        "must_match_all": [
            [r"contact@kbn\.university"],
            [r"245555", r"236041", r"228540"],
        ],
    },
]

HALLUCINATION_CASES: list[dict[str, Any]] = [
    {
        "id": "fake_year",
        "question": "Who is the Vice Chancellor of KBN University in 2027?",
    },
    {
        "id": "fake_person",
        "question": "Who is Prof. Zayn Fake at KBN University?",
    },
    {
        "id": "fake_department_basket",
        "question": "Tell me about the Department of Quantum Basket Weaving at KBN University.",
    },
    {
        "id": "fake_astrobiology",
        "question": "Does KBN University offer a degree in Astrobiology?",
    },
    {
        "id": "hostel_fees",
        "question": "What are the exact hostel fees at KBN University?",
    },
    {
        "id": "fake_department_hoverboards",
        "question": "Tell me about the Department of Hoverboards at KBN University.",
    },
    {
        "id": "fake_department_imaginary",
        "question": "Who is the Dean of the Department of Imaginary Studies at KBN University?",
    },
    {
        "id": "fake_course_fee",
        "question": "What is the admission fee for the B.Sc. in Quantum Computing at KBN University?",
    },
    {
        "id": "fake_professor_phone",
        "question": "What is the phone number of Prof. Imran Non Existent at KBN University?",
    },
    {
        "id": "fake_international_campus",
        "question": "What is the address of KBN University's London international campus?",
    },
]

# Strings that must never appear in an answer grounded in the KBN records.
FABRICATION_MARKERS = [
    "quantum basket weaving",
    "zayn fake",
    "hoverboard",
    "astrobiology",
    "department of quantum",
    "imaginary studies",
    "quantum computing",
    "imran non existent",
    "london international",
]

# A guard-blocked question never reaches Qwen (which takes many minutes on CPU).
GUARD_MAX_SECONDS = 180.0


# ------------------------------------------------------------------- helpers
def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalise(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def match_groups(answer: str, groups: list[list[str]]) -> tuple[bool, list[str]]:
    missing: list[str] = []
    for alternatives in groups:
        if not any(re.search(pattern, answer, re.IGNORECASE) for pattern in alternatives):
            missing.append(" | ".join(alternatives))
    return (not missing, missing)


def fallback_ok(text: str) -> bool:
    stripped = normalise(text)
    return stripped == normalise(FALLBACK) or (
        stripped.startswith(normalise(FALLBACK))
        and stripped.endswith(".")
        and len(stripped) <= len(normalise(FALLBACK)) + 2
    )


def load_report(path: Path) -> dict[str, Any]:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"created_at": iso_now(), "cases": {}}


def save_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    report["updated_at"] = iso_now()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def already_passed(report: dict[str, Any], cases: list[dict[str, Any]], resume: bool) -> list[dict[str, Any]]:
    if not resume:
        return cases
    return [case for case in cases if not report["cases"].get(case["id"], {}).get("passed")]


# -------------------------------------------------------------------- checks
def validate_valid(case: dict[str, Any], status: int, body: dict[str, Any], elapsed: float) -> list[str]:
    problems: list[str] = []
    if status != 200:
        problems.append(f"HTTP {status}")
        return problems

    message = (body.get("assistant_message") or {}).get("content", "")
    answer = normalise(message)
    if not answer:
        problems.append("empty answer")
    if fallback_ok(answer):
        problems.append("received fallback instead of a grounded answer")
    if not body.get("grounded"):
        problems.append("grounded flag is false")
    verification = (body.get("verification") or {}).get("status")
    if verification != "verified_kb":
        problems.append(f"verification.status={verification!r}, expected 'verified_kb'")
    sources = body.get("sources") or []
    if not sources:
        problems.append("no sources returned")
    for source in sources:
        if not source.get("file") or not source.get("title"):
            problems.append(f"source missing title/file: {source.get('id')}")
        if source.get("url") and not str(source["url"]).startswith(("http://", "https://")):
            problems.append(f"invalid source url: {source['url']}")

    ok, missing = match_groups(answer, case["must_match_all"])
    if not ok:
        problems.append("answer missing expected facts: " + "; ".join(missing))
    for marker in FABRICATION_MARKERS:
        if marker in answer.lower():
            problems.append(f"answer contains fabricated entity marker: {marker!r}")
    if elapsed > 2400:
        problems.append(f"request exceeded backend timeout budget ({elapsed:.1f}s)")
    return problems


def validate_hallucination(status: int, body: dict[str, Any], elapsed: float) -> list[str]:
    problems: list[str] = []
    if status != 200:
        problems.append(f"HTTP {status}")
        return problems

    message = (body.get("assistant_message") or {}).get("content", "")
    if not fallback_ok(message):
        problems.append("answer is not the exact mandatory fallback")
    if body.get("sources"):
        problems.append("fabricated/irrelevant sources returned for an unverified question")
    if body.get("grounded"):
        problems.append("grounded flag is true for an unverifiable question")
    verification = (body.get("verification") or {}).get("status")
    if verification != "unavailable":
        problems.append(f"verification.status={verification!r}, expected 'unavailable'")
    if elapsed > GUARD_MAX_SECONDS:
        problems.append(
            f"guard response took {elapsed:.1f}s (> {GUARD_MAX_SECONDS:.0f}s); "
            "the LLM may have been invoked"
        )
    return problems


# ---------------------------------------------------------------------- main
def run(base_url: str, mode: str, timeout: float, output: Path, resume: bool) -> int:
    cases = []
    if mode in {"valid", "all"}:
        cases += [("valid", case) for case in VALID_CASES]
    if mode in {"hallucinations", "all"}:
        cases += [("hallucination", case) for case in HALLUCINATION_CASES]

    report = load_report(output)
    report.setdefault("cases", {})
    report["base_url"] = base_url
    report["mode"] = mode
    report["contract"] = {"fallback_message": FALLBACK}

    with httpx.Client(
        base_url=base_url.rstrip("/"),
        timeout=httpx.Timeout(connect=15.0, read=timeout, write=60.0, pool=15.0),
        headers={"User-Agent": "kbn-live-verifier/1.0"},
    ) as client:
        health = client.get("/health")
        report["health"] = {
            "status_code": health.status_code,
            "body": health.json() if health.headers.get("content-type", "").startswith("application/json") else None,
            "checked_at": iso_now(),
        }
        print(f"[health] HTTP {health.status_code}")
        if health.status_code != 200:
            print("[fatal] backend health check failed")
            save_report(output, report)
            return 2

        for kind, case in cases:
            existing = report["cases"].get(case["id"], {})
            if resume and existing.get("passed"):
                print(f"[skip] {case['id']} (already passed)")
                continue

            print(f"\n[{kind}] {case['id']}: {case['question']}", flush=True)
            session = client.post("/sessions", json={"title": f"verify-{case['id']}"})
            entry: dict[str, Any] = {
                "kind": kind,
                "question": case["question"],
                "started_at": iso_now(),
                "session_id": None,
                "status_code": None,
                "elapsed_seconds": None,
                "assistant_message": None,
                "sources": [],
                "verification": None,
                "grounded": None,
                "retrieval": None,
                "performance": None,
                "transcript_message_count": None,
                "problems": [],
                "passed": False,
            }
            if session.status_code != 201:
                entry["problems"] = [f"session create HTTP {session.status_code}: {session.text[:300]}"]
                entry["finished_at"] = iso_now()
                report["cases"][case["id"]] = entry
                save_report(output, report)
                print("  FAILED to create session")
                continue

            session_id = session.json()["id"]
            entry["session_id"] = session_id
            started = time.perf_counter()
            try:
                response = client.post(
                    "/chat",
                    json={"session_id": session_id, "message": case["question"]},
                )
                elapsed = time.perf_counter() - started
                entry["status_code"] = response.status_code
                entry["elapsed_seconds"] = round(elapsed, 2)
                try:
                    body = response.json()
                except json.JSONDecodeError:
                    body = {}
                    entry["problems"].append("response was not JSON")
                entry["raw_error"] = body if response.status_code >= 400 else None
                entry["assistant_message"] = (body.get("assistant_message") or {}).get("content")
                entry["sources"] = body.get("sources") or []
                entry["verification"] = body.get("verification")
                entry["grounded"] = body.get("grounded")
                entry["retrieval"] = body.get("retrieval")
                response_ms = body.get("response_time_ms")
                assistant = body.get("assistant_message") or {}
                llm_seconds = assistant.get("llm_time_seconds")
                retrieval_ms = (body.get("retrieval") or {}).get("duration_ms")
                perf = {
                    "total_response_ms": response_ms,
                    "retrieval_ms": retrieval_ms,
                    "llm_generation_ms": int(llm_seconds * 1000) if isinstance(llm_seconds, (int, float)) else None,
                    "queue_and_overhead_ms": None,
                }
                if isinstance(response_ms, (int, float)) and isinstance(retrieval_ms, (int, float)) and isinstance(llm_seconds, (int, float)):
                    perf["queue_and_overhead_ms"] = int(response_ms - retrieval_ms - llm_seconds * 1000)
                entry["performance"] = perf

                if kind == "valid":
                    entry["problems"] = validate_valid(case, response.status_code, body, elapsed)
                else:
                    entry["problems"] = validate_hallucination(response.status_code, body, elapsed)

                transcript = client.get(f"/sessions/{session_id}")
                if transcript.status_code == 200:
                    entry["transcript_message_count"] = len(transcript.json().get("messages") or [])
                    expected = 2 if response.status_code == 200 else 1
                    if entry["transcript_message_count"] != expected:
                        entry["problems"].append(
                            f"transcript has {entry['transcript_message_count']} messages, expected {expected}"
                        )
                else:
                    entry["problems"].append(f"transcript read HTTP {transcript.status_code}")
            except httpx.TimeoutException as exc:
                entry["elapsed_seconds"] = round(time.perf_counter() - started, 2)
                entry["problems"] = [f"client timeout: {exc}"]
            except httpx.HTTPError as exc:
                entry["elapsed_seconds"] = round(time.perf_counter() - started, 2)
                entry["problems"] = [f"transport error: {exc}"]

            entry["passed"] = not entry["problems"]
            entry["finished_at"] = iso_now()
            report["cases"][case["id"]] = entry
            save_report(output, report)

            marker = "PASS" if entry["passed"] else "FAIL"
            print(f"  {marker} in {entry['elapsed_seconds']}s", flush=True)
            if entry.get("performance"):
                perf = entry["performance"]
                print(
                    "  perf: total={total}ms retrieval={retrieval}ms llm={llm}ms queue/overhead={queue}ms".format(
                        total=perf.get("total_response_ms"),
                        retrieval=perf.get("retrieval_ms"),
                        llm=perf.get("llm_generation_ms"),
                        queue=perf.get("queue_and_overhead_ms"),
                    ),
                    flush=True,
                )
            if entry["assistant_message"]:
                print("  answer: " + normalise(entry["assistant_message"])[:400], flush=True)
            if entry["problems"]:
                for problem in entry["problems"]:
                    print(f"  - {problem}", flush=True)

    totals = {
        "total": len(report["cases"]),
        "passed": sum(1 for value in report["cases"].values() if value.get("passed")),
    }
    totals["failed"] = totals["total"] - totals["passed"]
    report["totals"] = totals
    save_report(output, report)
    print(f"\n[summary] {totals['passed']}/{totals['total']} passed -> {output}")
    return 0 if totals["failed"] == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--mode",
        choices=["all", "valid", "hallucinations"],
        default="all",
        help="Which case set to run.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=2600.0,
        help="Per-request read timeout in seconds (backend timeout is 2400).",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--resume", action="store_true", help="Skip cases already marked as passed.")
    args = parser.parse_args()
    return run(args.base_url, args.mode, args.timeout, args.output, args.resume)


if __name__ == "__main__":
    sys.exit(main())
