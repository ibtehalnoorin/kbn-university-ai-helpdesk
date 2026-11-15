# KBN University AI Helpdesk: Production Redesign Proposal

Status: DRAFT for review (not yet reviewed with Ibtehal)
Basis: reading of the repo at commit `53e13bc` (README, file tree, verification report). The backend code was not audited line by line, and nothing was run.

---

## 1. Goal

Turn the KBN helpdesk from a single-laptop proof of concept into a **publicly reachable, reliable, maintainable service** that prospective students, current students, parents and staff can use, and that the university team can keep accurate without a developer.

"Production ready" is defined in section 10 as a checklist, so it can be tested rather than argued about.

## 2. Assumptions (flagged: please correct)

These were not answered, so I assumed them. Each changes the design if wrong.

| # | Assumption | If wrong |
|---|---|---|
| A1 | Mostly public, anonymous visitors (admissions questions); staff/admins log in | Student login (SSO) moves into Phase 1 |
| A2 | Tens to low hundreds of concurrent users at peak (admission season), not thousands | Add autoscaling and a queue earlier |
| A3 | The university accepts **hosted inference** (a cloud LLM API) | Keep Ollama on a GPU server; cost and ops go up, see 4.2 |
| A4 | Ibtehal controls hosting and the domain, and will deploy | We ship Docker images and a runbook only |
| A5 | Languages beyond English are valuable (Hindi, Urdu, Kannada likely) | Drop i18n work from Phase 2 |
| A6 | The "protected, unchanged original modules" rule was a development guardrail, not a permanent requirement | Needs Ibtehal's explicit OK (see 11) |

## 3. Where the current design breaks in production

| Area | Today | Problem live |
|---|---|---|
| Inference | `qwen3:8b` on CPU, one generation at a time, 2400 s request timeout | One question can take minutes; the second user waits behind the first |
| Concurrency | FIFO queue, one generation globally, `409 session_busy` | Does not scale past a handful of users |
| State | `sessions.json` file, in-memory TTL caches | Lost on redeploy, breaks with more than one process |
| Auth / abuse | None; open `/chat` | Anyone can burn compute, scrape, or spam |
| Knowledge | 3 scraped pages, 38 chunks, store committed to git | Stale quickly; no update path; repo bloat; no PDFs (fees, prospectus, calendar) |
| Operations | No metrics, alerts, backups, CI, staging | Failures are invisible |
| Quality | 59 unit tests with faked LLM, 17 live cases run by hand | No regression gate on answer quality |
| Product | Chat only, English, no feedback, no human handoff | A "no answer" is a dead end |
| Privacy | Transcripts stored indefinitely in a local file | Needs retention, deletion and a notice (India DPDP Act 2023) |

**What to keep:** the grounding philosophy (scope check, retrieval assessment, fixed fallback instead of guessing, post-generation verification of names/emails/phones/dates), the extractive fast path, citations from real metadata, and the typed API. This is the most valuable part of the project and it is provider-agnostic.

## 4. Target architecture

```text
            Cloudflare (DNS, TLS, WAF, bot protection, rate limiting)
                              │
        ┌─────────────────────┼──────────────────────┐
        │                     │                      │
  Web app (React)      Embeddable widget       WhatsApp channel (P2)
        └─────────────┬───────┴──────────────────────┘
                      ▼
          API service (FastAPI, stateless, N replicas)
            ├─ Guard: rate limit, captcha, prompt-injection filter
            ├─ Orchestrator (existing grounding pipeline, refactored)
            │     scope → retrieve → assess → fast-path → LLM → verify
            ├─ LLM provider interface ──► Hosted LLM (primary)
            │                         └─► Ollama (dev / offline fallback)
            └─ SSE streaming of answers
                      │
      ┌───────────────┼────────────────┬─────────────────┐
      ▼               ▼                ▼                 ▼
  Postgres        pgvector         Redis             Object storage
 (sessions,    (chunks+embeds,   (cache, rate        (uploaded PDFs,
  feedback,     versioned)        limits, queue)     crawl snapshots)
  audit, users)
                      ▲
          Ingestion worker (crawl, parse, chunk, embed, publish)
                      ▲
          Admin console (React): knowledge, review queue, analytics
```

### 4.1 Core decisions (my recommendations)

1. **Stateless API, state in managed stores.** Postgres for relational data, **pgvector** for embeddings (one fewer system than ChromaDB, transactional with the rest of the data, easy backups). Redis for cache, rate limits and job queue.
2. **Provider-agnostic LLM layer.** A small `LLMProvider` interface with streaming. Adapters: hosted API (primary), Ollama (local dev and offline). The existing grounding and verification code sits above this interface unchanged in behaviour.
3. **Stream answers (SSE).** Perceived latency drops from "minutes" to about one second to first token. Verification runs on the finished answer; if it fails, the UI replaces the streamed text with the fixed fallback. This needs care (see risk R2).
4. **Knowledge versioning.** Each ingest builds a new knowledge *version*; publishing is an atomic switch, rollback is one click. No downtime reindex.
5. **Containers and CI/CD.** Docker images, GitHub Actions (lint, type-check, tests, quality eval, build, deploy to staging, manual promote to prod).

### 4.2 Inference options

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| **Hosted LLM API**, small fast model | Seconds per answer, scales with no GPU, minimal ops | Data leaves the university's machine; per-use cost; vendor dependency | **Recommended** (assumes A3) |
| Self-hosted GPU (vLLM + Qwen) | Data stays in-house, fixed cost | GPU server is expensive and needs ops; still capacity-bound | Only if policy forbids hosted |
| Keep CPU Ollama | Free, private | Cannot serve concurrent users | Dev and emergency fallback only |

Cost is expected to be small because the context is a few retrieved chunks (a few thousand tokens per question), the fast path answers common questions with no LLM call, and caching handles repeats. I have not priced it; this should be estimated with real traffic numbers once A2 is known, and a monthly budget cap with alerting should be built in.

If the hosted route is taken, the university needs a short **data-handling statement**: what is sent (the question plus retrieved public chunks, never student identity), and a "don't enter personal information" notice in the UI. Because the knowledge base is public website content, the exposure is mostly the visitors' questions.

## 5. Redesigned data model (sketch)

- `knowledge_source` (url or upload, type, schedule, owner)
- `knowledge_version` (status: draft/published/archived, created_by, notes)
- `document`, `chunk` (version_id, source_id, text, embedding, metadata, content_hash)
- `session`, `message` (role, text, sources, verification_status, latency, model, token counts, knowledge_version_id)
- `feedback` (message_id, rating, reason, comment)
- `handoff_ticket` (session_id, contact, topic, status, assigned_to)
- `gap` (normalized unanswered question, count, status, resolution link)
- `user`, `role`, `audit_log`

Every answer records which knowledge version and model produced it, so any complaint can be reproduced.

## 6. Feature set

### Phase 0: Production foundation (must have before launch)

- Provider interface, hosted LLM adapter, SSE streaming
- Postgres + pgvector migration (Alembic migrations), Redis cache and rate limit
- Concurrency: remove global single-generation lock; per-session ordering only
- Abuse protection: per-IP and per-session rate limits, Cloudflare Turnstile on session start, max message length, prompt-injection defences (retrieved text treated as data, instruction-stripping, output verification)
- Security headers, strict CORS, secrets from environment, dependency and container scanning
- Observability: structured JSON logs with request ids, Sentry for errors, metrics (latency, fallback rate, verification-failure rate, token spend), uptime check, alerts
- Backups (daily Postgres, tested restore), health and readiness endpoints
- Docker, CI, staging and production environments
- Privacy: notice in UI, retention policy (for example transcripts deleted after 90 days), user-initiated "delete my chat"
- Quality gate: golden question set run in CI against a staging model (replaces hand-run live tests)
- Frontend: error and retry states, mobile polish, accessibility pass (WCAG 2.1 AA), stop-generation button

### Phase 1: Knowledge operations and admin (what makes it stay accurate)

- Crawler for `kbn.university` on a schedule with change detection and diff review
- PDF/DOCX/XLSX upload (prospectus, fee structure, academic calendar, circulars)
- Admin console: sources, versions (publish/rollback), chunk browser, "ask as admin with debug view" showing retrieved chunks and verification result
- Unanswered-question dashboard: grouped "gaps" ranked by frequency, with a button to add an answer or document that closes the gap
- Curated FAQ / pinned answers that override retrieval for high-stakes topics (fees, deadlines, contacts), reviewed by staff
- Roles: admin, editor, viewer; audit log of every knowledge change
- Analytics: volume, top topics, fallback rate, thumbs up/down, satisfaction, peak hours

### Phase 2: Reach and engagement

- Embeddable chat widget (one script tag) for the main university site
- Multilingual: detect language, answer in the user's language, verify against English sources (per A5)
- Human handoff: when the bot falls back, offer "send this to the admissions office" (collects name and contact, emails staff, creates a ticket)
- WhatsApp channel (strong fit for an Indian university audience), sharing the same orchestrator
- Suggested questions and context-aware follow-ups; thumbs feedback on every answer
- Proactive freshness: banner "Information last updated on <date>" from the knowledge version

### Phase 3: Advanced (only if usage justifies it)

- Structured tools for exact data (course/fee lookup tables, deadline calendar) so numbers never come from free text
- Student login (SSO) for personalised answers (timetable, results) via integration with the university ERP, if one exists
- Voice input and text-to-speech
- Admissions lead capture with consent, CRM export
- A/B testing of prompts and models using the eval harness

## 7. Quality and safety strategy

- **Golden set**: 100 to 200 real questions with expected facts and expected "must fall back" cases, built with the admissions office. Run on every PR and nightly. Gate: answer-correctness, zero hallucinated contacts/dates, fallback correctness.
- **Verification stays deterministic** and provider-independent; it is the safety net when the model changes.
- **Red-team suite**: prompt injection in questions and in crawled pages, requests for private data, abusive input.
- **Human review loop**: thumbs-down answers and fallbacks land in the admin queue weekly.
- **Disclaimers** on high-stakes topics (fees, eligibility): link to the official source.

## 8. Deployment and operations

Recommended starting point: containers on a managed platform (Render, Fly.io or Railway) or a single well-configured VM, managed Postgres with point-in-time recovery, managed Redis, Cloudflare in front. Move to AWS or Azure only if the university's IT requires it. This is small enough that a heavyweight setup would add cost without benefit.

- Environments: local (docker compose, Ollama optional), staging (auto-deploy from `main`), production (manual promote)
- Zero-downtime deploys, database migrations run as a release step, rollback runbook
- Runbook: on-call basics, how to rotate keys, how to restore a backup, how to disable the LLM and serve fast-path/FAQ only (degraded mode)
- Budget guard: daily spend cap on the LLM key, automatic degrade to fast-path plus fallback when exceeded

## 9. Delivery plan and how we work with Ibtehal

Suggested sequence of small, reviewable PRs, each independently mergeable:

1. Repo hygiene: CI, lint, Docker, `.gitignore` for the vector store, docs fix (placeholder clone URL)
2. Provider interface and Ollama adapter (no behaviour change), plus the golden-set harness
3. Postgres + pgvector + migration script from the existing Chroma data
4. Hosted LLM adapter, streaming, concurrency fix
5. Guard layer (rate limits, captcha, injection hardening), observability
6. Privacy and retention, accessibility and frontend polish
7. Staging deploy, load test, launch checklist, **go-live (Phase 0 done)**
8. Phase 1 in slices: ingestion worker, admin console, gap dashboard
9. Phase 2 slices by priority

Rough effort, assuming one focused developer working with Claude: Phase 0 about 2 to 3 weeks, Phase 1 about 3 to 4 weeks, Phase 2 about 3 to 4 weeks. These are estimates, not commitments, and depend heavily on decisions in section 11.

## 10. "Production ready" checklist (acceptance criteria)

- [ ] p95 time-to-first-token under 3 s and full answer under 15 s at the target concurrency (load test evidence)
- [ ] 50 concurrent chats sustained without errors (adjust to A2)
- [ ] No shared mutable state in the API process; restart loses nothing
- [ ] Rate limiting and bot protection active; abuse test passed
- [ ] Golden-set quality gate in CI; zero hallucination failures on the red-team suite
- [ ] Verified backup restore performed once
- [ ] Alerts firing to a real channel for: error rate, latency, spend, downtime
- [ ] Privacy notice, retention job and delete-my-chat working
- [ ] Staging mirrors production; rollback tested
- [ ] A non-developer at the university can update the knowledge base (Phase 1 gate for "maintainable")
- [ ] Runbook written and walked through with Ibtehal

## 11. Decisions needed (in priority order)

1. **Hosted LLM allowed?** (A3) Decides the whole inference design and the cost model.
2. **Lift the "original modules are read-only" rule?** (A6) The redesign refactors `kb_manager.py`, `processor.py` and the retrieval path, and replaces the SHA-256 integrity check with CI-based checks. Needs Ibtehal's agreement before the first PR.
3. **Expected traffic and launch date** (A2): sizes infrastructure and sets whether Phase 1 is before or after launch.
4. **Who owns knowledge updates** at the university (this is the person the admin console is built for).
5. **Languages** (A5) and whether WhatsApp is wanted.
6. **Hosting, domain and budget** (A4), including a monthly cap for LLM spend.
7. **Any ERP or student system** to integrate later (Phase 3)?

## 12. Risks

| # | Risk | Mitigation |
|---|---|---|
| R1 | Hosted model changes behaviour and breaks grounding | Pinned model versions, golden-set gate, deterministic verifier |
| R2 | Streaming shows unverified text before verification | Stream only after the guard pre-check passes; show a short "checking sources" state; swap in the fallback if final verification fails; or run fast-path and FAQ answers without streaming |
| R3 | Stale or wrong knowledge (the biggest real-world risk) | Scheduled crawl, freshness banner, gap dashboard, pinned reviewed answers for fees and dates |
| R4 | Cost spike from abuse | Rate limits, captcha, budget cap with automatic degrade |
| R5 | Privacy complaints | No identity collected by default, retention limit, notice, delete option |
| R6 | Scope creep across phases | Ship Phase 0 first; each later feature must show a metric it moves |
| R7 | Single maintainer on the university side | Runbook, admin console, simple managed-service hosting |
