"""trading — Hermes plugin exposing the quant-engineer toolset.

Why a plugin that talks HTTP instead of one that imports agent_tools:

Plugins load inside Hermes' own interpreter. Importing agent_tools would
drag nautilus_trader, duckdb, polars and databento in with it — a slow
import on every `hermes chat` launch, a standing risk of version conflicts
with Hermes' own dependency tree, and a 180-second CPU-bound backtest
running in the agent's process. So the heavy work stays in the FastAPI
backend that already runs it, and this plugin is a transport shim over
/api/agent/tools (main.py). The cost of that split is the obvious one:
the backend has to be up, and the tools say so plainly when it isn't.

Layout:
  client.py   — stdlib-only HTTP to the backend
  schemas.py  — GENERATED from agent_tools.TOOLS, see scripts/gen_hermes_schemas.py
  tools.py    — handler factory

Install by symlinking this directory into Hermes:
  ln -s <repo>/backend/hermes_plugin ~/.hermes/plugins/trading
"""

from __future__ import annotations

import logging

from . import client
from .schemas import _SCHEMAS, UNPREFIXED
from .tools import make_handler

logger = logging.getLogger(__name__)

TOOLSET = "trading"

# Rough grouping so `hermes tools` reads as something other than 15 identical
# rows: build/inspect strategies, run backtests, analyze results.
_EMOJI = {
    "trading_get_condition_vocabulary": "📖",
    "trading_create_strategy": "🧱",
    "trading_get_strategy": "🧱",
    "trading_list_strategies": "🧱",
    "trading_propose_strategy_revision": "✏️",
    "trading_run_backtest": "⏱️",
    "trading_get_backtest": "⏱️",
    "trading_get_backtest_analytics": "📊",
    "trading_get_win_rate": "📊",
    "trading_compare_backtests": "⚖️",
    "trading_get_trade_features": "🔬",
    "trading_compare_winners_vs_losers": "🔬",
    "trading_find_near_miss_entries": "🎯",
    "trading_log_finding": "📝",
    "trading_get_findings": "📝",
}


def _handle_slash(raw_args: str) -> str:
    """/trading — check the backend link and the schema copy."""
    sub = (raw_args or "status").strip().split()[0] if raw_args.strip() else "status"

    if sub in {"help", "-h", "--help"}:
        return (
            "/trading — trading-platform backend link\n\n"
            "  status   Is the backend reachable, and at what URL\n"
            "  drift    Compare shipped schemas against the live manifest\n"
        )

    if sub == "status":
        up = client.health()
        return (
            f"[trading] backend {'reachable' if up else 'UNREACHABLE'} at {client.BASE_URL}\n"
            f"          {len(_SCHEMAS)} tools registered in toolset '{TOOLSET}'"
            + ("" if up else "\n          start it: uvicorn main:app --port 8000 (from backend/)")
        )

    if sub == "drift":
        try:
            live = {t["function"]["name"] for t in client.fetch_manifest()}
        except client.BackendUnavailable as e:
            return f"[trading] can't check drift: {e}"
        shipped = set(UNPREFIXED.values())
        missing, extra = live - shipped, shipped - live
        if not missing and not extra:
            return f"[trading] schemas in sync ({len(shipped)} tools)."
        lines = ["[trading] schemas are STALE — run scripts/gen_hermes_schemas.py"]
        if missing:
            lines.append(f"  backend has, plugin lacks: {', '.join(sorted(missing))}")
        if extra:
            lines.append(f"  plugin has, backend lacks: {', '.join(sorted(extra))}")
        return "\n".join(lines)

    return f"Unknown subcommand: {sub}\n\nTry /trading help"


def register(ctx) -> None:
    """Called once by the plugin loader."""
    for name, schema in _SCHEMAS.items():
        ctx.register_tool(
            name=name,
            toolset=TOOLSET,
            schema=schema,
            handler=make_handler(name),
            description=schema["function"]["description"],
            emoji=_EMOJI.get(name, "📈"),
        )
    ctx.register_command(
        "trading",
        handler=_handle_slash,
        description="Check the trading-platform backend link and schema freshness.",
    )
    logger.debug("trading plugin registered %d tools", len(_SCHEMAS))
