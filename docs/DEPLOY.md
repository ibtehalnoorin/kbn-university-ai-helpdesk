# Deploying the KBN Helpdesk (free tier)

One web service (API + built React app) on Render, Groq for the AI, Neon for the
database. Everything below has a free plan. Free-plan terms change, so re-check
them before launch.

## What you need (about 15 minutes)

1. **Groq API key**: sign up at <https://console.groq.com>, create an API key.
   Free-tier limits (checked Nov 2026): roughly 30 requests/min, 1K requests/day and
   8K tokens/min on the models listed in
   <https://console.groq.com/docs/rate-limits>. The app stays under the daily limit
   with `KBN_DAILY_LLM_BUDGET` (default 900) and shows a polite message when it is hit.
2. **Neon database**: create a free project at <https://neon.tech>, copy the
   connection string (`postgresql://...?sslmode=require`). No setup needed; the app
   creates its own table.
3. **Render account** connected to the GitHub repo.

## Deploy

1. In Render choose **New → Blueprint**, pick this repo (it reads `render.yaml`).
2. When prompted, set the two secrets:
   - `KBN_GROQ_API_KEY` = your Groq key
   - `KBN_DATABASE_URL` = your Neon connection string
3. Deploy. First build takes several minutes (it downloads the embedding model into
   the image). Open the Render URL; `/health` should show `"status": "ok"`.

## Things to know about the free tier

- **Cold starts.** Render's free web service sleeps after ~15 minutes without traffic
  and takes roughly a minute to wake. Optional: ping `/health` every 10 minutes with a
  free uptime monitor (for example UptimeRobot) to keep it awake.
- **Memory.** Measured locally with production dependencies only: ~260-290 MB RSS,
  which fits Render's 512 MB free plan with headroom. Re-measure on Render
  (Metrics tab) after the first deploy.
- **Rate limits are per process** (in memory). That is correct for one instance. If
  you ever run several instances, move limits to Redis.
- **Proxies.** Rate limiting uses the client address added by the first proxy
  (`KBN_TRUSTED_PROXY_HOPS=1`, correct for Render alone). If you put Cloudflare in
  front, set it to `2`.
- **Privacy.** Visitors' questions are sent to Groq to generate answers. The UI tells
  visitors not to share personal details. Chats are deleted after
  `KBN_SESSION_RETENTION_DAYS` (default 30) days and each visitor sees only their own
  chats (anonymous per-browser id).

## Operations

| Task | How |
| --- | --- |
| Rotate the Groq key | Create a new key in Groq, update `KBN_GROQ_API_KEY` in Render, delete the old key |
| Emergency: stop using the AI | Set `KBN_DAILY_LLM_BUDGET=0`. Fast-path answers and the fixed "not in the records" reply still work |
| Switch model | Set `KBN_GROQ_MODEL` (default `llama-3.1-8b-instant`; larger models answer better but use the free quota faster) |
| Update knowledge | Edit `knowledge_docs/`, run `python backend/scripts/reindex.py`, commit `local_vector_kb/` and redeploy |
| Check health | `GET /health` (config-only for Groq, so it never spends quota) |

## Run the same image locally

```bash
docker build -t kbn-helpdesk .
docker run -p 8000:8000 -e KBN_LLM_PROVIDER=groq -e KBN_GROQ_API_KEY=... kbn-helpdesk
```

Without `KBN_DATABASE_URL` chats are kept in a JSON file inside the container
(lost on restart). The Docker image was written but could not be built on the
machine where this change was developed (no Docker installed); the CI workflow and
a clean production-dependency run were used to verify it instead, so run the build
once before relying on it.
