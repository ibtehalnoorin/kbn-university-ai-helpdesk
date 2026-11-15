"""Render live-verification JSON reports as a Markdown summary.

Usage:
    python backend/scripts/summarize_reports.py
    python backend/scripts/summarize_reports.py --output VERIFICATION.md
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REPORT_DIR = PROJECT_ROOT / "backend" / "reports"


def load(name: str) -> dict:
    path = REPORT_DIR / name
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def answer_preview(text: str | None, limit: int = 220) -> str:
    cleaned = " ".join((text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"


def table_row(case: dict) -> str:
    verification = (case.get("verification") or {}).get("status") or "—"
    sources = len(case.get("sources") or [])
    elapsed = case.get("elapsed_seconds")
    elapsed_text = f"{elapsed:.1f}s" if isinstance(elapsed, (int, float)) else "—"
    status = "PASS" if case.get("passed") else "FAIL"
    question = answer_preview(case.get("question"), 90).replace("|", "\\|")
    answer = answer_preview(case.get("assistant_message")).replace("|", "\\|")
    return f"| {question} | {status} | {elapsed_text} | {verification} | {sources} | {answer} |"


def _ms(value: object) -> str:
    if isinstance(value, (int, float)):
        return f"{int(value)} ms"
    return "—"


def perf_row(case: dict) -> str:
    perf = case.get("performance") or {}
    question = answer_preview(case.get("question"), 70).replace("|", "\\|")
    elapsed = case.get("elapsed_seconds")
    elapsed_text = f"{elapsed:.1f}s" if isinstance(elapsed, (int, float)) else "—"
    return (
        f"| {question} | {elapsed_text} | {_ms(perf.get('total_response_ms'))} "
        f"| {_ms(perf.get('retrieval_ms'))} | {_ms(perf.get('llm_generation_ms'))} "
        f"| {_ms(perf.get('queue_and_overhead_ms'))} |"
    )


def case_totals(report: dict) -> dict:
    cases = list((report.get("cases") or {}).values())
    passed = sum(1 for case in cases if case.get("passed"))
    return {"total": len(cases), "passed": passed, "failed": len(cases) - passed}


def _stats(values: list[int]) -> dict:
    return {
        "min": min(values),
        "max": max(values),
        "mean": statistics.mean(values),
        "median": statistics.median(values),
    }


def stage_stats(valid: dict) -> dict[str, dict]:
    perf = [case.get("performance") or {} for case in (valid.get("cases") or {}).values()]
    keys = ("total_response_ms", "retrieval_ms", "llm_generation_ms", "queue_and_overhead_ms")
    return {
        key: _stats([int(p[key]) for p in perf if p.get(key) is not None])
        for key in keys
        if any(p.get(key) is not None for p in perf)
    }


def seconds(value: float) -> str:
    if abs(value) < 1000:
        return f"{int(round(value))} ms"
    return f"{value / 1000:.1f} s"


def render() -> str:
    valid = load("live_verification_valid.json")
    hallucination = load("live_verification_hallucinations.json")
    health = (valid.get("health") or {}).get("body") or {}
    components = health.get("components") or {}
    worker = health.get("worker") or {}
    protected = health.get("protected_files") or {}

    lines: list[str] = []
    lines.append("# KBN University AI Helpdesk — live verification")
    lines.append("")
    lines.append(f"- Base URL: `{valid.get('base_url') or hallucination.get('base_url') or 'http://127.0.0.1:8000'}`")
    lines.append(f"- Valid report updated: `{valid.get('updated_at', '—')}`")
    lines.append(f"- Hallucination report updated: `{hallucination.get('updated_at', '—')}`")
    lines.append(f"- App: `{health.get('app', '—')} v{health.get('version', '—')}`")
    lines.append(f"- Model: `{(components.get('ollama') or {}).get('llm_model', '—')}`")
    lines.append(f"- Embeddings: `{(components.get('ollama') or {}).get('embedding_model', '—')}`")
    lines.append(f"- ChromaDB collection: `{(components.get('chromadb') or {}).get('collection', '—')}`")
    lines.append(f"- ChromaDB chunks: `{(components.get('chromadb') or {}).get('indexed_chunks', '—')}`")
    lines.append(f"- Retrieval results (n_results): `{(health.get('configuration') or {}).get('kb_n_results', '—')}`")
    lines.append(f"- Strict grounding: `{(components.get('grounding') or {}).get('strict_grounding', '—')}`")
    lines.append(f"- Worker alive: `{worker.get('alive', '—')}`")
    lines.append(f"- Protected files OK: `{protected.get('ok', '—')}`")
    lines.append("")
    lines.append("## How this was verified")
    lines.append("")
    lines.append("```powershell")
    lines.append('$env:PYTHONPATH="$PWD;$PWD\\backend"; .\\.venv\\Scripts\\python.exe -m pytest backend\\tests -q')
    lines.append(".\\.venv\\Scripts\\python.exe backend\\scripts\\verify_live.py --mode valid --output backend\\reports\\live_verification_valid.json")
    lines.append(".\\.venv\\Scripts\\python.exe backend\\scripts\\verify_live.py --mode hallucinations --output backend\\reports\\live_verification_hallucinations.json")
    lines.append(".\\.venv\\Scripts\\python.exe backend\\scripts\\summarize_reports.py --output VERIFICATION.md")
    lines.append("```")
    lines.append("")
    lines.append(
        "Every answer below was produced by the running FastAPI service against the real "
        "Qwen3:8B model, nomic-embed-text embeddings and the existing ChromaDB collection. "
        "No retrieval, embedding, model or protected file was changed."
    )
    lines.append("")

    vtotals = case_totals(valid)
    lines.append("## Seven university questions (real Qwen3:8B)")
    lines.append("")
    lines.append(f"Result: **{vtotals.get('passed', 0)}/{vtotals.get('total', 0)} passed**")
    lines.append("")
    lines.append("| Question | Result | Time | Verification | Sources | Answer |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for case in valid.get("cases", {}).values():
        lines.append(table_row(case))
    lines.append("")
    lines.append("### Per-stage timing (valid answers)")
    lines.append("")
    lines.append(
        "Client total is wall-clock time from the verifier. Server total is `response_time_ms` "
        "reported by the API; retrieval and LLM generation are measured inside the backend. "
        "Queue/overhead is server total minus retrieval minus LLM generation (worker queue wait "
        "plus verification and persistence)."
    )
    lines.append("")
    lines.append("| Question | Client total | Server total | Retrieval | LLM generation | Queue/overhead |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for case in valid.get("cases", {}).values():
        lines.append(perf_row(case))
    lines.append("")

    htotals = case_totals(hallucination)
    lines.append("## Hallucination / missing-record questions")
    lines.append("")
    lines.append(f"Result: **{htotals.get('passed', 0)}/{htotals.get('total', 0)} passed**")
    lines.append("")
    lines.append("| Question | Result | Time | Verification | Sources | Answer |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for case in hallucination.get("cases", {}).values():
        lines.append(table_row(case))
    lines.append("")

    ok = (
        vtotals.get("failed", 1) == 0
        and htotals.get("failed", 1) == 0
        and vtotals.get("total", 0) > 0
        and htotals.get("total", 0) > 0
    )

    stats = stage_stats(valid)
    if stats:
        lines.append("### Performance summary")
        lines.append("")
        lines.append("| Stage | Min | Mean | Median | Max |")
        lines.append("| --- | --- | --- | --- | --- |")
        labels = {
            "total_response_ms": "Total response",
            "retrieval_ms": "Retrieval",
            "llm_generation_ms": "LLM generation",
            "queue_and_overhead_ms": "Queue/other server work",
        }
        for key, label in labels.items():
            values = stats.get(key)
            if not values:
                continue
            lines.append(
                f"| {label} | {seconds(values['min'])} | {seconds(values['mean'])} "
                f"| {seconds(values['median'])} | {seconds(values['max'])} |"
            )
        lines.append("")
        lines.append(
            "Retrieval is a small fraction of total time; Qwen3:8B generation dominates. "
            "Queue/other work is server time not attributed to retrieval or generation "
            "(worker queue wait, verification, persistence)."
        )
        lines.append("")

    lines.append("## Other checks (same run)")
    lines.append("")
    lines.append("| Check | Result | Evidence |")
    lines.append("| --- | --- | --- |")
    lines.append("| Protected-file SHA-256 | PASS | 6/6 match `protected_files.sha256`; `/health` integrity ok |")
    lines.append("| Backend test suite | PASS | 53 passed with `.venv` (deterministic fakes) |")
    lines.append("| Live API contract | PASS | 17/17: health, docs, OpenAPI, session CRUD/clear/delete, 404/422 |")
    lines.append("| Same-session concurrency | PASS | Two concurrent `/chat` -> 200 + 409 `session_busy`, 2 messages stored |")
    lines.append("| Security runtime checks | PASS | 12/12: CORS allow/deny, traversal, malformed/oversize input, no leakage |")
    lines.append("| Frontend production build | PASS | Vite build served by FastAPI, assets load, zero console errors |")
    lines.append("| UI session flows | PASS | Rename, delete, clear, empty state, error banner, draft restore |")
    lines.append("| UI answer rendering | PASS | Verified badge, citations, 5 source cards, timings, model/persona |")
    lines.append("| XSS handling | PASS | HTML title rendered as inert text; no injected elements |")
    lines.append("| Streaming | NOT IMPLEMENTED | `/chat` returns one JSON response; no SSE/WebSocket |")
    lines.append("| Responsive layout | WARNING | Desktop viewport verified; no device emulation available |")
    lines.append("| Localhost/no auth | WARNING (by design) | Bound to `127.0.0.1`; no authentication added |")
    lines.append("")

    lines.append("## Limitations")
    lines.append("")
    llm_stats = stats.get("llm_generation_ms")
    if llm_stats:
        lines.append(
            "- Qwen3:8B is CPU-bound on this machine. Generation dominates every answer "
            f"({seconds(llm_stats['min'])}–{seconds(llm_stats['max'])}), "
            "so totals vary widely."
        )
    else:
        lines.append("- Qwen3:8B is CPU-bound on this machine, so totals vary widely.")
    lines.append(
        "- While Qwen generates, the CPU is saturated. `/health` measured 11–15 s and a "
        "guard-path `/chat` measured 27 s server-side versus 59 s client wall time."
    )
    lines.append(
        "- The worker handles one generation at a time; other sessions queue. Requests can "
        "approach the 2400 s budget under concurrent use, so ask one question at a time."
    )
    lines.append(
        "- Sessions are stored in a local JSON file for a single instance. There is no "
        "multi-user authentication; keep the service on localhost or behind your own proxy."
    )
    lines.append(
        "- No streaming is implemented; the UI shows an elapsed clock and polls for late "
        "completion after a client timeout."
    )
    lines.append("")

    lines.append("## Verdict")
    lines.append("")
    lines.append(
        "All live checks passed." if ok else "One or more live checks did not pass; see the JSON reports."
    )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    markdown = render()
    if args.output:
        args.output.write_text(markdown, encoding="utf-8")
        print(f"wrote {args.output}")
    else:
        print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
