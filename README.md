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
├── routers/          # 6 feature endpoints (ideas, research, scripts, packaging, seo, niche)
├── frontend/         # dashboard (index.html, styles.css, app.js)
├── brain/            # Viral Video Brain v4 (knowledge base)
└── tests/            # pytest suite
```

## 🔌 API (agar khud use karna ho)

- `POST /api/ideas` `{"niche": "...", "audience": "...", "count": 10}`
- `POST /api/research` `{"niche": "...", "examples": "..."}`
- `POST /api/scripts` `{"topic": "...", "duration_sec": 60, "audience": "..."}`
- `POST /api/packaging` `{"topic": "...", "audience": "..."}`
- `POST /api/seo` `{"title": "...", "topic": "..."}`
- `POST /api/niche` `{"niche": "..."}`
- `GET /api/history`, `GET /api/usage`, `GET /api/health`

---
*Private repo — zaati istemal ke liye.*
