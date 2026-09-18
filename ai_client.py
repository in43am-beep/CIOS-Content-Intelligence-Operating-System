"""OpenRouter client: free models only, fallback chain, quota guard, response cache.

Cost model: $0. Free tier = 20 req/min, 50 req/day (unfunded). The client
- reuses cached responses for identical prompts (saves quota),
- spaces calls to respect 20 RPM,
- stops before the daily cap with a friendly error,
- tries each model in the fallback chain on 429/5xx.
"""
import hashlib
import time
from datetime import date

import httpx

from config import settings
from database import get_conn

URL = "https://openrouter.ai/api/v1/chat/completions"


class QuotaExceeded(Exception):
    pass


class MissingApiKey(Exception):
    pass


class AllModelsFailed(Exception):
    pass


class _RetryableError(Exception):
    pass


def _hash(system_prompt: str, user_prompt: str, max_tokens: int) -> str:
    return hashlib.sha256(f"{system_prompt}\n---\n{user_prompt}\n---\n{max_tokens}".encode()).hexdigest()


def _call(model: str, system_prompt: str, user_prompt: str, max_tokens: int) -> str:
    if not settings.openrouter_api_key:
        raise MissingApiKey(
            "OPENROUTER_API_KEY set nahi hai. .env file me key dalo — "
            "free key banti hai: https://openrouter.ai/keys (koi card nahi chahiye)."
        )
    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": settings.http_referer,
        "X-Title": settings.app_title,
    }
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.7,
    }
    try:
        resp = httpx.post(URL, headers=headers, json=body, timeout=settings.request_timeout)
    except httpx.TimeoutException as e:
        raise _RetryableError(f"{model}: timeout ({e})")
    except httpx.HTTPError as e:
        raise _RetryableError(f"{model}: network error ({e})")

    if resp.status_code == 429 or resp.status_code >= 500:
        raise _RetryableError(f"{model}: HTTP {resp.status_code} (rate-limited ya down, agla model try hoga)")
    if resp.status_code != 200:
        raise RuntimeError(f"{model}: HTTP {resp.status_code}: {resp.text[:300]}")
    try:
        return resp.json()["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, ValueError) as e:
        raise _RetryableError(f"{model}: samajh na aane wala response ({e})")


def generate(system_prompt: str, user_prompt: str, max_tokens: int = 2000) -> dict:
    """Returns {'text', 'model', 'cached'}."""
    conn = get_conn()
    try:
        key = _hash(system_prompt, user_prompt, max_tokens)
        cutoff = time.time() - settings.cache_ttl_days * 86400
        conn.execute("DELETE FROM cache WHERE created_at < ?", (cutoff,))
        row = conn.execute("SELECT response FROM cache WHERE prompt_hash=?", (key,)).fetchone()
        if row:
            return {"text": row["response"], "model": "cache", "cached": True}

        today = date.today().isoformat()
        used = conn.execute("SELECT count FROM usage WHERE day=?", (today,)).fetchone()
        used_count = used["count"] if used else 0
        if used_count >= settings.daily_request_cap:
            raise QuotaExceeded(
                f"Aaj ki free limit ({settings.daily_request_cap} requests) poori ho gayi. "
                "Kal phir try karo — ya OpenRouter pe $10 one-time credit se 1000/day unlock hota hai (optional)."
            )

        last = conn.execute("SELECT last_call_at FROM rate_state WHERE id=1").fetchone()
        wait = settings.min_seconds_between_calls - (time.time() - last["last_call_at"])
        if wait > 0:
            time.sleep(wait)

        last_err: Exception | None = None
        for model in settings.model_chain:
            try:
                text = _call(model, system_prompt, user_prompt, max_tokens)
            except _RetryableError as e:
                last_err = e
                continue
            conn.execute(
                "INSERT OR REPLACE INTO cache (prompt_hash, response, created_at) VALUES (?,?,?)",
                (key, text, time.time()),
            )
            conn.execute(
                "INSERT INTO usage (day, count) VALUES (?,1)"
                " ON CONFLICT(day) DO UPDATE SET count = count + 1",
                (today,),
            )
            conn.execute("UPDATE rate_state SET last_call_at=? WHERE id=1", (time.time(),))
            conn.commit()
            return {"text": text, "model": model, "cached": False}

        raise AllModelsFailed(
            f"Saare free models fail ho gaye. Aakhri error: {last_err}. "
            "Thori dair baad try karo (free models peak hours me busy hote hain)."
        )
    finally:
        conn.close()


def usage_today() -> dict:
    conn = get_conn()
    try:
        today = date.today().isoformat()
        row = conn.execute("SELECT count FROM usage WHERE day=?", (today,)).fetchone()
        used = row["count"] if row else 0
        return {"today": today, "used": used, "cap": settings.daily_request_cap}
    finally:
        conn.close()
