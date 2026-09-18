# CIOS Frontend Audit — Findings Only (no fixes applied)

**Date:** 2026-09-18 · **Auditor:** Frontend/QA subagent (Phase 2, audit only)
**Scope:** `frontend/index.html`, `frontend/app.js`, `frontend/styles.css` cross-checked against `app.py` and `routers/*.py` (`models.py`, `ai_client.py`, `database.py`, `config.py` read for contracts).
**Method:** full file reads + greps verifying every JS-referenced `id`/class exists in HTML/CSS. No code changed. No server started (not needed — mapping verified statically).

## Verdict summary

- **Broken / unwired buttons: none.** All 7 action buttons have working handlers and all call real backend endpoints that exist with matching method + URL.
- **Missing CSS/JS files: none.** `/static/styles.css` and `/static/app.js` both exist and are served via `app.mount("/static", …)` in `app.py`. Every class referenced in HTML/JS is defined in `styles.css`; every `id` referenced in `app.js` exists in `index.html`.
- **Dead markup: none** (one dead CSS rule + one self-defeating rule, noted below).
- Ranked issues: **0 Critical · 3 High · 5 Medium · 5 Low.**

---

## 1. Button / control map

| # | Button (id/label/section) | Handler in app.js | Loading state | Error state | Backend endpoint (method + URL + source) | Notes |
|---|---|---|---|---|---|---|
| 1 | `Ideas Nikalo` — tab-ideas | ✅ `runIdeas()` (inline `onclick`) | ⚠️ broken — see H-1 | ✅ via `callApi` | ✅ `POST /api/ideas` — `routers/ideas.py` (`create_ideas`, `response_model=ApiResult`) | Request body `{niche, audience, count}` matches `IdeasRequest` |
| 2 | `Analyze Karo` — tab-research | ✅ `runResearch()` | ⚠️ broken — see H-1 | ✅ via `callApi` | ✅ `POST /api/research` — `routers/research.py` (`analyze`) | Body `{niche, examples}` matches `ResearchRequest`; `examples` optional |
| 3 | `Script Likho` — tab-scripts | ✅ `runScript()` | ⚠️ broken — see H-1 | ✅ via `callApi` | ✅ `POST /api/scripts` — `routers/scripts.py` (`write_script`) | Body `{topic, duration_sec, audience}` matches `ScriptRequest` |
| 4 | `Pack Banao` — tab-packaging | ✅ `runPackaging()` | ⚠️ broken — see H-1 | ✅ via `callApi` | ✅ `POST /api/packaging` — `routers/packaging.py` (`pack`) | Body `{topic, audience}` matches `PackagingRequest` |
| 5 | `SEO Pack Banao` — tab-seo | ✅ `runSeo()` | ⚠️ broken — see H-1 | ✅ via `callApi` | ✅ `POST /api/seo` — `routers/seo.py` (`seo_pack`) | Body `{title, topic}` matches `SeoRequest` |
| 6 | `Validate Karo` — tab-niche | ✅ `runNiche()` | ⚠️ broken — see H-1 | ✅ via `callApi` | ✅ `POST /api/niche` — `routers/niche.py` (`validate`) | Body `{niche}` matches `NicheRequest` |
| 7 | `Refresh` — tab-history | ✅ `loadHistory()` (inline `onclick`) | ✅ sets `innerHTML = "⏳ load ho raha hai…"` | ✅ `try/catch` → `<span class="err">` | ✅ `GET /api/history?limit=20` — `app.py` (`history()`, `Query(ge=1, le=100)`) | Fields used (`feature`, `model`, `created_at`, `input_json`) all returned by `get_history()` |
| 8 | 7 tab buttons (`#tabs button[data-tab]`: ideas, research, scripts, packaging, seo, niche, history) | ✅ loop at top of `app.js` toggles `.active` on button + `#tab-<name>` section | n/a (client-side) | n/a | n/a (client-side; history tab additionally fires `loadHistory()` → `GET /api/history`) | All 7 `data-tab` values match existing section ids |
| 9 | (auto) usage pill `#usage` in header | ✅ `refreshUsage()` on `init()` and after every `callApi` | shows `…` placeholder | ❌ silent `catch {}` — pill stuck at `…` on failure | ✅ `GET /api/usage` — `app.py` (`usage()`); reads `j.used`/`j.cap` from `usage_today()` → `{used, cap}` | Contract matches |
| 10 | (auto) footer `#model` | ✅ `init()` | n/a | ❌ no fallback — footer ends with dangling `•` on failure | ✅ `GET /api/health` — `app.py` (`health()`); reads `j.models[0]`, `j.key_configured` | Contract matches; missing-key warning shown |

**Error-state detail (`callApi`, used by buttons 1–6):** handles three cases — non-OK HTTP or `j.ok === false` → `Error: …`; network/JSON-parse failure → `Request fail: …`. FastAPI exception handlers (`QuotaExceeded`→429, `MissingApiKey`→400, `AllModelsFailed`→502 in `app.py`) all return `{"ok": false, "error": …}`, which `callApi` displays. **XSS: clean** — all dynamic output passes through `esc()` (including `j.error`, `j.model`, `e.message`, and the generic `render()`).

---

## 2. Ranked issues

### High

**H-1 — Loading indicator never renders (self-defeating CSS + JS interaction).**
`callApi()` does `out.classList.add("loading"); out.innerHTML = "";`. Because the element is now empty, `styles.css` rule `.out:empty{display:none}` matches, so the whole box — **including** the `.loading::after` pseudo-element with `"⏳ soch raha hai…"` — is hidden. Net effect: **zero visual feedback during AI calls that can run 10–120s** (`request_timeout: 120` in `config.py`; free models + 3.5s pacing in `ai_client.py`). Users will assume the click didn't register and re-click (see H-2). (`loadHistory` is unaffected — it sets non-empty placeholder text.)

**H-2 — No double-submit guard; each click burns daily free quota.**
No button is ever disabled during an in-flight request. `styles.css` even defines `main button:disabled` styling, but `app.js` never sets `disabled`. Double-clicking any generator fires parallel `POST`s; each successful one increments the daily counter toward `daily_request_cap: 48` (`ai_client.generate`). Under the $0 hard constraint, quota is the scarcest resource — accidental double spend is the most expensive UX failure this UI can produce.

**H-3 — Frontend does not enforce backend validation ranges → cryptic `HTTP 422` errors.**
- `ideas-count`: HTML `min="3" max="20"` only constrains the spinner, not typed values. `parseInt(v("ideas-count")) || 10` sends out-of-range values (e.g. `2`, `25`) raw → `IdeasRequest` (`ge=3, le=20`) → 422.
- `script-dur`: same pattern vs `ScriptRequest` (`ge=15, le=1800`).
- Single-char niche / 1–2-char titles: frontend `alert` only checks non-empty, but backend requires `min_length=2` (niche) / `min_length=3` (title, topic) → 422.
- `callApi` then shows only `Error: HTTP 422` — the useful FastAPI `detail` array is discarded (`j.error || ("HTTP " + r.status)`), so the user gets no guidance on what was wrong.

### Medium

**M-1 — Enter key doesn't submit.** Inputs are not wrapped in `<form>`, so pressing Enter in any text field does nothing; users must click the button. Expect repeated "it's broken" reports.

**M-2 — Validation via blocking `alert()` dialogs** (`"Niche likho"`, `"Title aur topic dono likho"`, …). Functional but jarring; no inline field-level messaging.

**M-3 — No request timeout / cancel.** `fetch` has no `AbortController`; a hung free-model call leaves the user stuck with no way to abort (backend gives up at 120s).

**M-4 — 422 detail discarded.** As noted in H-3: `callApi` ignores FastAPI's `{"detail": [...]}` body, rendering all validation failures as bare `HTTP 422`.

**M-5 — Results not announced to assistive tech.** Output divs have no `aria-live` region; screen-reader users get no notification when results arrive. Tab buttons lack `role="tab"` / `aria-selected`.

### Low

**L-1 — Usage pill silent failure.** `refreshUsage()` swallows errors (`catch {}`); pill stays at `…` with no indication.
**L-2 — Footer dangling bullet.** If `/api/health` fails, `#model` stays empty → footer reads `…free models • ` with trailing separator.
**L-3 — History tab refetches on every tab click** (idempotent GET, harmless but wasteful).
**L-4 — No `autocomplete` attributes** on inputs (niche/topic fields would benefit).
**L-5 — Dead CSS rule:** `main button:disabled` is styled but never triggered (symptom of H-2, listed here as the dead-markup/style note).

---

## 3. UX gaps (no empty-state / validation / labeling issues)

- **Empty states:** only the history tab has one (`<i>Abhi koi history nahi.</i>`). Generator outputs start as hidden empty boxes (`.out:empty{display:none}`) — acceptable, but combined with H-1 there is no "working" state at all.
- **Pre-submit validation:** presence checks exist (via `alert`), but **range/length validation is missing** (H-3). No trimming issues — `v()` trims.
- **Labels:** all inputs use implicit `<label>` wrapping — correctly associated. Placeholders are Roman Urdu examples — consistent with UI language.
- **Error copy:** backend error strings are user-friendly Roman Urdu (quota, missing key, all-models-failed); the frontend surfaces them verbatim — good. Only the 422 path is user-hostile (M-4).
- **Quota visibility:** the usage pill (`⚡ used/cap`) updates after each call — good for the $0 constraint. It does not warn as the cap approaches.

## 4. Dead markup / missing-file check

- **Missing files: none.** `index.html` references only `/static/styles.css` and `/static/app.js`; both exist in `frontend/` and are mounted at `/static` in `app.py`.
- **Dead markup: none.** All 7 sections, all inputs, `#usage`, `#model`, `#tabs` are referenced by JS; no orphaned elements or commented-out blocks.
- **Dead/contradictory styles:** `main button:disabled` (L-5) and `.out:empty{display:none}` conflicting with the JS loading pattern (H-1).

## 5. Endpoint contract verification (for the backend team)

| Frontend expectation | Backend reality | Match |
|---|---|---|
| `POST /api/*` → `{ok, model, cached, data}` | `finish()` in `routers/__init__.py` returns exactly this shape | ✅ |
| `j.cached` boolean for "cache se" note | `ai_client.generate` returns `cached: True/False` | ✅ |
| `GET /api/history` → `{items: [{feature, model, created_at, input_json}]}` | `get_history()` selects those columns | ✅ |
| `GET /api/usage` → `{used, cap}` | `usage_today()` → `{today, used, cap}` | ✅ |
| `GET /api/health` → `{models[], key_configured}` | `health()` returns `settings.model_chain`, `bool(key)` | ✅ |
| Error shape `{ok:false, error}` on 429/400/502 | exception handlers in `app.py` | ✅ |
| 422 validation errors | FastAPI default `{"detail": [...]}` — **not** consumed by frontend | ⚠️ (M-4) |

---
*Audit only — no code was modified. All findings verified against the source files listed above.*
