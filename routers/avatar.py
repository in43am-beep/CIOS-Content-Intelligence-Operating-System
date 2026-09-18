"""HeyGen "Avatar Studio" proxy — /api/v1/avatar/* (Builder D, V2-DESIGN §D).

HeyGen usage bills the USER's own HeyGen credits — hamara $0 sirf CIOS ki
hosting par hai. API key kabhi browser ko nahi milti: frontend sirf inhi
routes se baat karta hai, key hamesha server par rehti hai.

Tables (is module me, CREATE TABLE IF NOT EXISTS at import — database.py
ko Builder D edit nahi karta):
  avatar_jobs  — per-user video/agent job namespace
  media_usage  — per-(user, day, provider) create caps (B ke sath shared,
                 provider='heygen' se D apna hissa track karta hai)

NEVER hit the real https://api.heygen.com in tests — tests MockTransport use
karte hain (tests/test_avatar.py).
"""
import json
import logging
import time
import uuid
from datetime import date
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, Path, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from auth import get_current_user
from database import get_conn

log = logging.getLogger("cios.avatar")

router = APIRouter(prefix="/api/v1/avatar", tags=["avatar"])

HEYGEN_BASE = "https://api.heygen.com"
HEYGEN_HTTP_TIMEOUT = 60  # §E: MEDIA_HTTP_TIMEOUT — har outbound call pe timeout
AVATAR_DAILY_CREATE_CAP = 3  # §E: AVATAR_DAILY_CREATE_CAP (routes 6 & 10)
SCRIPT_MAX_CHARS = 1500  # credit-burn guard (§D2)
AGENT_PROMPT_MAX_CHARS = 10000  # (§D2)

# ------------------------------------------------- shared key-store (§0.2) ---
# provider_keys.py Builder B ka hai. Agar abhi land nahi hua to routes 503
# dete hain (friendly), import crash nahi hota.
try:
    from provider_keys import (  # type: ignore
        delete_provider_key,
        get_provider_key,
        has_provider_key,
        set_provider_key,
    )
    _KEYSTORE_AVAILABLE = True
except ImportError:
    _KEYSTORE_AVAILABLE = False
    log.warning("provider_keys.py missing — avatar key routes disabled (Builder B pending)")


def _require_keystore():
    """503 JSONResponse jab key-store pending ho, warna None."""
    if not _KEYSTORE_AVAILABLE:
        return JSONResponse(status_code=503, content={
            "ok": False,
            "error": "Key-store abhi tayyar nahi (provider_keys.py pending) — "
                     "thori dair baad try karo."})
    return None


# --------------------------------------- proxy-safe httpx (Gate 5, §0.4) ---
# ai_client.py ka pattern verbatim copy: trust_env=False + validated proxy=.
# Proxy URL KABHI log nahi hoti (credentials embed hote hain).
_client: httpx.Client | None = None


def _proxy_url() -> str | None:
    import os
    from urllib.parse import urlparse

    for var in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        raw = (os.environ.get(var) or "").strip()
        if not raw:
            continue
        try:
            p = urlparse(raw)
            scheme, host, port = p.scheme, p.hostname, p.port
        except Exception:
            continue  # invalid value -> skip, try next var
        if scheme in ("http", "https") and host and port:
            log.info("proxy configured: yes (from %s)", var)
            return raw
    log.info("proxy configured: no")
    return None


def _http() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(
            timeout=HEYGEN_HTTP_TIMEOUT,
            trust_env=False,
            proxy=_proxy_url(),
        )
    return _client


# ------------------------------------------------------------------ DB ---
_AVATAR_JOBS_SQL = """
CREATE TABLE IF NOT EXISTS avatar_jobs (
    user_id INTEGER NOT NULL,
    video_id TEXT PRIMARY KEY,
    session_id TEXT,
    title TEXT,
    avatar_id TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
)
"""
# §0.3 — B create karta hai, D extend karta hai (IF NOT EXISTS: dono safe).
_MEDIA_USAGE_SQL = """
CREATE TABLE IF NOT EXISTS media_usage (
    user_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    provider TEXT NOT NULL,
    creates INTEGER NOT NULL DEFAULT 0,
    tts_chars INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, day, provider)
)
"""


def init_avatar_tables() -> None:
    """Idempotent — lifespan, tests, ya lazy route call se safe hai.
    Import-time call NAHI (C-5): har avatar_jobs touchpoint lazily ensure karta hai."""
    conn = get_conn()
    try:
        conn.execute(_AVATAR_JOBS_SQL)
        conn.execute(_MEDIA_USAGE_SQL)
        conn.commit()
    finally:
        conn.close()


def _record_job(user_id: int, video_id: str, status: str,
                session_id: str | None = None,
                title: str | None = None,
                avatar_id: str | None = None) -> None:
    """Upsert: status hamesha fresh, pehle wali user_id/title barkarar."""


class CreateCapExceeded(Exception):
    pass


def _spend_create_cap(user_id: int) -> None:
    """Atomic per-(user, day, provider='heygen') cap — concurrent creates
    overshoot nahi kar sakte (single UPDATE ... WHERE creates < cap)."""
    day = date.today().isoformat()
    conn = get_conn()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO media_usage (user_id, day, provider)"
            " VALUES (?,?, 'heygen')",
            (user_id, day),
        )
        cur = conn.execute(
            "UPDATE media_usage SET creates = creates + 1"
            " WHERE user_id=? AND day=? AND provider='heygen' AND creates < ?",
            (user_id, day, AVATAR_DAILY_CREATE_CAP),
        )
        conn.commit()
        if cur.rowcount == 0:
            raise CreateCapExceeded(
                f"Aaj ki avatar-video limit ({AVATAR_DAILY_CREATE_CAP}) poori — "
                "kal try karo. (HeyGen credits bachat ke liye.)"
            )
    finally:
        conn.close()


def _record_job(user_id: int, video_id: str, status: str,
                session_id: str | None = None,
                title: str | None = None,
                avatar_id: str | None = None) -> None:
    """Upsert: status hamesha fresh, pehle wali user_id/title barkarar."""
    init_avatar_tables()  # lazy (import-time side effect nahi — C-5)
    now = time.time()
    conn = get_conn()
    try:
        conn.execute(
            """INSERT INTO avatar_jobs
               (user_id, video_id, session_id, title, avatar_id, status,
                created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(video_id) DO UPDATE SET
                 status=excluded.status, updated_at=excluded.updated_at""",
            (user_id, video_id, session_id, title, avatar_id, status, now, now),
        )
        conn.commit()
    finally:
        conn.close()


# ------------------------------------------------------------- HeyGen ---
class HeyGenError(Exception):
    """HeyGen call fail — friendly Roman Urdu msg client ko, raw sirf log me."""
    def __init__(self, friendly: str, http_status: int = 502, raw: str = ""):
        super().__init__(friendly)
        self.friendly = friendly
        self.http_status = http_status
        self.raw = raw


_MODERATION_HINTS = (
    "moderat", "content policy", "inappropriate", "violates",
    "policy violation", "unsafe", "prohibited content",
)


def _map_error(msg: str, status: int, rid: str, context: str) -> None:
    """Roman Urdu error catalog (§D4) — hamesha raise karta hai."""
    low = (msg or "").lower()
    log.warning("heygen error rid=%s ctx=%s http=%s: %s", rid, context, status,
                msg[:500])
    if status in (401, 403):
        raise HeyGenError(
            "🔑 HeyGen API key ghalat ya expire — Connect me dobara key dalo.",
            401, msg)
    if status == 402 or ("insufficient" in low and "credit" in low):
        raise HeyGenError(
            "💳 HeyGen credits khatam — app.heygen.com/billing pe top-up karo. "
            "Video banane par aapke credits use hote hain.",
            402, msg)
    if status == 429:
        raise HeyGenError(
            "⏳ HeyGen ne rate limit lagayi — thori dair baad try karo.", 429, msg)
    if any(h in low for h in _MODERATION_HINTS):
        raise HeyGenError(
            "🛡️ HeyGen ne script reject ki (moderation) — text naram karke "
            "dobara try karo.", 400, msg)
    if status == 400 and context == "create_video":
        raise HeyGenError(
            "🖼️ Avatar ya voice ID ghalat — dobara Fetch karo.", 400, msg)
    if status == 404 and context == "delete_video":
        raise HeyGenError(
            "🗑️ Video HeyGen par nahi mili — shayad pehle hi delete ho chuki hai.",
            404, msg)
    if status < 500:
        raise HeyGenError(
            f"HeyGen ne request reject ki (HTTP {status}) — input check karke "
            "dobara try karo.", 400, msg)
    raise HeyGenError(
        "🔧 HeyGen ke server me masla hai — thori dair baad try karo.", 502, msg)


def _hg(rid: str, method: str, path: str, api_key: str,
        params: dict | None = None, json_body: dict | None = None,
        context: str | None = None) -> httpx.Response:
    """Ek HeyGen call — key header me, timeout hamesha, key kabhi log nahi."""
    url = HEYGEN_BASE + path
    headers = {"X-Api-Key": api_key, "Content-Type": "application/json"}
    try:
        resp = _http().request(method, url, headers=headers,
                               params=params, json=json_body)
    except httpx.TimeoutException as e:
        log.warning("heygen timeout rid=%s %s %s", rid, method, path)
        raise HeyGenError(
            "⏱️ HeyGen se jawab nahi aaya (timeout) — thori dair baad try karo.",
            504, raw=str(e))
    except httpx.HTTPError as e:
        log.warning("heygen network rid=%s %s %s: %s", rid, method, path, e)
        raise HeyGenError(
            "🌐 HeyGen se connect nahi ho raha — thori dair baad try karo.",
            502, raw=str(e))
    if resp.status_code >= 400:
        raw = resp.text[:800]
        msg = raw
        try:
            b = resp.json()
            if isinstance(b, dict) and b.get("error"):
                msg = json.dumps(b["error"])[:500]
        except ValueError:
            pass
        _map_error(msg, resp.status_code, rid, context=context or path)
    return resp


def _data_of(resp: httpx.Response, rid: str, what: str):
    """HeyGen envelope {data:{...}, error:...} -> data. 200-pe-error bhi map."""
    try:
        body = resp.json()
    except ValueError:
        log.warning("heygen bad JSON rid=%s what=%s: %s", rid, what,
                    resp.text[:300])
        raise HeyGenError(
            "HeyGen se samajh na aane wala jawab aaya — thori dair baad try karo.",
            502, raw=resp.text[:300])
    if isinstance(body, dict) and body.get("error"):
        _map_error(json.dumps(body["error"])[:500], 400, rid, context=what)
    if isinstance(body, dict) and "data" in body:
        return body["data"]
    return body


def _norm_status(raw: object) -> str:
    """pending|processing|completed|failed — documented vocabulary barkarar.
    waiting/queued/unknown -> in-progress bucket ("pending"); kabhi complete
    misreport nahi (G1)."""
    s = str(raw or "").strip().lower()
    if s in ("completed", "complete", "success", "succeeded", "done"):
        return "completed"
    if s in ("failed", "fail", "error", "errored", "cancelled", "canceled"):
        return "failed"
    if s in ("processing", "in_progress", "in-progress", "rendering"):
        return "processing"
    return "pending"


def _api_key_for(user_id: int) -> str:
    if not _KEYSTORE_AVAILABLE:
        raise HeyGenError(
            "Key-store abhi tayyar nahi (provider_keys.py pending) — "
            "thori dair baad try karo.", 503)
    key = get_provider_key(user_id, "heygen")
    if not key:
        raise HeyGenError("Pehle API key connect karo — oopar Connect dabao.", 400)
    return key


def _ok(data: dict):
    return {"ok": True, "data": data}


def _fail(status: int, msg: str):
    return JSONResponse(status_code=status, content={"ok": False, "error": msg})


# -------------------------------------------------------------- models ---
class _Strip(BaseModel):
    @field_validator("*", mode="before")
    @classmethod
    def _strip(cls, v):
        return v.strip() if isinstance(v, str) else v


class KeyIn(_Strip):
    api_key: str = Field(..., min_length=10, max_length=500)


class VideoCreateIn(_Strip):
    avatar_id: str = Field(..., min_length=1, max_length=200)
    voice_id: str | None = Field(default=None, max_length=200)
    script: str = Field(..., min_length=1, max_length=SCRIPT_MAX_CHARS)
    engine: Literal["avatar_iv", "avatar_v", "avatar_iii"] = "avatar_iv"
    aspect_ratio: Literal["16:9", "9:16"] = "16:9"
    title: str | None = Field(default=None, max_length=120)
    caption: bool = False


class AgentCreateIn(_Strip):
    prompt: str = Field(..., min_length=1, max_length=AGENT_PROMPT_MAX_CHARS)
    voice_id: str | None = Field(default=None, max_length=200)
    avatar_id: str | None = Field(default=None, max_length=200)
    orientation: Literal["landscape", "portrait"] | None = None


# -------------------------------------------------------------- routes ---
# 1. Key save (verify-first) -------------------------------------------
@router.post("/key")
def save_key(body: KeyIn, current: dict = Depends(get_current_user)):
    if (r := _require_keystore()) is not None:
        return r
    rid = uuid.uuid4().hex[:12]
    key = body.api_key.strip()
    try:
        # Verify-first: sasti call (looks?limit=1). 401/403 -> store NAHI hota.
        resp = _hg(rid, "GET", "/v3/avatars/looks", key, params={"limit": 1})
        _data_of(resp, rid, "verify_key")
        set_provider_key(current["id"], "heygen", key)
    except HeyGenError as e:
        if e.http_status in (401,):
            log.info("avatar key verify failed rid=%s user=%s", rid, current["id"])
            return _fail(400, "🔑 Key ghalat ya expire hai — app.heygen.com → "
                              "Settings → API se dobara copy karo.")
        return _fail(e.http_status, e.friendly)
    # key local var yahin scope se bahar (Gate 4); response me key kabhi nahi.
    log.info("avatar key saved rid=%s user=%s has_key=True", rid, current["id"])
    return _ok({"has_key": True})


# 2. Key delete ---------------------------------------------------------
@router.delete("/key")
def delete_key(current: dict = Depends(get_current_user)):
    if (r := _require_keystore()) is not None:
        return r
    delete_provider_key(current["id"], "heygen")
    log.info("avatar key deleted user=%s", current["id"])
    return _ok({"has_key": False})


# 3. Key status ---------------------------------------------------------
@router.get("/key")
def key_status(current: dict = Depends(get_current_user)):
    if (r := _require_keystore()) is not None:
        return r
    return _ok({"has_key": bool(has_provider_key(current["id"], "heygen"))})


# 4. Avatars (looks — look ID hi avatar_id hai) --------------------------
@router.get("/avatars")
def list_avatars(limit: int = Query(default=20, ge=1, le=100),
                 token: str | None = Query(default=None, max_length=512),
                 current: dict = Depends(get_current_user)):
    rid = uuid.uuid4().hex[:12]
    try:
        key = _api_key_for(current["id"])
        params = {"limit": limit}
        if token:
            params["token"] = token
        resp = _hg(rid, "GET", "/v3/avatars/looks", key, params=params)
        data = _data_of(resp, rid, "avatars")
    except HeyGenError as e:
        return _fail(e.http_status, e.friendly)
    looks = []
    next_token = None
    if isinstance(data, dict):
        raw_looks = data.get("looks", data.get("data", []))
        next_token = data.get("token") or data.get("next_token")
    elif isinstance(data, list):
        raw_looks = data
    else:
        raw_looks = []
    if isinstance(raw_looks, list):
        for look in raw_looks:
            if not isinstance(look, dict) or not look.get("id"):
                continue
            looks.append({
                "id": look.get("id"),
                "name": look.get("name"),
                "preview_image_url": look.get("preview_image_url"),
                "default_voice_id": look.get("default_voice_id"),
            })
    log.info("avatar looks rid=%s user=%s n=%s", rid, current["id"], len(looks))
    return _ok({"looks": looks, "next_token": next_token})


# 5. Voices (+preview_audio_url) ----------------------------------------
@router.get("/voices")
def list_voices(limit: int = Query(default=50, ge=1, le=100),
                token: str | None = Query(default=None, max_length=512),
                language: str | None = Query(default=None, max_length=64),
                gender: str | None = Query(default=None, max_length=32),
                current: dict = Depends(get_current_user)):
    rid = uuid.uuid4().hex[:12]
    try:
        key = _api_key_for(current["id"])
        params = {"type": "public", "limit": limit}
        if token:
            params["token"] = token
        if language:
            params["language"] = language
        if gender:
            params["gender"] = gender
        resp = _hg(rid, "GET", "/v3/voices", key, params=params)
        data = _data_of(resp, rid, "voices")
    except HeyGenError as e:
        return _fail(e.http_status, e.friendly)
    voices = []
    next_token = None
    raw = data.get("voices", data.get("data", [])) if isinstance(data, dict) else []
    if isinstance(data, dict):
        next_token = data.get("token") or data.get("next_token")
    if isinstance(raw, list):
        for v in raw:
            if not isinstance(v, dict) or not v.get("voice_id"):
                continue
            voices.append({
                "voice_id": v.get("voice_id"),
                "name": v.get("name"),
                "language": v.get("language"),
                "gender": v.get("gender"),
                "preview_audio_url": v.get("preview_audio_url"),
            })
    log.info("avatar voices rid=%s user=%s n=%s", rid, current["id"], len(voices))
    return _ok({"voices": voices, "next_token": next_token})


# 6. Create video -------------------------------------------------------
@router.post("/videos")
def create_video(body: VideoCreateIn, current: dict = Depends(get_current_user)):
    rid = uuid.uuid4().hex[:12]
    uid = current["id"]
    try:
        key = _api_key_for(uid)
        _spend_create_cap(uid)  # cap HeyGen call se PEHLE (user ke credits ki hifazat)
        hg_body: dict = {
            "type": "avatar",
            "avatar_id": body.avatar_id,
            "script": body.script,
            "engine": {"type": body.engine},
            "aspect_ratio": body.aspect_ratio,
            "output_format": "mp4",
        }
        if body.voice_id:
            hg_body["voice_id"] = body.voice_id
        if body.title:
            hg_body["title"] = body.title
        if body.caption:
            hg_body["caption"] = {"file_format": "srt"}
        resp = _hg(rid, "POST", "/v3/videos", key, json_body=hg_body,
                   context="create_video")
        data = _data_of(resp, rid, "create_video")
    except HeyGenError as e:
        return _fail(e.http_status, e.friendly)
    except CreateCapExceeded as e:
        return _fail(429, str(e))
    data = data if isinstance(data, dict) else {}
    video_id = data.get("video_id")
    status = _norm_status(data.get("status"))
    if video_id:
        _record_job(uid, str(video_id), status,
                    title=(body.title or body.script[:60]),
                    avatar_id=body.avatar_id)
    log.info("avatar video created rid=%s user=%s video_id=%s", rid, uid, video_id)
    return _ok({"video_id": video_id, "status": status})


# 7. Video status (poll proxy) ------------------------------------------
@router.get("/videos/{video_id}")
def video_status(video_id: str = Path(min_length=1, max_length=200),
                 current: dict = Depends(get_current_user)):
    rid = uuid.uuid4().hex[:12]
    try:
        key = _api_key_for(current["id"])
        resp = _hg(rid, "GET", f"/v3/videos/{video_id}", key)
        data = _data_of(resp, rid, "video_status")
    except HeyGenError as e:
        return _fail(e.http_status, e.friendly)
    data = data if isinstance(data, dict) else {}
    status = _norm_status(data.get("status"))
    fail_text = None
    if status == "failed":
        err = data.get("error") or data.get("msg") or ""
        fail_text = ("Video banane me nakami — " +
                     (str(err)[:200] if err else "wajah maloom nahi, dobara try karo."))
    _record_job(current["id"], video_id, status)  # poll bhi job row fresh rakhta hai
    return _ok({
        "video_id": data.get("video_id", video_id),
        "status": status,
        "video_url": data.get("video_url"),
        "gif_url": data.get("gif_url"),
        "thumbnail_url": data.get("thumbnail_url"),
        "duration": data.get("duration"),
        "error": fail_text,
    })


# 8. Video list (per-user namespaced — sirf apne avatar_jobs) ------------
@router.get("/videos")
def list_videos(limit: int = Query(default=20, ge=1, le=100),
                current: dict = Depends(get_current_user)):
    rid = uuid.uuid4().hex[:12]
    init_avatar_tables()  # lazy (import-time side effect nahi — C-5)
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT video_id, session_id, title, avatar_id, status, created_at"
            " FROM avatar_jobs WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
            (current["id"], limit),
        ).fetchall()
        jobs = [dict(r) for r in rows]
    finally:
        conn.close()
    # Best-effort live enrichment: HeyGen list key-global hai, hamari table
    # per-user namespace deti hai. Fail ho to DB status hi dikhao.
    live: dict = {}
    try:
        key = _api_key_for(current["id"])
        resp = _hg(rid, "GET", "/v3/videos", key, params={"limit": 100})
        data = _data_of(resp, rid, "video_list")
        raw = data.get("videos", data.get("data", [])) if isinstance(data, dict) else []
        for v in (raw if isinstance(raw, list) else []):
            if isinstance(v, dict) and v.get("video_id"):
                live[str(v["video_id"])] = v
    except HeyGenError as e:
        log.info("avatar list enrichment skipped rid=%s: %s", rid, e.friendly)
    videos = []
    for j in jobs:
        lv = live.get(j["video_id"], {})
        status = _norm_status(lv.get("status")) if lv else j["status"]
        videos.append({
            "video_id": j["video_id"],
            "session_id": j["session_id"],
            "title": j["title"],
            "avatar_id": j["avatar_id"],
            "status": status,
            "video_url": lv.get("video_url"),
            "thumbnail_url": lv.get("thumbnail_url"),
            "created_at": j["created_at"],
        })
    return _ok({"videos": videos})


# 9. Video delete -------------------------------------------------------
@router.delete("/videos/{video_id}")
def delete_video(video_id: str = Path(min_length=1, max_length=200),
                 current: dict = Depends(get_current_user)):
    rid = uuid.uuid4().hex[:12]
    try:
        key = _api_key_for(current["id"])
        resp = _hg(rid, "DELETE", f"/v3/videos/{video_id}", key,
                   context="delete_video")
        _data_of(resp, rid, "delete_video")
    except HeyGenError as e:
        return _fail(e.http_status, e.friendly)
    init_avatar_tables()  # lazy (import-time side effect nahi — C-5)
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE avatar_jobs SET status='deleted', updated_at=?"
            " WHERE video_id=? AND user_id=?",
            (time.time(), video_id, current["id"]),
        )
        conn.commit()
    finally:
        conn.close()
    log.info("avatar video deleted rid=%s user=%s video_id=%s",
             rid, current["id"], video_id)
    return _ok({"deleted": True})


# 10. Video Agent create (mode:"generate" fixed — one-shot) --------------
@router.post("/agents")
def create_agent(body: AgentCreateIn, current: dict = Depends(get_current_user)):
    rid = uuid.uuid4().hex[:12]
    uid = current["id"]
    try:
        key = _api_key_for(uid)
        _spend_create_cap(uid)  # agent bhi user credits kharch karta hai
        hg_body: dict = {"prompt": body.prompt, "mode": "generate"}
        if body.voice_id:
            hg_body["voice_id"] = body.voice_id
        if body.avatar_id:
            hg_body["avatar_id"] = body.avatar_id
        if body.orientation:
            hg_body["orientation"] = body.orientation
        resp = _hg(rid, "POST", "/v3/video-agents", key, json_body=hg_body)
        data = _data_of(resp, rid, "create_agent")
    except HeyGenError as e:
        return _fail(e.http_status, e.friendly)
    except CreateCapExceeded as e:
        return _fail(429, str(e))
    data = data if isinstance(data, dict) else {}
    session_id = data.get("session_id")
    status = _norm_status(data.get("status"))
    video_id = data.get("video_id")
    if session_id:
        _record_job(uid, f"agent:{session_id}", status,
                    session_id=str(session_id),
                    title="Agent: " + body.prompt[:60])
    if video_id:
        _record_job(uid, str(video_id), status, session_id=str(session_id),
                    title="Agent: " + body.prompt[:60])
    log.info("avatar agent created rid=%s user=%s session=%s", rid, uid, session_id)
    return _ok({"session_id": session_id, "status": status, "video_id": video_id})


# 11. Agent status ------------------------------------------------------
@router.get("/agents/{session_id}")
def agent_status(session_id: str = Path(min_length=1, max_length=200),
                 current: dict = Depends(get_current_user)):
    rid = uuid.uuid4().hex[:12]
    try:
        key = _api_key_for(current["id"])
        resp = _hg(rid, "GET", f"/v3/video-agents/{session_id}", key)
        data = _data_of(resp, rid, "agent_status")
    except HeyGenError as e:
        return _fail(e.http_status, e.friendly)
    data = data if isinstance(data, dict) else {}
    status = _norm_status(data.get("status"))
    video_id = data.get("video_id")
    if video_id:
        _record_job(current["id"], str(video_id), status,
                    session_id=session_id)
    return _ok({
        "session_id": data.get("session_id", session_id),
        "status": status,
        "video_id": video_id,
    })
