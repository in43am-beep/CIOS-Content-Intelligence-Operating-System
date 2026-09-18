# 🧠 CIOS — Content Intelligence Operating System for YouTube

**$0 edition.** Poora working app: FastAPI backend + dashboard frontend + tests. AI ke liye sirf **OpenRouter ke free models** — koi kharcha nahi, card nahi chahiye.

## ✨ Features (opportunity-first loop)

| Tab | Kya karta hai | Brain source |
|---|---|---|
| 💡 Ideas | Niche pe scored video ideas (30 me se score) | systems/01, 02 |
| 🔍 Research | Outlier patterns + transferable angles | brain §14 Packaging Science |
| 📝 Script | Hook + poora script + beats + CTA | systems/03, scripting science |
| 🖼️ Packaging | 5 titles + 3 thumbnail concepts + best combo | systems/10 |
| 🚀 SEO | Tags, description, hashtags, chapters | systems/09 |
| 🎯 Niche Check | Validation scorecard (100 me se) | systems/02 |
| 📚 History | Pichli saari generations | SQLite |

Har feature ke peeche **Viral Video Brain** ka relevant hissa system prompt ki tarah laga hai (`brain/` folder).

## 🚀 Chalane ka tareeqa (3 steps)

```bash
cd cios
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # phir .env me OPENROUTER_API_KEY dalo
python app.py
```

Browser me kholo: **http://127.0.0.1:8000**

Free API key: https://openrouter.ai/keys (email/GitHub login, **koi card nahi**).

## 💰 $0 kaise?

- **Models:** sirf `:free` suffix wale — $0 per token. Primary + 3 fallbacks; ek busy ho to agla auto try hota hai.
- **Limits:** free tier = 20 req/min, 50 req/day. App khud 3.5s gap rakhti hai, 48/day pe politely rok deti hai, aur repeat prompts ka jawab **cache** se deti hai (quota bachta hai).
- **Data:** SQLite file — koi database/server kharcha nahi.
- **Hosting:** local run $0. (Optional: frontend GitHub Pages + backend kisi free tier pe — guide baad me.)

> Note: free models peak hours me slow/limit ho sakte hain — is liye fallback chain + caching built-in hai.

## 🔑 Environment variables (.env)

| Variable | Zaroori? | Default | Kaam |
|---|---|---|---|
| `OPENROUTER_API_KEY` | Haan (AI ke liye) | — | OpenRouter free key: https://openrouter.ai/keys |
| `JWT_SECRET` | Haan (prod me) | dev default | Login tokens sign karne ke liye — prod me lambi random string rakho |
| `AUTH_REQUIRED` | Nahi | `true` | `false` karo to login ke baghair chalta hai (local testing) |
| `DAILY_REQUEST_CAP` | Nahi | `48` | Har user ka roz ka AI request limit |
| `APP_HOST` / `APP_PORT` | Nahi | `127.0.0.1` / `8000` | Server kis address/port pe sune |

> 👑 **Pehla signup admin banta hai.** Fresh database pe jo pehla account banta hai usay admin rights milte hain — us ke baad Admin tab se users aur quota manage karo.

## 🚢 Publish karna (Render — free tier)

1. Code GitHub repo me push karo (private repo theek hai).
2. [Render dashboard](https://dashboard.render.com) → **New +** → **Web Service** → apna repo connect karo.
3. Settings:
   - **Build command:** `pip install -r requirements.txt`
   - **Start command:** `uvicorn app:app --host 0.0.0.0 --port $PORT --workers 1`
   - **Environment:** Python 3
4. **Environment tab** me secrets set karo: `OPENROUTER_API_KEY`, `JWT_SECRET` (lambi random string), baqi defaults theek hain.
5. Deploy ke baad health check kholo: `https://<tumhara-app>.onrender.com/api/v1/health` → `{"ok": true, ...}` aana chahiye.
6. Browser me app kholo, **pehla signup karo — wahi admin banega.**

⚠️ **Do zaroori caveats (free hosting):**
- **Single worker lazmi:** start command me `--workers 1` rakho. SQLite file-based hai — zyada workers lagao ge to database lock/corrupt ho sakti hai.
- **Ephemeral disk:** Render ke free tier pe har deploy/restart par disk wipe ho jati hai — matlab `cios.db` (users, history, usage, **provider API keys**, media/avatar usage counters) **reset ho jayega**. Restart ke baad ai33.pro / HeyGen keys dobara connect karni hongi (keys Fernet-encrypted hain — disk wipe = keys lost). Permanent data chahiye to paid disk ya external Postgres (wo $0 se bahar hai).
- **Known limit:** asli OpenRouter key ke baghair live AI test mumkin nahi — free key https://openrouter.ai/keys se 2 minute me milti hai, koi card nahi chahiye.

## 🧪 Testing

```bash
pytest -v
```

Saare AI calls mock hain — tests me **koi API key ya quota kharch nahi hota**.

## 📁 Structure

```
cios/
├── app.py            # FastAPI entrypoint (API + frontend serve karta hai)
├── config.py         # settings, free model fallback chain
├── ai_client.py      # OpenRouter client: fallback, quota guard, cache
├── brain_loader.py   # brain .md files -> system prompts
├── database.py       # SQLite: cache + history + usage
├── models.py         # request/response schemas
├── routers/          # /api/v1/* endpoints: 6 AI features + admin + media/avatar studios
├── frontend/         # dashboard (index.html, styles.css, app.js)
├── brain/            # Viral Video Brain v4 (knowledge base)
└── tests/            # pytest suite
```

## 🔌 API v1

Sab routes `/api/v1` prefix ke neeche hain. **Versioning policy:** breaking changes `/api/v2` par ayengi; publish ke baad v1 freeze hai. Purana bina-version `/api/*` prefix **404** deta hai (clean cut — koi deprecated alias nahi).

**Auth:** har protected request par header `Authorization: Bearer <JWT>`. Token 7 din me expire hota hai. 401 aaye to dobara login karo — frontend khud login view par le jata hai.

**Envelope:**
```json
// success
{"ok": true, "data": {...}}
// error
{"ok": false, "error": "Roman Urdu message"}
```
Mustasna (deliberate, documented):
- 6 AI POSTs: `{ok, model, cached, data}` — `model`/`cached` top-level metadata hain
- `GET /api/v1/health`: `{ok, version, key_configured, [models]}` — monitors sirf `ok` parhte hain
- 422: FastAPI ka `{detail: [...]}` — frontend `friendly422()` se Roman Urdu banata hai

**X-Request-ID:** har response (200, 4xx, 5xx sab) par `X-Request-ID` header aata hai. Error aaye to ye ID support ko bhejo — server logs me isi se request milti hai. Har POST par frontend `X-Idempotency-Key` bhejta hai (server log me note hota hai).

**Error catalog:**

| Code | Matlab (Roman Urdu) |
|---|---|
| 400 | Input ya key ka masla (message me detail) |
| 401 | Session khatam — dobara login karo |
| 403 | Ijazat nahi (admin-only) |
| 404 | Ghalat path ya user nahi mila |
| 422 | Validation — kaunsa field ghalat hai, message me |
| 429 (quota) | Aaj ki limit poori — kal try karo (har try 1 quota kharch karta hai) |
| 429 (throttle) | Bohat zyada requests — ek minute ruk kar try karo |
| 5xx | Server masla — `X-Request-ID` support ko bhejo |

**Endpoints:**

| Method | Path | Auth | Body / Notes |
|---|---|---|---|
| POST | `/api/v1/auth/signup` | nahi | `{"email","password" (≥8 chars)}` → `{ok, data:{token, user}}` |
| POST | `/api/v1/auth/login` | nahi | `{"email","password"}` → `{ok, data:{token, user}}` |
| GET | `/api/v1/auth/me` | haan | → `{ok, data:{user}}` |
| POST | `/api/v1/ideas` | haan | `{"niche","audience","count"}` — quota kharch |
| POST | `/api/v1/research` | haan | `{"niche","examples"}` — quota kharch |
| POST | `/api/v1/scripts` | haan | `{"topic","duration_sec","audience"}` — quota kharch |
| POST | `/api/v1/packaging` | haan | `{"topic","audience"}` — quota kharch |
| POST | `/api/v1/seo` | haan | `{"title","topic"}` — quota kharch |
| POST | `/api/v1/niche` | haan | `{"niche"}` — quota kharch |
| GET | `/api/v1/history?limit=20` | haan | → `{ok, data:{items, limit}}` (sirf apni rows) |
| GET | `/api/v1/usage` | haan | → `{ok, data:{used, cap, day}}` |
| GET | `/api/v1/health` | nahi | `{ok, version, key_configured}` (+`models` sirf valid token par) |
| GET | `/api/v1/admin/users` | admin | → `{ok, data:{users}}` |
| GET | `/api/v1/admin/usage?day=YYYY-MM-DD` | admin | → `{ok, data:{day, rows}}` |
| POST | `/api/v1/admin/quota` | admin | `{"user_id","cap"}` → `{ok, data:{user_id, cap}}` |

**Quota model:** 48/day **attempt-based** — har try (kamyab ho ya na ho) 1 quota kharch karta hai. Cache hit quota nahi kharch karta (cache quota se pehle check hota hai). Per-IP throttle: 30 POST/min AI/media/avatar create paths par.

**$0 notes:** SQLite file-based hai — free hosts par restart/deploy par wipe ho sakta hai (users/history/usage reset). Single worker lazmi (`--workers 1`).

---
*Private repo — zaati istemal ke liye.*
