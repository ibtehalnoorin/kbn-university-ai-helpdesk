"""Benchmark installed Ollama models on the real KBN retrieval contexts.

This script talks to Ollama directly but reuses the application's real
RetrievalService, intent classifier and grounding verifier. It never writes to
ChromaDB and never touches a protected module.

Examples
--------
python backend/scripts/benchmark_models.py --configs fast15 --questions all
python backend/scripts/benchmark_models.py --configs fast15 fast8 --questions identity,courses
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

PROJECT_ROOT = Path(__file__).resolve().parents[2]
for entry in (str(PROJECT_ROOT), str(PROJECT_ROOT / "backend")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import ollama  # noqa: E402

from app.config import Settings  # noqa: E402
from app.services.grounding import (  # noqa: E402
    FALLBACK_MESSAGE,
    grounding_policy,
    is_refusal,
    verify_answer,
)
from app.services.retrieval import RetrievalService  # noqa: E402
from backend.scripts.verify_live import (  # noqa: E402
    FABRICATION_MARKERS,
    VALID_CASES,
    match_groups,
    normalise,
)

OUTPUT = PROJECT_ROOT / "backend" / "reports" / "benchmark_models.json"

CONFIGS: dict[str, dict[str, Any]] = {
    # The protected processor.py hardcodes qwen3:8b with thinking enabled.
    "baseline8": {
        "model": "qwen3:8b",
        "think": None,
        "options": {"temperature": 0.3, "num_ctx": 8192},
    },
    # Same 8B model but with Qwen3 thinking disabled and a concise output cap.
    "fast8": {
        "model": "qwen3:8b",
        "think": False,
        "options": {
            "temperature": 0.2,
            "num_ctx": 4096,
            "num_predict": 320,
            "num_thread": 8,
        },
    },
    # Smallest installed chat model.
    "fast15": {
        "model": "qwen2.5:1.5b",
        "think": False,
        "options": {
            "temperature": 0.2,
            "num_ctx": 4096,
            "num_predict": 320,
            "num_thread": 8,
        },
    },
}


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_report() -> dict[str, Any]:
    if OUTPUT.exists():
        try:
            return json.loads(OUTPUT.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    return {"created_at": iso_now(), "runs": {}}


def save_report(report: dict[str, Any]) -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    report["updated_at"] = iso_now()
    tmp = OUTPUT.with_suffix(OUTPUT.suffix + ".tmp")
    tmp.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(OUTPUT)


def select_cases(selector: str) -> list[dict[str, Any]]:
    if selector == "all":
        return VALID_CASES
    wanted = {item.strip() for item in selector.split(",") if item.strip()}
    return [case for case in VALID_CASES if case["id"] in wanted]


def evaluate(case: dict[str, Any], answer: str, context: str, allowed_urls: set[str]) -> dict[str, Any]:
    text = normalise(answer)
    problems: list[str] = []
    if not text:
        problems.append("empty answer")
    if text == normalise(FALLBACK_MESSAGE):
        problems.append("returned mandatory fallback")
    if is_refusal(text):
        problems.append("model refused even though the guard allowed retrieval")
    ok_facts, missing = match_groups(text, case["must_match_all"])
    if not ok_facts:
        problems.append("missing facts: " + "; ".join(missing))
    for marker in FABRICATION_MARKERS:
        if marker in text.lower():
            problems.append(f"fabricated marker: {marker!r}")
    if text and not is_refusal(text):
        supported, reason = verify_answer(text, context, allowed_urls)
        if not supported:
            problems.append(f"verification rejected: {reason}")
    return {"passed": not problems, "problems": problems}


def run_config(
    name: str,
    config: dict[str, Any],
    contexts: dict[str, dict[str, Any]],
    allowed_urls: set[str],
) -> None:
    report = load_report()
    report.setdefault("runs", {})
    run = report["runs"].setdefault(name, {"config": config, "cases": {}})
    run["config"] = config

    for case in selected_cases:
        case_id = case["id"]
        if case_id in run["cases"] and run["cases"][case_id].get("passed"):
            print(f"[skip] {name}/{case_id} already passed", flush=True)
            continue

        item = contexts[case_id]
        system_prompt = f"{item['base_prompt']}\n\n{grounding_policy()}"
        user_prompt = (
            f"User Query: {case['question']}\n\n"
            f"Retrieved Context & Knowledge:\n{item['context']}"
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        print(f"\n[{name}] {case_id}: {case['question']}", flush=True)
        started = time.perf_counter()
        try:
            kwargs: dict[str, Any] = {
                "model": config["model"],
                "messages": messages,
                "keep_alive": "30m",
                "options": config["options"],
            }
            if config.get("think") is not None:
                kwargs["think"] = config["think"]
            response = ollama.chat(**kwargs)
        except Exception as exc:  # noqa: BLE001 - benchmark must record failures
            elapsed = time.perf_counter() - started
            entry = {
                "question": case["question"],
                "status": "error",
                "error": f"{type(exc).__name__}: {exc}",
                "wall_seconds": round(elapsed, 2),
                "passed": False,
                "problems": ["ollama error"],
                "finished_at": iso_now(),
            }
            run["cases"][case_id] = entry
            save_report(report)
            print(f"  ERROR after {elapsed:.1f}s: {entry['error']}", flush=True)
            continue

        elapsed = time.perf_counter() - started
        answer = str(response.get("message", {}).get("content") or "").strip()
        prompt_tokens = response.get("prompt_eval_count")
        eval_tokens = response.get("eval_count")
        prompt_ns = response.get("prompt_eval_duration")
        eval_ns = response.get("eval_duration")
        total_ns = response.get("total_duration")
        load_ns = response.get("load_duration")
        evaluation = evaluate(case, answer, item["context"], allowed_urls)
        entry = {
            "question": case["question"],
            "status": "ok",
            "answer": answer,
            "wall_seconds": round(elapsed, 2),
            "prompt_eval_count": prompt_tokens,
            "eval_count": eval_tokens,
            "prompt_eval_seconds": round(prompt_ns / 1e9, 2) if prompt_ns else None,
            "eval_seconds": round(eval_ns / 1e9, 2) if eval_ns else None,
            "total_seconds": round(total_ns / 1e9, 2) if total_ns else None,
            "load_seconds": round(load_ns / 1e9, 2) if load_ns else None,
            "prompt_tokens_per_second": (
                round(prompt_tokens / (prompt_ns / 1e9), 2)
                if prompt_tokens and prompt_ns
                else None
            ),
            "generated_tokens_per_second": (
                round(eval_tokens / (eval_ns / 1e9), 2)
                if eval_tokens and eval_ns
                else None
            ),
            "passed": evaluation["passed"],
            "problems": evaluation["problems"],
            "finished_at": iso_now(),
        }
        run["cases"][case_id] = entry
        save_report(report)

        marker = "PASS" if entry["passed"] else "FAIL"
        print(
            f"  {marker} wall={entry['wall_seconds']}s "
            f"prompt={prompt_tokens}tok/{entry['prompt_eval_seconds']}s "
            f"gen={eval_tokens}tok/{entry['eval_seconds']}s "
            f"gen_speed={entry['generated_tokens_per_second']}tok/s",
            flush=True,
        )
        print("  answer: " + normalise(answer)[:400], flush=True)
        for problem in entry["problems"]:
            print(f"  - {problem}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--configs", nargs="+", choices=sorted(CONFIGS), default=["fast15"])
    parser.add_argument(
        "--questions",
        default="all",
        help="'all' or a comma-separated list of valid case ids.",
    )
    args = parser.parse_args()

    global selected_cases
    selected_cases = select_cases(args.questions)
    if not selected_cases:
        print("No matching questions.")
        return 2

    settings = Settings()
    retrieval = RetrievalService(settings)
    retrieval.initialize()

    contexts: dict[str, dict[str, Any]] = {}
    for case in selected_cases:
        result = retrieval.retrieve(case["question"])
        _persona, base_prompt = _intent(case["question"])
        contexts[case["id"]] = {
            "context": result.context,
            "base_prompt": base_prompt,
        }
        print(
            f"[retrieval] {case['id']}: {result.result_count} chunks "
            f"{len(result.context)} chars in {result.duration_ms}ms",
            flush=True,
        )

    print(f"\n=== retrieving done; running {len(selected_cases)} question(s) ===", flush=True)
    for name in args.configs:
        run_config(name, CONFIGS[name], contexts, retrieval.allowed_urls)

    report = load_report()
    print("\n=== summary ===")
    for name, run in report.get("runs", {}).items():
        cases = [c for c in run.get("cases", {}).values() if c.get("status") == "ok"]
        if not cases:
            continue
        passed = sum(1 for c in cases if c.get("passed"))
        total = sum(c["wall_seconds"] for c in cases)
        print(
            f"{name:10s} {passed}/{len(cases)} passed  "
            f"total_wall={total:.1f}s mean={total / len(cases):.1f}s"
        )
    print(f"report -> {OUTPUT}")
    return 0


def _intent(question: str) -> tuple[str, str]:
    from helpers import classify_intent

    return classify_intent(question)


if __name__ == "__main__":
    raise SystemExit(main())
