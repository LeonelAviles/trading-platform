"""The complete tool boundary for Stratos Research.

The model can only search trusted knowledge and call existing platform APIs.
No shell, Python execution, web access, file writes, or OOS reveal is exposed.
"""

from __future__ import annotations

import json
from datetime import date

import data_store
import strategy_store
from config.instruments import load_instruments
from engine import expr, jobs, spec as spec_mod, validation
from research_agent import analysis
from research_agent import knowledge
from research_agent import workflow


TOOL_DEFINITIONS = [
    {
        "type": "function", "name": "search_knowledge",
        "description": "Search trusted, version-pinned quantitative research. Call this before making a research or strategy-design claim.",
        "strict": True,
        "parameters": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "query": {"type": "string", "description": "A focused research question."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 12},
            },
            "required": ["query", "limit"],
        },
    },
    {
        "type": "function", "name": "get_strategy_language",
        "description": "Get the only supported strategy primitives, operators, timeframes, and a valid Spec v2 template. Use before drafting or saving a strategy.",
        "strict": True,
        "parameters": {"type": "object", "additionalProperties": False, "properties": {}, "required": []},
    },
    {
        "type": "function", "name": "list_strategies",
        "description": "List saved platform strategies and their status.",
        "strict": True,
        "parameters": {"type": "object", "additionalProperties": False, "properties": {}, "required": []},
    },
    {
        "type": "function", "name": "get_strategy",
        "description": "Read one saved Strategy Spec by ID.",
        "strict": True,
        "parameters": {
            "type": "object", "additionalProperties": False,
            "properties": {"strategy_id": {"type": "string"}}, "required": ["strategy_id"],
        },
    },
    {
        "type": "function", "name": "save_strategy",
        "description": "Validate and save a complete Strategy Spec v2. Only call after the user explicitly asks to create or revise a strategy.",
        "strict": False,
        "parameters": {
            "type": "object", "additionalProperties": False,
            "properties": {"strategy": {"type": "object", "description": "Complete Strategy Spec v2."}},
            "required": ["strategy"],
        },
    },
    {
        "type": "function", "name": "run_validation",
        "description": "Queue in-sample and walk-forward backtests for a saved strategy. Never exposes or runs OOS. Only call when the user asks to test a strategy.",
        "strict": True,
        "parameters": {
            "type": "object", "additionalProperties": False,
            "properties": {"strategy_id": {"type": "string"}}, "required": ["strategy_id"],
        },
    },
    {
        "type": "function", "name": "get_validation",
        "description": "Read the latest in-sample/walk-forward validation report for a strategy. It never reveals OOS.",
        "strict": True,
        "parameters": {
            "type": "object", "additionalProperties": False,
            "properties": {"strategy_id": {"type": "string"}}, "required": ["strategy_id"],
        },
    },
    {
        "type": "function", "name": "get_daily_direction_stats",
        "description": "Directly answer an observational chat question about whether the prior RTH session predicts next-session continuation or reversal. Calculates probabilities from frozen in-sample data without creating a strategy, simulating trades, invoking Nautilus, or revealing OOS.",
        "strict": True,
        "parameters": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "symbol": {"type": "string", "description": "Continuous platform symbol, normally ES1! or NQ1!."},
                "date_from": {"type": ["string", "null"], "description": "Optional inclusive YYYY-MM-DD."},
                "date_to": {"type": ["string", "null"], "description": "Optional inclusive YYYY-MM-DD."},
            },
            "required": ["symbol", "date_from", "date_to"],
        },
    },
    {
        "type": "function", "name": "get_level_event_stats",
        "description": ("Observational event study on frozen in-sample data: what happens after the FIRST intraday "
                        "break of a session structure level — prior-day high/low/close, the opening range, or session "
                        "VWAP. Reports break frequency, continuation versus reversal into the close, follow-through and "
                        "pullback distributions, and (optionally) the same statistics for events whose breakout bar "
                        "meets order-flow conditions. Calculates directly from stored bars without creating a strategy, "
                        "simulating trades, invoking Nautilus, or revealing OOS."),
        "strict": True,
        "parameters": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "symbol": {"type": "string", "description": "Continuous platform symbol, normally ES1! or NQ1!."},
                "level": {"type": "string", "enum": list(analysis.LEVELS),
                          "description": "Which session structure level to study."},
                "direction": {"type": ["string", "null"],
                              "description": "'above' or 'below'. Required for prior_day_close and vwap; inferred for high/low levels."},
                "or_minutes": {"type": ["integer", "null"],
                               "description": "Opening-range length in minutes for opening_range_* levels (default 15; 60 = initial balance)."},
                "measure_until": {"type": ["string", "null"],
                                  "description": ("Where excursion measurement stops: 'session_close' (default), 'level' "
                                                  "(first return to the broken level), 'range_mid' (first touch of the source "
                                                  "range's midpoint — use when the user defines reversal as re-entering the "
                                                  "middle of the range), or 'range_opposite' (the range's other side). "
                                                  "Adds reversal rate, follow-through before reversal, and time to reversal.")},
                "date_from": {"type": ["string", "null"], "description": "Optional inclusive YYYY-MM-DD."},
                "date_to": {"type": ["string", "null"], "description": "Optional inclusive YYYY-MM-DD."},
                "min_aggressor_share": {"type": ["number", "null"],
                                        "description": "Optional 0–1: keep events whose breakout bar has at least this share of aggressive volume in the breakout direction."},
                "min_volume": {"type": ["number", "null"], "description": "Optional: minimum breakout-bar volume in contracts."},
                "max_range_ticks": {"type": ["number", "null"],
                                    "description": "Optional: maximum breakout-bar range in ticks (absorption-style narrow bars)."},
            },
            "required": ["symbol", "level", "direction", "or_minutes", "measure_until", "date_from", "date_to",
                         "min_aggressor_share", "min_volume", "max_range_ticks"],
        },
    },
    {
        "type": "function", "name": "list_backtest_jobs",
        "description": ("List backtest jobs and their live status: queued, running (with percent complete and ETA), "
                        "done (with headline metrics), or error. Use it to report whether validation is actually "
                        "running. Read-only; OOS job results stay hidden."),
        "strict": True,
        "parameters": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "strategy_id": {"type": ["string", "null"], "description": "Optional: only this strategy's jobs."},
                "limit": {"type": ["integer", "null"], "description": "Maximum rows, newest first (default 20)."},
            },
            "required": ["strategy_id", "limit"],
        },
    },
    {
        "type": "function", "name": "get_data_coverage",
        "description": ("What data exists to analyze: symbols, their bar date ranges, the frozen evidence windows "
                        "(in-sample and walk-forward), and which days have materialised MBO book-liquidity files. "
                        "Check this before claiming a data limitation."),
        "strict": True,
        "parameters": {"type": "object", "additionalProperties": False, "properties": {}, "required": []},
    },
    {
        "type": "function", "name": "conclude_research",
        "description": "Finish an active sequential research workflow with either a validated champion or no edge. Always call this instead of leaving a completed workflow open.",
        "strict": True,
        "parameters": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "outcome": {"type": "string", "enum": ["champion", "no_edge"]},
                "strategy_id": {"type": ["string", "null"]},
                "summary": {"type": "string"},
            },
            "required": ["outcome", "strategy_id", "summary"],
        },
    },
]


def _strategy_language() -> dict:
    return {
        "schemaVersion": 2,
        "timeframes": list(spec_mod.TIMEFRAMES),
        "operators": sorted(expr.OPS),
        "primitives": spec_mod.primitive_docs(),
        "schema": spec_mod.json_schema(),
    }


def _clamp_in_sample(symbol: str, date_from: str | None, date_to: str | None) -> tuple[date, date]:
    """Clamp a requested date range to the frozen in-sample window."""
    start = date.fromisoformat(date_from) if date_from else None
    end = date.fromisoformat(date_to) if date_to else None
    if start and end and start > end:
        raise ValueError("date_from must not be after date_to")
    root = load_instruments().root_for_symbol(symbol)
    if root is None:
        raise ValueError(f"unknown symbol '{symbol}'")
    is_start, is_end = validation.window_for(root.root, "is")
    start = max(start, is_start) if start else is_start
    end = min(end, is_end) if end else is_end
    if start > end:
        raise ValueError("requested dates do not overlap the frozen in-sample window")
    return start, end


def _latest_mode(strategy_id: str) -> str | None:
    for job in jobs.list_jobs():
        if job.get("strategyId") == strategy_id and job.get("status") == "done" and job.get("windowKind") == "is":
            return job.get("mode")
    return None


def execute(name: str, arguments: dict, context: dict | None = None) -> tuple[object, list[dict]]:
    """Return (JSON-serializable output, citations used by this call)."""
    if name == "search_knowledge":
        found = knowledge.search(arguments["query"], arguments.get("limit", 8))
        citations = [{k: item[k] for k in ("source", "path", "heading", "url", "citation")} for item in found]
        return {"results": found}, citations
    if name == "get_strategy_language":
        return _strategy_language(), []
    if name == "list_strategies":
        return [{k: s.get(k) for k in ("id", "name", "status", "description")} for s in strategy_store.list_strategies()], []
    if name == "get_strategy":
        strategy = strategy_store.get_strategy(arguments["strategy_id"])
        if strategy is None:
            raise ValueError(f"strategy '{arguments['strategy_id']}' not found")
        return strategy, []
    if name == "save_strategy":
        incoming = dict(arguments["strategy"])
        if context and context.get("run_id"):
            workflow.validate_revision(context["run_id"], incoming)
            incoming.pop("id", None)  # revisions are immutable children, never parent overwrites
        incoming["origin"] = {"type": "agent", "sourceId": (context or {}).get("run_id")}
        incoming["status"] = "draft"
        saved = strategy_store.save_strategy(incoming)
        if context is not None:
            context["last_saved_strategy_id"] = saved["id"]
        return saved, []
    if name == "run_validation":
        strategy = strategy_store.get_strategy(arguments["strategy_id"])
        if strategy is None:
            raise ValueError(f"strategy '{arguments['strategy_id']}' not found")
        if context is None or not context.get("thread_id"):
            raise ValueError("validation requires a Stratos thread")
        if context.get("run_id") and context.get("last_saved_strategy_id") != strategy["id"]:
            raise ValueError("validate only the single revision created from the completed candidate")
        queued = jobs.run_validation(strategy)
        if context.get("run_id"):
            run = workflow.queue_revision(context["run_id"], strategy, queued)
        else:
            run = workflow.start(context["thread_id"], strategy, queued)
            context["run_id"] = run["id"]
        return {"workflow": run, "jobs": queued}, []
    if name == "get_validation":
        strategy_id = arguments["strategy_id"]
        strategy = strategy_store.get_strategy(strategy_id)
        if strategy is None:
            raise ValueError(f"strategy '{strategy_id}' not found")
        mode = _latest_mode(strategy_id) or spec_mod.required_mode(strategy)
        report = validation.report(strategy_id, mode=mode, risk=strategy.get("risk"), include_oos=False)
        # Defense in depth: an optimizer/research conversation never receives OOS.
        report.pop("outOfSample", None)
        if isinstance(report.get("windows"), dict):
            report["windows"].pop("oos", None)
        return report, []
    if name == "get_daily_direction_stats":
        start, end = _clamp_in_sample(arguments["symbol"], arguments.get("date_from"), arguments.get("date_to"))
        result = data_store.get_daily_direction_stats(arguments["symbol"], start, end)
        result["scope"] = "in_sample_only"
        result["oosHidden"] = True
        return result, []
    if name == "get_level_event_stats":
        start, end = _clamp_in_sample(arguments["symbol"], arguments.get("date_from"), arguments.get("date_to"))
        result = analysis.level_event_stats(
            arguments["symbol"], arguments["level"], date_from=start, date_to=end,
            direction=arguments.get("direction"),
            or_minutes=int(arguments.get("or_minutes") or 15),
            measure_until=arguments.get("measure_until") or "session_close",
            min_aggressor_share=arguments.get("min_aggressor_share"),
            min_volume=arguments.get("min_volume"),
            max_range_ticks=arguments.get("max_range_ticks"),
        )
        result["scope"] = "in_sample_only"
        result["oosHidden"] = True
        return result, []
    if name == "list_backtest_jobs":
        rows = jobs.list_jobs()
        if arguments.get("strategy_id"):
            rows = [r for r in rows if r.get("strategyId") == arguments["strategy_id"]]
        out = []
        for r in rows[: int(arguments.get("limit") or 20)]:
            entry = {k: r.get(k) for k in ("id", "strategyId", "strategyName", "status", "message", "windowKind",
                                           "mode", "dateFrom", "dateTo", "createdAt", "finishedAt")}
            progress = r.get("progress") or {}
            entry["progress"] = {k: progress.get(k) for k in ("percent", "sessionsCompleted", "sessionsTotal",
                                                              "tradeCount", "etaSeconds")} if progress else None
            metrics = r.get("metrics") or {}
            entry["metrics"] = {k: metrics[k] for k in ("trades", "netPnl", "winRate", "profitFactor",
                                                        "expectancyR", "maxDrawdownPct") if k in metrics} or None
            if r.get("windowKind") == "oos":
                # Defense in depth: a research conversation never sees OOS outcomes.
                entry.update(message=None, progress=None, metrics=None, oosHidden=True)
            out.append(entry)
        return {"jobs": out, "queue": "one worker process; queued jobs run in creation order"}, []
    if name == "get_data_coverage":
        return analysis.data_coverage(), []
    if name == "conclude_research":
        if not context or not context.get("run_id"):
            raise ValueError("there is no active research workflow to conclude")
        return workflow.conclude(context["run_id"], arguments["outcome"], arguments["summary"], arguments.get("strategy_id")), []
    raise ValueError(f"unknown tool '{name}'")


def json_result(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), default=str)
