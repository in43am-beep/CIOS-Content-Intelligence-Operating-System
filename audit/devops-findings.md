# CIOS — DevOps / Deployment Findings (Phase 2)

**Date:** 2026-09-18. Research only — no code changed, no accounts created.
**App shape** (from `app.py`, `config.py`, `database.py`, `requirements.txt`): single FastAPI
service, serves vanilla-JS frontend from `frontend/` via `StaticFiles` + `FileResponse` at `/`,
6 API routers, `httpx` → OpenRouter free models, SQLite at `cios.db` (configurable via `DB_PATH`),
settings via pydantic-settings (`.env` file; env names `OPENROUTER_API_KEY`, `APP_HOST`, `APP_PORT`,
`DAILY_REQUEST_CAP`…). Default listen is `127.0.0.1:8000`; no Dockerfile, no CI, no `render.yaml`.

---

## 1. Recommended $0 deployment target(s)

### Primary recommendation: **Render — free Web Service**

Best fit because it natively runs long-lived containers, auto-deploys from GitHub, has the
simplest config, and CIOS needs **no build** (pure Python) — just `pip install` + uvicorn.

**Exact steps**
1. Push the repo to GitHub (do not commit `.env` or `cios.db`; `.gitignore` already excludes both).
2. Render Dashboard → **New → Web Service** → connect the GitHub repo.
3. Settings:
   - **Environment:** `Docker` (use the Dockerfile below) **or** `Python 3` native runtime.
   - **Build command:** `pip install -r requirements.txt`
   - **Start command:** `uvicorn app:app --host 0.0.0.0 --port $PORT`
     (Render injects `$PORT`; CIOS's `APP_HOST`/`APP_PORT` defaults are for local only. Passing
     flags on the CLI is the reliable way — do not rely on `python app.py` in production.)
   - **Health check path:** `/api/health` (CIOS has this endpoint already; Render expects 200).
   - **Plan:** Free.
4. **Environment variables** (Render Dashboard → Environment; never in `render.yaml`):
   - `OPENROUTER_API_KEY=<key>`
   - `APP_HOST=0.0.0.0` (defensive; CLI flag takes precedence anyway)
   - `DAILY_REQUEST_CAP=48` (default is already 48; set explicitly to be safe)
5. Auto-deploy from `main` on push.

**Free-tier limits & gotchas (verified Sep 2026)**
- 750 instance-hours/month (~31 days, enough for one always-defined service); **spins down
  after 15 min of inactivity** → 30–60 s cold start on first request.
  ([render.com/pricing](https://render.com/pricing?ref=bestghosthosting.fly.dev),
  [docs.openclaw.ai render guide](https://docs.openclaw.ai/install/render))
- **Ephemeral filesystem on free** — any files written after boot (including `cios.db`) are
  lost on restart/redeploy. Render Disks (the fix) are paid-only.
  ([Render community](https://community.render.com/t/is-a-sqlite-in-the-free-account-deleted-after-some-time/6960))
- **Bandwidth: 5 GB/month included, then $0.15/GB.** A single-user dashboard is fine, but do
  not ship the `brain/` *.md files as static assets or let the API return multi-MB outputs
  uncompressed. ([verified 2026-09-05 in robhunter/agentdeals](https://github.com/robhunter/agentdeals/commit/ee97fc9e30ddd344d392f3c002246f61b173858f))
- **Free Postgres is a 90-day trial (256 MB), then deleted** — not a durable option.
  ([dev.to summary](https://dev.to/0012303/render-has-a-free-cloud-platform-static-sites-apis-databases-and-cron-jobs-7i2),
  [Render community](https://community.render.com/t/i-cant-reset-my-apps-database-after-the-90-day-free-period-has-ended/14707))
- Keeping the service awake with a pinger to dodge the 15-min sleep is considered abuse of the
  free tier by Render support — expect the sleep, design around it.
  ([Render community](https://community.render.com/t/does-my-application-web-service-get-rate-limited/22445))

### Secondary recommendation: **Koyeb — free instance**

Second choice if Render's 15-min sleep proves annoying or Render hits a limit.
- 1 free instance per org: **512 MB RAM, 0.1 vCPU, 2 GB SSD**; scales to zero after **1 h**
  of no traffic (cannot be disabled); regions limited to Frankfurt / Washington D.C.;
  no volumes on free; usually **no credit card** required.
  ([srvrlss.io Koyeb FAQ](https://www.srvrlss.io/provider/koyeb/),
  [Koyeb Postgres free tiers blog](https://www.koyeb.com/blog/top-postgresql-database-free-tiers-in-2026))
- Same ephemeral-disk caveat → same SQLite/DB workaround as Render.
- Deploy: connect GitHub, Dockerfile or buildpack, `uvicorn app:app --host 0.0.0.0 --port $PORT`
  (Koyeb also injects `PORT`), env vars in the Koyeb console.
- Note: free-tier specs shift more often than Render's; re-verify at deploy time. Some users
  reported free-tier instability in 2026 (e.g. the Mistral acquisition period).

### Tertiary / fallback: **Hugging Face Spaces (Docker SDK)**

Works, but weaker for this use case — keep as a backup target:
- Free `cpu-basic` hardware, Docker SDK allowed, app must listen on **port 7860**.
- **Sleeps after 48 h inactivity** (auto-wakes on visit, ~30 s). Ephemeral storage.
  ([HF docs — manage spaces](https://huggingface.co/docs/huggingface_hub/main/guides/manage-spaces),
  [HF sleep-time docs](https://huggingface.co/docs/hub/en/spaces-gpus))
- **Free Spaces are public by default** (source visible); private Spaces need a paid plan.
  Never hard-code the API key — use Space **Secrets** in Settings.
  ([HF enterprise plans](https://huggingface.co/docs/hub/enterprise),
  [2026 free-tier roundup](https://github.com/chirag127/blog/blob/HEAD/src/content/blog/is-everything-possible-on-free-tier-vms-2026.mdx))

---

## 2. Evaluated and NOT recommended for this app

| Platform | Why not (as of Sep 2026) |
|---|---|
| **Railway** | No true free tier anymore: $5 one-time trial credit expires in 30 days, then a **$1/month** non-rolling Free-plan credit — a single always-on service exhausts it. Realistic floor is the $5/mo Hobby. ([dev.to 2026 pricing](https://dev.to/nayankyada/railway-pricing-2026-free-tier-limits-usage-costs-when-to-upgrade-1acm), [yuanbop/frugal research](https://github.com/yuanbop/frugal/blob/HEAD/research/railway.md)) |
| **Fly.io** | No free tier for new accounts (retired Oct 2024). Pay-as-you-go from the first dollar; **credit card required**. Sub-$5 invoices are waived only as an unofficial courtesy. ([techsy.io 2026 benchmark](https://techsy.io/en/blog/railway-vs-render-vs-fly-io), [Fly community](https://community.fly.io/t/free-tier-is-dead/20651)) |
| **Vercel** | Hobby = serverless Python functions only; 10 s function timeout. CIOS makes OpenRouter calls with `request_timeout=120` s and holds SQLite + in-memory rate state — architecturally wrong target. Frontend-only split isn't worth it: FastAPI already serves the static files. |
| **Replit** | Starter plan: one live project **expires after 30 days**; deployments consume cycles; not a durable $0 home. ([therundown.ai](https://www.therundown.ai/tools/replit-agent)) |

---

## 3. SQLite on the free target — persistence analysis

**Short answer: SQLite does NOT persist on Render/Koyeb/HF free.** All three have ephemeral
container filesystems; `cios.db` is re-created empty by `init_db()` on every cold start /
redeploy. Consequences beyond lost history: the **cache** table (OpenRouter quota savings),
the **usage** table (daily 48-req cap), and **rate_state** are wiped — the daily cap will
reset on each restart, so a frequently-sleeping service could overshoot OpenRouter's
50 req/day free limit more easily than local runs.

**$0 workarounds:**
1. **Accept ephemeral DB (zero work, zero code).** App stays fully functional; cache, history
   and usage reset on each sleep/wake cycle. Acceptable for personal single-user use — the
   quota risk is bounded (48/day local cap, OpenRouter still hard-caps at 50/day).
2. **External free Postgres (recommended when persistence matters) — requires a small code
   change** (add `psycopg2-binary`/`SQLAlchemy`, read `DATABASE_URL`; Phase-3 work, NOT done
   in this phase):
   - **Neon free (recommended):** 0.5 GB storage/project, 100 CU-hours/month/project,
     **auto-suspends after 5 min idle and auto-resumes on next query** — no manual un-pause,
     no card. Best idle behavior of the free options.
     ([Neon pricing/limits notes](https://github.com/joaquincampo/skills/blob/HEAD/neon-postgres/references/pricing-and-limits.md),
     [vibecoderslife 2026 comparison](https://vibecoderslife.com/post/free-database-storage-vibe-coding-2026))
   - **Supabase free:** 500 MB, 2 projects/org — but **pauses after 7 days idle and needs a
     manual "restore" click**; also counts against the 2-project cap. Runner-up.
     ([same comparison](https://vibecoderslife.com/post/free-database-storage-vibe-coding-2026))
   - **Turso (libSQL/SQLite-native):** generous (5 GB, 500 M row reads/mo) and SQLite-compatible
     — lowest-friction migration if we want to keep the SQLite query shape.
3. Do **not** use Render's free Postgres — 90-day deletion, and committing a seed `.db` to the
   repo is useless (resets to committed state on every boot).

---

## 4. Env / secret management on the target

- **Render:** Dashboard → Environment → add `OPENROUTER_API_KEY` etc. as **secret** entries
  (masked in logs). `render.yaml` may declare non-secret `envVars`; secrets should be entered
  in the dashboard (or `sync: false` entries) — never commit values to git. Secret rotation:
  change the value in the dashboard → Render auto-redeploys the service.
- **Koyeb:** Service → Environment variables (plain or secret) in the console; redeploy on change.
- **HF Spaces:** Settings → **Secrets** (injected as env vars at runtime, not in the public repo).
- Local dev keeps working unchanged via `.env` (already `.gitignore`d). No changes needed to
  `config.py` for deployment except remembering that **`PORT` ≠ `APP_PORT`** — all three
  platforms inject `PORT`; the start command maps it via the uvicorn `--port $PORT` flag.

---

## 5. Minimal Dockerfile + render.yaml (proposed specs — text only, not created)

**Dockerfile** (Python slim, non-root not strictly needed at this scale):

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# Exclude: .env, *.db, .venv, __pycache__ (use a .dockerignore mirroring .gitignore)
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000}"]
```

**`.dockerignore`** should mirror `.gitignore`: `.env`, `*.db`, `.venv/`, `__pycache__/`,
`.pytest_cache/`, `.DS_Store`. (Note: `brain/` *.md files are read by `brain_loader.py` at
runtime — they MUST be included in the image; they're small.)

**`render.yaml`** (Blueprint spec):

```yaml
services:
  - type: web
    name: cios
    runtime: docker            # or omit + use native python with build/start commands
    plan: free
    dockerfilePath: ./Dockerfile
    healthCheckPath: /api/health
    autoDeploy: true
    envVars:
      - key: APP_HOST
        value: 0.0.0.0
      - key: DAILY_REQUEST_CAP
        value: 48
      # OPENROUTER_API_KEY intentionally NOT here — set as a secret in the dashboard
```

Port note: Render sets `$PORT` and the Dockerfile `CMD` consumes it, so no port config is
needed in `render.yaml`.

---

## 6. Logging / monitoring at $0

- **Built-in platform logs (free):** Render dashboard shows live + recent deploy/runtime logs
  on the free tier (retention limited — treat as ephemeral; copy anything important out).
  Koyeb and HF Spaces have equivalent log viewers. This covers basic debugging at $0.
- **Uptime monitoring — UptimeRobot free:** 50 monitors, 5-minute checks, email alerts.
  ([uptimerobot.com](https://uptimerobot.com/knowledge-hub/comparisons-and-alternatives/11-best-uptime-monitoring-tools-compared/))
  ⚠️ **Two caveats:** (a) since Dec 2024 the free plan is **personal, non-commercial use only**
  — if CIOS becomes a revenue product, the free plan violates ToS (account suspension risk);
  ([dev.to](https://dev.to/r0tten0x/uptimerobot-free-plan-in-2026-the-limits-thatll-actually-bite-you-445g))
  (b) a 5-minute ping cadence doubles as an anti-sleep keep-alive, which Render treats as
  free-tier abuse — use it for **downtime alerting**, not for keeping the service warm.
- **Alternatives with commercial use allowed on free:** Better Stack (10 monitors, 3-min
  checks), HetrixTools (15 monitors, 1-min checks), StatusCake (unlimited, 5-min).
  ([dev.to alternatives](https://dev.to/r0tten0x/5-uptimerobot-alternatives-that-are-actually-free-in-2026-315o))
- **App-level:** `/api/health` already exposes `key_configured` and `usage` — point the
  uptime monitor at `/api/health` and optionally add a keyword check on `"ok": true`.

---

## 7. Backup / rollback note

- **Rollback:** Render auto-deploys from GitHub; the dashboard's **Deploys → Rollback**
  redeploys any previous commit in one click. Koyeb keeps deployment history similarly.
  Keep `main` deployable: run `pytest` before every push (cheap insurance, already $0).
- **Data backup:** with ephemeral SQLite there is no server-side backup to configure.
  Options at $0: (a) accept loss (history/cache are non-critical); (b) periodically
  download `cios.db` via the Render shell; (c) add a tiny authenticated `/api/backup`
  endpoint that returns a `sqlite3 .dump` (Phase-3 code task — flag, do not build now);
  (d) move to Neon free, which includes point-in-time recovery (6 h history on free).
- **Config backup:** `render.yaml` in the repo *is* the infrastructure backup — one more
  reason to add it in Phase 3 even though dashboard-created services work without it.

---

## 8. Bottom line

| Decision | Recommendation |
|---|---|
| Deploy target | **Render free Web Service** (Docker runtime), Koyeb as backup |
| Build / start | `pip install -r requirements.txt` → `uvicorn app:app --host 0.0.0.0 --port $PORT` |
| Secrets | Dashboard env vars (`OPENROUTER_API_KEY`); never in git/`render.yaml` |
| Health check | `/api/health` (already exists) |
| Database | Accept ephemeral SQLite now; migrate to **Neon free Postgres** when persistence matters (small Phase-3 code change) |
| Monitoring | Platform logs + UptimeRobot free on `/api/health` (alerting only, not keep-alive) |
| Rollback | Render one-click redeploy of previous commit; `render.yaml` as IaC backup |
| Cost | $0 build + $0 run on free tiers; no card required on Render/Koyeb/HF |

**Open items for Phase 3 (not done here):** write the actual `Dockerfile`, `.dockerignore`,
and `render.yaml`; add `gunicorn` vs single-worker uvicorn decision (single uvicorn worker is
fine for this load — SQLite + in-process rate limiting actually *require* a single worker);
consider `DATABASE_URL` support for Neon; optional `/api/backup` endpoint.
