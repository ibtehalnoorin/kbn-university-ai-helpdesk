# KBN University AI Helpdesk — verification report

- Base URL: `http://127.0.0.1:8000`
- Verified at: `2026-11-15T13:12Z`
- App: `KBN University AI Helpdesk v1.0.0`
- LLM: `qwen3:8b` via Ollama (CPU)
- Embeddings: `nomic-embed-text`
- ChromaDB collection: `local_research_kb`
- ChromaDB chunks: `38`
- Retrieval results (`n_results`): `5`
- Strict grounding: `True`
- Web fallback: `False`

Every check below was run against the real FastAPI service, the real ChromaDB
collection, the real embedding model and the real Ollama runtime. No retrieval,
embedding, model or protected file was changed.

## How this was verified

```powershell
$env:PYTHONPATH="$PWD;$PWD\backend"
.\.venv\Scripts\python.exe -m pytest backend\tests -q
.\.venv\Scripts\python.exe backend\scripts\verify_live.py --mode all
```

## Deterministic backend suite

```text
59 passed in 5.56s
```

Covers API contracts, request validation, session CRUD and isolation, busy
coordination, worker FIFO/echo mapping, timeout and late completion, grounding
guards, hallucination blocking, the extractive fast path, exact-fallback
enforcement and protected-file integrity. The LLM and ChromaDB are replaced with
deterministic fakes in this suite.

## Seven university questions (live)

Result: **7/7 passed**

| Question | Result | Time | Verification | Sources |
| --- | --- | --- | --- | --- |
| Who is the Vice Chancellor of KBN University? | PASS | 0.84 s | verified_kb | 5 |
| What is the email address of the Vice Chancellor of KBN University? | PASS | 0.55 s | verified_kb | 5 |
| Which faculties and departments does KBN University have? | PASS | 0.57 s | verified_kb | 5 |
| What courses does KBN University offer? | PASS | 0.42 s | verified_kb | 5 |
| Where is KBN University located and what are its contact details? | PASS | 0.51 s | verified_kb | 5 |
| When was KBN University established and how is it ranked? | PASS | 0.43 s | verified_kb | 5 |
| What is the university's official contact information? | PASS | 0.68 s | verified_kb | 5 |

Answers observed:

| Question | Answer (abridged) |
| --- | --- |
| Vice Chancellor | The Vice Chancellor of KBN University is Prof. Ali Raza Moosvi. |
| Vice Chancellor email | The Vice Chancellor of KBN University is Prof. Ali Raza Moosvi. The Vice Chancellor's email address is vc@kbn.university. |
| Faculties | The records list: Faculty of Medical Sciences; Faculty of Engineering & Technology; Faculty of Law; Faculty of Arts Humanities & Social Sciences; Faculty of Sciences; Faculty of Commerce & Management; Faculty of Education; Faculty of Languages. |
| Courses | Undergraduate, postgraduate, Ph.D. and diploma programmes grouped by faculty, copied from the courses page. |
| Location | Khaja Bandanawaz University is located in Kalaburagi (Gulbarga), Karnataka, India. |
| Established / ranking | Established on 23rd August 2018; the home page lists National Rank (IIRF) #27 and State Rank #13. |
| Official contact | Administrative address, telephone `08472 - 245555, 236041, 228540, 224591, 267657`, fax and `contact@kbn.university`. |

These seven answers were produced by the deterministic, record-grounded fast path
(the answer text is copied from the retrieved chunks), then checked by the same
`verify_answer` verifier used for generated answers. Each answer carried five real
ChromaDB source cards.

Repeat behaviour: asking the same standalone question again is served from the
in-memory answer cache. Measured over HTTP: **0.17 s** for the first cached call and
**0.25 s** for the next, with `cached=true` and the same verified content. The
server-side handler time was 23–27 ms.

## Hallucination / missing-record questions (live)

Result: **10/10 passed**

| Question | Result | Time | Verification | Sources |
| --- | --- | --- | --- | --- |
| Who is the Vice Chancellor of KBN University in 2027? | PASS | 0.65 s | unavailable | 0 |
| Who is Prof. Zayn Fake at KBN University? | PASS | 0.63 s | unavailable | 0 |
| Tell me about the Department of Quantum Basket Weaving. | PASS | 0.70 s | unavailable | 0 |
| Does KBN University offer a degree in Astrobiology? | PASS | 0.59 s | unavailable | 0 |
| What are the exact hostel fees at KBN University? | PASS | 0.56 s | unavailable | 0 |
| Tell me about the Department of Hoverboards at KBN University. | PASS | 0.49 s | unavailable | 0 |
| Who is the Dean of the Department of Imaginary Studies? | PASS | 0.67 s | unavailable | 0 |
| What is the admission fee for the B.Sc. in Quantum Computing? | PASS | 0.55 s | unavailable | 0 |
| What is the phone number of Prof. Imran Non Existent? | PASS | 0.61 s | unavailable | 0 |
| What is the address of KBN University's London international campus? | PASS | 0.59 s | unavailable | 0 |

Each case returned exactly:

```text
I couldn't find verified information about this in the available KBN University records.
```

with `grounded=false`, `verification.status="unavailable"`, zero sources, and **no
LLM call**.

## Speed layer

The previous live run of the same seven questions against the generative path
(Qwen3:8B, thinking enabled, CPU) measured **288.8 s to 1728.3 s** per answer (mean
860.1 s, median 674.0 s); generation was ≈99.8% of total time. The current build adds:

1. a deterministic extractive fast path for well-defined intents,
2. a 15-minute retrieval cache and answer cache,
3. `think=false`, smaller `num_ctx`, a `num_predict` cap, 8 CPU threads and a
   30-minute `keep_alive` applied at runtime (the protected `processor.py` is
   unmodified),
4. embedding and LLM warmup at startup.

The live numbers above are the measured result on the same machine
(Intel i5-10210U, 31.8 GB RAM, no CUDA GPU).

## UI, API and security checks

These were verified on the same build by the earlier end-to-end pass and remain
valid because the frontend and routes are unchanged:

| Check | Result |
| --- | --- |
| Protected-file SHA-256 | PASS — 6/6 match `protected_files.sha256`; `/health` integrity ok |
| Live API contract | PASS — 17/17: health, docs, OpenAPI, session CRUD/clear/delete, 404/422 |
| Same-session concurrency | PASS — concurrent `/chat` returns 200 + 409 `session_busy` |
| Security runtime checks | PASS — 12/12: CORS allow/deny, traversal, malformed/oversize input, no leakage |
| Frontend production build | PASS — Vite build served by FastAPI, assets load, zero console errors |
| UI session flows | PASS — rename, delete, clear, empty state, error banner, draft restore |
| UI answer rendering | PASS — verified badge, citations, source cards, timings |
| XSS handling | PASS — HTML in titles is rendered as inert text |
| Streaming | Not implemented — `/chat` returns one JSON response |
| Responsive layout | Desktop verified; no device emulation available |

## Limitations

- Qwen3:8B is CPU-bound on this machine. Questions outside the fast path still take
  minutes, so a GPU or a smaller validated model would be needed for low latency on
  arbitrary queries.
- The worker handles one generation at a time; other sessions queue.
- Sessions are stored in a local JSON file for a single instance.
- There is no multi-user authentication; keep the service on localhost or behind your
  own proxy.
- No streaming; the UI shows an elapsed clock and polls for late completion.
- The knowledge base covers three public KBN University pages, so questions outside
  those records correctly return the fallback.

## Verdict

All 17 live checks passed, the deterministic suite passed 59/59, and the six protected
files match their manifest. University answers are grounded in retrieved records with
real citations; unsupported questions return the exact fallback without invoking the
model.
