"""HTTP transport to the trading-platform backend.

Deliberately stdlib-only (urllib, not httpx/requests): this module is
imported into *Hermes'* interpreter, and the whole point of the HTTP split
is that the plugin adds no dependencies to it. See __init__.py for why the
plugin doesn't just import agent_tools directly.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

# Backtests run synchronously behind /api/agent/tools/run_backtest and
# agent_tools.run_backtest allows itself 180s, so the client has to wait
# longer than the server will — otherwise a legitimate slow backtest looks
# like a transport failure.
DEFAULT_TIMEOUT = 200.0

BASE_URL = os.environ.get("TRADING_API_URL", "http://127.0.0.1:8000").rstrip("/")


class BackendUnavailable(RuntimeError):
    """The backend isn't reachable — distinct from a tool that ran and failed."""


def _request(method: str, path: str, payload: dict | None = None, timeout: float = DEFAULT_TIMEOUT) -> dict:
    url = f"{BASE_URL}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        # call_tool() returns tool failures as 200s, so a non-2xx here is a
        # routing/validation problem with the bridge itself, not a bad idea
        # from the model. Surface it verbatim rather than dressing it up.
        body = e.read().decode(errors="replace")[:500]
        raise BackendUnavailable(f"HTTP {e.code} from {url}: {body}") from e
    except urllib.error.URLError as e:
        raise BackendUnavailable(
            f"cannot reach the trading backend at {BASE_URL} ({e.reason}). "
            f"Start it with: uvicorn main:app --port 8000 (from backend/), "
            f"or set TRADING_API_URL if it listens elsewhere."
        ) from e
    except TimeoutError as e:
        raise BackendUnavailable(f"{url} timed out after {timeout}s") from e


def call_tool(name: str, arguments: dict, timeout: float = DEFAULT_TIMEOUT) -> dict:
    """Invoke one agent_tools tool over the bridge."""
    return _request("POST", f"/api/agent/tools/{name}", arguments, timeout)


def fetch_manifest(timeout: float = 10.0) -> list[dict]:
    """Live TOOLS manifest — used by `hermes /trading drift`, not at register
    time (registration must work with the backend down)."""
    return _request("GET", "/api/agent/tools", None, timeout)


def health(timeout: float = 5.0) -> bool:
    try:
        _request("GET", "/api/engine/status", None, timeout)
        return True
    except BackendUnavailable:
        return False
