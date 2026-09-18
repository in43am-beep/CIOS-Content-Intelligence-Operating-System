# CIOS — Security Checklist (audit → verification)

**Date:** 2026-09-18 · **Basis:** `AUDIT-REPORT.md` (§A–D), `audit/security-findings.md` (H1–H2, M1–M4, L1–L8), smoke findings G-1…G-5.
**How verified:** `T`= named pytest (59/59 green) · `L`= live uvicorn exercise on 127.0.0.1:8123 (Runs A/B/C) · `C`= code read w/ file:line · `P`= in-process probe · `S`= static frontend audit.

## A. Critical

| # | Item | Status | How verified |
|---|---|---|---|
| A-1 | Stale model ID 500s every feature; no fallback; no 500 handler | **FIXED / VERIFIED** | `T`: `test_fallback_404_then_model2_serves`, `test_all_models_fail_raises_friendly`, `test_generic_500_shape_never_echoes_raw` · `C`: any non-200 → `_SkipModel`, chain continues (`ai_client.py:148-165`); generic 500 → `{ok:false,"Server error"}` (`app.py:191-194`) · `L`: missing-key path returned 400, never 500 (Run A) |
| A-2 | Quota check-then-act race → cap overshoot | **FIXED / VERIFIED** | `T`: `test_quota_cap_enforced_atomically` · `C`: single `UPDATE … WHERE count < cap` + `rowcount` check (`ai_client.py:104-118`) · `L`: `DAILY_REQUEST_CAP=1` → 1st gen 200, 2nd 429 (Run C) |
| A-3 | Pacing race → 20 RPM guard unenforced | **FIXED / VERIFIED** | `T`: `test_pace_updates_last_call_at` · `C`: slot reserved read+update in one transaction, monotonic clock, `threading.Event.wait()` outside the lock (`ai_client.py:123-134`) · `L`: sequential gens spaced ~3.5s apart (Runs B/C) |

## B. High

| # | Item | Status | How verified |
|---|---|---|---|
| B-1 | No auth on any endpoint | **FIXED / VERIFIED** | `T`: `test_post_without_token_401`, `test_me_no_token_401`, `test_admin_endpoints_non_admin_403` (+12 more auth tests) · `L`: no-token → 401 on `POST /api/ideas`, `/api/auth/me`, `/api/admin/users`; JWT signup/login/me round-trip (Run A) · `C`: `get_current_user` dep on all AI/history/usage routes, `get_admin_user` on admin (`auth.py`, `app.py`, `routers/admin.py`) |
| B-2 | Quota trivially weaponizable (no throttle) | **FIXED / VERIFIED** | `T`: `test_per_ip_throttle_429` · `L`: 31 rapid POSTs → 429 `{ok:false}` with throttle message (Run A) · `C`: 30 req/min/IP on the 6 POST paths, in-memory (`app.py:118-150`); single-worker constraint documented in plan/README |
| B-3 | Quota counts only successes, diverges from provider | **FIXED / VERIFIED** | `T`: `test_quota_cap_enforced_atomically` · `C`: `_spend_quota` called per model *attempt* inside the chain loop (`ai_client.py`) · `L`: cap=1 exhausted after exactly one generation attempt (Runs B/C) |
| B-4 | Whitespace-only input passes validation | **FIXED / VERIFIED** | `T`: `test_whitespace_rejected` · `L`: `{"niche":"   "}` → 422 (Run A) · `C`: `StripModel` strips every string before `min_length` (`models.py:10-22`) |
| B-5 | `save_history` failure → 500 on success | **FIXED / VERIFIED** | `T`: `test_save_history_failure_does_not_500` · `C`: best-effort try/except in `finish()` (`routers/__init__.py:33-38`) |
| B-6 | Zero coverage of the real AI-client path | **FIXED / VERIFIED** | `T`: `tests/test_ai_client.py` — 15 tests (quota, cache, fallback, pacing, parsing, proxy) all green; suite total 59 |
| B-7 | Loading indicator never renders | **FIXED / VERIFIED** | `S`: `setLoading` writes a real text node (`app.js`), so `.out:empty` never matches · `C`: `.loading` class + placeholder copy |
| B-8 | No double-submit guard | **FIXED / VERIFIED** | `S`: `if (btn.disabled) return` + `disabled`/`aria-disabled` set in `callApi`, `doAuth`, and all 3 admin fns; re-enabled in `finally` (`app.js`: 5× `btn.disabled = true`, 10× `aria-disabled`) |
| B-9 | Frontend ignores backend validation (cryptic 422) | **FIXED / VERIFIED** | `S`: `checkLen`/`checkInt` mirror backend ranges; `friendly422` parses FastAPI `detail` into inline Roman-Urdu messages (`app.js`) · `L`: backend 422 shapes confirmed (Run A). Minor: signup pw copy says 6, backend needs 8 (TEST-REPORT F-2, trivial) |
| B-10 | SQLite won't persist on $0 hosts | **DOCUMENTED (accepted)** | `C`: ephemeral-DB consequences documented for Render/Koyeb/HF; `DATABASE_URL`→Neon migration path planned (Phase C). Not "fixable" under the $0 constraint; blast radius bounded by OpenRouter's own 50/day wall + attempt-based accounting. Publish checklist keeps `AUTH_REQUIRED=true` |

## C. Medium

| # | Item | Status | How verified |
|---|---|---|---|
| C-1 | Prompt injection: raw f-string interpolation | **MITIGATED / VERIFIED** | `T`: `test_user_prompt_wrapped_in_delimiters` · `C`: `wrap_user` delimiters `<<<USER_INPUT>>>…<<<END>>>` (`routers/__init__.py:21-23`); instruction-hierarchy guard in `brain_loader.py:18-23`. (Inherent to LLM apps; documented as best-effort) |
| C-2 | Missing security headers | **FIXED, 1 LOW GAP** | `T`: `test_security_headers_present` · `L`: CSP / XFO / nosniff / Referrer-Policy on 200/400/401/422/429/404 + `/` + `/static/*` (Run A) · **Gap (TEST-REPORT F-1):** generic-500 response lacks headers — Starlette routes the `Exception`-keyed handler to outermost `ServerErrorMiddleware` (`starlette/applications.py:65-78`), bypassing `SecurityHeadersMiddleware` (`P` probe). Body is safe; defense-in-depth only. Fix: set headers in `unhandled_handler` |
| C-3 | Info leakage (`str(exc)`, health oracle, history) | **FIXED / VERIFIED** | `T`: `test_generic_500_shape_never_echoes_raw` · `L`: `/api/health` unauth hides `models` (Run A); missing-key/500 bodies are friendly Roman-Urdu, no provider text · `C`: raw bodies only in server logs (`ai_client.py:155,161`) |
| C-4 | No privacy disclosure for OpenRouter data flow | **FIXED / VERIFIED** | `S`: footer line in `frontend/index.html`: "🔒 Privacy: aapke inputs OpenRouter ko bheje jate hain (aapki apni API key se)…" |
| C-5 | `init_db()` at import time | **FIXED / VERIFIED** | `C`: lifespan handler (`app.py:44-51`); `__main__` guard; tests use tmp DBs via env |
| C-6 | SQLite concurrency (no WAL/busy-timeout) | **FIXED / VERIFIED** | `C`: `PRAGMA journal_mode=WAL` + `busy_timeout=5000` in `get_conn` (`database.py:33-37`) |
| C-7 | Blocking sleep, no client timeout | **MITIGATED / VERIFIED** | `C`: pacing via interruptible `threading.Event.wait()`; shared `httpx.Client(timeout=120)`; frontend `AbortController` 125s + cancel buttons (`app.js`). Sync endpoints retained by design (single worker) |
| C-8 | `brain_loader` swallows missing files | **FIXED / VERIFIED** | `C`: `fail_if_missing()` raises at startup, called in lifespan (`brain_loader.py:52-55`, `app.py:48`); missing files logged |
| C-9 | Cache TTL purge on every request | **FIXED / VERIFIED** | `C`: purge at most once/day via `meta.last_cache_purge` (`database.py: startup_maintenance`) |
| C-10 | History grows unbounded | **FIXED / VERIFIED** | `C`: 90-day prune + 500-rows/user cap in `startup_maintenance` (`database.py`) |
| C-11 | Zero logging | **FIXED / VERIFIED** | `C`: stdlib logging → stdout; request middleware logs method/path/status/ms/user (`app.py:86-107`); auth/quota/cache/model events logged. `L`: observed in server logs across runs. Key never logged (`ai_client._proxy_url` logs yes/no only) |
| C-12 | Enter doesn't submit; `alert()` validation | **FIXED / VERIFIED** | `S`: 10 `submit` listeners with `preventDefault` (all 6 feature forms + auth + admin); zero `alert()` calls (one comment mention) — inline `setFieldErr` instead |
| C-13 | No frontend timeout/cancel | **FIXED / VERIFIED** | `S`: `AbortController` 125s in `callApi`; 6 `data-cancel` buttons wired in `wireCancelButtons`; AbortError → friendly message |
| C-14 | No aria-live / tab roles | **FIXED / VERIFIED** | `S`: `aria-live="polite"` on all `.out` + `.ferr`; `role=tab`/`aria-selected`/`aria-controls` on tabs (`index.html`, `app.js`) |
| C-15 | Shallow pydantic validation | **FIXED / VERIFIED** | `T`: `test_whitespace_rejected`, `test_ideas_validation` · `L`: 422s for blank/out-of-range/malformed (Run A) · `C`: strip + `min/max_length` + `ge/le` (`models.py`) |
| C-16 | `http_referer` hardcoded to third-party URL | **FIXED / VERIFIED** | `C`: `app_public_url` defaults to `""` → header not sent (`config.py`, `ai_client.py:140-141`) |
| C-17 | Unpinned requirements | **FIXED / VERIFIED** | `C`: `requirements.txt` fully pinned (`==`) · `pip-audit`: app deps clean (only `pip 24.0` toolchain flagged) |

## D. Lows (grouped)

| Items | Status | How verified |
|---|---|---|
| XSS single-layer (`esc()`), no CSP backstop | **MITIGATED / VERIFIED** | `S`: every `innerHTML` write goes through `esc()`/`render()`/static strings; no attribute sinks; CSP header present (backstop) modulo F-1 on generic-500s |
| CSRF (L2) | **N/A BY DESIGN** | `C`: Bearer-header auth, no cookies (`auth.py` docstring); revisit if cookies are ever added |
| CORS absent (L3) | **VERIFIED SAFE** | `C`: no `CORSMiddleware`; same-origin default kept |
| SQL injection (L4) | **CLEAR** | `C`: all queries parameterized (`database.py`); `limit` Pydantic-bounded before use |
| Secrets hygiene (L5) | **VERIFIED** | `C`: no hardcoded keys; `.env` absent + gitignored; key only as outbound Bearer header, never logged |
| Deps / pip-audit (L6) | **VERIFIED** | `pip-audit`: 12 findings, all `pip 24.0` toolchain-only; app runtime deps clean |
| Blocking/timeout nits (L7) | **MITIGATED** | see C-7 |
| `esc()` misses `'`, no `maxlength` (L3) | **NOTED** | harmless in element-content context (no attribute sinks); `maxlength` absent → TEST-REPORT F-3 (trivial) |
| Frontend nits (L-1…L-5): usage pill silent-fail, footer bullet, history refetch, autocomplete, dead `button:disabled` | **FIXED / VERIFIED** | `S`: pill shows "⚡ retry karo" on failure; `initHealth` catch clears footer text; `historyLoaded` guard; `autocomplete` attrs on auth inputs; `button:disabled` style now actively used |
| No eval/exec/subprocess/pickle; no path traversal; no open redirects | **CLEAR** | `C`: none present; static serves fixed `FRONTEND_DIR` |

## G. Smoke-test findings (re-verified this phase)

| # | Status |
|---|---|
| G-1 nemotron verified w/ real key | Prior result stands; **not** re-verified (no key this phase — documented known limit) |
| G-2 inkling removed from chain | **VERIFIED**: no `inkling` in `config.py` chain or any `.py` (only historical docs); removal documented in `config.py` comment |
| G-3 defensive parsing | **VERIFIED**: `test_malformed_200_shapes_skip_to_next_model`, `test_empty_choices_skipped` green |
| G-4 httpx proxy crash | **VERIFIED**: `test_http_client_constructs_with_bracketed_no_proxy` green; independently reproduced this phase via test-client crash on sandbox `NO_PROXY` |
| G-5 gemma 429 → fallback | **VERIFIED**: `test_fallback_404_then_model2_serves`, `test_retryable_500_also_falls_through_chain` green |

**Honestly not fully verifiable this phase:** live OpenRouter transport (no key — G-1 covers it historically); real-browser clicking (no browser — static + HTTP equivalence instead); `AUTH_REQUIRED=false` dev mode (suite covers via `test_auth_required_false_skips_token`; not live-exercised); multi-worker behavior (out of scope — single-worker is a documented requirement).

---

## H. v2 — Media Studio (ai33.pro) + Avatar Studio (HeyGen) — 2026-09-18

**Basis:** `V2-DESIGN.md` §0.2–§0.4, §B, §D + the 7 launch gates. **How verified:** `T`= named pytest (144/144 green) · `L`= live uvicorn on :8124 with host-asserting mocked transports (59/59) · `C`= code read w/ file:line · `S`= static frontend audit. No real third-party calls (constraint).

| # | Item | Status | How verified |
|---|---|---|---|
| H-1 | All 16 `/api/v1/media/*` + 11 `/api/v1/avatar/*` routes require JWT | **FIXED / VERIFIED** | `T`: `test_unauthenticated_401` (media), `test_all_11_routes_require_auth` (avatar) · `L`: no-token → 401 on media/avatar create+read routes · `C`: AST scan of both routers — every route function signature contains `get_current_user`, zero exceptions |
| H-2 | Provider API keys encrypted at rest (Fernet+HKDF), never plaintext | **FIXED / VERIFIED** | `T`: `test_key_encryption_roundtrip` (real encrypt/decrypt vs temp DB), `test_key_wrong_secret_treated_as_missing` · `C`: `provider_keys.py:60-77` (HKDF-SHA256 `salt=b"cios-provider-keys-v1"`, `info=b"cios-fernet"`, lazy singleton); DB column holds Fernet token string only |
| H-3 | Key never in logs, responses, URLs, or reports | **FIXED / VERIFIED** | `T`: `test_key_never_in_logs` (`caplog` assertion) · `L`: all 59 response bodies grepped for both test keys (good+bad, both providers) → absent · `C`: logs emit only `has_key` bools + user ids (`provider_keys.py:126,146,162`); `_hg`/`_ai33_request` build headers server-side, key never in query strings; avatar UI clears the input field after save (`avatar-studio.js:192`) |
| H-4 | Verify-first: bad keys never stored | **FIXED / VERIFIED** | `T`: `test_key_verify_401_not_stored` (media), `test_key_save_bad_key_401_not_stored`, `test_key_save_verify_403_not_stored` (avatar) · `L`: bad key → 400 then `GET key` → `has_key:false` for both studios |
| H-5 | Per-user namespacing of `avatar_jobs` (user A can't see B's) | **FIXED / VERIFIED** | `T`: `test_history_per_user_namespaced` · `L`: user-B `GET /api/v1/avatar/videos` shows none of user-A's `vid-1` · `C`: every `avatar_jobs` query filters `user_id=?` (`routers/avatar.py:183,559,610`) |
| H-6 | Media/avatar caps atomic — concurrent creates can't overshoot | **FIXED / VERIFIED** | `T`: `test_daily_create_cap_429`, `test_tts_chars_cap_429`, `test_caps_are_per_user` (media); `test_daily_create_cap_3`, `test_cap_counts_agents_too` (avatar) · `C`: single `UPDATE media_usage … WHERE creates < cap` + `rowcount` check (`routers/media.py:225-232`, `routers/avatar.py:157-167`); spent BEFORE the provider call (media `tts`:464→`_run_ai33`; avatar `create_video`: `_api_key_for`→`_spend_create_cap`→`_hg`); reads (voices/credits/status/tasks) never spend |
| H-7 | Throttle covers new create paths | **FIXED / VERIFIED** | `T`: `test_request_id_on_throttle_429` · `L`: 31 rapid POSTs → 429 with `X-Request-ID` · `C`: `THROTTLE_PATHS` includes `/api/v1/media/{tts,dialogue,voice-clone,music,image,video}` + `/api/v1/avatar/{videos,agents}` (POST) (`app.py:155-166`) |
| H-8 | No POST auto-retry anywhere (quota/credit burn) | **FIXED / VERIFIED** | `C`: `api()` — "POST KABHI auto-retry nahi; GET max 1 retry, network-throw only" (`frontend/app.js:106-110,188-190`); manual "Dobara try karo" button in error toasts (`app.js:88-94`); `formKey` inflight map ignores double submits (`app.js:166-173`) · `S`: studio scripts retry nothing except GET polls |
| H-9 | ai33 crash paths → friendly Roman Urdu, never raw 500 | **FIXED / VERIFIED** | `T`: `test_ai33_401_mapped`, `test_ai33_429_mapped`, `test_insufficient_credits_mapped`, `test_tts_success_false_mapped`, `test_ai33_timeout_mapped`, `test_ai33_malformed_json_mapped`, `test_task_status_unknown_maps_to_processing` · `C`: `_ai33_request` maps 401/402/429/5xx/timeout/network/malformed-JSON/`success:false` (`routers/media.py:136-176`); unknown poll status → `processing`, never `completed` (`_map_status`, `routers/media.py:649-657`) |
| H-10 | HeyGen crash paths → §D4 Roman-Urdu catalog, never raw 500 | **FIXED / VERIFIED** | `T`: `test_error_401_bad_key_on_poll`, `test_error_402_insufficient_credits`, `test_error_429_rate_limit`, `test_error_moderation_4xx`, `test_error_invalid_avatar_400`, `test_error_heygen_500_maps_502`, `test_error_network_timeout_maps_friendly`, `test_error_malformed_json_maps_friendly`, `test_poll_unknown_status_never_completed`, `test_poll_failed_maps_error` · `L`: 402 → `💳 HeyGen credits khatam…`; moderation → `🛡️…` · `C`: `_map_error` catalog (`routers/avatar.py:213-255`); HeyGen `{error:…}` envelope normalized, raw bodies server-side only |
| H-11 | Proxy-safe HTTP clients (G-4 regression) for both new clients | **FIXED / VERIFIED** | `T`: `test_proxy_safe_construction_no_crash` (media, `test_media.py:591`), `test_http_client_constructs_with_bracketed_no_proxy` (avatar, `test_avatar.py:709`) — bracketed-IPv6 `NO_PROXY` no longer crashes `httpx 0.28.1` · `C`: `trust_env=False` + validated `proxy=` in both `_http()` (`routers/media.py:78-88`, `routers/avatar.py:93-101`); 60s timeout on every outbound call; connection reuse (module-level client) |
| H-12 | Proxy URL / API keys never logged | **FIXED / VERIFIED** | `C`: `_proxy_url()` logs only the env-var NAME (`"media proxy configured: yes (from %s)"`, `routers/media.py:69`; same pattern `routers/avatar.py:87`, `ai_client.py:72`) — the URL (which embeds credentials) is never logged |
| H-13 | Input validation on all new routes (G3) | **FIXED / VERIFIED** | `T`: `test_tts_validation`, `test_dialogue_bad_label_422`, `test_voice_clone_oversize_413`, `test_voice_clone_rejects_non_clone_delete`, `test_voices_provider_required_422`, `test_voices_bad_provider_422`, `test_create_validation`, `test_key_validation_too_short`, `test_key_length_validation` · `C`: TTS ≤20000 + speed 0.5–1.5 + voice `_` prefix (`routers/media.py:261-265,457`); provider allowlist 7 (`routers/media.py:40-44`); clone ≤10MB + audio/* + `clone_` gate (`routers/media.py:50,503-516,565`); script ≤1500, agent prompt ≤10000 (`routers/avatar.py:38-39`) · `S`: client mirrors (`maxlength=20000`, `SCRIPT_MAX=1500`, `PROMPT_MAX=10000`) |
| H-14 | Frontend: no `alert()`, inline errors + toasts only | **FIXED / VERIFIED** | `S`: `grep -rn "alert(" frontend/*.js` → zero call sites (one comment mention); `.ferr` inline + `#toast-stack` everywhere; two-step inline confirms for deletes/disconnect (no `confirm()`) |
| H-15 | Every new button wired to a live route (no fake buttons) | **FIXED / VERIFIED** | `S`: 22 controls traced handler → `CIOSApi.api()` → live-tested route (TEST-REPORT §8.4 tables); loading renders (`setBusy`/`CIOSApp.setBtn`); double-submit guards on all spending actions; Enter submits via `<form>` in Avatar Studio (Media Studio uses `type="button"` — Tab-focus Enter works; noted minor) · `L`: each button's exact request shape exercised over HTTP |
| H-16 | Avatar Studio dead-end fixed (no way back) | **FIXED / VERIFIED** | Integration fix: `← Wapas` button added (`frontend/avatar-studio.js:727`, styles `avatar-studio.css`) → `CIOSApp.closeStudio("avatar")` with self-contained fallback. Builder D shipped without it while `app.js` hides `#app-view` — users were trapped |
| H-17 | Import-time DB writes removed (C-5 regression) | **FIXED / VERIFIED** | Integration fix: `provider_keys.ensure_tables()` and `routers/avatar.init_avatar_tables()` no longer run at import; lazy ensure in public functions / table touchpoints + `app.py` lifespan. Verified: imports leave repo `cios.db` untouched; pytest re-run keeps it untouched (was: every run created 3 tables) |
| H-18 | Deps pinned + pip-audit clean (G7) | **FIXED / VERIFIED** | `C`: `requirements.txt` — `cryptography==50.0.1`, `python-multipart==0.0.32` (`==` pinned) · `pip-audit` 2.10.1: app runtime deps clean; only pre-existing `pip 24.0` toolchain findings (same as v1) |
| H-19 | Forced-500 path: safe body + all 4 security headers + request id | **FIXED / VERIFIED** | `L`: `/api/v1/__boom` → 500 `{"ok":false}`, marker absent, CSP/XFO/nosniff/Referrer-Policy + `X-Request-ID` present · `C`: `_set_security_headers` called in `unhandled_handler` too (`app.py:241-249`) — closes v1 finding F-1 |
| H-20 | Health exposes no key material; render health path v1 | **FIXED / VERIFIED** | `L`: `/api/v1/health` body grepped for `sk_`/secret/api_key → absent; old `/api/health` → 404 · `C`: `render.yaml` `healthCheckPath: /api/v1/health` |
| H-21 | Ephemeral-DB caveat covers v2 tables | **DOCUMENTED** | `C`: README now states restart wipes provider keys (re-connect required) + usage counters, alongside the v1 caveat. Dev-mode `AUTH_REQUIRED=false` ephemeral-`jwt_secret` → keys undecryptable after restart (by design, documented in `provider_keys` contract) |

**v2 honestly not verifiable (constraints):** real `api.ai33.pro` / `api.heygen.com` transports (mocks assert exact documented paths/headers; endpoint liveness rests on the 2026-09-18 docs check — re-verify at publish); real-browser clicking (static trace + HTTP equivalence); multi-worker (single-worker remains a hard requirement — SQLite + in-memory throttle).
