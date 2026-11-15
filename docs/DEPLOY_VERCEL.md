# Deploying the KBN Helpdesk on Vercel

One Vercel project serves everything: the React site from Vercel's CDN and the API as a
single Python (FastAPI) function. Chats are stored in Neon Postgres, and the AI is Groq.
All three have free plans. Free-plan terms change, so re-check them before launch.

## How it fits together

```text
Browser ──► Vercel CDN: React app (frontend/dist, built at deploy time)
        └─► Python function (asgi.py → FastAPI): /chat, /sessions, /health
              ├─ Groq (the AI)
              ├─ Neon Postgres: chats + shared rate-limit/budget counters
              └─ /tmp copy of the search index and embedding model (made on cold start)
Vercel Cron ──► /internal/cron/purge (daily): deletes old chats and expired counters
```

Why Postgres also holds the counters: Vercel runs several copies of the function at once,
so limits kept in each copy's memory would not add up. They live in the database instead.

## 1. Get the three secrets

| What | Where | Used as |
| --- | --- | --- |
| Groq API key (create a **new** one) | <https://console.groq.com> | `KBN_GROQ_API_KEY` |
| Neon connection string | <https://neon.tech> (or Vercel Marketplace → Neon). Use the **pooled** string (host contains `-pooler`) and keep `?sslmode=require` | `KBN_DATABASE_URL` |
| A random string you invent (32+ characters) | for example `openssl rand -hex 24` | `CRON_SECRET` (exactly this name: it is the only one Vercel Cron sends) |

## 2. Import the project

1. In Vercel choose **Add New → Project** and import the GitHub repo (use `main` after the
   merge). Leave **Root Directory** as the repo root. Vercel detects FastAPI from
   `pyproject.toml`; `vercel.json` already sets the build command.
2. Under **Environment Variables** add the three secrets above plus:

| Variable | Value |
| --- | --- |
| `KBN_LLM_PROVIDER` | `groq` |
| `KBN_ENVIRONMENT` | `production` |
| `KBN_GROQ_MODEL` | optional, default `qwen/qwen3.8-27b` (which models you can use depends on your Groq account) |

   Nothing else is required. `KBN_TRUSTED_PROXY_HOPS` defaults to `1`, which matches
   Vercel (it sets `X-Forwarded-For` to the visitor's address).
3. Click **Deploy**. The build runs `backend/scripts/vercel_build.py`: it builds the React
   app and downloads the embedding model (about 65 MB, stored as plain files in `.embedding_model/`) into the deployment.

## If the build fails

- **`LAMBDA_SIZE_EXCEEDED` (bundle over 500 MB).** The first real build hit this at 588.72 MB.
  Causes and fixes already in the repo: the model's Hugging Face cache is symlinks that Vercel
  counts once per link (now flattened to plain files), `node_modules` is deleted after the build,
  and `pyproject.toml` excludes the unused Kubernetes client that ChromaDB pulls in (74 MB).
  Expected size is now roughly 370-450 MB. If a future dependency pushes it over again, set the
  project environment variable `VERCEL_SUPPORT_LARGE_FUNCTIONS=1` (beta, allows up to 5 GB) or
  add `VERCEL_ANALYZE_BUILD_OUTPUT=1` to see which files are largest.
- **"npm is not available".** Not seen on Vercel (its build image has Node). If it ever happens,
  build locally (`cd frontend && npm ci && npm run build`), commit `frontend/dist`
  (remove it from `.gitignore`), and redeploy.

## 3. Check it

Open the deployment URL: the chat page should load and `/health` should show
`"status": "ok"`. Then run the checks in `backend/scripts/verify_live.py` and
`backend/scripts/load_smoke.py` against the URL (their docstrings explain the
`KBN_RATE_LIMIT_*` settings they need on the server being tested; for a real deployment
set them temporarily in Vercel and redeploy, then remove them).

## Things to know

- **Cold starts.** After a few idle minutes the first request waits for a new function
  instance: it copies the search index to `/tmp`, loads the embedding model and connects
  to the database. Locally this takes about 3-4 seconds; measure it on Vercel.
- **Free quotas.** Groq's free tier is about 30 requests/minute, 1K/day and 8K tokens/minute.
  The app stays under the daily number with `KBN_DAILY_LLM_BUDGET` (default 900) and each
  address can use at most `KBN_PER_IP_DAILY_LLM_CALLS` (default 100) answers a day. When a limit is hit, visitors
  get a polite message. Questions answered directly from the records (location, Vice Chancellor,
  contacts, courses) do not use the AI or the budget.
- **Vercel Hobby is for non-commercial use.** A university service may need a Pro plan; check
  Vercel's current terms.
- **Function time.** `vercel.json` gives the function 60 seconds; the app aborts a slow AI
  call after 50 seconds by default (`KBN_REQUEST_TIMEOUT_SECONDS`).
- **Cron.** The daily cleanup endpoint only works if `CRON_SECRET` is set. Without it, old chats
  and expired limit counters are still purged each time a new function instance starts.
- **Privacy.** Questions are sent to Groq. The UI tells visitors not to share personal details.
  Chats are deleted after 30 days (`KBN_SESSION_RETENTION_DAYS`) and each visitor sees only
  their own.

## Operations

| Task | How |
| --- | --- |
| Rotate the Groq key | Create a new key, update `KBN_GROQ_API_KEY` in Vercel, redeploy, delete the old key |
| Switch the AI off in an emergency | Set `KBN_DAILY_LLM_BUDGET=0` and redeploy. Direct answers and the fixed "not in the records" reply still work |
| Update the knowledge | Edit `knowledge_docs/`, run `python backend/scripts/reasgi.py`, commit `local_vector_kb/`, redeploy |
| See errors | Vercel dashboard → Logs |

## What was and was not tested

Observed on a real Vercel build: Python 3.12 and `uv` install from `pyproject.toml`, Node and `npm`
are available, the React app builds, the embedding model downloads. The only failure was the
bundle size above.

Tested locally: the full test suite (including a second "server copy" sharing limits through
Postgres and a run with the Kubernetes client blocked), Vercel's own local runtime
(`vercel dev --local`, both as one project and as separate web/api services), and the app started
from a read-only copy of the project with `VERCEL=1`.

**Not yet tested:** a successful Vercel deployment, real cold-start time, Neon's pooler, and the
real Groq model on Vercel.
