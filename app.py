"""CIOS — Content Intelligence Operating System for YouTube (zero-cost edition).

Run:  python app.py        ->  http://127.0.0.1:8000
Test: pytest
"""
import logging
import sys
import time
import uuid
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path

import jwt
import uvicorn
from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

import ai_client
import auth as auth_module
import brain_loader
from auth import AuthError, ForbiddenError, get_current_user
from config import settings
from database import get_history, init_db, startup_maintenance, usage_for_user
from routers import admin, ideas, niche, packaging, research, scripts, seo

BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR / "frontend"

# stdlib logging -> stdout (platforms collect it). API key KABHI log nahi hoti.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    stream=sys.stdout,
    force=True,
)
log = logging.getLogger("cios.app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.ensure_auth_config()  # JWT_SECRET missing + AUTH_REQUIRED -> fail loud
    init_db()
    # v2 studio tables (B/D): idempotent CREATE TABLE IF NOT EXISTS. lifespan
    # me ensure — import-time side effect nahi (C-5; provider_keys/avatar
    # modules bhi lazily ensure karte hain, ye startup determinism ke liye).
    try:
        import provider_keys as _pk
        _pk.ensure_tables()
    except Exception as e:
        log.warning("provider_keys tables ensure failed: %r", e)
    try:
        from routers.avatar import init_avatar_tables as _init_avatar_tables
        _init_avatar_tables()
    except Exception as e:
        log.warning("avatar tables ensure failed: %r", e)
    startup_maintenance()  # history pruning + daily cache purge
    brain_loader.fail_if_missing()  # koi brain file missing -> fail loud
    log.info("model chain: %s", settings.model_chain)
    log.info("auth_required=%s", settings.auth_required)
    yield


app = FastAPI(
    title="CIOS",
    description="Content Intelligence Operating System — $0 edition",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(auth_module.router)
app.include_router(admin.router)
app.include_router(ideas.router)
app.include_router(research.router)
app.include_router(scripts.router)
app.include_router(packaging.router)
app.include_router(seo.router)
app.include_router(niche.router)

# ---- v2 studios (Builders B & D): defensively include — they may be absent
# or have unmet optional deps when A finishes. Koi bhi failure -> warning log
# karke continue (Gate 7: app kabhi bhi boot honi chahiye); integration agent
# deps/pins reconcile karega.
try:
    from routers.media import router as media_router  # noqa: F401
    app.include_router(media_router)
    log.info("media router loaded (/api/v1/media/*)")
except ImportError as e:
    log.warning("media router not loaded (Builder B pending/missing dep): %r", e)

try:
    from routers.avatar import router as avatar_router  # noqa: F401
    app.include_router(avatar_router)
    log.info("avatar router loaded (/api/v1/avatar/*)")
except ImportError as e:
    log.warning("avatar router not loaded (Builder D pending/missing dep): %r", e)


# ------------------------------------------------- security headers (C-2) ---
CSP = "default-src 'self'; script-src 'self'; style-src 'self'"


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        resp = await call_next(request)
        _set_security_headers(resp)
        return resp


def _set_security_headers(resp):
    """4 security headers — middleware AND unhandled_handler (F-1) dono me."""
    resp.headers["Content-Security-Policy"] = CSP
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "same-origin"
    return resp


# --------------------------------- request id (V2-DESIGN.md §A5) ---
class RequestIdMiddleware(BaseHTTPMiddleware):
    """Har request pe uuid4 (16 hex chars) -> X-Request-ID header har response
    par (errors par bhi) + request.state.request_id server logs ke liye."""

    async def dispatch(self, request: Request, call_next):
        rid = uuid.uuid4().hex[:16]
        request.state.request_id = rid
        if k := request.headers.get("X-Idempotency-Key"):
            request.state.idempotency_key = k[:64]
        resp = await call_next(request)
        resp.headers["X-Request-ID"] = rid
        return resp


# ------------------------------------------------- request logging (C-11) ---
def _token_user_id(request: Request) -> str:
    authz = request.headers.get("Authorization", "")
    if not authz.lower().startswith("bearer "):
        return "-"
    try:
        payload = jwt.decode(authz[7:].strip(), options={"verify_signature": False})
        return str(payload.get("sub", "?"))
    except Exception:
        return "?"


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start = time.perf_counter()
        rid = getattr(request.state, "request_id", "-")
        idem = getattr(request.state, "idempotency_key", "-")
        try:
            resp = await call_next(request)
            status = resp.status_code
        except Exception:
            ms = (time.perf_counter() - start) * 1000
            log.info("%s %s 500 %.0fms user=%s rid=%s idem=%s", request.method,
                     request.url.path, ms, _token_user_id(request), rid, idem)
            raise
        ms = (time.perf_counter() - start) * 1000
        log.info("%s %s %s %.0fms user=%s rid=%s idem=%s", request.method,
                 request.url.path, status, ms, _token_user_id(request), rid, idem)
        return resp


# --------------------------------- per-IP abuse throttle (B-2, H2; in-mem) ---
THROTTLE_PATHS = {
    # 6 AI endpoints (v1, quota-spending)
    "/api/v1/ideas", "/api/v1/research", "/api/v1/scripts",
    "/api/v1/packaging", "/api/v1/seo", "/api/v1/niche",
    # Media Studio create paths (§B2 — Builder B; router abhi/defensively)
    "/api/v1/media/tts", "/api/v1/media/dialogue", "/api/v1/media/voice-clone",
    "/api/v1/media/music", "/api/v1/media/image", "/api/v1/media/video",
    # Avatar Studio create paths (§D2 — Builder D; router abhi/defensively)
    "/api/v1/avatar/videos", "/api/v1/avatar/agents",
}
THROTTLE_LIMIT = 30  # requests per minute per IP
_throttle_hits: dict[str, list[float]] = defaultdict(list)


class ThrottleMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path.rstrip("/") or "/"
        if request.method == "POST" and path in THROTTLE_PATHS:
            ip = request.client.host if request.client else "?"
            now = time.monotonic()
            hits = [t for t in _throttle_hits[ip] if now - t < 60]
            if len(hits) >= THROTTLE_LIMIT:
                log.warning("throttle 429 ip=%s path=%s", ip, path)
                return JSONResponse(
                    status_code=429,
                    content={"ok": False,
                             "error": "Bohat zyada requests — ek minute ruk kar try karo."},
                )
            hits.append(now)
            _throttle_hits[ip] = hits
            if len(_throttle_hits) > 5000:  # memory hygiene
                _throttle_hits.clear()
        return await call_next(request)


app.add_middleware(ThrottleMiddleware)
app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
# LAST add = FIRST execute (outermost): header har response par aata hai —
# throttle-429s aur ExceptionMiddleware ke 4xx/5xx par bhi. Generic-500 path
# (ServerErrorMiddleware, is se bhi bahar) ke liye unhandled_handler me
# headers explicitly set hain (F-1 fix).
app.add_middleware(RequestIdMiddleware)


# ------------------------------------------------------- exception handlers ---
@app.exception_handler(ai_client.QuotaExceeded)
async def quota_handler(request: Request, exc: ai_client.QuotaExceeded):
    log.info("quota rejected path=%s", request.url.path)
    return JSONResponse(status_code=429, content={"ok": False, "error": str(exc)})


@app.exception_handler(ai_client.MissingApiKey)
async def missing_key_handler(request: Request, exc: ai_client.MissingApiKey):
    return JSONResponse(status_code=400, content={"ok": False, "error": str(exc)})


@app.exception_handler(ai_client.AllModelsFailed)
async def models_failed_handler(request: Request, exc: ai_client.AllModelsFailed):
    return JSONResponse(status_code=502, content={"ok": False, "error": str(exc)})


@app.exception_handler(AuthError)
async def auth_error_handler(request: Request, exc: AuthError):
    return JSONResponse(status_code=401, content={"ok": False, "error": str(exc)})


@app.exception_handler(ForbiddenError)
async def forbidden_handler(request: Request, exc: ForbiddenError):
    return JSONResponse(status_code=403, content={"ok": False, "error": str(exc)})


@app.exception_handler(Exception)
async def unhandled_handler(request: Request, exc: Exception):
    # F-1 fix (TEST-REPORT.md): @app.exception_handler(Exception) wale handlers
    # ServerErrorMiddleware me split ho jate hain — sab se bahar ki layer — is
    # liye response middleware stack se guzarta hi nahi. Security headers +
    # X-Request-ID yahan explicitly set karo.
    rid = getattr(request.state, "request_id", None) or uuid.uuid4().hex[:16]
    log.exception("unhandled error path=%s rid=%s", request.url.path, rid)
    resp = JSONResponse(status_code=500, content={"ok": False, "error": "Server error"})
    _set_security_headers(resp)
    resp.headers["X-Request-ID"] = rid
    return resp


# ----------------------------------------------------------------- routes ---
@app.get("/api/v1/health")
def health(request: Request):
    """Unauthenticated minimal view for platform health checks (audit L-2/M3).
    Detailed model info sirf authed users ko."""
    body = {"ok": True, "version": app.version,
            "key_configured": bool(settings.openrouter_api_key)}
    authz = request.headers.get("Authorization", "")
    if authz.lower().startswith("bearer ") and authz[7:].strip():
        try:
            jwt.decode(authz[7:].strip(), settings.jwt_secret, algorithms=["HS256"])
            body["models"] = settings.model_chain
        except jwt.InvalidTokenError:
            pass
    return body


@app.get("/api/v1/history")
def history(limit: int = Query(default=20, ge=1, le=100),
            current: dict = Depends(get_current_user)):
    """Sirf caller ki apni rows (per-user isolation, B-1). v2 envelope."""
    return {"ok": True, "data": {"items": get_history(limit, user_id=current["id"]),
                                "limit": limit}}


@app.get("/api/v1/usage")
def usage(current: dict = Depends(get_current_user)):
    """v2 envelope: {ok, data:{used, cap, day}} (§A5)."""
    u = usage_for_user(current["id"])
    return {"ok": True, "data": {"used": u["used"], "cap": u["cap"], "day": u["today"]}}


if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(str(FRONTEND_DIR / "index.html"))


if __name__ == "__main__":
    init_db()
    uvicorn.run("app:app", host=settings.app_host, port=settings.app_port, reload=False)
