"""CIOS — Content Intelligence Operating System for YouTube (zero-cost edition).

Run:  python app.py        ->  http://127.0.0.1:8000
Test: pytest
"""
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import ai_client
from config import settings
from database import get_history, init_db
from routers import ideas, niche, packaging, research, scripts, seo

BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR / "frontend"

app = FastAPI(title="CIOS", description="Content Intelligence Operating System — $0 edition", version="1.0.0")

# Eager init (idempotent): tables har run/test me maujood hon.
init_db()

app.include_router(ideas.router)
app.include_router(research.router)
app.include_router(scripts.router)
app.include_router(packaging.router)
app.include_router(seo.router)
app.include_router(niche.router)


@app.exception_handler(ai_client.QuotaExceeded)
async def quota_handler(request: Request, exc: ai_client.QuotaExceeded):
    return JSONResponse(status_code=429, content={"ok": False, "error": str(exc)})


@app.exception_handler(ai_client.MissingApiKey)
async def missing_key_handler(request: Request, exc: ai_client.MissingApiKey):
    return JSONResponse(status_code=400, content={"ok": False, "error": str(exc)})


@app.exception_handler(ai_client.AllModelsFailed)
async def models_failed_handler(request: Request, exc: ai_client.AllModelsFailed):
    return JSONResponse(status_code=502, content={"ok": False, "error": str(exc)})


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "models": settings.model_chain,
        "key_configured": bool(settings.openrouter_api_key),
        "usage": ai_client.usage_today(),
    }


@app.get("/api/history")
def history(limit: int = Query(default=20, ge=1, le=100)):
    return {"ok": True, "items": get_history(limit)}


@app.get("/api/usage")
def usage():
    return {"ok": True, **ai_client.usage_today()}


if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(str(FRONTEND_DIR / "index.html"))


if __name__ == "__main__":
    init_db()
    uvicorn.run("app:app", host=settings.app_host, port=settings.app_port, reload=False)
