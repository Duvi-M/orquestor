from __future__ import annotations

import json
import os
import sys
from urllib.error import URLError
from urllib.request import urlopen

API_BASE = os.getenv("SMOKE_API_BASE", "http://127.0.0.1:9000")
WEB_BASE = os.getenv("SMOKE_WEB_BASE", "http://127.0.0.1:5173")
TIMEOUT_SECONDS = float(os.getenv("SMOKE_TIMEOUT_SECONDS", "3"))


def _get_json(url: str) -> dict:
    with urlopen(url, timeout=TIMEOUT_SECONDS) as response:
        return json.loads(response.read().decode("utf-8"))


def _get_text(url: str) -> str:
    with urlopen(url, timeout=TIMEOUT_SECONDS) as response:
        return response.read().decode("utf-8")


def main() -> int:
    try:
        health = _get_json(f"{API_BASE}/healthz")
        ready = _get_json(f"{API_BASE}/readyz")
        html = _get_text(WEB_BASE)
    except (OSError, URLError, json.JSONDecodeError) as exc:
        print(f"smoke_local failed: {exc}", file=sys.stderr)  # noqa: T201
        return 1

    if health.get("ok") is not True:
        print(f"smoke_local failed: healthz returned {health}", file=sys.stderr)  # noqa: T201
        return 1
    if ready.get("ok") is not True:
        print(f"smoke_local failed: readyz returned {ready}", file=sys.stderr)  # noqa: T201
        return 1
    if "Computer Use Orchestrator" not in html:
        print("smoke_local failed: frontend did not return expected HTML", file=sys.stderr)  # noqa: T201
        return 1

    print("smoke_local ok: API health/readiness and frontend responded")  # noqa: T201
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
