"""Shared helpers for routers: tolerant JSON extraction + history logging."""
import json
import logging

from database import save_history

log = logging.getLogger("cios.routers")


def wrap_user(content: str) -> str:
    """User content ko explicit delimiters me lapeto (audit M1)."""
    return f"<<<USER_INPUT>>>\n{content}\n<<<END>>>"


def parse_or_text(raw: str) -> dict:
    """Free models kabhi kabhi JSON ke ird-gird text lapet dete hain — pehla {...} block nikaalo."""
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            pass
    return {"text": raw}


def finish(feature: str, req, result: dict, user_id: int = 0) -> dict:
    data = parse_or_text(result["text"])
    # History best-effort hai (audit B-5): DB fail ho to bhi generation wapas do.
    try:
        save_history(feature, req.model_dump_json(), result["text"], result["model"],
                     user_id=user_id)
    except Exception:
        log.exception("save_history failed (best-effort) feature=%s user=%s", feature, user_id)
    return {"ok": True, "model": result["model"], "cached": result["cached"], "data": data}
