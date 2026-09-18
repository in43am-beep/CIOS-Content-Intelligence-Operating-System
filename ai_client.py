"""OpenRouter client: free models only, fallback chain, quota guard, response cache.

Cost model: $0. Free tier = 20 req/min, 50 req/day (unfunded). The client
- reuses cached responses for identical prompts, per user (saves quota),
- spaces calls to respect 20 RPM (atomic slot reservation, monotonic clock),
- counts EVERY outbound provider request against the daily cap (atomic UPDATE),
- tries each model in the fallback chain; a provider 4xx just skips that model.
"""
import hashlib
import logging
import threading
import time
from datetime import date

import httpx

from config import settings
from database import get_conn, user_daily_cap

log = logging.getLogger("cios.ai")

URL = "https://openrouter.ai/api/v1/chat/completions"


class QuotaExceeded(Exception):
    pass


class MissingApiKey(Exception):
    pass


class AllModelsFailed(Exception):
    pass


class _SkipModel(Exception):
    """Ek model skip karo, agla try karo. Friendly msg client ko, raw server log me."""
    def __init__(self, friendly: str, raw: str = ""):
        super().__init__(friendly)
        self.raw = raw


def _hash(system_prompt: str, user_prompt: str, max_tokens: int) -> str:
    return hashlib.sha256(f"{system_prompt}\n---\n{user_prompt}\n---\n{max_tokens}".encode()).hexdigest()


# Shared keep-alive client (audit L-6). Lazily built so tests can swap it.
_client: httpx.Client | None = None


def _proxy_url() -> str | None:
    """Proxy URL from env, validated. httpx 0.28.1 crashes parsing bracketed IPv6
    entries in NO_PROXY (InvalidURL), so we bypass trust_env entirely and hand
    httpx one explicit, validated proxy URL instead.

    NEVER log the value — it embeds credentials. Log only yes/no.
    """
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
        # trust_env=False: skip httpx's broken no_proxy parsing; proxy (if any)
        # comes from _proxy_url(). proxy=None = direct connection (Render).
        _client = httpx.Client(
            timeout=settings.request_timeout,
            trust_env=False,
            proxy=_proxy_url(),
        )
    return _client


def _quota_message(cap: int) -> str:
    return (
        f"Aaj ki free limit ({cap} requests) poori ho gayi. "
        "Kal phir try karo — ya OpenRouter pe $10 one-time credit se 1000/day unlock hota hai (optional)."
    )


def _spend_quota(conn, user_id: int, day: str, cap: int) -> None:
    """Attempt-based accounting: har outbound provider request pe +1, atomic.

    Single UPDATE ... WHERE count < cap; rowcount==0 matlab cap full (audit A-2).
    """
    conn.execute(
        "INSERT OR IGNORE INTO usage (user_id, day, count) VALUES (?,?,0)",
        (user_id, day),
    )
    cur = conn.execute(
        "UPDATE usage SET count = count + 1 WHERE user_id=? AND day=? AND count < ?",
        (user_id, day, cap),
    )
    conn.commit()
    if cur.rowcount == 0:
        raise QuotaExceeded(_quota_message(cap))


_pace_event = threading.Event()


def _pace(conn) -> None:
    """Atomic pacing: slot reserve karo (read+update ek transaction me), phir
    DB lock ke BAHAR interruptible wait (audit A-3). Monotonic clock."""
    with conn:  # transaction
        row = conn.execute("SELECT last_call_at FROM rate_state WHERE id=1").fetchone()
        now = time.monotonic()
        wait = settings.min_seconds_between_calls - (now - (row["last_call_at"] if row else 0))
        wait = max(wait, 0)
        conn.execute("UPDATE rate_state SET last_call_at=? WHERE id=1", (now + wait,))
    if wait > 0:
        _pace_event.wait(wait)


def _call(model: str, system_prompt: str, user_prompt: str, max_tokens: int) -> str:
    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "Content-Type": "application/json",
        "X-Title": settings.app_title,
    }
    if settings.http_referer:
        headers["HTTP-Referer"] = settings.http_referer
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
        resp = _http().post(URL, headers=headers, json=body)
    except httpx.TimeoutException as e:
        raise _SkipModel(
            f"{model}: timeout — agla model try ho raha hai.",
            raw=f"{model}: timeout ({e})",
        )
    except httpx.HTTPError as e:
        raise _SkipModel(
            f"{model}: network masla — agla model try ho raha hai.",
            raw=f"{model}: network error ({e})",
        )

    if resp.status_code != 200:
        # 4xx (retired model, bad key...) ya 429/5xx: model skipped, chain jari.
        # Raw provider body KABHI client ko nahi — sirf server log me (audit M-7).
        log.warning("%s: provider HTTP %s: %s", model, resp.status_code, resp.text[:500])
        raise _SkipModel(
            f"{model}: provider ne HTTP {resp.status_code} diya — agla model try ho raha hai.",
            raw=f"{model}: HTTP {resp.status_code}: {resp.text[:500]}",
        )
    # Defensive shape check: openrouter/free kabhi 200 ke sath ghalat shape bhejta
    # hai (choices[0].message.content nahi hota). Koi bhi mismatch -> model skip.
    try:
        body = resp.json()
        if not isinstance(body, dict):
            raise TypeError(f"top-level JSON is {type(body).__name__}, not dict")
        choices = body["choices"]
        if not isinstance(choices, list) or not choices:
            raise TypeError("choices missing or empty")
        message = choices[0]["message"]
        if not isinstance(message, dict):
            raise TypeError("message is not a dict")
        content = message["content"]
        if not isinstance(content, str):
            raise TypeError(f"content is {type(content).__name__}, not str")
        return content.strip()
    except (KeyError, IndexError, ValueError, TypeError, AttributeError) as e:
        log.warning("%s: unparseable provider JSON: %s", model, e)
        raise _SkipModel(
            f"{model}: samajh na aane wala response — agla model try ho raha hai.",
            raw=f"{model}: bad JSON ({e})",
        )


def generate(system_prompt: str, user_prompt: str, max_tokens: int = 2000,
            user_id: int = 0) -> dict:
    """Returns {'text', 'model', 'cached'}. Cache quota se PEHLE check hota hai."""
    conn = get_conn()
    try:
        key = _hash(system_prompt, user_prompt, max_tokens)
        row = conn.execute(
            "SELECT response FROM cache WHERE prompt_hash=? AND user_id=?",
            (key, user_id),
        ).fetchone()
        if row:
            log.info("cache hit user=%s", user_id)
            return {"text": row["response"], "model": "cache", "cached": True}

        # Pre-condition, ek dafa up front (audit L-7).
        if not settings.openrouter_api_key:
            raise MissingApiKey(
                "OPENROUTER_API_KEY set nahi hai. .env file me key dalo — "
                "free key banti hai: https://openrouter.ai/keys (koi card nahi chahiye)."
            )

        today = date.today().isoformat()
        cap = user_daily_cap(user_id)

        _pace(conn)

        skipped: list[str] = []
        for model in settings.model_chain:
            _spend_quota(conn, user_id, today, cap)  # har attempt gin'ti hai (audit B-3)
            log.info("provider call user=%s model=%s", user_id, model)
            try:
                text = _call(model, system_prompt, user_prompt, max_tokens)
            except _SkipModel as e:
                if e.raw:
                    log.info("skip model: %s", e.raw)
                skipped.append(str(e))
                continue
            conn.execute(
                "INSERT OR REPLACE INTO cache (prompt_hash, user_id, response, created_at)"
                " VALUES (?,?,?,?)",
                (key, user_id, text, time.time()),
            )
            conn.commit()
            log.info("served user=%s model=%s", user_id, model)
            return {"text": text, "model": model, "cached": False}

        raise AllModelsFailed(
            "Saare free models fail ho gaye. "
            "Thori dair baad try karo (free models peak hours me busy hote hain)."
        )
    finally:
        conn.close()


def usage_today(user_id: int = 0) -> dict:
    """Kept for compat; prefer database.usage_for_user."""
    from database import usage_for_user
    return usage_for_user(user_id)
