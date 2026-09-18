# CIOS v2 — Technical Design Document

**Date:** 2026-09-18 · **Status:** design (read-only, no code changed) · **Constraint:** $0 build + $0 run
**Stack:** FastAPI (sync, single worker) + vanilla JS SPA (no framework) + SQLite · Roman Urdu UI
**Reads:** `SAAS-PLAN.md`, `AUDIT-REPORT.md`, `app.py`, `ai_client.py`, `config.py`, `frontend/*`, skill `saas-launch-audit` (7 gates)

**v2 scope:**
- **A.** Frontend UX & robustness (typography, button state machine, unified API client, PDF reports, API conventions)
- **B.** ai33.pro "Media Studio" — **Outcome 1 CONFIRMED** (public API exists, docs https://ai33.pro/app/api-document). Native Studio UI on the real API. **No iframe** (research: no embed option; do not iframe ai33.pro).
- **C.** 7-gates checklist + test plan
- **D.** HeyGen "Avatar Studio" (public v3 API, verified against https://developers.heygen.com 2026-09-18)

---

## 0. Builder work-split & shared contracts (read this first)

Three builders work **independently, no cross-talk**. These contracts are the only shared surface.

| Builder | Owns | Files |
|---|---|---|
| **A** (frontend/API) | §A1–A5: type scale, buttons, unified `api()` client, toasts, print reports, `/api/v1` migration, request-id middleware, README API docs | `frontend/app.js`, `frontend/index.html`, `frontend/styles.css`, `app.py`, `routers/*`, `auth.py`, `README.md` |
| **B** (Media Studio) | §B: shared encrypted key-store, all `/api/v1/media/*` routes, ai33.pro proxy client, `studio-media.js` | `provider_keys.py` (NEW, shared), `routers/media.py` (NEW), `ai33_client.py` (NEW), `frontend/studio-media.js` (NEW), `database.py` (migrations only), `config.py` (MEDIA_* settings), `requirements.txt` (+cryptography) |
| **D** (Avatar Studio) | §D: HeyGen proxy client, all `/api/v1/avatar/*` routes, `studio-avatar.js` | `heygen_client.py` (NEW), `routers/avatar.py` (NEW), `frontend/studio-avatar.js` (NEW), `database.py` (migrations only), `config.py` (AVATAR_* settings) |

### 0.1 Shell contract (A provides, B and D consume)

A adds to `index.html` (exact IDs — B/D must use these, never invent others):

```html
<!-- main nav, inside #tabs -->
<button type="button" data-tab="avatar" id="nav-avatar">🎭 Avatar Studio</button>
<!-- full-screen studio shell, direct child of body -->
<div id="studio-view" hidden>
  <div id="studio-shell"></div>   <!-- B renders here -->
</div>
```

- B ships `frontend/studio-media.js` exposing `window.CIOSMedia = { mount(rootEl), unmount() }`. A adds `<script src="/static/studio-media.js">` and a nav button "🎙️ Media Studio" (`id="nav-studio"`); on click A hides `#app-view`, shows `#studio-view`, calls `CIOSMedia.mount($("studio-shell"))`; on close ("← Wapas" button rendered by **B** inside shell) B calls `window.CIOSApp.closeStudio()` (A provides), A calls `CIOSMedia.unmount()`.
- D ships `frontend/studio-avatar.js` exposing `window.CIOSAvatar = { mount(rootEl), unmount() }`. Avatar Studio is a **dashboard section, not full-screen**: A adds `<section id="tab-avatar" class="tab" …><div id="avatar-root"></div></section>`; tab switching calls `CIOSAvatar.mount($("avatar-root"))` on first open (lazy) and `unmount()` on tab leave.
- If B's or D's script fails to load, A must show "Studio load nahi hua — page refresh karo" (never a dead button).
- A provides the unified `api()` (see §A3) on `window.CIOSApi.api`; B/D **must** use it for every backend call (no raw `fetch` to our backend).

### 0.2 Shared provider key-store (B implements, D consumes — exact signatures)

`provider_keys.py` — the ONLY place third-party user keys live. Providers: `"ai33"`, `"heygen"`.

```python
def set_provider_key(user_id: int, provider: str, raw_key: str) -> None
def get_provider_key(user_id: int, provider: str) -> str | None   # decrypted, mem-only
def delete_provider_key(user_id: int, provider: str) -> None
def has_provider_key(user_id: int, provider: str) -> bool
```

- **Encryption:** Fernet (`cryptography` lib, pinned in requirements). Key derivation: `HKDF(SHA256, length=32, salt=b"cios-provider-keys-v1", info=b"cios-fernet", ikm=settings.jwt_secret.encode())` → `base64.urlsafe_b64encode` → Fernet key. Module-level lazy singleton.
- **Rules (Gate 4):** never plaintext at rest; never logged (log only `has_key` bool); never returned in any response; never sent to browser; `raw_key` validated (10–500 chars, stripped) then the caller's variable should go out of scope. DB column `enc_key TEXT` stores the Fernet token string.
- **Dev-mode caveat:** `AUTH_REQUIRED=false` generates an ephemeral `jwt_secret` → keys become undecryptable after restart. Document in README; acceptable (dev only).
- Table (B adds in `database.py::init_db`, `CREATE TABLE IF NOT EXISTS`):
  ```sql
  CREATE TABLE provider_keys (
    user_id INTEGER NOT NULL, provider TEXT NOT NULL,
    enc_key TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
    PRIMARY KEY (user_id, provider)
  );
  ```

### 0.3 Shared media-usage table (B creates, D extends)

```sql
CREATE TABLE media_usage (
  user_id INTEGER NOT NULL, day TEXT NOT NULL, provider TEXT NOT NULL,
  creates INTEGER NOT NULL DEFAULT 0, tts_chars INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (user_id, day, provider)
);
```
- B enforces ai33 caps; D enforces HeyGen caps using the same table with `provider='heygen'`.
- D additionally creates `avatar_jobs` (§D).

### 0.4 Outbound HTTP for third parties (B and D, Gate 5)

Both `ai33_client.py` and `heygen_client.py` reuse the **proxy-safe pattern** from `ai_client.py`: module-level `httpx.Client(timeout=MEDIA_HTTP_TIMEOUT, trust_env=False, proxy=_proxy_url())` — copy `_proxy_url()` verbatim (never log proxy URL). `MEDIA_HTTP_TIMEOUT = 60` (config, separate from OpenRouter's 120s). Every third-party call sends `X-Request-ID`-style correlation: include our `rid` in server logs for that call.

---

## A. Frontend UX & robustness (Builder A)

### A1. Typography — type scale for long Roman Urdu AI outputs

No framework. System font stack stays (Roman Urdu is Latin script). CSS custom properties in `:root`:

```css
:root{
  --fs-meta: 12px;
  --fs-sm:   clamp(13px, 0.8125rem + 0.20vw, 14px);   /* labels, buttons */
  --fs-body: clamp(15px, 0.9375rem + 0.25vw, 17px);   /* .out long-form output */
  --fs-h3:   clamp(17px, 1.0625rem + 0.40vw, 20px);
  --fs-h2:   clamp(20px, 1.25rem   + 0.80vw, 26px);
  --lh-body: 1.75;
  --measure: 70ch;
}
```

| Element | Size | Line-height | Max-width |
|---|---|---|---|
| `.out` (AI output) | `var(--fs-body)` | 1.75 | `70ch` (desktop), full width <480px |
| `.out .meta` | `var(--fs-meta)` | 1.5 | — |
| `h2` | `var(--fs-h2)` | 1.3 | — |
| `label`, `main button` | `var(--fs-sm)`→15px | 1.5 | — |
| `.ferr`, `.toast` | 13px | 1.5 | — |

Rules:
- **Floor:** body text never renders below 15px on any viewport (phone legibility). `clamp()` minimums enforce this.
- **Phone (<480px):** `main{padding:16px 12px}`, `.out{padding:14px; line-height:1.7}`, `nav{overflow-x:auto; flex-wrap:nowrap}` (horizontal scroll, no wrap-stack), `.btnrow{flex-wrap:wrap}`.
- **Desktop (≥1024px):** `.out` caps at 17px and `70ch`, centered by `main`'s existing max-width.
- **Wrapping:** `.out{overflow-wrap:break-word}`; URLs/code get `overflow-wrap:anywhere`. No hyphenation (no Roman Urdu dictionary).
- `text-rendering: optimizeLegibility`; letter-spacing 0.

### A2. Button UX — state machine

Every button goes through helper `setBtn(btn, state)` in `app.js` (B/D reuse via `window.CIOSApp.setBtn`):

| State | Visual | Behavior |
|---|---|---|
| `default` | current brand style | — |
| `hover` | darken (existing) | — |
| `active` | `transform: translateY(1px)` | CSS `:active` |
| `focus-visible` | `outline: 2px solid #7dd3fc; outline-offset:2px` | CSS `:focus-visible` (new — keyboard a11y) |
| `disabled` | `opacity:.55; cursor:not-allowed` | `disabled` + `aria-disabled="true"` |
| `loading` | `disabled` + spinner `⟳` prefix + label suffix `"…"` (min-width set so no layout shift) | set at submit, cleared on settle |
| `success-flash` | label → `"✅ ho gaya"` for 1.2s (non-AI actions: quota update, key save, delete) | then restore |

**Double-submit guard (quota-spending actions):**
1. Client: `api()` keeps `inflight` map keyed by `formKey`; second submit while pending is ignored (existing pattern, extended to ALL forms incl. admin/media/avatar via the shared client).
2. Client sends `X-Idempotency-Key: <uuid per submission>` on every POST; server logs it in the request log line (observability for support; no DB needed at this scale).
3. Server: existing per-IP throttle + atomic quota stay the second line of defense.

**Enter-submits-forms:** keep `<form onsubmit>` pattern for every new form (B/D must follow). **Zero `alert()`** — inline `.ferr` + toasts only (existing pattern; B/D must not introduce `alert`/`confirm` — use inline confirm pattern: delete buttons turn into "Pakka? Haan / Nahi" two-step inline).

### A3. Unified API client

**One wrapper for the whole app.** B/D are forbidden from calling raw `fetch` to our backend.

```js
// window.CIOSApi.api(path, opts)
// opts: {method='GET', body=null, formData=null, timeoutMs, formKey, idempotency=true}
async function api(path, opts = {})
```

Behavior spec:

| Concern | Rule |
|---|---|
| Timeout | POST → 125s `AbortController`; GET → 30s. User-initiated cancel (cancel buttons) aborts immediately. |
| **Retry policy** | **POST: NEVER auto-retry.** Each attempt on the 6 AI endpoints spends 1 of the 48/day quota and changes pacing state; a blind retry doubles the burn and can 429 the provider. Same for media/avatar create endpoints (spend user credits). Instead: error toast carries a manual **"Dobara try karo"** button (user's explicit choice). **GET: max 1 retry, only on network-level failure** (`fetch` threw `TypeError`, i.e. no HTTP response at all). Never retry on HTTP status codes (incl. 429/5xx — server already decided), never on `AbortError`. Backoff: 1500ms + random 0–500ms jitter. |
| Request ID | Read `X-Request-ID` response header on every response; attach to thrown errors; error toasts show `"ID: <rid> — support ke liye ye ID bhejo"`. If header missing (proxy strip), show `"ID: n/a"`. |
| Auth | Adds `Authorization: Bearer` when token exists. On 401 → logout + login view + toast (existing). |
| Envelope | Success → return `j.data` (v2 envelope, §A5). Error → throw `ApiError{status, message, requestId, kind}`. |

**Roman Urdu message catalog** (single `MSGS` object; `kind` derived from status/exception):

| kind | Copy |
|---|---|
| `network` | `🌐 Server se connect nahi ho raha — internet check karo ya thori dair baad try karo.` |
| `timeout` | `⏱️ 125s me jawab nahi aaya (model busy ho sakta hai). Dobara try karo — yaad rahe har naya try 1 quota kharch karta hai.` |
| `server5xx` | `🔧 Server me masla ho gaya. Ye ID support ko bhejo: {rid}` |
| `quota429` | backend message verbatim + ` Kal phir try karo.` (attempt-based accounting — see SAAS-PLAN §3) |
| `throttle429` | backend message verbatim (`Bohat zyada requests — ek minute ruk kar try karo.`) |
| `auth401` | `🔑 Session khatam ho gayi — dobara login karo.` |
| `forbidden403` | `⛔ Ijazat nahi hai.` |
| `validation422` | existing `friendly422()` |
| `cancelled` | `⏹️ Cancel kar diya.` (silent-ish: inline note, no error toast) |

**Toast system:** `#toast-stack` fixed bottom-right (`position:fixed; bottom:18px; right:18px; z-index:100`). Types: `error` (red, 9s), `warn` (amber, 7s), `success` (green, 5s), `info` (blue, 6s). Max 4 stacked (oldest dismissed). Click-to-dismiss. `role="alert"` on error toasts; container `aria-live="polite"`. **Never silent:** every `catch` in every flow ends in either inline `.ferr`/`.out` error or a toast — audit this in review. Toasts carry the request ID and, for POST failures, the manual retry button.

### A4. PDF reports — print-view approach (not a server lib)

**Decision: print-optimized report view + `@media print` + `window.print()`.**

Why NOT a server-side lib: Render free tier = 512MB RAM, ephemeral disk. WeasyPrint needs Cairo/Pango system libraries via `apt` → Docker image bloat, slow cold starts, build fragility — all for a $0 app. ReportLab is lighter but cannot reuse our HTML `render()` output (would need a parallel PDF layout engine = double maintenance). Browser print is $0, zero dependencies, zero server load, and the user's "Save as PDF" destination is native and familiar.

Design:

1. **Per-tab button:** `🖨️ PDF / Print` (class `ghost`) in each feature tab's `.btnrow`, next to the cancel button. Enabled only when that tab has a fresh successful result — `state.lastResult[feature] = {inputs, data, model, cached, at, requestId}` set in `callApi` success path. Disabled otherwise with `title="Pehle result generate karo"`.
2. **Report DOM:** `<div id="report-print" aria-hidden="true">` as a **direct child of `<body>`** (required for the print-hiding selector). Populated by `buildReport(feature)`:
   ```html
   <article class="report">
     <header class="rep-head">
       <h1>CIOS Report — {FeatureName}</h1>
       <table class="rep-meta">
         <tr><th>Date</th><td>{toLocaleString(), user tz}</td></tr>
         <tr><th>Model</th><td>{model} {cached ? "(cache)" : ""}</td></tr>
         <tr><th>Request ID</th><td>{requestId}</td></tr>
         <tr><th>Inputs</th><td>{each input label: value}</td></tr>
       </table>
     </header>
     <section class="rep-body">{render(j.data) — existing renderer REUSED verbatim}</section>
     <footer class="rep-foot">CIOS $0 edition • {generated-at} • Privacy: inputs OpenRouter ko bheje gaye thay (aapki key se).</footer>
   </article>
   ```
   Report body reuses the **exact HTML** already rendered in the tab's `.out` (minus the `.meta` line) — single source of truth, no divergent layout.
3. **Print CSS** (`@media print`):
   ```css
   @page { size: A4; margin: 15mm; }
   body.printing > :not(#report-print){ display:none !important; }
   #report-print{ display:block !important; }
   .report{ color:#000; background:#fff; font-size:12pt; line-height:1.6; max-width:none; }
   .rep-body > *{ break-inside: avoid; }
   ```
   Light theme forced for print (ink-friendly); dark screen theme untouched.
4. **Flow:** click → `buildReport` → `document.body.classList.add("printing")` → `window.print()` → `onafterprint` removes class + clears node. If `window.print` unavailable → toast error.
5. Covers all six features: ideas, script, packaging, seo, niche check, research.

### A5. API conventions

**Versioned prefix: `/api/v1/*` for everything.** Migration choice: **clean cut — no deprecated aliases.**

Reasoning: the app is **not yet published** (SAAS-PLAN §13 acceptance criteria unmet; no external consumers exist). Dual `/api/*` + `/api/v1/*` surfaces double the throttle path list, double the test matrix, and permanent tech debt for zero benefit. Single deploy unit (frontend + backend ship together), so the cutover is atomic. `render.yaml` health check moves to `/api/v1/health`.

**Envelope standard (v2):**
```json
// success
{"ok": true, "data": { /* always an object */ }}
// error
{"ok": false, "error": "Roman Urdu message"}
```
`X-Request-ID` header on every response. Exceptions to the envelope (documented, deliberate):

| Endpoint | Current shape | v2 decision |
|---|---|---|
| `POST /api/auth/signup`, `/login` | `{ok, token, user}` | **Normalize** → `{ok, data:{token, user}}` |
| `GET /api/auth/me` | `{ok, user}` | **Normalize** → `{ok, data:{user}}` |
| `GET /api/history` | `{ok, items}` | **Normalize** → `{ok, data:{items, limit}}` |
| `GET /api/usage` | `{ok, used, cap}` | **Normalize** → `{ok, data:{used, cap, day}}` |
| `GET /api/admin/users` | `{ok, users}` | **Normalize** → `{ok, data:{users}}` |
| `GET /api/admin/usage` | `{ok, day, rows}` | **Normalize** → `{ok, data:{day, rows}}` |
| `POST /api/admin/quota` | `{ok, user_id, cap}` | **Normalize** → `{ok, data:{user_id, cap}}` |
| 6 AI POSTs (`ApiResult`) | `{ok, model, cached, data}` | **Keep** — `model`/`cached` are documented top-level metadata, `data` is an object |
| `GET /api/health` | `{ok, version, key_configured, [models]}` | **Keep** — health-check convention; monitors read `ok` only |
| 422 errors | FastAPI `{detail:[...]}` | **Keep** — documented; frontend parses via `friendly422()` |
| All exception handlers | `{ok:false, error}` | **Keep** — already compliant |

**X-Request-ID middleware** (`app.py`, new):

```python
class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        rid = uuid.uuid4().hex[:16]
        request.state.request_id = rid
        if k := request.headers.get("X-Idempotency-Key"):
            request.state.idempotency_key = k[:64]
        resp = await call_next(request)
        resp.headers["X-Request-ID"] = rid
        return resp
```

- **Order:** add LAST in `app.py` so it executes FIRST (outermost) — guarantees the header on every response including throttle-429s and unhandled 500s.
- `RequestLoggingMiddleware` reads `request.state.request_id` (and idempotency key) into the log line: `… user=%s rid=%s`.
- Throttle 429 responses also get the header (they're built inside middleware, below RequestId in execution order).

**Route table (post-migration):** every prefix below gains `/v1`:

| Method | Path | Auth | Notes |
|---|---|---|---|
| POST | `/api/v1/auth/signup`, `/login` | no | |
| GET | `/api/v1/auth/me` | yes (or no-auth passthrough) | |
| POST | `/api/v1/ideas`, `/research`, `/scripts`, `/packaging`, `/seo`, `/niche` | yes | quota-spending |
| GET | `/api/v1/history`, `/api/v1/usage`, `/api/v1/health` | yes / yes / no(minimal) | |
| GET | `/api/v1/admin/users`, `/api/v1/admin/usage` | admin | |
| POST | `/api/v1/admin/quota` | admin | |
| * | `/api/v1/media/*` | yes | §B (Builder B) |
| * | `/api/v1/avatar/*` | yes | §D (Builder D) |

`THROTTLE_PATHS` updated to the six v1 AI paths **plus** media/avatar create paths (§B/§D give exact paths; A wires them — B/D must list theirs in the shared section of the doc… they are listed below; A copies them verbatim).

**README API-docs outline** (A writes; new `README.md` section "API v1"):
1. Base URL + versioning policy ("`/api/v1` — breaking changes bump to `/api/v2`; v1 frozen after publish").
2. Auth: `Authorization: Bearer <JWT>`, 7-day expiry, 401 handling.
3. Envelope + error catalog (table of HTTP codes → Roman Urdu meaning) + `X-Request-ID` support note.
4. Endpoint table (method, path, auth, body schema, response `data` shape) for all routes above.
5. Quota model: 48/day attempt-based, cache-before-quota, throttle 30/min/IP.
6. `$0` notes: ephemeral SQLite on free hosts, single worker requirement.

---

## B. Media Studio — ai33.pro connector (Builder B)

**Research status: Outcome 1 CONFIRMED.** Public API exists: base `https://api.ai33.pro`, docs `https://ai33.pro/app/api-document`. **No iframe branch** — research found no embed/iframe option; do NOT iframe `https://ai33.pro/`. Build a native Studio UI on the real REST API + a plain "🌐 ai33.pro kholo" external link.

### B0. Verified API facts (2026-09-18, from official docs)

- **Auth:** custom header `xi-api-key: <KEY>` on **every** request (NOT `Authorization: Bearer`). Keys issued from the user's ai33.pro dashboard after login.
- **TTS (v3 unified, multipart/FormData):** `POST /v3/text-to-speech` — `voice_id` REQUIRED and MUST carry provider prefix; `text` max 1M chars; `speed` 0.5–1.5; `with_transcript` flag; optional `receive_url` webhook → returns `{"success":true,"task_id":"…"}` → poll `GET /v1/task/{task_id}` for completion + audio URL.
- **Dialogue:** `POST /v3/text-to-speech/dialogue` — multi-speaker, `A>`/`B>`/`C>` text labels + `speakers` array.
- **Voices:** `GET /v3/voices?provider=<REQUIRED>` — providers: `elevenlabs_`, `minimax_`, `clone_`, `edge_`, `kokoro_`, `vbee_`, `fishaudio_`; filters: `language`, `gender`, `accent`, etc. `voice_id` MUST include the prefix (e.g. `elevenlabs_XXXX`).
- **Voice clone:** `POST /v3/text-to-speech/voice-clone` (`voice_name` + `audio_file` ≤10MB → returns `clone_<id>`); `DELETE /v3/text-to-speech/voice-clone/{id}`. Pronunciation dictionaries CRUD exist — **phase 2, not exposed in v2** (honest labeling: no button).
- **ElevenLabs-compat:** `POST /v1/text-to-speech/{voice_id}` (+`/stream`), `GET /v1/voices`, `POST /v1/voices/add` — **alternative path, NOT exposed in v2 UI** (documented only).
- **Music (Suno, async):** `POST /v1/suno/wav | /mp3 | /mp3-45 | /mp3-lite | /instrumental` (`prompt`, `style`, `title`, `receive_url`) → `task_id` → poll `GET /v1/task/{task_id}`. `GET /v1/suno/credits`.
- **Images (async):** `POST /v1/image-to-image`, `/v1/gpt-image-1`, `/v1/upscale`, `/v1/imagen-3` (+ upscale variants).
- **Video (async):** `POST /v1/image-to-video`, `/v1/image-to-video-continue`, `/v1/image-to-video-frame`, `/v1/veo3-fast`, `/v1/veo3` (`prompt`, optional `image`, `duration: 8`, `aspect_ratio: 16:9|9:16`, `generate_audio`).
- **Common:** `GET /v1/credits` (balance), `GET /v1/tasks`, `POST /v1/task/delete` (**refunds credits**), `GET /v1/health-check`.
- **Billing:** single credit pool, no separate API metering. ≈588 credits/min TTS, ≈500 credits/image. Promo through Sept 30: $5 = 1,000,000 credits. New Gmail/Apple signups get 3,333 free trial credits. **UI MUST state (Roman Urdu) that usage spends the user's own ai33.pro credits** and show the live balance from `GET /v1/credits`.

**Design decisions:**
- **Polling, not webhooks:** `receive_url` is NOT used. Webhooks need a public, verified receiver + secret handling — out of v2 scope. All async work is polled.
- **No `text` passthrough of 1M chars:** v2 caps single TTS requests at 20,000 chars client+server (abuse + accidental credit burn).
- **Result URLs:** ai33 returns public result URLs (audio/image/video) — frontend uses them directly for playback/download (no key needed on those URLs). Backend never proxies media bytes (saves our bandwidth).

### B1. Key management (Gate 4)

Uses the shared `provider_keys.py` (§0.2) with `provider="ai33"`. New dep: `cryptography==<pinned, pip-audit clean>` in `requirements.txt` (B runs `pip-audit` after adding).

- `POST /api/v1/media/key` body `{"api_key": "…"}` → backend **verify-first**: `GET https://api.ai33.pro/v1/credits` with `xi-api-key`. 401 → `400 {ok:false, error:"Key ghalat hai — dashboard se dobara copy karo."}` (key NOT stored). Else `set_provider_key()` + return `{ok:true, data:{has_key:true, credits:<int>}}`.
- `DELETE /api/v1/media/key` → `{ok:true, data:{has_key:false}}`.
- `GET /api/v1/media/key` → `{ok:true, data:{has_key:bool}}` — the key itself is NEVER returned, logged, or sent to the browser.

### B2. Backend routes — `/api/v1/media/*` (all JWT; JSON unless noted)

| # | Method | Path | Request | ai33 call | Response `data` |
|---|---|---|---|---|---|
| 1 | POST | `/api/v1/media/key` | `{"api_key": string(10..500)}` | `GET /v1/credits` (verify) | `{has_key:true, credits:int}` |
| 2 | DELETE | `/api/v1/media/key` | — | — | `{has_key:false}` |
| 3 | GET | `/api/v1/media/key` | — | — | `{has_key:bool}` |
| 4 | GET | `/api/v1/media/status` | — | `GET /v1/credits` (cheap) | `{has_key:bool, credits:int\|null}` — single source of truth for the UI |
| 5 | GET | `/api/v1/media/credits` | — | `GET /v1/credits` | `{credits:int}` (normalize ai33 shape) |
| 6 | GET | `/api/v1/media/voices?provider=&language=&gender=&accent=` | `provider` required, must end with `_` | `GET /v3/voices?...` | `{voices:[{voice_id,name,language,gender,accent,provider}], cached:bool}` — backend caches 6h per (provider+filters), single-worker dict |
| 7 | POST | `/api/v1/media/tts` | `{voice_id (must contain "_"), text (1..20000), speed (0.5..1.5, default 1.0), with_transcript (bool)}` | multipart `POST /v3/text-to-speech` | `{task_id}` — ai33 returns `{"success":true,"task_id"}`; on `success:false` map error |
| 8 | POST | `/api/v1/media/dialogue` | `{speakers:{"A":voice_id,"B":voice_id,…}, text:"A>…\nB>…", speed?}` | `POST /v3/text-to-speech/dialogue` | `{task_id}` |
| 9 | POST | `/api/v1/media/voice-clone` | **multipart**: `voice_name` (2..50), `audio_file` (≤10MB, audio/*) | multipart `POST /v3/text-to-speech/voice-clone` | `{voice_id:"clone_…"}` — appears in TTS picker (provider `clone_`) |
| 10 | DELETE | `/api/v1/media/voice-clone/{voice_id}` | voice_id must start `clone_` | `DELETE /v3/text-to-speech/voice-clone/{id}` | `{deleted:true}` |
| 11 | POST | `/api/v1/media/music` | `{mode: wav\|mp3\|mp3-45\|mp3-lite\|instrumental, prompt (1..500), style? (≤200), title? (≤100)}` | `POST /v1/suno/{mode}` | `{task_id}` |
| 12 | POST | `/api/v1/media/image` | `{model: image-to-image\|gpt-image-1\|upscale\|imagen-3, prompt (1..1000), image_url? (≤2048, for image-to-image/upscale)}` | `POST /v1/{model}` | `{task_id}` |
| 13 | POST | `/api/v1/media/video` | `{model: veo3\|veo3-fast\|image-to-video\|image-to-video-continue\|image-to-video-frame, prompt (1..1000), image_url?, aspect_ratio: "16:9"\|"9:16", generate_audio: bool}` | `POST /v1/{model}` (duration fixed 8 per docs) | `{task_id}` |
| 14 | GET | `/api/v1/media/tasks/{task_id}` | — | `GET /v1/task/{task_id}` | `{task_id, status: pending\|processing\|completed\|failed, audio_url?, image_url?, video_url?, transcript?, error?}` — normalized (see B3) |
| 15 | GET | `/api/v1/media/tasks?limit=20` | — | `GET /v1/tasks` | `{tasks:[{task_id,type,status,created_at}]}` |
| 16 | POST | `/api/v1/media/tasks/{task_id}/delete` | — | `POST /v1/task/delete` | `{deleted:true, refunded:true}` |

- **Caps (safety defaults, config):** `MEDIA_DAILY_CREATE_CAP=20` per user/day across routes 7,8,9,11,12,13 (polls/reads/voices/credits excluded); `MEDIA_TTS_DAILY_CHARS=100000`. Enforced in `media_usage` table (§0.3) BEFORE calling ai33. 429 → `"Aaj ki media limit poori ho gayi — kal try karo."`
- **Throttle:** A adds these create paths to `THROTTLE_PATHS` (v1): `/api/v1/media/tts`, `/dialogue`, `/voice-clone`, `/music`, `/image`, `/video`.
- **ai33 error mapping:** HTTP 401 → `"ai33.pro key ghalat/expire — dobara connect karo."`; ai33 `{"success":false,…}` → friendly passthrough of its message + rid; insufficient-credits signal → `"💳 ai33.pro credits khatam — balance upar dekho, ai33.pro pe top-up karo."`; network/timeout → standard A3 catalog. Raw ai33 bodies stay server-side (log only).

### B3. Task polling design

- ai33 is async everywhere: create → `task_id` → poll `GET /v1/task/{task_id}`.
- **Frontend polls OUR backend** `GET /api/v1/media/tasks/{task_id}` every **5s**, max **100 attempts** (~8 min); after 20 attempts back off to 10s. On `completed` → render player (audio/img/video) + download link (direct ai33 result URL). On `failed` → error text + "dobara try karo". On attempts exhausted → `"⏳ Abhi bhi ban raha hai — Tasks tab me status dekho."` (task continues server-side at ai33).
- **Status normalization** (backend): map ai33's status strings to `pending|processing|completed|failed`; unknown non-empty strings → `processing` (never misreport completion); only explicit completed/success markers → `completed`. Extract `audio_url`/`video_url`/`image_url`/`transcript` defensively (missing → null, not crash).
- **Key never leaves server:** the browser never sees `xi-api-key`; it only ever holds `task_id`s.

### B4. Studio UI — full-screen view, 7 tabs (`frontend/studio-media.js`)

Shell: B renders inside `#studio-shell` (§0.1): header row (`🎙️ Media Studio`, credit pill `💳 {credits} credits` refreshed on mount + after every create, `← Wapas` button, `🌐 ai33.pro kholo` external link `target=_blank rel=noopener`), tab bar, per-tab panels.

| Tab | Controls (all via `CIOSApi.api`) | Honest label |
|---|---|---|
| **TTS** | textarea (maxlength 20000, char counter + est. credits `≈ chars/60*588/min` hint), provider-grouped voice picker (loads ALL providers' voices: 7 × `GET voices`, language/gender filter dropdowns), speed slider 0.5–1.5, transcript toggle, Generate → poll UI → `<audio controls>` + ⬇️ download | LIVE |
| **Dialogue** | textarea with `A>`/`B>`/`C>` labels (format hint + validate), per-speaker voice pickers (A/B/C), speed, Generate → poll → audio | LIVE |
| **Voice Clone** | `voice_name` input, file input (accept audio/*, client-side ≤10MB check + server re-check), Upload → new `clone_<id>` appears in TTS picker provider group; list "meri clones" + delete (two-step inline confirm) | LIVE |
| **Music** | Suno mode select (wav/mp3/mp3-45/mp3-lite/instrumental + one-line cost note), prompt/style/title, Generate → poll → audio | LIVE |
| **Image** | model select (4), prompt, optional image URL (for image-to-image/upscale), Generate → poll → `<img>` preview + download | LIVE |
| **Video** | model select (5), prompt, optional image URL, aspect 16:9/9:16, audio toggle, Generate → poll → `<video controls>` + download | LIVE |
| **Tasks** | history table (`GET tasks`): task_id, type, status, date; per-row Delete (refunds credits — label says so: `"🗑️ Delete (credits wapas milenge)"`) | LIVE |

**Without a key:** the whole studio shows the **setup panel** instead of dead controls: numbered Roman Urdu steps (1. `🌐 ai33.pro kholo` → login, 2. dashboard me API key banao, 3. neeche paste karo → Connect), trial-credit note (`"Naye Gmail/Apple signup par 3,333 free trial credits"`), billing disclosure. The key input + Connect button are the only enabled controls; everything else renders disabled with `title`/note explanation.

**Billing disclosure (always visible in Studio header):**
> `⚠️ ai33.pro paid service hai — har generation aapke apne ai33.pro credits se katega (balance: 💳 X). Hamara $0 sirf CIOS ki hosting par hai. Task delete karne par credits wapas milte hain.`

**Per-tab credit hints:** TTS shows `≈ {est} credits`; image `≈500 credits`; video `model ke hisaab se — confirm karne ke baad katega`. Estimates are labeled estimates.

### B5. What v2 does NOT ship (documented, no fake buttons)

Pronunciation dictionaries CRUD, ElevenLabs-compat endpoints, `receive_url` webhooks — noted in code comments + README as phase-2. No UI controls for them.

---

## D. Avatar Studio — HeyGen connector (Builder D)

**Verification (2026-09-18, developers.heygen.com + official reference):**

| Research claim | Live docs | Verdict |
|---|---|---|
| v3 REST at `https://api.heygen.com`, API key from app.heygen.com → Settings → API | Confirmed; reference: "HeyGen API key. Obtain from your HeyGen dashboard." | ✅ |
| Auth scheme | **`X-Api-Key` header** on every request (not Bearer) | ✅ confirmed — use `X-Api-Key` |
| `POST /v3/videos` (`type:"avatar"`, `avatar_id`, `script`, `voice_id`, engines `avatar_iv`/`avatar_v`/`avatar_iii`) | Confirmed; discriminated union `type: avatar|image`; `avatar_iv` default | ✅ |
| `GET /v3/videos/{video_id}` poll → `completed` | Confirmed; statuses `pending/processing/completed/failed` | ✅ |
| `GET /v3/videos` list, `DELETE /v3/videos/{video_id}` | Confirmed | ✅ |
| `POST /v3/video-agents` prompt→video | Confirmed: body `{prompt}`, resp `{data:{session_id, status, video_id:null}}`; `GET /v3/video-agents/{session_id}`; `…/videos`; `…/stop`; modes `generate` (one-shot) vs `chat` (multi-turn) | ✅ + **fix: always send `mode:"generate"`** (research omitted; chat mode stalls on `waiting_for_input`) |
| Avatar list → `avatar_id` | **Nuance:** two endpoints — `GET /v3/avatars` (groups) and `GET /v3/avatars/looks` (per-look: `id` = the `avatar_id` for create, plus `default_voice_id`, preview image). Design uses **`/v3/avatars/looks` as primary** | ⚠️ refined |
| Voice list + preview | `GET /v3/voices` (params `type|engine|language|gender|limit|token`, cursor pagination) → fields incl. **`preview_audio_url`** — preview IS supported | ✅ (research's "where the API allows" → allowed) |
| Poll statuses `pending→processing→completed` | Docs table has those four; **`waiting`** also observed in practice | ⚠️ treat unknown non-terminal as in-progress |
| 30-min max per scene | Per research; not re-verified — keep as UI hint, not enforced | ➖ |
| "Show remaining credits/quota from API" | **No verified official v3 credits endpoint.** A third-party source cites `GET /v2/user/remaining_quota` — **UNVERIFIED**, do NOT depend on it | ⚠️ design shows the billing banner always; surfaces credit info only if a response carries it; 402/insufficient-credit errors map to top-up copy |
| Engine cost notes (iii cheap / iv default / v best) | Community-sourced, plausible; official docs confirm `avatar_iv` default | ➖ selector defaults `avatar_iv`, labels honest |

**Billing (must surface in UI, Roman Urdu):** HeyGen API usage is billed against the **user's own HeyGen credits — NOT free**. Trial tokens (Creator/Teams plans) have limits (e.g. ~5 watermarked videos/day). Our $0 covers CIOS build/hosting only.

### D1. Key management (Gate 4) — reuse §0.2

`provider="heygen"`, same `provider_keys.py`. HeyGen keys look like `sk_V2_…`.

- `POST /api/v1/avatar/key` `{"api_key"}` → **verify-first** via cheap `GET https://api.heygen.com/v3/avatars/looks?limit=1` with `X-Api-Key`. 401/403 → `400 {ok:false, error:"Key ghalat ya expire hai."}` (not stored). Else store + `{ok:true, data:{has_key:true}}`.
- `DELETE /api/v1/avatar/key`, `GET /api/v1/avatar/key` → same shapes as §B1.

### D2. Backend routes — `/api/v1/avatar/*` (all JWT)

`heygen_client.py`: proxy-safe httpx client (§0.4), header `X-Api-Key: <decrypted key>`, base `https://api.heygen.com`. HeyGen's envelope is `{data:{…}, error:…}` — normalize to ours; raw bodies stay server-side.

| # | Method | Path | Request | HeyGen call | Response `data` |
|---|---|---|---|---|---|
| 1 | POST | `/api/v1/avatar/key` | `{"api_key": string(10..500)}` | `GET /v3/avatars/looks?limit=1` (verify) | `{has_key:true}` |
| 2 | DELETE | `/api/v1/avatar/key` | — | — | `{has_key:false}` |
| 3 | GET | `/api/v1/avatar/key` | — | — | `{has_key:bool}` |
| 4 | GET | `/api/v1/avatar/avatars?limit=20&token=` | — | `GET /v3/avatars/looks?limit=&token=` | `{looks:[{id,name,preview_image_url,default_voice_id}], next_token}` |
| 5 | GET | `/api/v1/avatar/voices?limit=50&token=&language=&gender=` | — | `GET /v3/voices?type=public&…` | `{voices:[{voice_id,name,language,gender,preview_audio_url}], next_token}` |
| 6 | POST | `/api/v1/avatar/videos` | `{avatar_id (req), voice_id?, script (1..1500, req), engine: "avatar_iv"\|"avatar_v"\|"avatar_iii" (default avatar_iv), aspect_ratio: "16:9"\|"9:16" (default 16:9), title? (≤120), caption: bool (default false)}` → body `{type:"avatar", avatar_id, script, voice_id?, engine:{type}, aspect_ratio, title?, output_format:"mp4", caption:{file_format:"srt"}?}` | `POST /v3/videos` | `{video_id, status}` |
| 7 | GET | `/api/v1/avatar/videos/{video_id}` | — | `GET /v3/videos/{video_id}` | `{video_id, status: pending\|processing\|completed\|failed, video_url?, gif_url?, thumbnail_url?, duration?, error?}` — also upserts `avatar_jobs` row |
| 8 | GET | `/api/v1/avatar/videos?limit=20` | — | `GET /v3/videos` **filtered to `avatar_jobs` of this user** (per-user namespacing) | `{videos:[{video_id,title,status,video_url,thumbnail_url,created_at}]}` |
| 9 | DELETE | `/api/v1/avatar/videos/{video_id}` | — | `DELETE /v3/videos/{video_id}` | `{deleted:true}` (+ mark job deleted) |
| 10 | POST | `/api/v1/avatar/agents` | `{prompt (1..10000, req), voice_id?, avatar_id?, orientation: "landscape"\|"portrait"?}` → body `{prompt, mode:"generate", …}` | `POST /v3/video-agents` | `{session_id, status, video_id}` |
| 11 | GET | `/api/v1/avatar/agents/{session_id}` | — | `GET /v3/video-agents/{session_id}` | `{session_id, status, video_id}` |

- **Caps:** `AVATAR_DAILY_CREATE_CAP=3` per user/day on routes 6 & 10 (HeyGen minutes cost the USER real credits — safety default; admin-configurable later). Tracked in `media_usage` with `provider='heygen'`. 429 → `"Aaj ki avatar-video limit (3) poori — kal try karo. (HeyGen credits bachat ke liye.)"`
- **Throttle:** A adds to `THROTTLE_PATHS`: `/api/v1/avatar/videos` (POST), `/api/v1/avatar/agents` (POST).
- **Script cap 1500 chars v2** (credit-burn guard; raisable later).
- **Tables** (D adds):
  ```sql
  CREATE TABLE avatar_jobs (
    user_id INTEGER NOT NULL, video_id TEXT PRIMARY KEY, session_id TEXT,
    title TEXT, avatar_id TEXT, status TEXT NOT NULL DEFAULT 'pending',
    created_at REAL NOT NULL, updated_at REAL NOT NULL
  );
  ```
  History (route 8) = `avatar_jobs` rows for this user, enriched with live status from HeyGen (best-effort; HeyGen list is key-global, our table gives per-user namespacing).

### D3. Polling design

- Frontend polls **our** `GET /api/v1/avatar/videos/{video_id}` every **5s**, max **60 attempts** (5 min). Terminal `completed` → `<video controls src=video_url>` + ⬇️ download link (HeyGen's `video_url` is a direct `files.heygen.ai` URL — no key needed). `failed` → mapped error. Attempts exhausted → `"⏳ Video abhi bhi ban rahi hai — History me status dekho."`
- Agent mode: poll `GET /api/v1/avatar/agents/{session_id}` until `video_id` appears, then poll route 7 as above. Same 5s/60-attempt budget.
- Polls are reads: NOT counted against the daily create cap (but they are per-IP throttled naturally as GETs — no, throttle only covers POSTs; add nothing — HeyGen-side rate limits return 429 → mapped message).

### D4. HeyGen error catalog (Roman Urdu)

| HeyGen signal | UI copy |
|---|---|
| 401/403 | `🔑 HeyGen API key ghalat ya expire — Connect me dobara key dalo.` |
| 402 / `insufficient credits` | `💳 HeyGen credits khatam — app.heygen.com/billing pe top-up karo. Video banane par aapke credits use hote hain.` |
| 429 (+`Retry-After`) | `⏳ HeyGen ne rate limit lagayi — thori dair baad try karo.` |
| 400 invalid avatar/voice | `🖼️ Avatar ya voice ID ghalat — dobara Fetch karo.` |
| 4xx moderation/content | `🛡️ HeyGen ne script reject ki (moderation) — text naram karke dobara try karo.` |
| Timeout/network | standard A3 catalog |

### D5. Avatar Studio UI — dashboard SECTION (`frontend/studio-avatar.js`)

NOT full-screen (distinct from Media Studio). Mounted in `#avatar-root` (§0.1), lazy on first tab open.

Layout:
1. **Billing banner (always on top):** `⚠️ HeyGen API paid hai — har video aapke apne HeyGen credits se katega (trial plan me ~5 watermarked videos/day ki limit ho sakti hai). CIOS ka $0 sirf hamari hosting par hai.`
2. **Key row:** no key → setup instructions (1. `app.heygen.com → Settings → API` se key banao, 2. neeche paste karo, 3. Connect & Verify) + input + `Connect` button. With key → `✅ Connected` + `Disconnect` (two-step inline) — key value never displayed.
3. **Fetch row:** `🔄 Avatars lao` / `🔄 Voices lao` buttons → avatar thumbnail grid (radio-select cards: preview image, name) + voice list (name, language, gender, `▶` preview via `preview_audio_url` in `<audio>`).
4. **Create form:** script textarea (maxlength 1500 + counter), engine select (`avatar_iv` default — label notes "default; cost model ke hisaab se"), aspect select, captions toggle (`caption.srt` sidecar), title input, `🎬 Video banao` → progress area (status text + attempt count) → preview + download.
5. **Video Agent mode:** separate sub-panel: prompt textarea (maxlength 10000), optional voice/avatar override, `🤖 Agent se video banao` → same polling/preview flow (note: `mode:"generate"` fixed — one-shot).
6. **History:** table from route 8: thumbnail, title, status, date, preview/download, delete (two-step inline).
7. **Honest labels:** every control LIVE except: engine cost notes marked `(andaza)`; if HeyGen ever returns credit info, a pill appears — otherwise no fake balance widget.

**Without a key:** sections 3–6 render **disabled with one-line explanations** (`"Pehle API key connect karo"`), never as dead-looking buttons.

---

## C. 7-gates checklist & test plan (all builders)

### C1. Gates → v2 features mapping

| Gate | v2 features mapped | What the final test agent must verify |
|---|---|---|
| **G1** Crash paths & live deps | unified `api()` (§A3); ai33 proxy (§B2); HeyGen proxy (§D2); polling loops | Every third-party failure (401/402/429/5xx/timeout/network, `success:false`, malformed task JSON, unknown poll status) → friendly Roman Urdu message, never raw 500/traceback; every ai33/HeyGen endpoint ID used TODAY is live (re-check docs); fallback: unknown poll statuses treated as in-progress, never as completed |
| **G2** Auth & abuse | key-store (§0.2); media/avatar caps; throttle paths; no-retry-on-POST | All `/api/v1/media/*` + `/api/v1/avatar/*` require JWT (401 without); caps atomic per (user, day, provider) — concurrent creates can't overshoot; POST create endpoints never auto-retried by frontend; per-IP throttle includes new create paths |
| **G3** Input validation | TTS 20k chars, speed 0.5–1.5, voice_id prefix check, clone ≤10MB audio/*, script ≤1500, agent prompt ≤10000, `provider` allowlist (7 prefixes) | Server rejects + client pre-validates; 422 → `friendly422` inline; oversized upload → 413 friendly before proxying |
| **G4** Secrets | Fernet+HKDF key-store; `xi-api-key` / `X-Api-Key` handling | Keys encrypted at rest (decrypt roundtrip test); never in logs/responses/URLs/reports; verify-first never stores bad keys; transient handling if a key is pasted for tests (env-only, temp DB) |
| **G5** HTTP client safety | `ai33_client.py`, `heygen_client.py` proxy-safe construction | Same regression test as `ai_client` (bracketed-IPv6 NO_PROXY must not crash); timeout on every outbound call (60s); connection reuse; never log proxy URL or API keys |
| **G6** Frontend QA | button state machine; toasts; print reports; Studio tabs; honest labels | Every button wired; loading renders; double-submit guard on all quota/credit-spending actions; Enter submits; zero `alert()`; no enabled button without a working route (LIVE vs JALD AA RAHA HAI audit); print report contains date+inputs+outputs |
| **G7** Deploy reality | `/api/v1` cutover; `cryptography` dep; ephemeral SQLite | `requirements.txt` pinned + `pip-audit` clean; old `/api/*` paths 404 (clean cut verified); `render.yaml` health → `/api/v1/health`; CSP still without `unsafe-inline` (no new inline handlers); SQLite-ephemeral consequences documented for new tables |

### C2. Test plan — what builders must add

**Backend (pytest, all green required):**
- `tests/test_v1_routes.py`: every `/api/v1/*` route responds with the v2 envelope; old `/api/*` (non-v1) paths 404; `X-Request-ID` present on success, 401, 422, throttle-429, and unhandled-500 responses and appears in server logs.
- `tests/test_provider_keys.py`: Fernet roundtrip with temp `JWT_SECRET`; `get_provider_key` returns None when missing; key never appears in any API response body (assert on `GET key`/`POST key` responses); wrong-secret decrypt → treated as missing (re-enter prompt path).
- `tests/test_media.py` (B): **mock ai33.pro** via `httpx.MockTransport` (or monkeypatched client) — NEVER hit the real API (assert mock-only: transport raises on any unmocked host). Cover ALL 16 routes: key verify 401→400, voices provider-required 422, tts multipart shape sent to ai33, task polling `pending→processing→completed` transitions, `success:false` mapping, 413 on >10MB clone upload, daily cap 429 on 21st create, task-delete returns refunded flag.
- `tests/test_avatar.py` (D): **mock api.heygen.com** the same way. Cover ALL 11 routes: verify 401→400, looks-list normalization, voices `preview_audio_url` passthrough, create body contains `mode` only for agents / `type:"avatar"` for videos, polling `pending→processing→completed` and `failed` mapping, 401/402/429/moderation-4xx error catalog entries, per-user namespacing (user A can't see B's `avatar_jobs`), daily cap 3 enforced.
- `tests/test_throttle_v1.py`: v1 create paths (AI ×6, media ×6, avatar ×2) all throttled per-IP.

**Frontend (no JS test infra at $0 — static + manual, documented honestly):**
- `scripts/frontend_smoke.py` (A writes): static assertions — exactly one `fetch` wrapper (`CIOSApi.api`); `AbortController` present; no `alert(`; retry logic only on GET + max 1; `X-Request-ID` read from headers; `#toast-stack`, `#report-print` (direct body child), `@media print` present. Fails the build if violated. (Static, not behavioral — labeled as such.)
- Manual QA matrix → `TEST-REPORT.md`: every button (wired/loading/error/double-submit), toast types, request-ID in error toast, print→PDF per feature (6), Studio 7 tabs end-to-end with mocked backend, Avatar Studio full flow with mocked backend, honest-label audit (no enabled-but-dead control).

---

## Appendix E. Settings, deps, migration checklist

**`config.py` additions:** `MEDIA_HTTP_TIMEOUT=60`, `MEDIA_DAILY_CREATE_CAP=20`, `MEDIA_TTS_DAILY_CHARS=100000`, `AVATAR_DAILY_CREATE_CAP=3`, `AI33_BASE_URL="https://api.ai33.pro"`, `HEYGEN_BASE_URL="https://api.heygen.com"`.
**`requirements.txt`:** add `cryptography==<latest pip-audit-clean pin>` (B pins + audits).
**DB migrations:** `provider_keys`, `media_usage`, `avatar_jobs` via `CREATE TABLE IF NOT EXISTS` in `init_db` (B adds first two, D adds third; single-worker, no version table needed at this scale — document).
**Cutover checklist (A):** router prefixes → `/api/v1`; `auth.py` prefix → `/api/v1/auth`; `THROTTLE_PATHS` → v1 set; `render.yaml` health path; frontend all fetch paths → v1; README API section; old paths return 404 (verify in tests).

**$0 reaffirmation:** no new paid services; ai33.pro/HeyGen spend the USER's credits on the USER's keys — our hosting/compute stays $0; `cryptography` ships wheels (no build cost); browser-print PDF = zero deps.
