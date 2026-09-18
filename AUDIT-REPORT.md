# CIOS — Consolidated Audit Report (v1 → SaaS)

**Date:** 2026-09-18 · **Scope:** `~/workspace/cios` (FastAPI + vanilla-JS dashboard, 6 AI endpoints, OpenRouter free-model client, SQLite)  
**Method:** Four independent expert audits (backend, security, frontend/QA, DevOps) + market study of 7 live products. All findings verified against source; **no code was changed** in the audit phase.  
**Totals: 3 Critical · 10 High · 17 Medium · 20 Low** (detailed per-stream reports live in `audit/`)

---

## A. Critical (fix before any public run)

### A-1. Stale/retired model ID kills EVERY feature with an unhandled 500
`ai_client._call` raises a bare `RuntimeError` for any non-429/5xx provider status (e.g. **404 "model not found"**); `generate()`'s fallback loop only catches `_RetryableError`, so the first bad model aborts the whole chain — remaining models are never tried. `app.py` has no handler for `RuntimeError` → raw HTTP 500, breaking the app's `{ok:false}` error contract. The configured `model_chain` contains **unverified, speculative model IDs** (`nvidia/nemotron-3-ultra-550b-a55b:free`, `google/gemma-4-31b-it:free`, `thinkingmachines/inkling-small:free`) — free-model IDs rotate on OpenRouter, so one retirement takes down all six features at once. *(backend C-1)*

### A-2. Daily quota is check-then-act → cap can be overshot under concurrency
Quota check (`SELECT`) and increment (`UPDATE`) are separate statements with no transaction. Two concurrent requests can both read 47, both pass, both increment — eroding the 2-request safety buffer against OpenRouter's 50/day hard wall. *(backend C-2; security H2)*

### A-3. Rate pacing has the same race → 20 RPM guard unenforced under concurrency
`last_call_at` is only updated after success, so a second request reads a stale timestamp and fires (near-)simultaneously with the first. The 3.5s pacing degrades to best-effort; two rapid users can hit OpenRouter's 20 RPM limit, each 429 burning another fallback attempt. *(backend C-3)*

---

## B. High

### B-1. No authentication/authorization on ANY endpoint
All 6 POST endpoints plus `/api/history`, `/api/usage`, `/api/health` are open. No `Depends()`, no tokens, no sessions. Anyone with network access can spend the owner's quota, read the entire generation history (all past user inputs + full AI outputs), and poison the shared response cache. The app is one `APP_HOST` change away from being a public open API. *(security H1; backend H-3)*

### B-2. Quota exhaustion is trivially weaponizable
No per-client throttle; one unauthenticated client can burn the whole 48/day quota in minutes. Pacing is a blocking `time.sleep()` in a sync endpoint; cache hits skip pacing entirely. *(security H2)*

### B-3. Quota accounting doesn't match provider-side consumption
Only *successful* generations increment the counter. Every fallback retry, timeout, and network error is another real OpenRouter request that costs $0-money but real quota — a bad patch of 429s can exhaust OpenRouter's 50/day allowance while the app reports `used < 48`. *(backend H-1)*

### B-4. Whitespace-only input passes all validation
`niche: "  "` satisfies `min_length=2`; every required string field on every endpoint can be whitespace. Each one wastes an AI call + quota. No strip/blank validators in `models.py`; frontend only checks truthiness. *(backend H-2)*

### B-5. `save_history` failure turns a successful AI call into a 500
History logging is on the critical path (`finish()` → `save_history()` after success). A locked DB / full disk converts a good, quota-consuming result into an unrecoverable 500. Should be best-effort. *(backend H-4)*

### B-6. Zero test coverage of the real AI-client path
All 13 tests mock `ai_client.generate`. Quota logic, cache hit/miss/TTL, the fallback loop, and `_call`'s status-code branching are never executed. Any regression in `ai_client.py` — the highest-risk file — ships silently. *(backend H-5)*

### B-7. Loading indicator never renders
`callApi` clears `innerHTML`, which makes `.out:empty{display:none}` match, hiding the `.loading::after` spinner. **Zero feedback during 10–120s AI calls**; users re-click, burning quota (see B-8). *(frontend H-1)*

### B-8. No double-submit guard — every extra click burns scarce quota
Buttons are never disabled in-flight (a `button:disabled` style exists but is never used). Under the $0 hard constraint, accidental double-spend of the 48/day quota is the most expensive UX failure possible. *(frontend H-2)*

### B-9. Frontend ignores backend validation → cryptic `HTTP 422`
HTML `min`/`max` don't constrain typed values; frontend doesn't enforce ranges/lengths; `callApi` discards FastAPI's `detail` array and shows bare `Error: HTTP 422` with no guidance. *(frontend H-3/M-4)*

### B-10. SQLite won't persist on any $0 host
Render/Koyeb/HF free tiers all have ephemeral filesystems: `cios.db` (cache, usage counter, history, rate state) is wiped on every sleep/wake/redeploy. Consequences: the daily cap resets on wake, so a sleeping service can overshoot OpenRouter's 50/day limit. *(devops)*

---

## C. Medium (17)

| # | Finding | Source |
|---|---|---|
| C-1 | Prompt injection: raw user input f-stringed into prompts in all routers, no delimiters, no instruction-hierarchy guard; 4000-char `examples` field is the widest funnel; cache poisoning amplified by B-1 | security M1 |
| C-2 | Missing security headers: no CSP, HSTS, X-Frame-Options, X-Content-Type-Options, Referrer-Policy, TrustedHost | security M2 |
| C-3 | Information leakage: `str(exc)` echoes raw httpx/OpenRouter error text; `/api/health` exposes `key_configured` oracle + full model chain; `/api/history` leaks all past inputs/outputs | security M3 |
| C-4 | Privacy: every user input is POSTed to OpenRouter by design; no disclosure in the UI | security M4 |
| C-5 | `init_db()` at import time (`app.py:22`): side effects on import, creates real `cios.db` during test runs | backend M-1 |
| C-6 | SQLite concurrency: default journal mode, no WAL, no busy-timeout tuning → `database is locked` risk under concurrent writes | backend M-2 |
| C-7 | Blocking `time.sleep` + blocking `httpx.post` in sync endpoints; no client timeout communicated (frontend `fetch` has no abort) | backend M-3; frontend M-3; security L7 |
| C-8 | `brain_loader` silently swallows missing brain files → degraded system prompts with no log or error | backend M-4 |
| C-9 | Cache TTL purge does a full-table `DELETE` on every generation | backend M-5 |
| C-10 | History table grows without bound (full input + output per row, no pruning/retention) | backend M-6 |
| C-11 | Zero logging anywhere — no request logs, no model-served logs, no quota/cache diagnostics | backend M-8 |
| C-12 | Enter key doesn't submit (no `<form>`); validation via blocking `alert()` | frontend M-1/M-2 |
| C-13 | No request timeout/cancel in frontend; hung free-model calls trap the user | frontend M-3 |
| C-14 | Results not announced to assistive tech (no `aria-live`); tabs lack `role=tab` | frontend M-5 |
| C-15 | Pydantic validation is shallow beyond whitespace (see B-4) | security L8 |
| C-16 | `http_referer` hardcodes a third-party GitHub URL (`config.py:49`) sent on every OpenRouter call | backend L-1 |
| C-17 | Unpinned `requirements.txt` (`fastapi>=0.115`…); fresh installs drift from tested versions; test suite emits a `StarletteDeprecationWarning` (httpx2) | backend L-5; security L6 |

---

## D. Low (20, summarized)

- `/api/health` info exposure (model chain, key oracle) — fine locally, gate when hosted *(backend L-2, security)*
- Frontend `esc()` misses single quotes; no `maxlength` on inputs *(backend L-3)*
- `{"text": raw}` fallback contract ambiguity *(backend L-4)*
- New `httpx.post` per attempt, no connection reuse *(backend L-6)*
- `MissingApiKey` checked per-model instead of once up front *(backend L-7)*
- XSS currently mitigated (`esc()` everywhere) but single-layer, no CSP backstop *(security L1)*
- CSRF n/a today (no cookies) — **must add when auth lands** *(security L2)*
- CORS correctly absent — keep as-is while same-origin *(security L3)*
- SQL injection: none — all queries parameterized *(security L4 — cleared)*
- Secrets handling clean (no hardcoded keys, `.env` gitignored/absent, key never logged); `cios.db` perms `rw-rw----` *(security L5)*
- Dependencies: `pip-audit` clean on all app deps (fastapi 0.141.1, uvicorn 0.53.0, starlette 1.6.0, httpx 0.28.1, pydantic 2.13.5); only `pip 24.0` itself flagged (12 PYSEC, toolchain-only) *(security L6)*
- Usage pill silent-failure (`…` stuck); footer dangling `•` on health failure; history refetch on every tab click; no `autocomplete`; dead `button:disabled` rule *(frontend L-1…L-5)*
- No `eval`/`exec`/subprocess/pickle; no path traversal; no open redirects *(security — cleared)*

---

## E. What the market study adds (from `MARKET-STUDY.md`)

CIOS's audit-relevant competitive gaps, ranked: **(1)** no real data connection (AI-only research); **(2)** no live outlier discovery; **(3)** no thumbnail generation; **(4)** no auth/multi-user/cloud — addressed in this plan; **(5–7)** no scheduling/publishing, no video output, no keyword demand data; **(8–10)** no onboarding, no docs, no idea library/bookmarks. The study's defensible niche for CIOS: **Roman-Urdu-native AI copilot**, **$0 free-tier architecture (48/day BYO-key beats every studied free tier)**, **methodology-backed prompts (12-system brain)**, **private by default**, **quota-transparent UX**. Recommended $0 feature ideas (Phase B): YouTube Autocomplete Keyword Explorer (free, keyless), RSS-based Outlier Tracker (1of10-lite), Idea Swipe File + 30-day Content Calendar. Recommended $0 deploy target: **Render free Web Service** (Koyeb backup); rejected: Railway ($1/mo floor), Fly.io (no free tier), Vercel (10s serverless timeout), Replit (30-day expiry). Neon free Postgres recommended for persistence when needed.

---

## G. Live OpenRouter smoke test — real-key findings (2026-09-18)

A real OpenRouter key was supplied transiently via env only (never stored; not in `.env`). Results:

- **G-1. `nvidia/nemotron-3-ultra-550b-a55b:free` VERIFIED working** — real Roman Urdu generation end-to-end. The `generate()` path works when HTTP works. (Positive — closes the "AI quality untested" caveat for the transport layer.)
- **G-2. `thinkingmachines/inkling-small:free` must be REMOVED from `model_chain`** — returns HTTP 403 "only available on agentic harnesses"; it can never work via plain chat/completions. Replaced with another verified `:free` model via `scripts/check_models.py`. (Validates audit A-1's warning that the chain contained unverified IDs.)
- **G-3. Defensive parsing required** — `openrouter/free` returned HTTP 200 with JSON not matching the `choices[0].message.content` schema. Parser hardened: shape-validated (dict → non-empty choices list → dict message → str content), any mismatch → skip model. Raw bodies stay server-side only (audit M-7).
- **G-4. httpx 0.28.1 proxy crash (this runtime)** — `Client()` construction crashes on the bracketed-IPv6 entries in `NO_PROXY` (`InvalidURL: Invalid port: ':1]'`), while direct TLS fails without the sandbox proxy. Fixed: `trust_env=False` + explicit `proxy=` from a validated `HTTPS_PROXY`/`HTTP_PROXY` env value (never logged — it embeds credentials), with a regression test. On Render (no proxy vars) the client runs direct; no behavior change there.
- **G-5. `google/gemma-4-31b-it:free` transient upstream 429** — handled by the fallback chain as designed; no action.

**Key handling note:** the test key was transient and discarded. No key material exists in the repo, `.env`, logs, or reports. Live-AI verification remains user-side (their own key in `.env` / Render dashboard).

---

## F. Verdict

The v1 is a **solid local prototype**: all 7 buttons wired, XSS escaped, SQL parameterized, secrets clean, 13/13 tests green. It is **not yet publishable**: any stale model ID 500s every feature (A-1), there is no auth and no abuse protection on a shared 48/day budget (B-1/B-2), quota accounting diverges from real provider usage (B-3), the UI gives zero feedback during 10–120s calls (B-7), and the DB evaporates on free hosts (B-10). All of these are fixed in the build phase per `SAAS-PLAN.md`.
