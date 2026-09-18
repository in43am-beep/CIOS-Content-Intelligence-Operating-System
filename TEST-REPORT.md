# CIOS — Phase 5 Test Report

**Date:** 2026-09-18 · **Engineer:** test-engineer subagent · **Scope:** full independent re-verification of the SaaS build (FastAPI + vanilla-JS, SQLite)
**Method:** pytest re-run → live uvicorn exercise on port 8123 (3 runs) → in-process 500 probe → button-by-button static audit of `frontend/app.js` + `frontend/index.html` → `pip-audit`.

**Known limits (honest):** (1) No real OpenRouter key exists (never stored) → all live AI calls were **mocked** by patching `ai_client._call` to return canned JSON in the test-server process; the transport path to OpenRouter was verified earlier with a real key (AUDIT-REPORT §G, finding G-1) and is NOT re-verified here. (2) No live browser available → button verification is static JS audit + live HTTP exercise of each button's exact request shape, **not** clicked-button testing.

---

## 1. pytest — 59 passed, 0 failed (8.29s)

`cd ~/workspace/cios && .venv/bin/python -m pytest tests/ -q` → **59 passed** (exceeds the plan's 30-test gate).

| File | Tests | Coverage highlights |
|---|---|---|
| `tests/test_api.py` | 29 | health (auth/unauth), all 6 endpoints, validation, whitespace, delimiters, history isolation, quota→429, missing-key→400, 500 shape, history-failure tolerance, usage, security headers, per-IP throttle, admin CRUD + 403s |
| `tests/test_auth.py` | 15 | signup (admin-first, duplicate, weak pw, bad email), login ok/wrong/unknown, me ok/no-token/bad-token/wrong-scheme/expired, POST 401s, password hashing |
| `tests/test_ai_client.py` | 15 | missing-key upfront, fallback chain (404→model2, 500), all-fail friendly, atomic quota, cache hit/per-user/before-quota, pacing, malformed-200 skip, empty-choices skip, proxy URL validation ×3, **bracketed-IPv6 proxy regression** |

Warnings (non-blocking): `StarletteDeprecationWarning` (httpx vs httpx2 in TestClient), `InsecureKeyLengthWarning` from the 19-byte **test-only** JWT secret.

---

## 2. Live endpoint exercise (uvicorn, port 8123, `AUTH_REQUIRED=true`, `JWT_SECRET=a-long-test-secret-32-chars-min`)

| Run | Env | DB | Result |
|---|---|---|---|
| A — auth/validation/admin/throttle/static | no `OPENROUTER_API_KEY` | `/tmp/cios_test.db` (deleted after) | **47/47 passed** |
| B — mocked-AI success + isolation + quota override | `MOCK_AI=1` (patched `ai_client._call` → canned JSON), `OPENROUTER_API_KEY=dummy-test-key` | `/tmp/cios_test2.db` (deleted after) | **16/16 passed** |
| C — quota via env | `MOCK_AI=1`, `DAILY_REQUEST_CAP=1` | `/tmp/cios_test4.db` (deleted after) | **4/4 passed** |
| 500-shape probe | in-process `TestClient`, temp route raising `RuntimeError("…marker…")` | `/tmp/cios_test3.db` (deleted after) | shape **PASS**; 1 header gap found (F-1) |

### Per-endpoint results

| Route | Cases exercised | Result |
|---|---|---|
| `POST /api/auth/signup` | 200 + token; first user `is_admin=true`; second user `false`; duplicate → 400 `{ok:false}`; weak pw → 400 | **PASS** |
| `POST /api/auth/login` | ok → 200 + token; wrong password → 401 `{ok:false}` | **PASS** |
| `GET /api/auth/me` | token → 200; no token → 401; garbage token → 401 | **PASS** |
| `POST /api/ideas|research|scripts|packaging|seo|niche` | mocked AI → 200 `{ok:true, model, data}` (all 6, Run B); no token → 401; no key → 400 friendly (all 6, Run A, **not** 500); whitespace-only → 422; `count=100` / `duration_sec=5` → 422; malformed JSON body → 422 | **PASS** |
| `POST /api/ideas` (missing-key path) | token set, no `OPENROUTER_API_KEY` → 400 with Roman-Urdu message pointing at `openrouter.ai/keys` | **PASS** |
| Quota | `DAILY_REQUEST_CAP=1` (Run C): 1st gen 200, 2nd → 429 `{ok:false}` friendly message; admin override `cap=1` (Run B): same; `/api/usage` → `{used:1, cap:1}` | **PASS** |
| `GET /api/history` | per-user isolation with real rows: A saw only A's 1 row, B saw only B's 6 rows, rows carry `user_id` (Run B) | **PASS** |
| `GET /api/usage` | `{ok, used, cap}` per user | **PASS** |
| `GET /api/health` | unauth → `{ok:true, version, key_configured}` **without** model chain; authed → includes `models` | **PASS** |
| Security headers | `Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self'`, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin` present on 200/400/401/422/429/404 + `/` + `/static/*` | **PASS** (1 gap: F-1) |
| `GET /api/admin/users`, `/api/admin/usage`, `POST /api/admin/quota` | non-admin → 403; no token → 401; admin → 200 list/rows; override persisted (`cap=5` visible in users list); unknown user → 404 | **PASS** |
| Per-IP throttle (30/min) | 31 rapid `POST /api/ideas` → last is 429 `{ok:false, error:"Bohat zyada requests — ek minute ruk kar try karo."}`; 429 also carries CSP header | **PASS** |
| `GET /` + `/static/app.js` + `/static/styles.css` | 200; served HTML contains **zero** `onclick=` (CSP-safe); `app.js`/`styles.css` serve 200 | **PASS** |
| `GET /no-such-route` | 404 JSON `{"detail":"Not Found"}` — not HTML | **PASS** |
| Generic 500 handler | `{"ok":false,"error":"Server error"}`; marker string from the raised exception **absent** from body (no raw leakage) | **PASS** (shape) |

---

## 3. Button-by-button audit (`frontend/app.js` + `frontend/index.html`)

No live browser → each control was traced handler → `fetch` URL/method/body → matched against the live-tested endpoint contract; loading/disabled/error paths checked in code. 18 listeners found (8 `click` + 10 `submit`); `btn.disabled=true` ×5, `aria-disabled` ×10; `innerHTML` writes all go through `esc()`/`render()` or static strings; zero `onclick=`; zero `alert()` calls (one comment mention only).

| # | Control | Handler → request | Shape matches live contract | Loading | Disabled in-flight | Error paths | Verdict |
|---|---|---|---|---|---|---|---|
| 1 | Login tab / Signup tab (`authtab-*`) | `wireAuthTabs` — UI-only toggle | n/a (no request) | n/a | n/a | n/a | **PASS** |
| 2 | `login-btn` (`login-form` submit, Enter works) | `doAuth("login")` → `POST /api/auth/login` `{email,password}` | ✅ = `LoginRequest` | btn disabled + `aria-disabled` | ✅ | inline field errors; 422 → `friendly422`; network → inline | **PASS** |
| 3 | `signup-btn` (`signup-form` submit) | `doAuth("signup")` → `POST /api/auth/signup` `{email,password}` | ✅ = `SignupRequest` | same as login | ✅ | same as login (see F-2) | **PASS** |
| 4 | `logout-btn` | `logout()` — clears token + form fields, shows auth view | n/a (local) | n/a | n/a | n/a | **PASS** |
| 5 | Usage pill (`#usage`, is a `<button>`) | `refreshUsage()` → `GET /api/usage` | ✅ `{used,cap}` | — | — | failure → "⚡ retry karo" (not silent); warn class ≥80% | **PASS** |
| 6–11 | 6 feature submit buttons (`btn-ideas` … `btn-niche`; Enter submits via `<form>`) | `runX()` → client validation (`checkLen`/`checkInt` mirror backend ranges) → `callApi` → `POST /api/{ideas,research,scripts,packaging,seo,niche}` with `{niche,audience,count}` / `{niche,examples}` / `{topic,duration_sec,audience}` / `{topic,audience}` / `{title,topic}` / `{niche}` | ✅ all six match `models.py` + routers (live-tested 200 & 400 & 422) | real text node "⏳ soch raha hai…" (`.out:empty` never matches — B-7 fix) | ✅ `if (btn.disabled) return` + `disabled`/`aria-disabled`; cancel button revealed | 401→logout+login view; 403→⛔; 422→`friendly422` inline; key-missing→setup box; abort→cancel msg; network→inline; `finally` re-enables + `refreshUsage()` | **PASS** |
| 12 | 6 cancel buttons (`data-cancel`) | `wireCancelButtons` → `AbortController.abort()` | n/a | n/a | n/a | AbortError → "⏹️ Request cancel…" message | **PASS** |
| 13 | 8 nav tabs (`nav-ideas`…`nav-admin`) | `wireTabs` — `role=tab`/`aria-selected` toggling; admin tab `hidden` unless `is_admin` | n/a | n/a | n/a | n/a | **PASS** |
| 14 | `history-refresh` | `loadHistory(true)` → `GET /api/history` | ✅ `{items[]}` | ✅ | — | 401→logout; errors → `showErr`; single-load guard (`historyLoaded`) | **PASS** |
| 15 | `admin-users-btn` | `loadAdminUsers()` → `GET /api/admin/users` | ✅ `{users[]}` | ✅ | ✅ | 403→"⛔ Sirf admin…"; table via escaped `adminTable` | **PASS** |
| 16 | `admin-usage-btn` (`form-admin-usage` submit) | `loadAdminUsage()` → `GET /api/admin/usage?day=YYYY-MM-DD` (date regex-validated) | ✅ `{day,rows[]}` | ✅ | ✅ | 403→⛔; bad date→inline | **PASS** |
| 17 | `btn-quota` (`form-quota` submit) | `updateQuota()` → `POST /api/admin/quota` `{user_id, cap}` | ✅ = `QuotaOverrideRequest` (`user_id` string coerced by pydantic; non-numeric → 422 → `friendly422`) | ✅ | ✅ | 403/404/422 handled inline; success → ✅ message (escaped) | **PASS** |

`esc()` spot-check: covers `& < > "` (single quote not escaped — harmless in element-content context, no attribute sinks; matches audit L1). `render()` recurses objects/arrays; `meta` line, history rows, admin table, quota message all escaped.

---

## 4. pip-audit

`pip-audit` 2.10.1 on the venv: **12 findings, all against `pip 24.0` itself** (PYSEC-2026-1795/1796/2875/2876/196/3721; fix ≥ 25.3/26.x) — installer toolchain only, not a runtime dependency. **All application runtime deps clean** (fastapi 0.141.1, uvicorn 0.53.0, starlette 1.6.0, httpx 0.28.1, pydantic 2.13.5, PyJWT 2.14.0, pwdlib 0.3.1, argon2-cffi 25.1.0). `requirements.txt` is pinned (`==`). Matches the audit's L6 note exactly.

---

## 5. New findings from this phase

| ID | Severity | Finding | Evidence | Suggested fix |
|---|---|---|---|---|
| F-1 | **Low** | Generic-500 response (`@app.exception_handler(Exception)`) carries **no** security headers (no CSP/XFO/nosniff/Referrer-Policy). All other error responses (401/400/422/429/404) and the throttle-429 DO carry them. | In-process probe: `/_boom2` → 500 `{"ok":false,"error":"Server error"}` with headers `{}`; `/_boom3` with a *specific* `RuntimeError` handler → headers present. Root cause: Starlette splits the `Exception`-keyed handler into `ServerErrorMiddleware(handler=…)` — the **outermost** layer (`starlette/applications.py:65-78`, `middleware/errors.py:173-178`) — so its response never passes back through `SecurityHeadersMiddleware`. Body is safe (no leak); this is defense-in-depth only. | Set the four headers explicitly inside `unhandled_handler`, or replace `BaseHTTPMiddleware` with pure-ASGI middleware (which sees exception responses). |
| F-2 | Trivial | Signup password copy mismatch: frontend `doAuth` enforces ≥6 chars and the HTML placeholder says "kam az kam 6 characters", but backend requires ≥8 → 400. Handled gracefully (inline error), but the client check disagrees with the server. | `frontend/app.js` `doAuth` (`pass.length < 6`) vs `auth.py` `signup` (`len(body.password) < 8`) | Align frontend to 8 (and the placeholder). |
| F-3 | Trivial | No `maxlength` attributes on text inputs (plan §5 listed them; audit L-3). Backend `max_length` + JS `checkLen` enforce limits; this is defense-in-depth only. | `grep -c maxlength frontend/index.html` → 0 | Add `maxlength` matching `models.py` bounds. |
| F-4 | Trivial | Stale copy: cancel/timeout message says "quota sirf kamyab calls pe kharch hota hai" — accounting has been attempt-based since the B-3 fix. | `frontend/app.js` `callApi` AbortError branch | Reword to "quota har try pe kharch hota hai". |

---

## 6. Smoke-test findings G-1…G-5 (folded in)

- **G-1** (nemotron verified with real key): prior result stands; this phase had no key, so live AI stayed mocked. Model chain order unchanged (`config.py`).
- **G-2** (inkling removed): **re-verified** — `inkling` appears nowhere in `config.py`'s `model_chain` or any `.py` file (only in historical audit docs); `config.py` comments document the removal.
- **G-3** (defensive parsing): **re-verified by tests** — `ai_client.py:169-190` shape-validates `choices[0].message.content`; `test_malformed_200_shapes_skip_to_next_model` + `test_empty_choices_skipped` pass.
- **G-4** (httpx proxy crash): **re-verified by test** — `test_http_client_constructs_with_bracketed_no_proxy` passes; **independently reproduced** this phase (my own httpx test-client crashed on the sandbox's bracketed-IPv6 `NO_PROXY`; worked with `trust_env=False`).
- **G-5** (gemma transient 429 → fallback): **re-verified by tests** — `test_fallback_404_then_model2_serves`, `test_retryable_500_also_falls_through_chain` pass.

---

## 7. Acceptance criteria (SAAS-PLAN §13) — status

- [x] `pytest`: 30+ tests, 100% green → **59/59**
- [x] Every Critical + High audit item fixed and re-verified → see `SECURITY-CHECKLIST.md` (B-10 accepted/documented by design)
- [x] Every dashboard button: wired, loading, error, double-submit safe → §3 table (17/17 PASS; no live clicking — static + HTTP)
- [x] Auth: signup/login/me, per-user quota/history, admin view, non-admin blocked → live-verified
- [x] Security headers present; no raw provider errors; deps pinned + pip-audit clean → headers verified (F-1 gap noted); no raw leakage; pinned + clean
- [x] `Dockerfile` + `render.yaml` present; local uvicorn run verified → present; 3 local runs done
- [~] README run + publish steps → file present; content **not** re-verified this phase (out of scope for test engineer)
- [x] `TEST-REPORT.md` + `SECURITY-CHECKLIST.md` written; known limit documented → this file + sibling; limits in the header above

**Cleanup:** test server killed; `/tmp/cios_test*.db*` deleted. Repo `cios.db` untouched by tests (all runs used `/tmp` DBs; the pre-existing `cios.db` was never written by this phase).

---

# 8. v2 verification — Media Studio (ai33.pro) + Avatar Studio (HeyGen)

**Date:** 2026-09-18 · **Engineer:** v2 integration + launch-audit subagent · **Scope:** reconcile builders A/B/D, re-run everything, run the 7 launch gates, extend docs.
**Method:** pytest re-run → live uvicorn exercise on port 8124 with ALL external transports mocked (no real `api.ai33.pro` / `api.heygen.com` / OpenRouter calls — $0 constraint) → static button-by-button audit of `frontend/media-studio.js` + `frontend/avatar-studio.js` → `pip-audit`.
**Constraint honored:** zero real external API hits. Mocks assert the target host on every call, so a stray real-network call would fail loudly, not silently pass.

## 8.1 pytest — 144 passed, 0 failed (24.6s)

`cd ~/workspace/cios && .venv/bin/python -m pytest tests/ -q` → **144 passed** (v1 phase: 59; +85 new).

| File | Tests | v2 coverage highlights |
|---|---|---|
| `tests/test_media.py` | 35 | Fernet roundtrip + wrong-secret-as-missing; key never in logs (`caplog`); 401 unauth; verify-first 401→400 not stored; save/delete cycle; credits normalization; voices provider-required 422 / bad-provider 422 / list+6h cache; TTS create→poll `pending→processing→completed`; TTS validation (20k, speed, prefix); `success:false` mapping; ai33 401/429/credits-exhausted mappings; dialogue ok + bad-label 422; voice-clone upload + delete + non-`clone_` reject + 413 oversize; music modes; image models; video models; task list + delete(refunded); unknown status→`processing`; daily create cap 429; TTS chars cap 429; caps per-user; **proxy-safe construction (bracketed IPv6 NO_PROXY)**; timeout mapping; malformed-JSON mapping; real-network-impossible guard |
| `tests/test_avatar.py` | 36 | key save verify-first + 401/403→400 not stored; key status/delete; key length validation; keystore-missing→503 (D's defensive path); **all 11 routes require auth**; looks-list normalization; voices `preview_audio_url`; key-required-before-listing; create body shape (`type:"avatar"`, defaults omit optionals); daily cap 3; cap counts agents too; create validation (script ≤1500, prompt ≤10000); poll `pending→processing→completed`; unknown status never completed; `failed` mapping; poll upserts `avatar_jobs`; history per-user namespaced; live-enrichment best-effort + failure→200; delete video; agent create sends `mode:"generate"`; agent status→video; error catalog: 401/402/429/moderation-4xx/invalid-avatar-400/HeyGen-500/network-timeout/malformed-JSON; proxy regression tests |
| `tests/test_v1.py` | 14 | old `/api/*` prefix 404s; `X-Request-ID` on 200/401/422/throttle-429/unhandled-500; request-id in server logs; idempotency key logged; envelope on signup/login/me/history/usage/admin; AI-result shape kept; health shape kept; 422 shape kept |
| `tests/test_api.py` | 29 | (v1 — unchanged, still green) |
| `tests/test_auth.py` | 15 | (v1 — unchanged, still green) |
| `tests/test_ai_client.py` | 15 | (v1 — unchanged, still green) |

Warnings (non-blocking): same `StarletteDeprecationWarning` + test-only short-JWT-secret warnings as the v1 phase.

## 8.2 Live endpoint exercise (uvicorn, port 8124, `AUTH_REQUIRED=true`, `JWT_SECRET=long-test-secret-32c`, `DB_PATH=/tmp/cios_v2test.db`, no real keys)

Launcher mocked `ai_client.generate`, `routers.media._http` (host-asserting fake), and `routers.avatar._client` (host-asserting `httpx.MockTransport`). **59/59 checks passed.** Server killed afterwards; `/tmp/cios_v2test.db*` deleted.

| Area | Checks | Result |
|---|---|---|
| Old prefix / health | `GET /api/health` → 404; `GET /api/v1/health` → 200 + `X-Request-ID`; envelope `{ok:true}`; body contains no `sk_`/secret/api_key material | **59/59 PASS** |
| Auth | signup → 200; first user `is_admin=true`; second user `false`; login → token; `me` → 200 | PASS |
| AI (mocked) | `POST /api/v1/ideas` → 200 envelope `{ok,model,cached,data}` + `X-Request-ID`; whitespace → 422; no token → 401 | PASS |
| Media (mocked ai33) | bad key → 400 **not stored** (`has_key:false` after); good key → stored encrypted, `credits:4321`, key absent from body; voices w/o provider → 422; bad provider → 422; voices list ok; TTS → `task_id`; poll `pending→processing→completed` with `audio_url`+`transcript`; tasks list; task delete → `{deleted:true, refunded:true}` | PASS |
| Avatar (mocked HeyGen) | bad key → 400 not stored; good key stored (absent from body); looks list w/ `preview_image_url`; voices w/ `preview_audio_url`; create → `video_id`; poll → `completed` w/ `video_url`; history lists own job; **402 → `💳 HeyGen credits khatam…` (Roman Urdu)**; moderation 4xx → `🛡️…`; delete ok; user-B cannot see user-A's jobs | PASS |
| Key-leak grep | every collected response body scanned for both test keys (good+bad, both providers) → **absent everywhere** | PASS |
| Security | forced 500 → `{"ok":false}`, marker string absent, all 4 security headers + `X-Request-ID`; 31-POST burst → 429 with `X-Request-ID` | PASS |
| Static | `/` + `/static/app.js|media-studio.js|avatar-studio.js|styles.css|media-studio.css|avatar-studio.css` → 200; `index.html` has zero `onclick=`; `nav-media`/`nav-avatar` carry `hidden` until `window.CIOSMedia`/`window.CIOSAvatar` exist; `#studio-media`/`#studio-avatar` mount divs present; both studio scripts wired | PASS |

## 8.3 Per-new-route table (live-verified shapes against mocks)

Media (`/api/v1/media/*`, all JWT): `POST /key` (verify-first), `DELETE /key`, `GET /key` (bool only), `GET /status`, `GET /credits`, `GET /voices` (provider allowlist 7, 6h cache), `POST /tts`, `POST /dialogue`, `POST /voice-clone` (multipart, ≤10MB, audio/*), `DELETE /voice-clone/{id}` (`clone_` only), `POST /music`, `POST /image`, `POST /video`, `GET /tasks/{id}` (normalized `pending|processing|completed|failed`), `GET /tasks`, `POST /tasks/{id}/delete` (refund flag). — 16/16 exercised by pytest; 12/16 additionally live-exercised (music/image/video/dialogue/clone covered by pytest only — same `_run_ai33` path as live-verified TTS).

Avatar (`/api/v1/avatar/*`, all JWT): `POST /key`, `DELETE /key`, `GET /key`, `GET /avatars`, `GET /voices`, `POST /videos`, `GET /videos/{id}`, `GET /videos`, `DELETE /videos/{id}`, `POST /agents` (`mode:"generate"` fixed), `GET /agents/{session_id}`. — 11/11 exercised by pytest; 9/11 additionally live-exercised (agent create/status covered by pytest only).

## 8.4 Per-new-button table (static trace: control → handler → route)

**Media Studio** (`frontend/media-studio.js`, full-screen `#studio-media`): every control traced handler → `CIOSApi.api()` → live-tested route. Loading via `setBusy` (disabled + label), double-submit via `formKey` inflight map, errors inline `.ferr` + toasts, zero `alert()`.

| Control | Route | Loading | Double-submit | Verdict |
|---|---|---|---|---|
| Setup: `🔌 Connect & Verify` (+ key input) | `POST /api/v1/media/key` | ✅ | ✅ `media-key` | PASS |
| ← Wapas | `CIOSApp.closeStudio()` (local) | n/a | n/a | PASS |
| 7 studio tabs | UI-only switch | n/a | n/a | PASS |
| TTS `🎤 Generate` | `POST /api/v1/media/tts` | ✅ | ✅ `media-tts` | PASS |
| Dialogue `🗣️ Generate` | `POST /api/v1/media/dialogue` | ✅ | ✅ (formKey) | PASS |
| Clone `⬆️ Clone banao` | `POST /api/v1/media/voice-clone` (multipart) | ✅ | ✅ `media-clone` | PASS |
| `🔄 Clones list refresh` | `GET /api/v1/media/voices` (cache-busted) | ✅ | — (GET) | PASS |
| Clone `🗑️ Delete` (two-step inline) | `DELETE /api/v1/media/voice-clone/{id}` | ✅ | ✅ `media-clone-del` | PASS |
| Music/Image/Video `Generate` | `POST /api/v1/media/{music,image,video}` | ✅ | ✅ (formKey each) | PASS |
| Tasks `🔄 Refresh` | `GET /api/v1/media/tasks` | ✅ | — (GET) | PASS |
| Task `🗑️ Delete (credits wapas milenge)` | `POST /api/v1/media/tasks/{id}/delete` | ✅ | ✅ (formKey) | PASS |
| Poll loops | `GET /api/v1/media/tasks/{id}` 5s→10s backoff, 100 attempts | progress text | — (GET) | PASS |

**Avatar Studio** (`frontend/avatar-studio.js`, full-screen `#studio-avatar`): every control traced. Loading via `CIOSApp.setBtn` (shared A state machine), key field cleared after save (Gate 4), zero `alert()`, two-step inline confirms (no `confirm()`).

| Control | Route | Loading | Double-submit | Verdict |
|---|---|---|---|---|
| Setup: `Connect`/`Verify` (+ key input) | `POST /api/v1/avatar/key` | ✅ | ✅ `avatar-key` | PASS |
| `🔌 Disconnect` (two-step) | `DELETE /api/v1/avatar/key` | ✅ | ✅ | PASS |
| `← Wapas` (**added by integration**, was missing → dead-end) | `CIOSApp.closeStudio("avatar")` | n/a | n/a | PASS (fixed) |
| `🔄 Avatars lao` | `GET /api/v1/avatar/avatars` | ✅ | — (GET) | PASS |
| `🔄 Voices lao` | `GET /api/v1/avatar/voices` | ✅ | — (GET) | PASS |
| Avatar card / voice `▶` preview | UI select / direct `preview_audio_url` `<audio>` | n/a | n/a | PASS |
| `🎬 Video banao` (`<form>` submit → Enter works) | `POST /api/v1/avatar/videos` | ✅ | ✅ `avatar-create-*` | PASS |
| `🤖 Agent se video banao` (`<form>` submit) | `POST /api/v1/avatar/agents` → poll `GET /agents/{sid}` | ✅ | ✅ (formKey) | PASS |
| Poll loops | `GET /api/v1/avatar/videos/{id}` 5s, 60 attempts | progress + attempt count | — (GET) | PASS |
| History `🔄` refresh | `GET /api/v1/avatar/videos` | ✅ | — (GET) | PASS |
| History delete (two-step) | `DELETE /api/v1/avatar/videos/{id}` | ✅ | ✅ | PASS |

Honest-label audit: no enabled-but-dead control found. Without a key, sections render disabled with one-line explanations; phase-2 items (pronunciation dictionaries, ElevenLabs-compat, webhooks) have no UI controls — code comments + README only. Minor deviation: Media Studio buttons are `type="button"` (not `<form onsubmit>`), so Enter-submits applies only via Tab-focus; Avatar Studio uses real forms. Low severity.

## 8.5 Seven gates — summary (full evidence in §8.6 of SECURITY-CHECKLIST.md)

| Gate | Verdict | One-line evidence |
|---|---|---|
| G1 Crash paths & live deps | **PASS** | ai33 401/402/429/5xx/timeout/malformed-JSON/`success:false` + HeyGen 401/402/429/moderation/timeout/malformed-JSON all map to Roman-Urdu (never raw 500); unknown poll statuses → in-progress, never completed; endpoint IDs verified against official docs 2026-09-18 (V2-DESIGN §B0/§D) — real APIs never hit per $0 constraint |
| G2 Auth & abuse | **PASS** | AST scan: all 27 media/avatar routes carry `get_current_user`; caps atomic single `UPDATE…WHERE` (media) / per-(user,day,provider) (avatar), spent BEFORE provider calls, reads excluded; `api()` never auto-retries POST (app.js:106-110,188-190) + `formKey` inflight map; `THROTTLE_PATHS` covers 6 AI + 6 media + 2 avatar create paths |
| G3 Input validation | **PASS** | Server: TTS ≤20000, speed 0.5–1.5, voice prefix `_`, provider allowlist (7), clone ≤10MB + audio/*, script ≤1500, agent prompt ≤10000; client mirrors via `maxlength`/JS constants; 422 → inline friendly text |
| G4 Secrets | **PASS** | No hardcoded keys; Fernet+HKDF at rest; `caplog` test asserts key absent from logs; live grep: test keys absent from all 59 response bodies; proxy URL never logged (var name only); key field cleared post-save in UI |
| G5 HTTP client safety | **PASS** | Bracketed-IPv6 `NO_PROXY` regression tests for `ai33` + `heygen` clients (test_media.py:592, test_avatar.py:709); 60s timeout on both; `trust_env=False` + validated `proxy=` |
| G6 Frontend QA | **PASS (1 minor)** | 22 studio controls traced handler→route (§8.4); loading renders; double-submit guards on all spending actions; zero `alert()`; no fake buttons; minor: media studio uses `type="button"` not `<form onsubmit>` (Enter via Tab-focus) |
| G7 Deploy reality | **PASS** | Deps pinned (`cryptography==50.0.1`, `python-multipart==0.0.32`), pip-audit clean (only pre-existing `pip 24.0` toolchain findings); 4 security headers incl. forced-500 path (live-verified); health leaks nothing; `render.yaml` health → `/api/v1/health`; ephemeral-DB caveat extended to v2 tables (README) |

## 8.6 Reconciliations made by the integration agent

1. **`frontend/avatar-studio.js` + `frontend/avatar-studio.css` — ADDED `← Wapas` back button.** Builder D shipped no back control while `app.js` hides `#app-view` on studio open → users were trapped in Avatar Studio. Button calls `window.CIOSApp.closeStudio("avatar")` with a self-contained fallback. (Real bug, user-facing.)
2. **`provider_keys.py` — removed import-time `ensure_tables()`; lazy ensure inside the 4 public functions.** Import-time DDL wrote to the repo's `cios.db` on every pytest run (C-5 regression). Verified: imports no longer touch the repo DB.
3. **`routers/avatar.py` — removed import-time `init_avatar_tables()`; lazy ensure at the 3 `avatar_jobs` touchpoints** (`_record_job`, `list_videos`, `delete_video`). Same C-5 reason. (Also repaired an accidental deletion of the `CreateCapExceeded` class mid-edit — caught by syntax check, restored, tests re-green.)
4. **`app.py` lifespan — defensive `ensure_tables()` + `init_avatar_tables()` at startup** (startup determinism; mirrors the existing defensive router includes).
5. **Repo `cios.db` restored:** dropped the 3 empty v2 tables (`provider_keys`, `media_usage`, `avatar_jobs`) that earlier import/test side-effects had created; re-ran pytest → DB still untouched.
6. **`README.md` — ephemeral-disk caveat extended** to name v2 tables (provider keys lost on restart → re-connect needed).
7. **Left as-is (noted):** media config constants duplicated in `routers/media.py` vs `config.py` (same defaults; centralizing not worth the churn); nav/mount IDs differ from V2-DESIGN §0.1 (`nav-media` vs `nav-studio`, `#studio-media`/`#studio-avatar` vs `#studio-shell`/`#avatar-root`) but are internally consistent across `index.html` + `app.js` + both studio scripts — this report's checklist names the built IDs.

## 8.7 Known limits (honest)

- No real `api.ai33.pro` / `api.heygen.com` / OpenRouter calls — all live checks used host-asserting mocks. Endpoint-ID liveness rests on the 2026-09-18 docs verification (V2-DESIGN §B0/§D); free-model/API rotation can invalidate it — re-check before/after publish.
- No live browser: button verification is static trace + live HTTP of each button's exact request shape, not clicked-button testing (same method as the v1 phase).
- `scripts/frontend_smoke.py` from the design was not written by Builder A — the static assertions it would have run (`alert(`, single `fetch` wrapper, retry policy, toast/print nodes) were performed manually in this phase instead.
