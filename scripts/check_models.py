#!/usr/bin/env python3
"""List OpenRouter :free models so the operator can refresh config.model_chain.

Free-model IDs rotate on OpenRouter; a retired ID breaks every feature until it is
replaced (audit A-1). Run:  python scripts/check_models.py
Documented in README (run/publish section).

No API key needed for listing — /api/models is public.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from ai_client import _proxy_url  # same explicit-proxy handling as the app

URL = "https://openrouter.ai/api/v1/models"


def main() -> int:
    try:
        # trust_env=False: httpx 0.28.1 crashes parsing bracketed IPv6 entries in
        # NO_PROXY; the proxy (if any) comes from _proxy_url() explicitly.
        client = httpx.Client(timeout=60, trust_env=False, proxy=_proxy_url())
        resp = client.get(URL)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:  # noqa: BLE001 - operator tool, print and exit
        print(f"OpenRouter /api/models fetch failed: {e}", file=sys.stderr)
        return 1

    free = [
        m["id"] for m in data.get("data", [])
        if ":free" in m.get("id", "") or (m.get("pricing") or {}).get("prompt") == "0"
    ]
    print(f"Found {len(free)} free models:")
    for mid in sorted(free):
        print(f"  {mid}")

    from config import Settings  # noqa: E402
    current = Settings().model_chain
    print("\nCurrent config.model_chain:")
    for mid in current:
        status = "OK" if mid in free else "MISSING/RETIRED?"
        print(f"  [{status}] {mid}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
