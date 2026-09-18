# CIOS — SaaS Build Plan (v1 → publishable, $0)

**Date:** 2026-09-18 · **Constraint:** $0 build + $0 run. No paid APIs, no paid hosting, no card.  
**Basis:** `AUDIT-REPORT.md` (3 Critical · 10 High · 17 Medium · 20 Low), `MARKET-STUDY.md`, `audit/devops-findings.md`.  
**Current stack:** FastAPI (sync endpoints) · vanilla JS dashboard · SQLite · OpenRouter `:free` models · pytest.

---

## 1. Target architecture (Phase A — this build)

```
Browser (dashboard, Roman Urdu)
   │  HTTPS, same origin
   ▼
FastAPI app (single uvicorn worker — REQUIRED: SQLite + in-process quota state)
   ├─ SecurityHeadersMiddleware (CSP, X-Frame-Options, X-Content-Type-Options, Referrer-Policy)
   ├─ Request logging middleware (method, path, ms, user, cache/quota outcome)
   ├─ /api/auth/*      — signup / login / me  (JWT, pwdlib[argon2] hashing)
   ├─ /api/ideas|research|scripts|packaging|seo|niche   (require JWT when auth on)
   ├─ /api/keywords    — (Phase B) YouTube autocomplete explorer, keyless
   ├─ /api/outliers    — (Phase B) RSS outlier tracker, keyless
   ├─ /api/saved       — (Phase B) swipe file CRUD
   ├─ /api/admin/*     — user list, per-user usage, quota overrides (admin only)
   ├─ /api/health · /api/usage (per-user) · /api/history (per-user)
   └─ SQLite (cios.db) — users, usage(user_id,day), cache, rate_state, history(user_id), saved
        └─ DATABASE_URL support stub → Neon free Postgres (Phase C, migration path)
OpenRouter :free models (user's own key = BYOK; operator pays $0 per user)
```

**Why single worker:** SQLite + in-process quota/rate state are correct only with one worker. Documented in README and `render.yaml` comment.

**Why keep SQLite now:** $0, zero ops, matches dev. Migration path (Phase C): all DB access already goes through `database.py` functions → add a `DATABASE_URL` branch using the same function signatures (Neon free Postgres). No ORM rewrite needed for Phase A.

---

## 2. Auth & multi-user (fixes B-1)

- **JWT auth**: `POST /api/auth/signup` (email + password, first user becomes admin), `POST /api/auth/login` → JWT (pyjwt, 7-day expiry), `GET /api/auth/me`. Dependency `get_current_user` on all AI endpoints + history/usage. Passwords hashed with **pwdlib[argon2]**.
- **`AUTH_REQUIRED` env** (default `true`): when false (explicit local-dev opt-out), endpoints behave as v1 single-user. Publish checklist requires it true.
- **Per-user isolation**: `history` and `cache` rows keyed by `user_id`; `/api/history` returns only the caller's rows; `/api/usage` returns the caller's counter.
- **Admin view**: `is_admin` flag; `GET /api/admin/users` (id, email, created, is_admin), `GET /api/admin/usage?day=` (per-user counts), `POST /api/admin/quota` (override a user's daily cap). Frontend: admin sees a simple "Admin" tab.
- **CSRF note**: auth uses `Authorization: Bearer` header (not cookies) → CSRF n/a by design. If cookies are ever added, add CSRF tokens (audit L2).

## 3. Quota & rate limiting (fixes A-2, A-3, B-2, B-3)

- **Attempt-based accounting**: the local counter increments on **every outbound provider request** (not only successes) → local cap finally measures what OpenRouter measures. Keep the 48 vs 50 safety buffer (`DAILY_REQUEST_CAP=48`).
- **Atomic increment**: single `UPDATE usage SET count = count+1 WHERE day=? AND user_id=? AND count < ?` and check `rowcount` → no overshoot, no transaction dance.
- **Atomic pacing**: read+update `last_call_at` inside one transaction using monotonic clock; sleep **before** firing, outside the DB lock, using `threading.Event.wait()` so it's interruptible.
- **Per-user daily quotas**: `usage(user_id, day, count, cap)`; cap default 48, admin-overridable.
- **Abuse throttle (new, lightweight, no extra deps)**: per-IP in-memory sliding window on the 6 POST endpoints (e.g. 30 req/min/IP) + per-user 60 req/hour soft cap → B-2 without slowapi. Document that this is single-worker in-memory.
- **Cache still checked before quota** (correct order, keeps free hits free) — but cache is now per-user namespaced.

## 4. AI client hardening (fixes A-1, plus C-7/M items)

- **Fallback chain**: catch provider 4xx (400/401/403/404) per-model → mark model skipped, try next; only raise `AllModelsFailed` after the whole chain is exhausted. Add a generic `500` exception handler in `app.py` returning the standard `{ok:false, error}` shape (never raw tracebacks).
- **Model chain maintenance**: add `scripts/check_models.py` (uses `/api/models` free filter) so the operator can refresh `model_chain` when a free ID rotates; README documents it. Replace hardcoded third-party `http_referer` with the user's own URL or empty (C-16).
- **Connection reuse**: one module-level `httpx.Client` (timeouts, keep-alive) instead of `httpx.post` per attempt.
- **Error hygiene**: never echo raw provider response bodies to clients — map to friendly Roman Urdu messages, log the raw text server-side.
- **`save_history` best-effort** (B-5): wrap in try/except, log failure, still return the generation.
- **`brain_loader` fails loud** (C-8): log + raise at startup if a router's brain file is missing (startup check in `app.py` lifespan).
- **Startup via lifespan**: move `init_db()` into a lifespan handler (C-5), not import time.
- **SQLite WAL mode + busy timeout** (C-6); **history retention**: prune rows older than 90 days on startup + cap per-user at 500 (C-10); cache purge once per day, not per request (C-9).
- **Prompt-injection hygiene** (C-1): wrap user content in explicit delimiters (`<<<USER_INPUT>>>…<<<END>>>`) + add an instruction-hierarchy guard line to the system prompt composition in `brain_loader.py`. (Can't eliminate injection in an LLM app; delimiters + guard are the $0 best practice.)
- **Missing-key check once**, up front in `generate()` (L-7).

## 5. Input validation (fixes B-4, B-9)

- `models.py`: add `strip()` + non-blank validators on every string field (`min_length` currently accepts `"  "`).
- Frontend: enforce the same ranges client-side (count 3–20, duration 15–1800, min lengths), and **parse FastAPI's 422 `detail` array into a friendly inline message** instead of `Error: HTTP 422`.
- `maxlength` attributes on all text inputs (backend L-3).

## 6. Frontend rebuild (fixes B-7, B-8, B-9 + mediums)

- **Auth screens**: login/signup views, token in `localStorage`, `Authorization` header on all calls, logout, admin tab (admin only). Friendly "API key missing" setup hint linking to `openrouter.ai/keys`.
- **Loading states that render**: fix the `.out:empty` vs `.loading` conflict — placeholder text node inside the output box during flight.
- **Double-submit guard**: disable button + `aria-disabled` while in flight; re-enable on settle. (Protects the 48/day budget.)
- **Fetch timeout**: `AbortController`, 125s (just above the 120s backend timeout), with a cancel-friendly message.
- **Enter-to-submit** via `<form onsubmit>`; replace `alert()` with inline field errors.
- **`aria-live="polite"`** on result regions; `role=tab`/`aria-selected` on tabs.
- **Privacy disclosure** in footer (C-4): one line — "Inputs OpenRouter ko bheje jate hain (aapki key se)."
- **Usage pill**: per-user `{used}/{cap}`, warn styling at ≥80%; silent-failure fixed with retry text.
- **History tab**: per-user rows; "Save to Swipe File" star hooks (Phase B backend).
- Keep: `esc()` everywhere + add CSP header as backstop (C-2).

## 7. Security headers & config (fixes C-2, C-3, misc)

- Middleware: `Content-Security-Policy` (default-src 'self'; script/style 'self' — no inline-event bypass needed since we keep `onclick`? **Decision:** keep inline `onclick` and set `script-src 'self' 'unsafe-inline'` *or* move to `addEventListener`. **Do the clean thing: move to addEventListener, CSP without unsafe-inline.**), `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin`. HSTS off by default (only behind HTTPS; note in README).
- `/api/health`: when `AUTH_REQUIRED`, require auth for the detailed view; keep a minimal unauthenticated `{ok:true}` for platform health checks.
- Pinned `requirements.txt` (exact versions = what pip-audit cleared) + `requirements-dev.txt` (pytest, pip-audit).
- Logging: stdlib `logging` to stdout (platforms collect it); log auth events, quota rejections, cache hits, model used, latency, errors (never the API key).

## 8. Testing (Phase 5 gate: ALL GREEN required)

- Keep/adapt the 13 existing tests to the auth model (test client signs up + uses token).
- New tests: auth flow (signup/login/me, wrong password, expired token), per-user isolation (user A can't see B's history), quota atomicity (attempt-based counting), fallback chain with mocked `httpx` (404 on model 1 → model 2 serves), whitespace rejection, `save_history` failure doesn't 500, security headers present, per-IP throttle, admin endpoints (non-admin 403).
- Target: **30+ tests, all green**. Then live local verification: uvicorn + curl every endpoint (success + 400/401/403/404/422/429 cases) + every frontend button flow (HTTP-level + static JS check). No real OpenRouter key exists → **live AI stays mocked; documented as the known limit.**
- Deliverables: `TEST-REPORT.md` (per-endpoint + per-button pass/fail), `SECURITY-CHECKLIST.md` (each audit item → verified fixed).

## 9. Deployment — $0 publish path

- **Primary: Render free Web Service.** Add `Dockerfile`, `.dockerignore`, `render.yaml` (specs from `audit/devops-findings.md` §5). Build: `pip install -r requirements.txt`. Start: `uvicorn app:app --host 0.0.0.0 --port $PORT --workers 1`. Health check: `/api/health`. Secrets (`OPENROUTER_API_KEY`, `JWT_SECRET`) in Render dashboard, never in git.
- **Backup: Koyeb** (same container), **HF Spaces** (port 7860 note).
- **DB on free hosts**: accept ephemeral SQLite for v1-publish (documented: cache/history/usage reset on sleep; quota risk bounded by OpenRouter's own 50/day wall). Phase C: `DATABASE_URL` → Neon free Postgres.
- **Publish checklist** (also in README):
  1. `pytest` green · 2. `OPENROUTER_API_KEY` set in dashboard · 3. `JWT_SECRET` = long random, set in dashboard · 4. `AUTH_REQUIRED=true` · 5. push to GitHub → Render auto-deploys · 6. hit `/api/health` → `{ok:true}` · 7. sign up (first user = admin) · 8. one mocked-off test generation with a real key · 9. UptimeRobot/Better Stack monitor on `/api/health` (alerting only, not keep-alive — Render treats ping-keepalive as abuse) · 10. never commit `.env`/`*.db`.
- **Monitoring at $0**: platform logs + `/api/health` + free uptime monitor. **Rollback**: Render one-click redeploy of previous commit.

## 10. Payments path (future, optional — NOT built now)

Stripe Checkout is the documented Phase-D path: Pro tier (~$9–12/mo per market study) = cloud history, higher caps, swipe file, shared key pool. Requires: Stripe account, webhook endpoint, `subscriptions` table. **Not implemented in this build** — the architecture (per-user quotas, admin overrides) is already payment-ready. $0 constraint preserved.

## 11. Phase B (market features, after publish — $0 each)

1. **YouTube Autocomplete Keyword Explorer** (`POST /api/keywords`, keyless `suggestqueries.google.com`, cached) — first real-data feature.
2. **RSS Outlier Tracker** (`POST /api/outliers`, up to 3 channel IDs, outlier score = views ÷ channel median, ≥2× flagged) — 1of10-lite.
3. **Swipe File + 30-day Content Calendar** (local CRUD on `saved` table; calendar layout from niche output) — retention features.
4. Thumbnail text-overlay preview (canvas, browser-side). 5. Onboarding tour + docs page.

## 12. Build work breakdown (for the build team)

- **Backend builder**: `auth.py` (JWT), `database.py` (users/per-user usage/history/cache, WAL, retention), `ai_client.py` (fallback 4xx-skip, atomic quota/pacing, attempt accounting, shared httpx.Client, best-effort history), `models.py` (strip validators), `routers/*` (auth deps, delimiters), `app.py` (lifespan, headers middleware, logging middleware, throttles, exception handlers, `/api/auth/*`, `/api/admin/*`), `requirements.txt` pinned (+pyjwt, pwdlib[argon2]), `Dockerfile`, `.dockerignore`, `render.yaml`, `scripts/check_models.py`, tests.
- **Frontend builder**: auth UI, all B-7/B-8/B-9 + medium fixes, admin tab, privacy line, `addEventListener` conversion, Roman Urdu copy, README run/publish section update.
- **Test agent**: full suite + live endpoint/button verification → `TEST-REPORT.md` + `SECURITY-CHECKLIST.md`.

## 13. Acceptance criteria (done = all true)

- [ ] `pytest`: 30+ tests, 100% green
- [ ] Every AUDIT-REPORT Critical + High item fixed and re-verified
- [ ] Every dashboard button: wired, loading state, error state, double-submit safe
- [ ] Auth: signup/login/me, per-user quota/history, admin view, non-admin blocked
- [ ] Security headers present; no raw provider errors to clients; deps pinned + pip-audit clean
- [ ] `Dockerfile` + `render.yaml` present; local docker-less uvicorn run verified
- [ ] README: run + publish steps (Roman Urdu user-facing), env var table, $0 notes
- [ ] `TEST-REPORT.md` + `SECURITY-CHECKLIST.md` written; known limit documented (no live AI test — no key)
