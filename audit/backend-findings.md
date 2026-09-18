# CIOS Backend Audit — Findings

**Scope:** `~/workspace/cios` — FastAPI app, 6 AI routers, `ai_client.py` (OpenRouter free-model client), cache, daily quota, SQLite, config. **Audit only; no code changed.**

**Test baseline (run 2026-09-18):** `cd ~/workspace/cios && .venv/bin/python -m pytest tests/ -q` → **13/13 passed** in 1.73s. Note: every test mocks `ai_client.generate`, so the real quota/cache/fallback/HTTP paths are **entirely untested** (see H-5).

---

## Critical

### C-1. Non-429/5xx provider errors bypass the fallback chain AND have no HTTP handler → unhandled 500
- **Files:** `ai_client.py:71-72`, `ai_client.py:95-99`, `app.py:28-38`
- `ai_client._call` raises `_RetryableError` only for 429/≥500. Any other non-200 (400, 401, 403, 404 — e.g. **invalid/retired model name** or bad key) raises a bare `RuntimeError`.
- `generate()`'s model loop (`ai_client.py:95`) catches **only** `_RetryableError`, so a `RuntimeError` from the *first* model aborts the whole fallback chain — the remaining models are never tried.
- `app.py` registers exception handlers only for `QuotaExceeded`, `MissingApiKey`, `AllModelsFailed`. A `RuntimeError` therefore falls through to FastAPI's default handler → **HTTP 500 "Internal Server Error"** with no structured `{"ok": false, ...}` body, inconsistent with the app's error contract.
- This is high-blast-radius because the configured `model_chain` (`config.py:42-47`) contains speculative model IDs (`nvidia/nemotron-3-ultra-550b-a55b:free`, `google/gemma-4-31b-it:free`, `thinkingmachines/inkling-small:free`) that were never verified against OpenRouter. If any returns 404/400 "model not found", **every feature endpoint breaks with a 500** instead of falling back.

### C-2. Daily quota is check-then-act with no atomicity → cap can be overshot under concurrency
- **Files:** `ai_client.py:84-92` (check), `ai_client.py:104-108` (increment)
- The quota check (`SELECT count …; if used_count >= cap: raise`) and the increment (`INSERT … ON CONFLICT DO UPDATE SET count = count + 1`) are separate statements on a short-lived connection with no transaction/locking.
- Two concurrent requests can both read `count = 47`, both pass the check, and both increment → provider-side usage exceeds the app's own 48 cap, eroding the 2-request safety buffer against OpenRouter's 50/day hard wall (`config.py:33-36`).

### C-3. Rate pacing has the same race → the 20 RPM guard is not enforced under concurrency
- **Files:** `ai_client.py:90-93`, `ai_client.py:110`
- `last_call_at` is read, then `time.sleep(wait)`, then (only on success) updated. While a request is in flight (up to `request_timeout` = 120s), a second request reads the **stale** `last_call_at` and computes the same/shorter wait → both fire nearly simultaneously.
- Under real concurrent use the "3.5s gap" (`config.py`) degrades to best-effort; two rapid users can hit OpenRouter's 20 RPM limit, which then burns a fallback attempt per 429.

---

## High

### H-1. Quota accounting doesn't match provider-side consumption — failed attempts are free locally but cost OpenRouter quota
- **Files:** `ai_client.py:60-68` (429/5xx retry → next model = another real HTTP call), `ai_client.py:104-108` (usage incremented only on success)
- Every `_RetryableError` triggers a retry on the next model, i.e. another live OpenRouter request. Timeouts/network errors likewise (`ai_client.py:60-64`). None of these increment the local `usage` counter.
- A bad patch of weather (peak-hour 429s) can therefore burn through OpenRouter's 50/day allowance while the app reports `used < 48`. The local cap is a measure of *successes*, not of *provider requests*.

### H-2. Whitespace-only input passes all Pydantic validation
- **Files:** `models.py` (all request models)
- Fields like `niche: str = Field(..., min_length=2)` accept `"  "` (two spaces). Every endpoint's required string field (`niche`, `topic`, `title`) can be satisfied with whitespace, producing a wasted AI call (and wasted quota) on an empty prompt. `models.py` has no `strip()`/non-blank validators. The frontend also only checks truthiness after `.trim()` (`frontend/app.js:56`) but a direct API caller bypasses that.

### H-3. No multi-user isolation at all: shared quota, shared cache, shared history, no auth
- **Files:** `app.py` (no auth/dependency), `ai_client.py` (global `usage`/`rate_state`/`cache` tables), `database.py`
- One SQLite DB, one daily counter, one pacing clock for everyone. Any user can exhaust the 48/day quota for all users; `/api/history` exposes every user's inputs and full AI outputs to anyone who asks; there is no per-user rate limit or API key scoping. Fine for a single-user local run, but it will break (socially and functionally) the moment a second user touches it.

### H-4. `save_history` failure turns a successful AI call into a 500
- **Files:** `routers/__init__.py:20-24`, `database.py:39-49`
- `finish()` calls `save_history()` *after* a successful generation. If that write fails (locked DB, full disk, corrupt file), the exception propagates and the endpoint returns 500 — the user loses a good (quota-consuming) result they can never retrieve. History logging should be best-effort, not on the critical path.

### H-5. Zero test coverage of the real AI client path
- **Files:** `tests/test_api.py` (all 13 tests mock `ai_client.generate`)
- Quota check/increment, cache hit/miss/TTL purge, the fallback loop, `MissingApiKey`/`AllModelsFailed` raising, and `_call`'s status-code branching are **never executed by tests**. The 13 passing tests verify routing, Pydantic validation, and the three exception handlers only. Any regression in `ai_client.py` (the highest-risk file) ships silently.

---

## Medium

### M-1. `init_db()` runs at import time → side effects on import, test pollution
- **Files:** `app.py:22` (`init_db()` at module top level), `database.py:17`
- Importing `app` (as the test suite does) creates/touches the real `cios.db` in the project dir before the test fixture monkeypatches `config.settings.db_path` to a tmp path. Tests pass anyway because the fixture re-runs `init_db()` on the temp DB, but the import-time side effect is the reason the real DB file gets created during test runs.

### M-2. SQLite concurrency: default journal mode, no busy-timeout tuning
- **Files:** `database.py:11-14`
- `get_conn()` uses `sqlite3.connect` defaults (rollback journal, 5s busy timeout). Concurrent writes (`cache` INSERT, `usage` upsert, `rate_state` UPDATE in one transaction at `ai_client.py:102-111`, plus `save_history`) from threadpool workers can hit `sqlite3.OperationalError: database is locked` under load, surfacing as 500s. No WAL mode, no retry-on-locked.

### M-3. Blocking `time.sleep` + blocking `httpx.post` inside sync endpoints
- **Files:** `ai_client.py:62, 92-93`, all six routers (sync `def` endpoints)
- Each generation can block a worker thread for up to 120s × 4 models plus pacing sleeps. With uvicorn's default threadpool this serializes poorly under concurrent users; there is also no upper bound communicated to the client (frontend `fetch` in `frontend/app.js:36-43` has no timeout/abort).

### M-4. `brain_loader` silently swallows missing brain files
- **Files:** `brain_loader.py:26-33`
- `load_brain` returns `""` for a missing file and `system_for` proceeds without complaint. A typo in a filename (e.g. in any router's `SYSTEM = system_for(...)`) silently degrades the system prompt — and therefore output quality — with no log or error. (Today all referenced files exist; this is a latent footgun.)

### M-5. Cache TTL purge does a full-table `DELETE` on every generation
- **Files:** `ai_client.py:80`
- `DELETE FROM cache WHERE created_at < ?` runs unconditionally on each `generate()` call. Correct but wasteful; as the cache grows it adds latency to every request including cache hits. A periodic/one-per-day purge would do.

### M-6. History table grows without bound
- **Files:** `database.py:24-31`, `routers/__init__.py:22`
- `history` stores full input JSON + full model output per generation with no pruning, retention limit, or pagination beyond `LIMIT`. Long-term use bloats `cios.db` (which also holds the cache); the only consumers are `GET /api/history?limit=N`.

### M-7. Exception messages leak provider response text
- **Files:** `ai_client.py:72` (`resp.text[:300]` embedded in `RuntimeError`)
- On unhandled 500s (see C-1) the raw upstream response fragment can surface; more importantly the pattern embeds external content in errors without sanitization. Low practical risk locally, but worth noting before any hosted deployment.

### M-8. No request logging / observability
- **Files:** entire backend — zero `logging` calls (verified by grep)
- No access logs, no log of which model served a request, no log of quota rejections, cache hit rate, or latency. When (not if) the free models misbehave at peak hours, there is nothing to diagnose with. `/api/health` + `/api/usage` are the only introspection.

---

## Low

### L-1. `http_referer` hardcodes someone else's GitHub URL
- **File:** `config.py:49` — `https://github.com/in43am-beep/CIOS-Content-Intelligence-Operating-System`. Sent as `HTTP-Referer` on every OpenRouter call. Harmless functionally, but it's another account's identity on the user's traffic; should be the user's own URL or omitted.

### L-2. `/api/health` exposes model chain + key-presence to anyone
- **File:** `app.py:41-48`. Minor info disclosure; acceptable for localhost, reconsider if ever bound to a non-loopback host (`APP_HOST` is env-overridable, `config.py:22`).

### L-3. Frontend `esc()` doesn't escape single quotes; no `maxlength` on inputs
- **Files:** `frontend/app.js:11-14`, `frontend/index.html`
- `esc` covers `&<>"` only — adequate for the current `innerHTML` usage (no single-quoted attributes), but fragile. Inputs have no `maxlength`, so a user can paste megabytes into a textarea; the backend caps at 200–4000 chars (`models.py`) and returns 422, which the frontend renders as an error — handled, just unfriendly.

### L-4. `ApiResult.data: dict` vs `parse_or_text` fallback shape
- **Files:** `models.py:38-42`, `routers/__init__.py:8-17`
- Consistent (`parse_or_text` always returns a dict), but the `{"text": raw}` fallback means clients can't distinguish "model returned JSON" from "model returned prose" — the frontend renders both generically. Minor contract ambiguity, not a bug.

### L-5. `requirements.txt` has no pinned versions and no `httpx2`
- **File:** `requirements.txt`; test output shows a `StarletteDeprecationWarning` urging `httpx2` for the test client. Unpinned `fastapi>=0.115` etc. risk future breakage; add a lockfile or pins before any deployment.

### L-6. New `httpx.post` connection per model attempt
- **File:** `ai_client.py:62`. No shared `httpx.Client` → no connection reuse/keep-alive across the up-to-4 fallback attempts or across requests. Minor latency overhead per call.

### L-7. `MissingApiKey` is checked inside the per-model loop rather than once up front
- **File:** `ai_client.py:51-56`. Functionally fine (raises on the first iteration and maps to 400 via `app.py:32`), but the check is conceptually a pre-condition of `generate()`, not of each model attempt.

---

## What's actually solid (for the record)
- Pydantic bounds on numeric fields (`count` 3–20, `duration_sec` 15–1800, history `limit` 1–100) are present and tested (`tests/test_api.py::test_ideas_validation`).
- Cache key includes system prompt + user prompt + `max_tokens` (`ai_client.py:45-46`), so prompt changes correctly miss; cache hits skip the quota check, which is the right order.
- The three custom exceptions map to correct status codes (429/400/502) and are covered by tests.
- `.gitignore` correctly excludes `.env` and `*.db`; no secrets are logged or committed (`.env` absent, only `.env.example`).
- Frontend escapes HTML in rendered output (`frontend/app.js:11-14`), so model-generated markup isn't an XSS vector today.
