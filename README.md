# KBN University AI Helpdesk

A local-first, source-grounded helpdesk chatbot for **Khaja Bandanawaz University (KBN University)**.
It answers questions about the university from a Retrieval-Augmented Generation (RAG)
pipeline built on **ChromaDB**, **nomic-embed-text** embeddings and a local **Qwen3:8B**
model served by **Ollama**, wrapped in a **FastAPI** backend and a **React + TypeScript**
chat interface.

Every university answer is checked against the retrieved records before it is returned.
If the records do not support an answer, the app returns one fixed fallback sentence
instead of guessing. It can run fully local (Ollama) or, for a public launch, on free
hosted services (Groq for the model, Neon for chat storage); see
[docs/DEPLOY_VERCEL.md](docs/DEPLOY_VERCEL.md) (Vercel) or [docs/DEPLOY.md](docs/DEPLOY.md) (Render).

---

## Overview

The project started as a command-line RAG proof of concept. The web layer keeps that
original pipeline intact and adds:

- a typed HTTP API,
- persistent chat sessions with citations,
- deterministic grounding and hallucination guards,
- an optional extractive fast path for common questions,
- a production-style React UI served by the same FastAPI process.

The six original core modules and the vector store are treated as read-only inputs. The
web application imports and reuses them; it does not fork or rewrite the model,
embeddings, retrieval logic, knowledge documents or ChromaDB collection.

---

## Features

- **Grounded answers with citations** — source cards are built from real ChromaDB
  metadata (file, page URL, chunk index, similarity score).
- **Strict hallucination prevention** — scope detection, retrieval assessment,
  sensitive-intent checks and post-generation verification. Unsupported questions never
  reach the model and return an exact, fixed fallback.
- **Deterministic extractive fast path** — for a small set of well-defined intents
  (Vice Chancellor / Pro Vice Chancellor / Registrar details, location, address,
  official contact, establishment date, rankings, faculty and course catalogues) the
  answer is copied from retrieved text instead of generated. The same verifier checks it;
  anything uncertain falls through to the LLM.
- **Answer and retrieval caches** — identical standalone questions and repeated
  retrievals are served from an in-memory TTL cache.
- **Session management** — create, list, rename, clear and delete conversations;
  transcripts persist to a local JSON file.
- **Session isolation and FIFO queuing** — one generation at a time, one request per
  session, with a clear `409 session_busy` response.
- **Late-answer recovery** — if a long CPU generation outlives the HTTP request, the
  backend keeps waiting and stores the verified answer in the transcript; the UI polls
  for it.
- **Integrity checks** — `/health` verifies SHA-256 hashes of the protected modules on
  every request.
- **Typed API and OpenAPI docs** — Pydantic v2 schemas plus `/docs` and `/openapi.json`.
- **Two run modes** — local (`KBN_LLM_PROVIDER=ollama`, no external services) or hosted
  (`KBN_LLM_PROVIDER=groq`, free tier, answers in seconds, many users at once).
- **Visitor privacy and abuse protection** — each browser sees only its own chats, chats
  are deleted after 30 days, per-visitor rate limits and a daily LLM budget protect the
  free quota.

---

## Technology stack

| Layer | Technology |
| --- | --- |
| LLM runtime | [Ollama](https://ollama.com) with `qwen3:8b` |
| Embeddings | `nomic-embed-text` via Ollama |
| Vector store | ChromaDB (persistent, local) |
| Backend | Python 3.11, FastAPI, Uvicorn, Pydantic v2 |
| Frontend | React 19, TypeScript, Vite, Tailwind CSS |
| Testing | pytest, pytest-asyncio, httpx |
| Optional web fallback | `ddgs` + `crawl4ai` (disabled by default) |

---

## Architecture

```text
React UI (frontend/src)
        │  POST /chat, GET /sessions/...
        ▼
FastAPI backend (backend/app)
        │
        ├─ 1. Scope detection        → is this a KBN University question?
        ├─ 2. Retrieval              → ChromaDB + nomic-embed-text
        ├─ 3. Grounding assessment   → are the required records present?
        │      └─ no  → exact fallback (model is never called)
        ├─ 4. Extractive fast path   → copy supported facts (optional)
        │      └─ uncertain → continue to the LLM
        ├─ 5. LLM generation         → child process running the original
        │                              processor.process_worker (Qwen3:8B)
        └─ 6. Verification           → names, emails, phones, dates and
                                       topic anchors must exist in the
                                       retrieved context
```

The original CLI is still available separately:

```text
main.py → helpers.py → kb_manager.py (ChromaDB) → processor.py (Qwen3:8B)
```

---

## Project structure

```text
.
├─ main.py                     # original CLI (read-only, unchanged)
├─ processor.py                # original Qwen worker (read-only, unchanged)
├─ kb_manager.py               # original ChromaDB wrapper (read-only, unchanged)
├─ ingest.py                   # original ingestion script (read-only, unchanged)
├─ helpers.py                  # original prompts/intents (read-only, unchanged)
├─ researcher.py               # original web fallback (read-only, unchanged)
├─ knowledge_docs/             # source pages used to build the knowledge base
├─ local_vector_kb/            # ChromaDB store (38 chunks, collection local_research_kb)
├─ protected_files.sha256      # SHA-256 manifest for the read-only modules
├─ backend/
│  ├─ run_server.py            # backend entry point
│  ├─ requirements.txt
│  ├─ .env.example
│  ├─ app/
│  │  ├─ main.py               # FastAPI factory, CORS, logging, static frontend
│  │  ├─ config.py             # settings
│  │  ├─ schemas.py            # request/response models
│  │  ├─ errors.py             # error envelope and handlers
│  │  ├─ api/                  # /chat, /health, /sessions routes
│  │  └─ services/             # grounding, retrieval, fast answers, worker, sessions
│  ├─ scripts/
│  │  ├─ verify_live.py        # end-to-end verification against a running server
│  │  ├─ benchmark_models.py   # model speed/accuracy benchmark harness
│  │  └─ summarize_reports.py  # renders verification JSON to Markdown
│  └─ tests/                   # deterministic backend test suite
└─ frontend/
   ├─ src/                     # React 19 + TypeScript chat UI
   └─ index.html, vite.config.ts, tailwind config
```

`backend/data/` (chat transcripts) and `backend/reports/` (local verification output)
are intentionally git-ignored and stay on the machine that created them.

---

## Prerequisites

- Windows 10/11, Linux or macOS
- Python 3.11+
- Node.js 20+ and npm
- [Ollama](https://ollama.com) installed and running locally
- At least ~8 GB of free RAM for `qwen3:8b` (more is better)
- The included `local_vector_kb/` directory (38 chunks) **or** the ability to rebuild it
  from `knowledge_docs/`

> The application was developed and measured on a Windows 11 laptop with an Intel
> i5-10210U (4 cores / 8 threads), 31.8 GB RAM and integrated Intel UHD graphics. There
> is no CUDA GPU in that machine, so generation runs on the CPU.

---

## Installation

### 1. Get the code

```powershell
git clone https://github.com/ibtehalnoorin/kbn-university-ai-helpdesk.git
cd kbn-university-ai-helpdesk
```

### 2. Create the Python environment

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r backend\requirements.txt
```

On Linux/macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r backend/requirements.txt
```

### 3. Choose the model provider

**Hosted (recommended for a public site; no Ollama needed):** get a free key at
<https://console.groq.com> and set

```bash
export KBN_LLM_PROVIDER=groq
export KBN_GROQ_API_KEY=...
```

Embeddings run in-process (ONNX, `BAAI/bge-small-en-v1.5`); the first start downloads
the model (~70 MB). Skip step 3b below.

**Local (fully offline):** use Ollama as described next. Set
`KBN_EMBEDDING_PROVIDER=ollama`, `KBN_EMBEDDING_MODEL=nomic-embed-text`, then run
`python backend/scripts/reindex.py` once so the vector store matches that embedder.

### 3b. Ollama setup (local mode only)

Install Ollama, start it, then pull the two models used by the project:

```powershell
ollama pull qwen3:8b
ollama pull nomic-embed-text
ollama list
```

The embedding model name and the ChromaDB path are fixed by the original modules:

- embedding model: `nomic-embed-text`
- vector store: `./local_vector_kb`
- collection: `local_research_kb`
- expected chunks: `38`

### 4. Knowledge base

The repository ships the existing ChromaDB store generated from the public KBN
University pages in `knowledge_docs/`:

| File | Source |
| --- | --- |
| `kbn_clean_page_1.md` | <https://www.kbn.university/> |
| `kbn_clean_page_2.md` | <https://www.kbn.university/courses> |
| `kbn_clean_page_3.md` | <https://www.kbn.university/contact> |

If you delete or rebuild the store, run the original ingestion script from the project
root (this regenerates embeddings with `nomic-embed-text`):

```powershell
.\.venv\Scripts\python.exe ingest.py
```

Rebuilding is optional; the committed store already matches the committed documents.

### 5. Frontend

```powershell
cd frontend
npm.cmd install
npm.cmd run build      # emits frontend/dist, served by FastAPI
cd ..
```

On Linux/macOS use `npm install` / `npm run build`.

---

## Running locally

### Production-style, single origin (recommended)

```powershell
.\.venv\Scripts\python.exe backend\run_server.py
```

Open <http://127.0.0.1:8000/>.

The same origin serves:

- `/` — built React app
- `/docs` — Swagger UI
- `/openapi.json` — OpenAPI schema
- `/health`, `/chat`, `/sessions` — API

### Frontend development mode

Terminal 1:

```powershell
.\.venv\Scripts\python.exe backend\run_server.py
```

Terminal 2:

```powershell
cd frontend
npm.cmd run dev
```

Open <http://127.0.0.1:5173/>. The Vite dev server calls `http://127.0.0.1:8000`
(CORS is preconfigured). Set `VITE_API_BASE_URL` to point somewhere else.

---

## API documentation

Base URL: `http://127.0.0.1:8000`

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/chat` | Ask a question in a session |
| `GET` | `/health` | Component health, worker status, protected-file integrity |
| `POST` | `/sessions` | Create a conversation |
| `GET` | `/sessions` | List conversations |
| `GET` | `/sessions/{id}` | Read a full transcript |
| `PATCH` | `/sessions/{id}` | Rename a conversation |
| `DELETE` | `/sessions/{id}` | Delete a conversation |
| `POST` | `/sessions/{id}/clear` | Clear messages, keep the session |

### `POST /chat`

```json
{
  "session_id": "8f1c…",
  "message": "Who is the Vice Chancellor of KBN University?"
}
```

Abridged response:

```json
{
  "session_id": "8f1c…",
  "user_message": { "role": "user", "content": "…" },
  "assistant_message": {
    "role": "assistant",
    "content": "The Vice Chancellor of KBN University is Prof. Ali Raza Moosvi.",
    "sources": [ { "file": "kbn_clean_page_3.md", "url": "https://www.kbn.university/contact" } ],
    "grounded": true,
    "verification": {
      "status": "verified_kb",
      "label": "Verified against KBN University records"
    },
    "fast_path": "vc_identity",
    "cached": false
  },
  "sources": [ "…real ChromaDB metadata…" ],
  "grounded": true,
  "verification": { "status": "verified_kb", "label": "…" },
  "persona": "KBN University Helpdesk",
  "response_time_ms": 1042,
  "retrieval": { "attempted": true, "used": true, "result_count": 5, "n_results": 5 },
  "fast_path": "vc_identity",
  "cached": false
}
```

`fast_path` names the deterministic extractor when one was used (otherwise `null`);
`cached` is `true` when the answer came from the in-memory answer cache.

Errors use a consistent envelope:

```json
{ "error": { "code": "session_busy", "message": "…", "details": null } }
```

Common statuses: `404 not_found`, `409 session_busy`, `422 validation_error`,
`502 worker_error`, `503 knowledge_base_unavailable` / `worker_unavailable`,
`504 llm_timeout`.

### curl examples

```powershell
curl.exe http://127.0.0.1:8000/health
curl.exe -X POST http://127.0.0.1:8000/sessions -H "Content-Type: application/json" -d "{}"
curl.exe -X POST http://127.0.0.1:8000/chat -H "Content-Type: application/json" -d '{\"session_id\":\"<id>\",\"message\":\"Who is the Vice Chancellor?\"}'
```

---

## Environment configuration

All backend settings are optional and prefixed `KBN_`. Copy
`backend/.env.example` to `backend/.env` only if you need to override a default.

| Variable | Default | Notes |
| --- | --- | --- |
| `KBN_OLLAMA_HOST` | `http://127.0.0.1:11434` | Also exported as `OLLAMA_HOST` |
| `KBN_HOST` / `KBN_PORT` | `127.0.0.1` / `8000` | Bind address |
| `KBN_LLM_MODEL` | `qwen3:8b` | Runtime override; must be an installed Ollama model |
| `KBN_EMBEDDING_MODEL` | `nomic-embed-text` | Fixed by the protected modules |
| `KBN_CHROMA_DIR` | `./local_vector_kb` | Existing store |
| `KBN_COLLECTION_NAME` | `local_research_kb` | Existing collection |
| `KBN_EXPECTED_INDEXED_CHUNKS` | `38` | Health-check expectation |
| `KBN_KB_N_RESULTS` | `5` | CLI uses 2; 5 preserves named contacts |
| `KBN_REQUEST_TIMEOUT_SECONDS` | `2400` | CPU latency budget |
| `KBN_LATE_COMPLETION_TIMEOUT_SECONDS` | `7200` | Background wait after HTTP timeout |
| `KBN_RETRIEVAL_TIMEOUT_SECONDS` | `120` | Retrieval guard |
| `KBN_LLM_THINK` | `false` | Disables Qwen3 reasoning tokens for lower latency |
| `KBN_LLM_NUM_CTX` / `KBN_LLM_NUM_PREDICT` | `4096` / `320` | Context window and answer cap |
| `KBN_LLM_KEEP_ALIVE` | `30m` | Keeps the model loaded between questions |
| `KBN_FAST_ANSWERS_ENABLED` | `true` | Deterministic extractive path |
| `KBN_ANSWER_CACHE_TTL_SECONDS` | `900` | Repeat-answer cache |
| `KBN_ENABLE_WEB_FALLBACK` | `false` | Off: never fill gaps with random web text |
| `KBN_STRICT_GROUNDING` | `true` | Answer verification and exact fallback |
| `KBN_ALLOW_GENERAL_QUESTIONS` | `true` | Non-KBN questions are labelled unverified |
| `KBN_CORS_ORIGINS` | localhost:5173/4173 | Comma-separated |
| `KBN_SESSION_STORE_PATH` | `backend/data/sessions.json` | Local transcript file |
| `KBN_START_WORKER` | `true` | Tests use `false` |

Frontend: `VITE_API_BASE_URL` is optional and only needed when the API is not on the
same origin. See `frontend/.env.example`.

---

## Grounding and hallucination prevention

Every university-scoped question passes through deterministic guards before generation:

1. **Scope detection** — KBN, university, campus, roles, faculties, courses, fees, …
2. **Retrieval** over the real ChromaDB collection.
3. **Retrieval assessment** — required topic anchors and critical entities must be
   present in the retrieved text.
4. **Sensitive-intent checks** — fees, dates, contact details, policies and rankings
   require a direct record match.
5. **Exact fallback** when records are insufficient — the LLM is **not called**:
   `I couldn't find verified information about this in the available KBN University records.`
6. **Post-generation verification** — names, emails, phone numbers, dates, identifiers
   and topic anchors in the answer must be supported by the retrieved context. A
   rejected answer is replaced with the exact fallback and its sources are removed.

Source cards are generated only from ChromaDB metadata. There are no hardcoded answers,
mock responses, fabricated sources or simulated successes in the request path.

---

## Tests and verification

### Deterministic backend suite

The LLM and ChromaDB are replaced with fakes, so this suite is fast and repeatable.

```powershell
$env:PYTHONPATH="$PWD;$PWD\backend"
.\.venv\Scripts\python.exe -m pytest backend\tests -q
```

It covers API contracts, validation, session CRUD and isolation, busy coordination,
worker FIFO/echo mapping, timeout and late completion, grounding guards, hallucination
blocking, extractive fast answers, exact-fallback enforcement and protected-file
integrity. Latest result on the development machine: **59 passed**.

### Live verification

Start the real server first, then:

```powershell
# Seven university questions, fresh session each
.\.venv\Scripts\python.exe backend\scripts\verify_live.py --mode valid

# Hallucination / missing-record cases (fast, guard short-circuit)
.\.venv\Scripts\python.exe backend\scripts\verify_live.py --mode hallucinations

# Everything; --resume re-runs only failed/pending cases
.\.venv\Scripts\python.exe backend\scripts\verify_live.py --mode all --resume
```

Results are written to git-ignored files under `backend/reports/`. The rendered summary
is committed as [`VERIFICATION.md`](VERIFICATION.md).

Measured on the development machine:

| Suite | Result | Notes |
| --- | --- | --- |
| Deterministic backend suite | **59 passed** | LLM/ChromaDB faked |
| Seven real university questions | **7/7 passed** | Verified answers, real sources |
| Hallucination / missing-record suite | **10/10 passed** | Exact fallback, 0 sources, `unavailable` |

### Protected-file integrity

```powershell
Get-FileHash main.py, processor.py, kb_manager.py, ingest.py, helpers.py, researcher.py -Algorithm SHA256
Get-Content protected_files.sha256
curl.exe -s http://127.0.0.1:8000/health   # protected_files.ok == true
```

The `/health` payload reports expected and actual SHA-256 values for all six files.

---

## Security considerations

- **Local-first by design.** The server binds to `127.0.0.1` and has no authentication.
  Do not expose it directly to the internet. Put it behind your own authentication and
  TLS reverse proxy if you need remote access.
- **No API keys or cloud credentials.** All inference happens through a local Ollama
  instance. `.env` files are git-ignored; only `.env.example` placeholders are committed.
- **Transcripts stay local.** `backend/data/` is git-ignored because chat logs can contain
  personal questions.
- **Untrusted output is rendered as text.** User input is validated (length, type) and
  Markdown is rendered without raw HTML injection.
- **CORS is explicit.** Only configured development origins are allowed.
- **Integrity manifest.** The original modules are hashed and verified at runtime; the web
  layer does not modify them.
- **One user at a time.** The LLM worker is a single process, and each session can have at
  most one in-flight request.

If you fork or deploy this project, review the guards in
`backend/app/services/grounding.py` before adding new data or new intents.

---

## Performance and limitations

- **CPU-bound generation.** The measured baseline with `qwen3:8b` on the development
  laptop was **288.8 s to 1728.3 s** per grounded answer (4.8 to 28.8 minutes), with a
  mean of 860.1 s and a median of 674.0 s. Generation accounted for ≈99.8% of the time.
  A GPU or a smaller model would change this substantially.
- **Fast path.** The deterministic extractor answers the common identity, contact,
  location, establishment, ranking, faculty and course questions directly from retrieved
  records, so those responses do not wait for the LLM.
- **One generation at a time.** Requests from other sessions queue; queue wait counts
  against the request timeout. Ask one question at a time for predictable latency.
- **No streaming.** `/chat` returns a single JSON response. The UI shows an elapsed timer
  and polls for late completion after a client timeout.
- **Single-instance storage.** Sessions live in a local JSON file, with no database or
  multi-user isolation.
- **Small knowledge base.** The vector store covers three public pages (home, courses,
  contact). Questions outside those records correctly return the fallback.
- **No screenshots or hosted demo.** The project runs locally; there is no public
  deployment.

---

## Future improvements

- Optional GPU acceleration and model auto-selection based on available hardware.
- SSE/WebSocket streaming that only emits text after verification.
- Authentication, per-user sessions and a database-backed store.
- Multilingual answers and retrieval.
- Incremental ingestion for additional official KBN University pages.
- Automated CI for the deterministic test suite and integrity check.
- Docker Compose for a reproducible Ollama + backend + frontend stack.

---

## Acknowledgements

Built on top of the original KBN University RAG prototype (`main.py`, `processor.py`,
`kb_manager.py`, `ingest.py`, `helpers.py`, `researcher.py`) and the public information
published at <https://www.kbn.university>. University names, contacts and programme
details belong to KBN University; this project is an independent helpdesk interface.
