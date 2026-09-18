"""Media Studio backend — ai33.pro proxy (/api/v1/media/*).

Auth: JWT required on every route (get_current_user).
Auth to ai33: custom header xi-api-key, server-side ONLY — the browser never
sees it; it only ever holds task_ids.

$0 notes: polling is client-driven (no webhooks — no public receiver needed).
Result URLs from ai33 are public; backend never proxies media bytes.

NEVER hit https://api.ai33.pro in tests — tests monkeypatch `_http`.
"""
import logging
import os
import time
from datetime import date
from typing import Any, Optional
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

import provider_keys
from auth import get_current_user
from database import get_conn

log = logging.getLogger("cios.media")

# ------------------------------------------------------------- settings ---
# (V2-DESIGN.md Appendix E puts these in config.py, which Builder A owns —
# B keeps them as module constants so it never edits A's files.)

AI33_BASE_URL = "https://api.ai33.pro"
MEDIA_HTTP_TIMEOUT = 60
MEDIA_DAILY_CREATE_CAP = 20        # per user/day across create routes
MEDIA_TTS_DAILY_CHARS = 100000     # per user/day
TTS_MAX_CHARS = 20000              # per-request cap (credit-burn guard)

VOICE_PROVIDERS = (
    "elevenlabs_", "minimax_", "clone_", "edge_",
    "kokoro_", "vbee_", "fishaudio_",
)
VOICES_CACHE_TTL = 6 * 3600  # 6h server cache per (provider+filters)

SUNO_MODES = ("wav", "mp3", "mp3-45", "mp3-lite", "instrumental")
IMAGE_MODELS = ("image-to-image", "gpt-image-1", "upscale", "imagen-3")
VIDEO_MODELS = ("veo3", "veo3-fast", "image-to-video",
                "image-to-video-continue", "image-to-video-frame")
CLONE_MAX_BYTES = 10 * 1024 * 1024  # 10MB, server-enforced

router = APIRouter(prefix="/api/v1/media", tags=["media"])

# ---------------------------------------------------- proxy-safe http ---
# Gate 5: copy of ai_client._proxy_url() (verbatim pattern). NEVER log the value.


def _proxy_url() -> Optional[str]:
    for var in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        raw = (os.environ.get(var) or "").strip()
        if not raw:
            continue
        try:
            p = urlparse(raw)
            scheme, host, port = p.scheme, p.hostname, p.port
        except Exception:
            continue
        if scheme in ("http", "https") and host and port:
            log.info("media proxy configured: yes (from %s)", var)
            return raw
    log.info("media proxy configured: no")
    return None


_client: Optional[httpx.Client] = None


def _http() -> httpx.Client:
    """Shared keep-alive client. Tests monkeypatch this name to a fake."""
    global _client
    if _client is None:
        _client = httpx.Client(
            timeout=MEDIA_HTTP_TIMEOUT,
            trust_env=False,          # bypass httpx 0.28.1 broken no_proxy parsing
            proxy=_proxy_url(),
        )
    return _client


def _reset_http() -> None:
    """Test-only: drop the cached client (e.g. after env/proxy changes)."""
    global _client
    _client = None


# --------------------------------------------------------- error model ---

class Ai33Error(Exception):
    """Friendly Roman Urdu message safe for the browser."""

    def __init__(self, friendly: str, status: Optional[int] = None):
        super().__init__(friendly)
        self.status = status


def _err(status: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status,
                        content={"ok": False, "error": message})


def _rid() -> str:
    return os.urandom(8).hex()


_CREDIT_HINTS = ("insufficient", "credit", "balance", "top-up", "topup",
                 "quota", "not enough")


def _ai33_request(method: str, path: str, *, key: str,
                  params: Optional[dict] = None,
                  json_body: Optional[dict] = None,
                  form: Optional[dict] = None,
                  files: Optional[dict] = None) -> Any:
    """One ai33 call, full error mapping. Raw bodies stay server-side (logs only).
    Returns the decoded JSON body (or {} for empty 204s)."""
    rid = _rid()
    url = AI33_BASE_URL + path
    headers = {"xi-api-key": key}  # server-side ONLY
    try:
        resp = _http().request(method, url, headers=headers, params=params,
                               json=json_body, data=form, files=files,
                               timeout=MEDIA_HTTP_TIMEOUT)
    except httpx.TimeoutException:
        log.warning("ai33 timeout rid=%s path=%s", rid, path)
        raise Ai33Error("⏱️ ai33.pro se jawab nahi aaya (timeout) — dobara try karo.")
    except httpx.HTTPError as e:
        log.warning("ai33 network fail rid=%s path=%s kind=%s",
                    rid, path, type(e).__name__)
        raise Ai33Error("🌐 ai33.pro se connect nahi ho raha — thori dair baad "
                        "dobara try karo.")

    if resp.status_code == 401:
        log.warning("ai33 401 rid=%s path=%s", rid, path)
        raise Ai33Error("🔑 ai33.pro key ghalat/expire ho gayi — Media Studio me "
                        "dobara Connect karo.", status=401)
    if resp.status_code == 402:
        raise Ai33Error("💳 ai33.pro credits khatam — balance upar dekho, "
                        "ai33.pro pe top-up karo.", status=402)
    if resp.status_code == 429:
        log.warning("ai33 429 rid=%s path=%s", rid, path)
        raise Ai33Error("⏳ ai33.pro ne rate limit lagayi — thori dair baad try karo.",
                        status=429)
    if resp.status_code >= 500:
        log.warning("ai33 5xx rid=%s path=%s body=%.200s", rid, path, resp.text)
        raise Ai33Error("🔧 ai33.pro server me masla — thori dair baad dobara "
                        "try karo.", status=resp.status_code)

    try:
        body = resp.json() if resp.content else {}
    except ValueError:
        log.warning("ai33 bad json rid=%s path=%s", rid, path)
        raise Ai33Error("🔧 ai33.pro se samajh na aane wala jawab — dobara try karo.")

    if isinstance(body, dict) and body.get("success") is False:
        msg = str(body.get("message") or body.get("error") or "request reject")
        lowered = msg.lower()
        log.warning("ai33 success=false rid=%s path=%s msg=%.200s", rid, path, msg)
        if any(h in lowered for h in _CREDIT_HINTS):
            raise Ai33Error("💳 ai33.pro credits khatam — balance upar dekho, "
                            "ai33.pro pe top-up karo.")
        raise Ai33Error(f"ai33.pro: {msg}")

    if resp.status_code >= 400:
        log.warning("ai33 %s rid=%s path=%s body=%.200s",
                    resp.status_code, rid, path, resp.text)
        raise Ai33Error("🔧 ai33.pro ne request reject ki — inputs check karke "
                        "dobara try karo.", status=resp.status_code)
    return body if isinstance(body, (dict, list)) else {}


def _need_key(user_id: int) -> str:
    key = provider_keys.get_provider_key(user_id, "ai33")
    if not key:
        raise Ai33Error("🔑 Pehle apni ai33.pro API key connect karo (Studio me "
                        "'Connect' button se).")
    return key


def _run_ai33(method: str, path: str, *, user_id: int, **kwargs) -> Any:
    """Wrap _ai33_request: Ai33Error -> JSONResponse with the right status."""
    try:
        key = _need_key(user_id)
    except Ai33Error as e:
        return _err(400, str(e))
    try:
        return _ai33_request(method, path, key=key, **kwargs)
    except Ai33Error as e:
        status = e.status or (400 if str(e).startswith("🔑") and "connect" in str(e).lower()
                              else 502)
        # Map common cases explicitly
        if e.status == 401:
            status = 401
        elif e.status == 402 or "credits khatam" in str(e):
            status = 402
        elif e.status == 429:
            status = 429
        elif "Pehle apni ai33.pro API key" in str(e):
            status = 400
        return _err(status, str(e))


# ------------------------------------------------------------ caps ---

class CapError(Exception):
    pass


def _spend_create(user_id: int, *, tts_chars: int = 0) -> None:
    """Atomic per-(user, day, provider) accounting. Raises CapError on cap."""
    day = date.today().isoformat()
    conn = get_conn()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO media_usage (user_id, day, provider, creates, tts_chars)"
            " VALUES (?, ?, 'ai33', 0, 0)",
            (user_id, day),
        )
        cur = conn.execute(
            "UPDATE media_usage SET creates = creates + 1, tts_chars = tts_chars + ?"
            " WHERE user_id=? AND day=? AND provider='ai33'"
            " AND creates < ? AND tts_chars + ? <= ?",
            (tts_chars, user_id, day, MEDIA_DAILY_CREATE_CAP,
             tts_chars, MEDIA_TTS_DAILY_CHARS),
        )
        conn.commit()
        if cur.rowcount:
            return
        row = conn.execute(
            "SELECT creates, tts_chars FROM media_usage"
            " WHERE user_id=? AND day=? AND provider='ai33'",
            (user_id, day),
        ).fetchone()
        creates = row["creates"] if row else 0
        chars = row["tts_chars"] if row else 0
        log.info("media cap hit user=%s creates=%s chars=%s", user_id, creates, chars)
        if creates >= MEDIA_DAILY_CREATE_CAP:
            raise CapError("Aaj ki media limit (20 creations) poori ho gayi — "
                           "kal try karo.")
        raise CapError("Aaj ki TTS character limit (100,000) poori ho gayi — "
                       "kal try karo.")
    finally:
        conn.close()


# ------------------------------------------------------------- models ---

class KeyBody(BaseModel):
    api_key: str = Field(min_length=10, max_length=500)


class TTSBody(BaseModel):
    voice_id: str = Field(min_length=3, max_length=200)
    text: str = Field(min_length=1, max_length=TTS_MAX_CHARS)
    speed: float = Field(default=1.0, ge=0.5, le=1.5)
    with_transcript: bool = False


class DialogueBody(BaseModel):
    speakers: dict[str, str]  # {"A": "elevenlabs_xxx", "B": "minimax_yyy"}
    text: str = Field(min_length=1, max_length=TTS_MAX_CHARS)
    speed: Optional[float] = Field(default=None, ge=0.5, le=1.5)


class MusicBody(BaseModel):
    mode: str
    prompt: str = Field(min_length=1, max_length=500)
    style: Optional[str] = Field(default=None, max_length=200)
    title: Optional[str] = Field(default=None, max_length=100)


class ImageBody(BaseModel):
    model: str
    prompt: str = Field(min_length=1, max_length=1000)
    image_url: Optional[str] = Field(default=None, max_length=2048)


class VideoBody(BaseModel):
    model: str
    prompt: str = Field(min_length=1, max_length=1000)
    image_url: Optional[str] = Field(default=None, max_length=2048)
    aspect_ratio: str = "16:9"
    generate_audio: bool = True


def _ok(data: dict) -> dict:
    return {"ok": True, "data": data}


def _task_id_from(body: Any) -> str:
    """ai33 create calls return {"success": true, "task_id": ...} (or bare)."""
    task_id = None
    if isinstance(body, dict):
        task_id = body.get("task_id") or body.get("id")
        inner = body.get("data")
        if not task_id and isinstance(inner, dict):
            task_id = inner.get("task_id") or inner.get("id")
    if not task_id:
        raise Ai33Error("🔧 ai33.pro se task ID nahi mili — dobara try karo.")
    return str(task_id)


# ============================================================ routes ====

# 1. key save (verify-first)
@router.post("/key")
def save_key(body: KeyBody, current: dict = Depends(get_current_user)):
    api_key = body.api_key.strip()
    try:
        verified = _ai33_request("GET", "/v1/credits", key=api_key)
    except Ai33Error as e:
        if e.status == 401:
            return _err(400, "Key ghalat hai — dashboard se dobara copy karo.")
        return _err(e.status or 502, str(e))
    try:
        provider_keys.set_provider_key(current["id"], "ai33", api_key)
    except ValueError as e:
        return _err(400, str(e))
    return _ok({"has_key": True, "credits": _credits_of(verified)})


# 2. key delete
@router.delete("/key")
def delete_key(current: dict = Depends(get_current_user)):
    provider_keys.delete_provider_key(current["id"], "ai33")
    return _ok({"has_key": False})


# 3. key status (bool only — key NEVER returned)
@router.get("/key")
def key_status(current: dict = Depends(get_current_user)):
    return _ok({"has_key": provider_keys.has_provider_key(current["id"], "ai33")})


def _credits_of(body: Any) -> Optional[int]:
    """Normalize ai33's credits shape -> int. None when unreadable."""
    def coerce(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return None
    if isinstance(body, dict):
        for k in ("credits", "balance", "remaining"):
            if k in body:
                v = coerce(body[k])
                if v is not None:
                    return v
        d = body.get("data")
        if isinstance(d, dict):
            for k in ("credits", "balance", "remaining"):
                if k in d:
                    v = coerce(d[k])
                    if v is not None:
                        return v
    return None


# 4. status (single source of truth for the UI)
@router.get("/status")
def status(current: dict = Depends(get_current_user)):
    has = provider_keys.has_provider_key(current["id"], "ai33")
    credits = None
    if has:
        res = _run_ai33("GET", "/v1/credits", user_id=current["id"])
        if isinstance(res, JSONResponse):
            log.info("status credits fetch failed user=%s", current["id"])
        else:
            credits = _credits_of(res)
    return _ok({"has_key": has, "credits": credits})


# 5. credits
@router.get("/credits")
def credits(current: dict = Depends(get_current_user)):
    res = _run_ai33("GET", "/v1/credits", user_id=current["id"])
    if isinstance(res, JSONResponse):
        return res
    c = _credits_of(res)
    if c is None:
        return _err(502, "🔧 ai33.pro se balance samajh nahi aaya — dobara try karo.")
    return _ok({"credits": c})


# 6. voices (provider REQUIRED; 6h server cache)
_voices_cache: dict[tuple, tuple[float, dict]] = {}


@router.get("/voices")
def voices(provider: str = Query(..., description="voice id prefix, e.g. elevenlabs_"),
           language: Optional[str] = Query(default=None),
           gender: Optional[str] = Query(default=None),
           accent: Optional[str] = Query(default=None),
           current: dict = Depends(get_current_user)):
    if provider not in VOICE_PROVIDERS:
        return _err(422, "Provider in me se ho: " + ", ".join(VOICE_PROVIDERS))
    cache_key = (provider, language, gender, accent)
    now = time.time()
    hit = _voices_cache.get(cache_key)
    if hit and now - hit[0] < VOICES_CACHE_TTL:
        data = dict(hit[1])
        data["cached"] = True
        return _ok(data)

    params = {"provider": provider}
    if language:
        params["language"] = language
    if gender:
        params["gender"] = gender
    if accent:
        params["accent"] = accent
    res = _run_ai33("GET", "/v3/voices", user_id=current["id"], params=params)
    if isinstance(res, JSONResponse):
        return res
    out = _normalize_voices(res, provider)
    out["cached"] = False
    _voices_cache[cache_key] = (now, out)
    return _ok(dict(out))


def _normalize_voices(body: Any, provider: str) -> dict:
    items: list = []
    if isinstance(body, dict):
        cand = body.get("voices") or body.get("data")
        if isinstance(cand, dict):
            cand = cand.get("voices") or []
        if isinstance(cand, list):
            items = cand
    voices = []
    for v in items:
        if not isinstance(v, dict):
            continue
        vid = str(v.get("voice_id") or v.get("id") or "")
        if not vid:
            continue
        voices.append({
            "voice_id": vid,
            "name": str(v.get("name") or vid),
            "language": v.get("language"),
            "gender": v.get("gender"),
            "accent": v.get("accent"),
            "provider": provider,
        })
    return {"voices": voices}


# 7. tts
@router.post("/tts")
def tts(body: TTSBody, current: dict = Depends(get_current_user)):
    if "_" not in body.voice_id:
        return _err(422, "voice_id me provider prefix hona chahiye (misal: elevenlabs_xxx).")
    try:
        _spend_create(current["id"], tts_chars=len(body.text))
    except CapError as e:
        return _err(429, str(e))
    form = {
        "voice_id": body.voice_id,
        "text": body.text,
        "speed": str(body.speed),
        "with_transcript": "true" if body.with_transcript else "false",
    }
    res = _run_ai33("POST", "/v3/text-to-speech", user_id=current["id"], form=form)
    if isinstance(res, JSONResponse):
        return res
    try:
        task_id = _task_id_from(res)
    except Ai33Error as e:
        return _err(502, str(e))
    return _ok({"task_id": task_id})


# 8. dialogue
@router.post("/dialogue")
def dialogue(body: DialogueBody, current: dict = Depends(get_current_user)):
    import json as _json
    if not body.speakers or len(body.speakers) > 8:
        return _err(422, "speakers me 1 se 8 tak voices do (A, B, C …).")
    for label, vid in body.speakers.items():
        if not (isinstance(label, str) and len(label) == 1 and label.isupper()):
            return _err(422, "Speaker labels A, B, C … (ek capital letter) hone chahiye.")
        if not (isinstance(vid, str) and "_" in vid):
            return _err(422, f"Speaker {label} ki voice_id me provider prefix chahiye.")
    try:
        _spend_create(current["id"], tts_chars=len(body.text))
    except CapError as e:
        return _err(429, str(e))
    form = {"speakers": _json.dumps(body.speakers), "text": body.text}
    if body.speed is not None:
        form["speed"] = str(body.speed)
    res = _run_ai33("POST", "/v3/text-to-speech/dialogue",
                    user_id=current["id"], form=form)
    if isinstance(res, JSONResponse):
        return res
    try:
        task_id = _task_id_from(res)
    except Ai33Error as e:
        return _err(502, str(e))
    return _ok({"task_id": task_id})


# 9. voice-clone (multipart; ≤10MB server-enforced)
@router.post("/voice-clone")
async def voice_clone(voice_name: str = Form(...),
                      audio_file: UploadFile = File(...),
                      current: dict = Depends(get_current_user)):
    name = (voice_name or "").strip()
    if not (2 <= len(name) <= 50):
        return _err(422, "voice_name 2 se 50 characters ka ho.")
    data = await audio_file.read()
    if len(data) > CLONE_MAX_BYTES:
        return _err(413, "⚠️ Audio file 10MB se bari hai — choti file upload karo.")
    if not data:
        return _err(422, "Audio file khaali hai.")
    ctype = (audio_file.content_type or "").lower()
    fname = (audio_file.filename or "").lower()
    if not (ctype.startswith("audio/") or fname.endswith(
            (".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac", ".webm"))):
        return _err(422, "Sirf audio file upload karo (mp3/wav/m4a …).")
    try:
        _spend_create(current["id"])
    except CapError as e:
        return _err(429, str(e))
    files = {"audio_file": (audio_file.filename or "clip",
                            data, ctype or "audio/mpeg")}
    res = _run_ai33("POST", "/v3/text-to-speech/voice-clone",
                    user_id=current["id"],
                    form={"voice_name": name}, files=files)
    if isinstance(res, JSONResponse):
        return res
    voice_id = None
    if isinstance(res, dict):
        voice_id = res.get("voice_id") or res.get("id")
        d = res.get("data")
        if not voice_id and isinstance(d, dict):
            voice_id = d.get("voice_id") or d.get("id")
    if not voice_id:
        return _err(502, "🔧 ai33.pro se clone voice ID nahi mili — dobara try karo.")
    _voices_cache.clear()  # nayi clone TTS picker me turant nazar aaye
    return _ok({"voice_id": str(voice_id)})


# 10. voice-clone delete
@router.delete("/voice-clone/{voice_id}")
def voice_clone_delete(voice_id: str, current: dict = Depends(get_current_user)):
    if not voice_id.startswith("clone_"):
        return _err(422, "Sirf clone_ voices delete ho sakti hain.")
    res = _run_ai33("DELETE", f"/v3/text-to-speech/voice-clone/{voice_id}",
                    user_id=current["id"])
    if isinstance(res, JSONResponse):
        return res
    _voices_cache.clear()
    return _ok({"deleted": True})


# 11. music (Suno, async)
@router.post("/music")
def music(body: MusicBody, current: dict = Depends(get_current_user)):
    if body.mode not in SUNO_MODES:
        return _err(422, "mode in me se ho: " + ", ".join(SUNO_MODES))
    try:
        _spend_create(current["id"])
    except CapError as e:
        return _err(429, str(e))
    payload = {"prompt": body.prompt}
    if body.style:
        payload["style"] = body.style
    if body.title:
        payload["title"] = body.title
    res = _run_ai33("POST", f"/v1/suno/{body.mode}",
                    user_id=current["id"], json_body=payload)
    if isinstance(res, JSONResponse):
        return res
    try:
        task_id = _task_id_from(res)
    except Ai33Error as e:
        return _err(502, str(e))
    return _ok({"task_id": task_id})


# 12. image (async)
@router.post("/image")
def image(body: ImageBody, current: dict = Depends(get_current_user)):
    if body.model not in IMAGE_MODELS:
        return _err(422, "model in me se ho: " + ", ".join(IMAGE_MODELS))
    if body.model in ("image-to-image", "upscale") and not body.image_url:
        return _err(422, f"{body.model} ke liye image_url zaroori hai.")
    try:
        _spend_create(current["id"])
    except CapError as e:
        return _err(429, str(e))
    payload = {"prompt": body.prompt}
    if body.image_url:
        payload["image_url"] = body.image_url
    res = _run_ai33("POST", f"/v1/{body.model}",
                    user_id=current["id"], json_body=payload)
    if isinstance(res, JSONResponse):
        return res
    try:
        task_id = _task_id_from(res)
    except Ai33Error as e:
        return _err(502, str(e))
    return _ok({"task_id": task_id})


# 13. video (async)
@router.post("/video")
def video(body: VideoBody, current: dict = Depends(get_current_user)):
    if body.model not in VIDEO_MODELS:
        return _err(422, "model in me se ho: " + ", ".join(VIDEO_MODELS))
    if body.aspect_ratio not in ("16:9", "9:16"):
        return _err(422, "aspect_ratio '16:9' ya '9:16' ho.")
    try:
        _spend_create(current["id"])
    except CapError as e:
        return _err(429, str(e))
    payload = {
        "prompt": body.prompt,
        "duration": 8,  # docs ke mutabiq fixed
        "aspect_ratio": body.aspect_ratio,
        "generate_audio": body.generate_audio,
    }
    if body.image_url:
        payload["image_url"] = body.image_url
    res = _run_ai33("POST", f"/v1/{body.model}",
                    user_id=current["id"], json_body=payload)
    if isinstance(res, JSONResponse):
        return res
    try:
        task_id = _task_id_from(res)
    except Ai33Error as e:
        return _err(502, str(e))
    return _ok({"task_id": task_id})


# 14. task status (poll proxy) — normalized
_COMPLETED = {"completed", "complete", "success", "succeeded", "done", "finished"}
_FAILED = {"failed", "failure", "error", "cancelled", "canceled"}
_PENDING = {"pending", "queued", "waiting"}


def _map_status(status_raw: str) -> str:
    s = (status_raw or "").strip().lower()
    if s in _COMPLETED:
        return "completed"
    if s in _FAILED:
        return "failed"
    if s in _PENDING or not s:
        return "pending"
    return "processing"  # unknown non-empty -> in-progress, never completed


def _normalize_task(body: Any, task_id: str) -> dict:
    d: dict = {}
    if isinstance(body, dict):
        inner = body.get("data")
        d = inner if isinstance(inner, dict) else body
    status_raw = str(d.get("status") or d.get("state")
                     or d.get("task_status") or "")
    status = _map_status(status_raw)

    audio_url = d.get("audio_url") or d.get("audio")
    image_url = d.get("image_url") or d.get("image")
    video_url = d.get("video_url") or d.get("video")
    # catch-all result fields (ai33 shape varies); extension se slot guess karo
    result = d.get("result_url") or d.get("url") or d.get("media_url") \
        or d.get("download_url")
    if isinstance(result, str) and result:
        low = result.lower()
        if not (audio_url or image_url or video_url):
            if low.endswith((".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac")):
                audio_url = result
            elif low.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")):
                image_url = result
            elif low.endswith((".mp4", ".mov", ".webm")):
                video_url = result
            else:
                audio_url = result  # default: music/tts task
    transcript = d.get("transcript") or d.get("text")
    error = d.get("error") or d.get("message")
    return {
        "task_id": task_id,
        "status": status,
        "audio_url": audio_url if isinstance(audio_url, str) else None,
        "image_url": image_url if isinstance(image_url, str) else None,
        "video_url": video_url if isinstance(video_url, str) else None,
        "transcript": transcript if isinstance(transcript, str) else None,
        "error": error if isinstance(error, str) else None,
    }


@router.get("/tasks/{task_id}")
def task_status(task_id: str, current: dict = Depends(get_current_user)):
    res = _run_ai33("GET", f"/v1/task/{task_id}", user_id=current["id"])
    if isinstance(res, JSONResponse):
        return res
    return _ok(_normalize_task(res, task_id))


# 15. task list
@router.get("/tasks")
def task_list(limit: int = Query(default=20, ge=1, le=100),
               current: dict = Depends(get_current_user)):
    res = _run_ai33("GET", "/v1/tasks", user_id=current["id"],
                    params={"limit": limit})
    if isinstance(res, JSONResponse):
        return res
    items: list = []
    if isinstance(res, dict):
        cand = res.get("tasks") or res.get("data")
        if isinstance(cand, dict):
            cand = cand.get("tasks") or []
        if isinstance(cand, list):
            items = cand
    tasks = []
    for t in items:
        if not isinstance(t, dict):
            continue
        tid = str(t.get("task_id") or t.get("id") or "")
        if not tid:
            continue
        s = str(t.get("status") or "")
        tasks.append({
            "task_id": tid,
            "type": str(t.get("type") or t.get("task_type") or ""),
            "status": _map_status(s),
            "created_at": t.get("created_at") or t.get("createdAt"),
        })
    return _ok({"tasks": tasks})


# 16. task delete (ai33 refunds credits)
@router.post("/tasks/{task_id}/delete")
def task_delete(task_id: str, current: dict = Depends(get_current_user)):
    res = _run_ai33("POST", "/v1/task/delete", user_id=current["id"],
                    json_body={"task_id": task_id})
    if isinstance(res, JSONResponse):
        return res
    return _ok({"deleted": True, "refunded": True})
