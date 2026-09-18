# CIOS Security Audit — Findings

**Date:** 2026-09-18 · **Scope:** `~/workspace/cios` (app.py, routers/, ai_client.py, brain_loader.py, config.py, database.py, models.py, frontend/, tests/, requirements.txt, .env.example, .gitignore) · **Mode:** audit only, no code changed
**Method:** full source read (PHASE 0), targeted greps, `pip-audit` on the venv.

**Bottom line:** No Critical findings (no SQLi, no RCE, no hardcoded secrets). The biggest real risks are (1) **zero authentication/authorization on any endpoint** combined with (2) a **shared, easily exhaustible 48/day API quota** and (3) **no abuse-oriented rate limiting** — anyone with network access can burn the daily budget and read every past generation. Prompt injection is inherent to the design and has no delimiters/guards.

---

## Critical

_None found._

---

## High

### H1 — No authentication or authorization on any endpoint (any user isolation)
- **Refs:** `app.py:52-67` (health/history/usage), `routers/ideas.py:25-28`, `routers/research.py:26-29`, `routers/scripts.py:26-29`, `routers/packaging.py:26-29`, `routers/seo.py:26-29`, `routers/niche.py:24-27`
- **Evidence:** Every route is an undecorated `def` with no `Depends(...)`, no API key header check, no session/cookie/token anywhere in the codebase (grep for `cookie|session|token|Depends` returned only the OpenRouter outbound `Bearer` header and `max_tokens` params — nothing inbound). `config.py` exposes `APP_HOST`/`APP_PORT` env overrides, and `README.md` suggests hosting the backend on a free tier, so this app is one config change away from being a public, open API.
- **Impact:** Anyone who can reach the port can (a) spend the owner's OpenRouter quota (H2), (b) read the full generation history including all past user inputs (`GET /api/history`, `database.py:57-70`), (c) poison the shared response cache (`ai_client.py:88-93`).

### H2 — Quota exhaustion is trivially weaponizable (no abuse-oriented rate limiting)
- **Refs:** `ai_client.py:96-105` (48/day cap), `ai_client.py:107-111` (3.5 s pacing)
- **Evidence:** The daily cap exists to protect the *budget*, not against *abuse*. With no auth (H1) and no per-IP/per-client throttle, a single unauthenticated client can fire up to ~48 requests and consume the entire day's free quota in minutes, locking the owner out. Pacing is a global `time.sleep()` in a sync endpoint that blocks the event loop; identical prompts hit the cache path *before* pacing (`ai_client.py:92-94`), so cached requests are unbounded in rate.
- **Impact:** Cheap denial-of-service on the app's core function for the rest of the day; also multi-worker races on `rate_state`/`usage` counters (SQLite SELECT-then-UPDATE with no locking, `ai_client.py:107-128`).

---

## Medium

### M1 — Prompt injection: raw user input interpolated into prompts, no sanitization or delimiters
- **Refs:** all routers, e.g. `routers/ideas.py:20-24`, `routers/research.py:23-27`, `routers/scripts.py:23-27`; system prompt composition in `brain_loader.py:38-44`
- **Evidence:** User fields (`niche`, `topic`, `examples`, `audience`, `title`) are f-stringed directly into the user prompt with no escaping, no XML/Markdown delimiters, and no instruction hierarchy. `ResearchRequest.examples` allows 4000 chars (`models.py:17`). The system prompt contains no "never follow instructions in user content" guard, and the brain files are concatenated raw into the system message.
- **Impact:** Inherent to this app's purpose, so not "fixable" by blocking input — but: (a) a crafted prompt can derail outputs (junk/malicious content persisted to history and re-rendered), (b) cache poisoning is amplified by H1 — anyone can store a poisoned response under a prompt hash, and any later identical request is served the poisoned cached text (`ai_client.py:88-93`, 30-day TTL via `cache_ttl_days`, `config.py:27`). Pydantic `max_length` bounds (`models.py`) limit size but not semantics.

### M2 — Missing security headers
- **Refs:** `app.py` (no middleware registered at all — confirmed by grep)
- **Evidence:** No `X-Content-Type-Options`, no `X-Frame-Options`, no HSTS, no `Content-Security-Policy`, no `Referrer-Policy`, no `TrustedHostMiddleware`.
- **Impact:** No defense-in-depth: if any future rendering path misses escaping (see X1), there's no CSP to contain it; the dashboard is clickjackable if framed when hosted. Low practical risk while bound to `127.0.0.1`, rises with any deployment.

### M3 — Information leakage in API responses
- **Refs:** `app.py:35-49` (exception handlers), `app.py:53-60` (health), `app.py:62-64` (history)
- **Evidence:** `str(exc)` is echoed into JSON for `MissingApiKey` (includes the key-creation docs URL), `QuotaExceeded`, and `AllModelsFailed` — the latter embeds `last_err`, which contains raw `httpx` exception text and OpenRouter error bodies (`ai_client.py:76`). `GET /api/health` exposes `key_configured` (boolean oracle) plus the full `model_chain` (`app.py:53-60`). `GET /api/history` returns every stored `input_json` + `output_text` with no auth (compounds H1).
- **Impact:** Low while local; error-text echo becomes a real leak vector once hosted (provider error bodies can contain request context).

### M4 — Third-party data flow: all user inputs sent to OpenRouter
- **Refs:** `ai_client.py:56-71` (outbound POST), every router's `user` prompt
- **Evidence:** By design, every prompt (including the 4000-char free-text `examples` field) is POSTed to `https://openrouter.ai`. There is no notice in the UI/privacy surface that inputs leave the machine, and no opt-out for sensitive topics.
- **Impact:** Privacy, not a vuln per se — but a user pasting confidential material (e.g. unreleased video ideas, client data in `examples`) gets it shipped to a third party. Worth a UI disclosure.

---

## Low

### L1 — XSS: checked, currently mitigated but single-layer
- **Refs:** `frontend/app.js:10-14` (`esc()`), `frontend/app.js:17-31` (`render()`), `frontend/app.js:49, 66, 79-85`
- **Evidence:** All AI-derived and user-derived strings are passed through `esc()` before `innerHTML` assignment; `render()` recurses through nested objects/arrays so no raw value slips through; error paths and `j.model`/`j.error`/`e.message` are escaped. `index.html` uses only static `onclick` handlers.
- **Caveat:** `esc()` covers `& < > "` but not `'` — harmless in element-content context (no attribute injection points exist today). No CSP (M2) means escaping is the *only* layer; any future template that forgets `esc()` is immediately exploitable. Recommend CSP as backstop.

### L2 — CSRF: not applicable today, becomes relevant if auth is added
- **Evidence:** Six state-changing `POST` endpoints exist, but there are no cookies, sessions, or ambient credentials of any kind, so cross-site request forgery has nothing to ride on. **Do not treat this as cleared** — the moment any cookie/session auth is introduced (e.g. for H1), all six POSTs need CSRF tokens or `SameSite` enforcement.

### L3 — CORS: correctly absent
- **Evidence:** No `CORSMiddleware` anywhere. Same-origin frontend + API means the default same-origin policy applies — this is the safe configuration. Only revisit if a separately-hosted frontend is introduced.

### L4 — SQL injection: not present — all queries parameterized
- **Refs:** `database.py` (all `execute()` calls use `?` placeholders, including the `LIMIT ?` in `get_history`, `database.py:61-65`); `init_db()` uses static DDL
- **Evidence:** No string interpolation into SQL anywhere; user-controlled `limit` is Pydantic-validated (`Query(ge=1, le=100)`, `app.py:63`) before reaching the parameterized query. `db_path` comes from env config, not from requests.

### L5 — Secrets handling: clean, with minor notes
- **Evidence:** No hardcoded keys/tokens anywhere (grep clean). `.env` is gitignored (`.gitignore:5`) and no `.env` file exists in the repo dir (verified). Key is read only from env (`config.py:11`), sent only as an outbound `Authorization: Bearer` header over HTTPS (`ai_client.py:59-65`), and never printed/logged (no logging calls in the codebase). `.env.example` ships an empty placeholder.
- **Notes:** (a) `HTTP-Referer`/`X-Title` are hardcoded to a GitHub URL (`config.py:43-44`) — benign fingerprinting at most. (b) `cios.db`/`cios.db-journal` exist on disk with group-readable perms (`-rw-rw----`) — they contain the full prompt/response history; fine for a personal machine, worth tightening if multi-user.

### L6 — Dependencies: runtime deps clean; only `pip` itself flagged
- **Evidence:** `pip-audit` run on the venv (installed ad hoc for this audit): all application dependencies clean — `fastapi 0.141.1`, `uvicorn 0.53.0`, `starlette 1.6.0`, `httpx 0.28.1`, `pydantic 2.13.5`, `pydantic-settings 2.15.0`, `pytest 9.1.1` → **no known vulnerabilities**.
- The only findings are 12 entries against `pip 24.0` itself (`PYSEC-2026-1795/1796/2875/2876/196/3721`, fix ≥ 25.3) — the venv's installer toolchain, not a runtime dependency; low relevance to the app's attack surface.
- **Residual:** `requirements.txt` uses unpinned lower bounds (`fastapi>=0.115` etc.), so fresh installs drift over time; resolved versions above are what was actually tested.

### L7 — Availability nits: blocking sleeps and long outbound timeouts in sync endpoints
- **Refs:** `ai_client.py:109-111` (`time.sleep` blocks the event loop), `config.py:26` (`request_timeout: 120`)
- **Evidence:** All routers are sync `def`s; the 3.5 s pacing sleep and the 120 s OpenRouter timeout both hold a worker thread. With default single-worker uvicorn this is a self-DoS under concurrent use; combined with H1/H2 an attacker can also just hold connections open.

### L8 — Pydantic validation is present but shallow
- **Refs:** `models.py` (all fields), `app.py:63`
- **Evidence:** Positive: `min_length`/`max_length`/`ge`/`le` bounds exist on every input, and `extra="ignore"` on settings (`config.py:9`). Negative: no content validation anywhere (expected for an LLM app), and the 4000-char `examples` field is the widest untrusted-input funnel (feeds M1/M4).

---

## Checked and cleared (no finding)

| Area | Result |
|---|---|
| SQL injection | All queries parameterized (`database.py`) |
| Hardcoded secrets | None; `.env` gitignored, absent from disk |
| Secrets in logs/errors | No logging framework in use; key never in exception text |
| CORS misconfig | No CORS middleware — safe default |
| XSS sink audit | All `innerHTML` writes pass through `esc()` |
| `eval`/`exec`/subprocess/pickle | None in codebase |
| Path traversal in static serving | `StaticFiles` on fixed `FRONTEND_DIR`; no user-controlled paths |
| Open redirects | None |
| Dependency CVEs (app deps) | `pip-audit`: clean |
| Test isolation | Tests mock `ai_client` and use tmp DB (`tests/test_api.py`) — no key/quota touched |

## Suggested priority for the fix phase (not implemented)
1. H1: add at least a shared-secret header or basic auth before any network exposure; gate `/api/history` and `/api/health` details.
2. H2: per-client rate limiting (e.g. `slowapi`) independent of the quota budget; move pacing sleep off the event loop.
3. M1: wrap user content in explicit delimiters + add an instruction-hierarchy guard to the system prompt; consider cache namespacing if multi-user.
4. M2/M3: add security headers middleware; stop echoing raw provider/httpx error text to clients.
