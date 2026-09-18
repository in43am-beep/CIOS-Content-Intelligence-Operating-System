"""Shared helpers for routers: tolerant JSON extraction + history logging."""
import json

from database import save_history


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


def finish(feature: str, req, result: dict) -> dict:
    data = parse_or_text(result["text"])
    save_history(feature, req.model_dump_json(), result["text"], result["model"])
    return {"ok": True, "model": result["model"], "cached": result["cached"], "data": data}
