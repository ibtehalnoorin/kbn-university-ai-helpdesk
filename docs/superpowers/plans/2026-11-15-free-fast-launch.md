# Free & Fast Launch (Phase 0) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the KBN helpdesk answer in seconds for many concurrent users on free hosting, by replacing the CPU Ollama worker with Groq and removing every dependency on a local machine.

**Architecture:** `WorkerClient` (in `backend/app/services/worker_client.py`) is the single seam between `ChatService` and the LLM: `submit(...) -> Future`, `await_result(future, timeout)`, `start/stop/status/alive/pending_count`. We add `GroqWorkerClient` with the same public surface and select it by config, so grounding, verification, fast path and caching stay untouched. Embeddings move from Ollama to an in-process ONNX model, sessions move from a JSON file to Postgres, and the app ships as one Docker image serving API plus built frontend.

**Tech Stack:** Python 3.11, FastAPI, httpx, Groq OpenAI-compatible API, fastembed (ONNX), ChromaDB (existing store, re-indexed), psycopg 3 + Neon Postgres, Docker, Render, GitHub Actions.

**Spec:** `KBN_REDESIGN_PROPOSAL.md` (sections 3, 4, 6 Phase 0) as narrowed by the approved free-stack message: free tiers only, Groq for LLM, no streaming in this phase, staff admin console and channels deferred.

## Global Constraints

- Cost: every service used must have a free plan. No paid dependency.
- Ibtehal approved editing the previously "read-only" modules (`kb_manager.py`, `processor.py`, etc.). Whenever one is edited, regenerate `protected_files.sha256` in the same commit.
- Existing behaviour must not regress: the 59 backend tests keep passing (`pytest backend/tests -q` from repo root with `PYTHONPATH=.:backend`); the exact fixed fallback sentence and verification statuses are unchanged.
- No secret is ever committed. Keys come only from `KBN_`-prefixed env vars; `.env.example` holds placeholders.
- Default provider stays `ollama` so local development and existing tests are unaffected; production sets `KBN_LLM_PROVIDER=groq`.
- Free-tier limits change: before Task 1, check Groq's current model list and rate limits in its docs and record the chosen default model and limits in `backend/.env.example` comments.
- Single web service (API serves `frontend/dist`), so no CORS changes are needed in production.
- Branch per task group, small PRs to Ibtehal's repo; commit messages end with the required co-author line.

## Review Focus

- Groq returns HTTP 429 (free-tier quota hit): user sees a polite "busy, try again shortly" with HTTP 503, never a stack trace, and the session stays usable.
- Groq returns empty or null `content`, or a `finish_reason` of `length`: treated as an error or truncated answer, never stored as a blank assistant message.
- Many parallel chats in different sessions: results must reach the right session (no cross-talk), and one slow request must not block others.
- Behind Render's proxy every request has the same socket IP: rate limiting must key on `X-Forwarded-For` (first hop), or it throttles everyone together.
- Postgres unreachable at startup or mid-request: `/health` reports degraded and chat returns a clear 503 instead of crashing the process.
- Browser holds an active session id that no longer exists server-side (after retention purge or redeploy): API 404 must lead the UI to start a fresh session, not a dead screen.

---

## File Structure

- Create `backend/app/services/groq_client.py`: `GroqWorkerClient`, history handling, Groq HTTP call.
- Create `backend/app/services/embeddings.py`: `embed(text: str) -> list[float]`, provider switch.
- Create `backend/app/services/pg_session_store.py`: `PostgresSessionStore`.
- Create `backend/app/services/rate_limit.py`: sliding-window limiter + daily LLM budget.
- Create `backend/scripts/reindex.py`: rebuild the Chroma collection with the new embedder.
- Modify `backend/app/config.py`, `backend/app/main.py`, `backend/app/services/health_service.py`, `backend/app/services/retrieval.py` (only if it calls Ollama), `kb_manager.py`, `ingest.py`, `backend/requirements.txt`, `backend/.env.example`, `protected_files.sha256`, `README.md`.
- Create `Dockerfile`, `.dockerignore`, `render.yaml`, `.github/workflows/ci.yml`, `docs/DEPLOY.md`.
- Modify `frontend/src/components/EmptyState.tsx` (privacy note) and `frontend/src/App.tsx` (stale-session recovery).
- Tests under `backend/tests/`: `test_groq_client.py`, `test_embeddings.py`, `test_pg_session_store.py`, `test_rate_limit.py`, additions to `test_api.py`.

---

### Task 1: Groq worker client

**Files:**
- Create: `backend/app/services/groq_client.py`
- Modify: `backend/app/config.py` (add settings)
- Test: `backend/tests/test_groq_client.py`

**Interfaces:**
- Consumes: `WorkerClient`'s public surface (see Architecture), `app.errors.{WorkerQueueFullError, WorkerResultError, WorkerTimeoutError, WorkerUnavailableError}`, `Settings`.
- Produces: `class GroqWorkerClient` with `__init__(settings: Settings, transport: httpx.BaseTransport | None = None)`, and the same methods as `WorkerClient`: `start() -> None`, `stop() -> None`, `submit(*, worker_key, query, kb_knowledge, web_context, persona_name, system_prompt) -> Future`, `async await_result(future, timeout_seconds) -> dict`, `alive: bool`, `pending_count: int`, `status() -> dict`. Result dict keys identical to the Ollama worker: `status, session_id, query, answer, time, persona, tokens_used, max_tokens, utilization`. New settings: `llm_provider: Literal["ollama","groq"]="ollama"`, `groq_api_key: str=""`, `groq_model: str`, `groq_base_url: str="https://api.groq.com/openai/v1"`, `groq_timeout_seconds: float=30.0`, `groq_max_parallel: int=8`.

- [ ] **Step 1: Write failing tests** using `httpx.MockTransport`:
  - `test_submit_returns_answer_with_worker_result_keys`: mock returns one choice `"Hello"`; result `answer == "Hello"`, `session_id == worker_key`, `status == "success"`.
  - `test_history_is_kept_per_worker_key_and_sessions_do_not_mix`: two keys, second call for key A includes A's first exchange in the request body and none of B's.
  - `test_system_prompt_and_context_are_sent`: request body messages start with the system prompt; last user message contains `"User Query: ..."` and the `kb_knowledge` text.
  - `test_http_429_raises_queue_full`: mock 429 → future raises `WorkerQueueFullError`.
  - `test_empty_content_raises_result_error`: `content: null` → `WorkerResultError`.
  - `test_missing_api_key_raises_unavailable` on `submit`.
  - `test_parallel_requests_do_not_block_each_other`: mock delays 0.3 s; four submits finish in under 0.9 s total.
- [ ] **Step 2: Run** `pytest backend/tests/test_groq_client.py -v`; expect FAIL (module missing).
- [ ] **Step 3: Implement `GroqWorkerClient`** with a `ThreadPoolExecutor(max_workers=groq_max_parallel)` and a sync `httpx.Client` (so it fits the existing `concurrent.futures.Future` contract); history trimmed with the same 75%-of-8192-token rule as `processor.truncate_history` (import and reuse it, do not copy); `await_result` copied from `WorkerClient` (reuse by subclassing or importing the shared helper, do not duplicate). Request: `POST {base}/chat/completions` with `model`, `messages`, `temperature=settings.llm_temperature`, `max_tokens=settings.llm_num_predict`. Map 429 to `WorkerQueueFullError("The assistant is busy right now. Please try again in a minute.")`, other non-2xx and empty content to `WorkerResultError`. Pick the default `groq_model` after checking Groq docs (see Global Constraints).
- [ ] **Step 4: Run** the test file; expect PASS.
- [ ] **Step 5: Commit** `feat: add Groq worker client behind the existing WorkerClient interface`.

---

### Task 2: Select provider at startup, health, and tests

**Files:**
- Modify: `backend/app/main.py` (lifespan worker construction), `backend/app/services/health_service.py`, `backend/requirements.txt`
- Test: `backend/tests/test_api.py` (add), `backend/tests/test_runtime_support.py` (add)

**Interfaces:**
- Consumes: `GroqWorkerClient`, `WorkerClient`, `Settings.llm_provider`.
- Produces: `make_worker(settings: Settings) -> WorkerClient | GroqWorkerClient` in `backend/app/services/worker_factory.py`; `/health` `components` contains `"llm": {"provider": str, "status": "ok"|"error", ...}` instead of an `ollama`-only entry when provider is `groq`.

- [ ] **Step 1: Write failing tests:** `test_factory_returns_groq_client_when_configured`; `test_health_skips_ollama_when_provider_is_groq` (no request to the Ollama host is made, status `ok` with fake retrieval and Groq key set); `test_health_degraded_when_groq_key_missing`.
- [ ] **Step 2: Run** them; expect FAIL.
- [ ] **Step 3: Implement** `make_worker`, use it in the lifespan, and make `HealthService.check` run `check_ollama` only for the `ollama` provider (Groq check = key present; no network call, to keep `/health` free of quota use). Remove `ollama` and `crawl4ai`/`ddgs` from the production requirement set by moving them to `backend/requirements-local.txt` (included by `requirements.txt` via `-r`); the Docker image installs only `requirements-prod.txt` (new: fastapi, uvicorn, httpx, pydantic-settings, chromadb, fastembed, psycopg[binary]).
- [ ] **Step 4: Run** `pytest backend/tests -q`; expect all 59 existing plus new tests PASS.
- [ ] **Step 5: Commit** `feat: choose LLM provider by config and keep /health provider-aware`.

---

### Task 3: Embeddings without Ollama, and re-index

**Files:**
- Create: `backend/app/services/embeddings.py`, `backend/scripts/reindex.py`
- Modify: `kb_manager.py` (`_get_embedding` uses `embeddings.embed`; lazy `import ollama`), `ingest.py` if it embeds directly, `protected_files.sha256`, `local_vector_kb/` (rebuilt), `backend/app/config.py` (`embedding_provider`, `embedding_model_name`, `expected_indexed_chunks` stays 38)
- Test: `backend/tests/test_embeddings.py`

**Interfaces:**
- Produces: `embed(text: str) -> list[float]` (provider `onnx` using fastembed model `BAAI/bge-small-en-v1.5`, 384-dim; provider `ollama` keeps the old path for local use); `reindex.py` CLI: `python backend/scripts/reindex.py` rebuilds collection `local_research_kb` from `knowledge_docs/` and prints the chunk count.

- [ ] **Step 1: Write failing tests:** `test_embed_returns_384_floats_for_onnx_provider` (marked `slow`, skipped in CI unless `RUN_SLOW=1`); `test_embed_dispatches_to_ollama_provider` (monkeypatched client); `test_reindex_produces_38_chunks_with_fake_embedder` (monkeypatched `embed` returning a fixed vector).
- [ ] **Step 2: Run**; expect FAIL.
- [ ] **Step 3: Implement** `embeddings.py` with a lazily created, thread-safe singleton model; edit `kb_manager._get_embedding` to call it; build `reindex.py` by reusing `ingest.py`'s chunking so chunk ids and metadata stay identical (inspect `ingest.py` first and call its functions rather than reimplementing). Commit the rebuilt store, regenerate `protected_files.sha256`.
- [ ] **Step 4: Verify real quality:** run the reindex locally, then `pytest backend/tests -q` and ask 5 known questions (VC email, location, establishment date, ranking, courses) against retrieval; the expected source file must be in the top 3 for each. If any fail, switch to `nomic-ai/nomic-embed-text-v1.5` and rerun.
- [ ] **Step 5: Commit** `feat: in-process ONNX embeddings, re-indexed knowledge base`.

---

### Task 4: Postgres sessions and retention

**Files:**
- Create: `backend/app/services/pg_session_store.py`
- Modify: `backend/app/main.py`, `backend/app/config.py` (`database_url: str = ""`, `session_retention_days: int = 30`)
- Test: `backend/tests/test_pg_session_store.py`

**Interfaces:**
- Consumes: `SessionRecord`, `SessionStore`'s method list (`create, list, detail, worker_key_for, recent_user_messages, append_exchange, rename, clear, delete, count`).
- Produces: `class PostgresSessionStore` with the same methods and signatures, plus `purge_older_than(days: int) -> int`. Table `sessions(id text primary key, data jsonb not null, updated_at timestamptz not null)` created idempotently on init. Selected when `database_url` is set; otherwise the JSON store is used.

- [ ] **Step 1: Write failing tests** against a real Postgres when `TEST_DATABASE_URL` is set (skipped otherwise), parametrized so the same behavioural tests also run against the JSON `SessionStore` and both must pass: create/list/detail round-trip; `append_exchange` ordering; `clear` keeps the session and rotates `worker_key`; `delete` then `detail` raises `NotFoundError`; `purge_older_than(30)` removes only sessions untouched for 30+ days and returns the count; `max_sessions` limit raises `SessionLimitError`.
- [ ] **Step 2: Run**; expect FAIL.
- [ ] **Step 3: Implement** with psycopg 3 and a small connection pool; store the whole `SessionRecord` as JSON; serialize writes per session id with an advisory lock or `SELECT ... FOR UPDATE`. On startup purge once; schedule a daily purge task in the lifespan. Wrap connection errors as `WorkerUnavailableError`-style 503 (`StoreUnavailableError`, add to `errors.py`, status 503, code `store_unavailable`) and have `/health` report it.
- [ ] **Step 4: Run** all session tests on both stores; expect PASS.
- [ ] **Step 5: Commit** `feat: Postgres session store with retention purge`.

---

### Task 5: Abuse protection, quota safety, privacy note

**Files:**
- Create: `backend/app/services/rate_limit.py`
- Modify: `backend/app/main.py` (middleware on `POST /chat` and `POST /sessions`), `backend/app/config.py`, `frontend/src/components/EmptyState.tsx`, `frontend/src/App.tsx`
- Test: `backend/tests/test_rate_limit.py`, `backend/tests/test_api.py` (add)

**Interfaces:**
- Produces: `class SlidingWindowLimiter(limit: int, window_seconds: float)` with `allow(key: str, now: float | None = None) -> bool`; `class DailyBudget(max_calls: int)` with `try_consume(now: float | None = None) -> bool` (resets at UTC midnight); `client_ip(request) -> str` (first `X-Forwarded-For` entry, else socket host). Settings: `rate_limit_per_minute: int = 12`, `rate_limit_sessions_per_hour: int = 20`, `daily_llm_budget: int = 5000`, `max_message_chars: int = 1000`. Over limit → HTTP 429 `{"error":{"code":"rate_limited",...}}` with `Retry-After`; budget exhausted → 503 code `daily_limit_reached` with a friendly message (fast-path and fixed fallback answers still work because they never consume budget).

- [ ] **Step 1: Write failing tests:** `test_limiter_blocks_after_limit_and_recovers_after_window`; `test_budget_resets_next_utc_day`; `test_client_ip_uses_first_forwarded_hop`; `test_chat_returns_429_with_retry_after`; `test_fast_path_answer_does_not_consume_budget`; `test_message_over_max_chars_returns_422`.
- [ ] **Step 2: Run**; expect FAIL.
- [ ] **Step 3: Implement** limiter and budget as in-memory (single instance on free hosting; note the limitation in `docs/DEPLOY.md`); consume the budget only where `ChatService` actually submits to the worker (inject `DailyBudget` into `ChatService` and check just before `worker.submit`).
- [ ] **Step 4: Frontend:** add a one-line notice in `EmptyState.tsx` ("Don't share personal details. Answers come from KBN University's public information."); in `App.tsx`, when loading the stored active session returns 404, clear it via `writeActiveSession(null)` and create a new one. Verify with `npm run build` and a manual check of both states.
- [ ] **Step 5: Run** `pytest backend/tests -q`; expect PASS.
- [ ] **Step 6: Commit** `feat: rate limiting, daily LLM budget, privacy notice, stale-session recovery`.

---

### Task 6: Packaging, CI, deploy guide

**Files:**
- Create: `Dockerfile`, `.dockerignore`, `render.yaml`, `.github/workflows/ci.yml`, `docs/DEPLOY.md`
- Modify: `README.md` (replace placeholder clone URL, add "Deploy" and "Run without Ollama" sections), `backend/.env.example`

**Interfaces:**
- Produces: image that runs `python backend/run_server.py` with `KBN_HOST=0.0.0.0`, `KBN_PORT` from `$PORT`, healthcheck on `/health`; CI jobs `backend-tests` (pytest) and `frontend-build` (`npm ci && npm run build`) on every PR.

- [ ] **Step 1:** Multi-stage Dockerfile: Node stage builds `frontend/dist`; Python 3.11-slim stage installs `requirements-prod.txt`, pre-downloads the ONNX embedding model at build time (so cold starts do not download), copies app, store and `frontend/dist`. Non-root user.
- [ ] **Step 2:** `render.yaml` defines one free web service from the Dockerfile with env vars listed (`KBN_LLM_PROVIDER=groq`, `KBN_GROQ_API_KEY` and `KBN_DATABASE_URL` as `sync: false` secrets, `KBN_ENVIRONMENT=production`, `KBN_START_WORKER=true`).
- [ ] **Step 3:** CI workflow as above, with a `concurrency` group and pip/npm caching.
- [ ] **Step 4:** `docs/DEPLOY.md`: Groq key, Neon database (enable nothing special, plain Postgres), Render blueprint deploy, setting the three secrets, free-tier cold-start note and an optional uptime pinger on `/health`, how to rotate the key, how to disable the LLM in an emergency (`KBN_DAILY_LLM_BUDGET=0` serves only fast-path and fixed fallback).
- [ ] **Step 5: Verify:** `docker build .` succeeds; `docker run` with a fake key and no database serves `/health` (degraded because the key is invalid is acceptable, process must stay up) and the UI at `/`; image size and idle memory recorded in `docs/DEPLOY.md` (target: under 512 MB idle for Render's free plan; if exceeded, note it and suggest the smallest paid tier or a different free host).
- [ ] **Step 6: Commit** `chore: Docker image, Render blueprint, CI, deploy guide`.

---

### Task 7: End-to-end live verification

**Files:**
- Modify: `backend/scripts/verify_live.py` (provider-aware; reads `KBN_BASE_URL`), `VERIFICATION.md` (regenerate via `summarize_reports.py`)

- [ ] **Step 1:** With a real `KBN_GROQ_API_KEY` supplied by the user (never committed or echoed), run the app locally with `KBN_LLM_PROVIDER=groq` and `python backend/scripts/verify_live.py --mode all`; expected: the seven university questions and ten guard cases pass as before, and each grounded answer returns in under 10 s.
- [ ] **Step 2:** Run 20 parallel chats in distinct sessions using a short script in `backend/scripts/load_smoke.py`; expected: no errors, no cross-session answers, p95 under 15 s, or clean 429/503 messages if free limits are hit (record which).
- [ ] **Step 3:** Repeat Steps 1 and 2 against the deployed URL once Ibtehal has deployed.
- [ ] **Step 4:** Commit regenerated `VERIFICATION.md` with the measured numbers and the open-PR summary for Ibtehal.

---

## Deferred (explicitly not in this plan)

Streaming answers, Turnstile captcha, staff admin console and crawler, PDF ingestion, widget, multilingual, WhatsApp, human handoff, analytics. Each becomes its own plan after launch.
