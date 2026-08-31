"""Tools for the quant-engineer agent (Hermes or any OpenAI-tool-calling-
compatible framework).

Scope, matching the intended workflow: the human supplies name/symbol/
direction/idea; the agent turns the idea into a deterministic strategy
(strategy_spec.py's condition vocabulary), runs it through the real
NautilusTrader backtest (nautilus_runner.py), then does the "heavy work" —
attach market context to every trade at entry, compare winners vs losers,
surface near-miss entries — so it can propose specific, evidence-backed
rule changes instead of guessing.

Two deliberate scope decisions:

1. `direction: "both"` isn't something the underlying engine supports —
   ConfigStrategy (nautilus_backtest.py) is single-directional per run. So
   "both" is handled here, not in the engine: create_strategy() saves two
   sibling strategies (one long, one short) sharing a `directionGroup` id,
   and the agent backtests/analyzes each side separately. That's usually
   more honest anyway — long and short setups rarely share one rule set.

2. Findings are persisted as JSON alongside the backtest job
   (backtests/<job_id>/findings.json), the same pattern job.json/
   strategy.json/trades.json already use — NOT the ai_sessions/
   analysis_findings Postgres tables from models.py. Those use UUID
   primary keys; strategies/jobs here use uuid4().hex[:12] file-based ids
   with no row to FK against. Bridging that is a real migration decision
   (move strategies/backtests into Postgres as the source of truth), not
   something to half-do as a side effect of adding agent tools.
"""

import json
import statistics
import uuid
from datetime import datetime, timezone
from pathlib import Path

import data_store
import nautilus_runner
import strategy_spec
import strategy_store
from condition_engine import Indicators, condition_lookback, eval_condition, session_minutes
from engine import spec as spec_mod

BACKEND_DIR = Path(__file__).resolve().parent
STRATEGIES_DIR = BACKEND_DIR / "strategies"
JOBS_DIR = nautilus_runner.JOBS_DIR


class ToolError(Exception):
    """Raised for expected, tool-caller-facing failures (bad id, invalid
    strategy, ...) — caught by call_tool() and reported back to the model
    as a normal (non-crashing) tool result."""


# --------------------------------------------------------------------------
# Strategy DSL
# --------------------------------------------------------------------------

def get_spec_schema() -> dict:
    """The Strategy Spec v2 JSON Schema, every executable primitive with its
    parameters and docstring, the expression operators, and three worked
    examples (an ORB breakout, an ORB retest, a teaching-derived spec). Call
    this before building or revising a strategy: the expression tree may only
    reference primitives listed here; concepts that are missing must be
    composed from these or requested with request_primitive (Phase 4)."""
    from engine import expr as X

    examples = [
        {"name": "ORB 15m — breakout", "spec": _EXAMPLE_ORB_BREAKOUT},
        {"name": "ORB 15m — retest", "spec": _EXAMPLE_ORB_RETEST},
        {"name": "Teaching-derived: OR-low absorption longs", "spec": _EXAMPLE_TEACHING},
    ]
    # Compact on purpose: the full JSON Schema is 40 KB and would be truncated in a tool result.
    prims = [{"name": d["name"], "doc": d["doc"], "output": d["output"], "updateOn": d["updateOn"], "tf": d["tfCapable"],
              "params": {k: (v["type"] + (f" default {v['default']}" if v.get("default") is not None else " REQUIRED") + (f" one of {v['choices']}" if v.get("choices") else ""))
                         for k, v in d["params"].items()}} for d in spec_mod.primitive_docs()]
    return {"specShape": _SPEC_SHAPE, "primitives": prims, "operators": sorted(X.OPS), "operatorSemantics": _OPERATOR_DOCS,
            "fields": list(X.FIELDS), "timeframes": list(spec_mod.TIMEFRAMES), "structures": list(spec_mod.STRUCTURES),
            "levels": list(spec_mod.LEVELS), "examples": examples,
            "notes": ["Omit fields that equal their defaults to keep tool inputs short.",
                      "Leaves: numbers, {\"ind\": name, \"params\": {...}, \"tf\"?}, {\"field\": open|high|low|close|volume|delta, \"tf\"?}.",
                      "Context timeframes must be coarser multiples of the primary and listed in timeframes.context."]}


_SPEC_SHAPE = {
    "schemaVersion": 2, "name": "str", "description": "str?",
    "instrument": {"root": "ES|NQ|MES|MNQ", "symbol": "ES1!|NQ1!|..."},
    "timeframes": {"primary": "1min|5min|15min|30min|1h|4h|1D", "context": ["same choices, coarser than primary"]},
    "direction": "long|short|both (both = short side mirrors the long rules)",
    "session": {"entryWindow": {"start": "HH:MM ET", "end": "HH:MM ET"}, "noTradeWindows": [{"start": "HH:MM", "end": "HH:MM"}], "flattenAt": "HH:MM ET (default 15:58)"},
    "entry": {"trigger": "expr", "sequence": [{"when": "expr", "withinBars": "int"}], "orderType": "market|limit|stop",
              "limitOffsetTicks": "int", "stopOffsetTicks": "int", "timeoutBars": "int"},
    "filters": ["expr (ANDed with trigger; one per toggle-able idea)"],
    "exit": {"stop": {"type": "atr|ticks|points|percent|structure", "value": "number", "period": "int (atr)", "structure": "swing_low|swing_high|or_low|or_high|session_low|session_high|bar_low|bar_high", "bufferTicks": "int"},
             "target": {"type": "rr|ticks|points|level", "value": "number", "level": "session_high|session_low|vah|val|poc|prior_day_high|prior_day_low|or_high|or_low|vwap"},
             "trailing": {"type": "atr|ticks", "value": "number", "period": "int", "activateAtR": "number"}, "breakeven": {"atR": "number", "offsetTicks": "int"},
             "timeStop": {"bars": "int"}, "scaleOut": [{"atR": "number", "fraction": "0-1"}]},
    "sizing": {"type": "fixed_risk|fixed_contracts|vol_scaled", "value": "number (% risk or contracts)", "maxContracts": "int"},
    "constraints": {"maxTradesPerDay": "int", "cooldownBars": "int", "stopAfterConsecutiveLosses": "int"},
    "execution": {"mode": "bars|ticks|l3"},
}
_OPERATOR_DOCS = {
    "and/or/not": "boolean combinators", "gt/gte/lt/lte/eq": "compare two values", "between(x, lo, hi)": "lo <= x <= hi",
    "cross_above(a, b)/cross_below(a, b)": "a crossed b on this bar close", "rising(x, bars)/falling(x, bars)": "x monotonic for N bars",
    "within_ticks(a, b, n)": "|a - b| <= n ticks", "touched(level, toleranceTicks, withinBars)": "price came within tolerance of level in the last N bars",
    "held_above(level, bars)/held_below(level, bars)": "closes stayed beyond level for N bars", "bars_since(expr)": "bars since expr was last true (compare with gt/gte)",
    "retest(level, toleranceTicks, withinBars)": "broke level in the trade direction, came back within tolerance, closed back on the breakout side",
}


def get_condition_vocabulary() -> dict:
    """Deprecated alias of get_spec_schema (the v1 vocabulary is gone)."""
    return get_spec_schema()


_EXAMPLE_ORB_BREAKOUT = {
    "schemaVersion": 2, "name": "ORB 15m — breakout", "instrument": {"root": "ES", "symbol": "ES1!"},
    "timeframes": {"primary": "1min", "context": []}, "direction": "both",
    "session": {"entryWindow": {"start": "09:45", "end": "11:30"}, "flattenAt": "15:58"},
    "entry": {"trigger": {"op": "gt", "args": [{"field": "close"}, {"ind": "opening_range_high", "params": {"minutes": 15}}]},
              "orderType": "market", "timeoutBars": 1},
    "filters": [],
    "exit": {"stop": {"type": "structure", "structure": "or_low", "bufferTicks": 2}, "target": {"type": "rr", "value": 2.0},
             "trailing": None, "breakeven": None, "timeStop": None, "scaleOut": []},
    "sizing": {"type": "fixed_risk", "value": 0.5, "maxContracts": 5},
    "constraints": {"maxTradesPerDay": 1, "cooldownBars": 0, "stopAfterConsecutiveLosses": 1, "maxConcurrentPositions": 1},
    "execution": {"mode": "ticks"},
}
_EXAMPLE_ORB_RETEST = {**_EXAMPLE_ORB_BREAKOUT, "name": "ORB 15m — retest",
    "entry": {"sequence": [{"when": {"op": "gt", "args": [{"field": "close"}, {"ind": "opening_range_high", "params": {"minutes": 15}}]}, "withinBars": 30}],
              "trigger": {"op": "retest", "args": [{"ind": "opening_range_high", "params": {"minutes": 15}}, 4, 20]},
              "orderType": "market", "timeoutBars": 1}}
_EXAMPLE_TEACHING = {
    "schemaVersion": 2, "name": "Leonel — OR low absorption longs", "origin": {"type": "teaching", "sourceId": None},
    "instrument": {"root": "ES", "symbol": "ES1!"}, "timeframes": {"primary": "1min", "context": ["5min"]}, "direction": "long",
    "session": {"entryWindow": {"start": "09:45", "end": "15:00"}, "flattenAt": "15:58"},
    "entry": {"trigger": {"op": "and", "args": [
        {"op": "within_ticks", "args": [{"field": "low"}, {"ind": "opening_range_low", "params": {"minutes": 15}}, 6]},
        {"op": "gte", "args": [{"ind": "absorption", "params": {"side": "bid", "min_volume": 800, "max_range_ticks": 3}}, 1]},
        {"op": "gt", "args": [{"ind": "cvd_slope", "params": {"n": 5}}, 0]}]},
        "orderType": "market", "timeoutBars": 1},
    "filters": [],
    "exit": {"stop": {"type": "structure", "structure": "bar_low", "bufferTicks": 3}, "target": {"type": "level", "level": "vwap"}},
    "sizing": {"type": "fixed_risk", "value": 0.5, "maxContracts": 5}, "execution": {"mode": "ticks"},
}


def _save_one(strategy: dict) -> dict:
    try:
        return strategy_store.save_strategy(strategy)
    except strategy_store.StrategyError as e:
        raise ToolError(str(e))


def create_strategy(spec: dict | None = None, **legacy) -> dict:
    """Validate and save a new Strategy Spec v2 (pass the whole spec as `spec`).
    Errors come back as readable messages — fix them and call again. The
    legacy v1 keyword form (name, symbol, direction, conditions, stop,
    target, ...) is still accepted and converted; `direction: "both"` is a
    single v2 strategy whose short side mirrors the long rules."""
    if spec is None:
        if not legacy:
            raise ToolError("pass the strategy as `spec`")
        spec = dict(legacy)
        if spec.get("direction") == "both" and "conditions" in spec:
            spec = {**strategy_store.coerce({**spec, "direction": "long"}), "direction": "both", "name": spec["name"]}
    return _save_one(spec)


def get_strategy(strategy_id: str) -> dict:
    s = strategy_store.get_strategy(strategy_id)
    if s is None:
        raise ToolError(f"strategy '{strategy_id}' not found")
    return s


def list_strategies() -> list[dict]:
    return strategy_store.list_strategies()


def _set_path(doc: dict, path: str, value):
    cur = doc
    parts = path.split(".")
    for p in parts[:-1]:
        if p not in cur or not isinstance(cur[p], dict):
            cur[p] = {}
        cur = cur[p]
    cur[parts[-1]] = value


def propose_strategy_revision(base_strategy_id: str, changes: dict, rationale: str, name: str | None = None,
                              changed_variable: str | None = None) -> dict:
    """Clone a strategy with ONE variable changed and save it as a lineage
    child (parentId = base, trialIndex + 1). `changes` maps dotted paths to
    values, e.g. {"exit.target.value": 3.0} or {"filters": [...]}. Say which
    variable changed in `changed_variable`; if two fields move together
    because one is the unit of the other, that counts as one change."""
    base = get_strategy(base_strategy_id)
    revised = json.loads(json.dumps(base))
    for k, v in changes.items():
        if "." in k:
            _set_path(revised, k, v)
        else:
            revised[k] = v
    revised["id"] = None
    revised["name"] = name or f"{base['name']} (revised)"
    revised["status"] = "draft"
    revised["lineage"] = {"parentId": base_strategy_id, "changedVariable": changed_variable or ", ".join(changes),
                          "rationale": rationale, "trialIndex": int((base.get("lineage") or {}).get("trialIndex", 0)) + 1}
    for k in ("createdAt", "updatedAt"):
        revised.pop(k, None)
    return _save_one(revised)


def update_strategy(strategy_id: str, changes: dict, rationale: str | None = None) -> dict:
    """Edit a saved strategy IN PLACE (same id). `changes` maps dotted paths
    or top-level keys to values. Destructive — for A/B experiments use
    propose_strategy_revision."""
    current = get_strategy(strategy_id)
    updated = json.loads(json.dumps(current))
    for k, v in changes.items():
        if "." in k:
            _set_path(updated, k, v)
        else:
            updated[k] = v
    if rationale:
        updated.setdefault("meta", {})["rationale"] = rationale
    for k in ("createdAt", "updatedAt"):
        updated.pop(k, None)
    try:
        return strategy_store.save_strategy(updated, strategy_id=strategy_id)
    except strategy_store.StrategyError as e:
        raise ToolError(str(e))


# --------------------------------------------------------------------------
# Backtesting
# --------------------------------------------------------------------------

def run_backtest(strategy_id: str, timeout_s: float = 180.0) -> dict:
    """Run a strategy through the real NautilusTrader backtest and block
    until it finishes (or times out) — the point of calling this is to get
    trades back to analyze next, so it waits rather than returning a job id
    to poll."""
    strategy = get_strategy(strategy_id)
    # In-sample only: the agent never sees out-of-sample data before
    # finalize (PLATFORM-SPEC.md §4.5). Walk-forward rows join in Phase 4.
    job = nautilus_runner.start_backtest(strategy, window_kind="is")
    job_id = job["id"]

    import time
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        job = nautilus_runner.get_job(job_id)
        if job["status"] in ("done", "error"):
            break
        time.sleep(0.5)
    else:
        raise ToolError(f"backtest '{job_id}' still running after {timeout_s}s — check back with get_backtest later")

    if job["status"] == "error":
        raise ToolError(f"backtest failed: {job.get('message') or 'unknown error'}")
    return {k: v for k, v in job.items() if k != "trades"} | {"tradeCount": len(job.get("trades") or [])}


def get_backtest(job_id: str) -> dict:
    job = nautilus_runner.get_job(job_id)
    if job is None:
        raise ToolError(f"backtest '{job_id}' not found")
    return job


def get_backtest_analytics(job_id: str) -> dict:
    """Win rate, profit factor, expectancy (R), drawdown, Sharpe/SQN, equity
    curve, R-distribution, monthly table, exit-reason mix."""
    return nautilus_runner.strategy_analytics(get_backtest(job_id))


def get_win_rate(job_id: str) -> dict:
    """Cheap, direct answer to "what's the win rate" — pull get_backtest_analytics
    for the full picture."""
    job = get_backtest(job_id)
    trades = [t for t in (job.get("trades") or []) if t.get("exitTime") is not None]
    wins = [t for t in trades if t["pnl"] > 0]
    return {
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(trades) - len(wins),
        "winRate": round(len(wins) / len(trades) * 100, 1) if trades else 0.0,
        "netPnl": round(sum(t["pnl"] for t in trades), 2),
    }


MIN_TRADES_FOR_CONFIDENCE = 20


def compare_backtests(job_id_a: str, job_id_b: str) -> dict:
    """Side-by-side comparison of two backtests — use this whenever a user
    is choosing between two entry approaches ("the breakout or the retest,
    which is better") rather than eyeballing two get_backtest_analytics
    calls yourself. Ranks by expectancy (R), not raw win rate, since
    expectancy accounts for risk sizing and a low-win-rate/high-R strategy
    can beat a high-win-rate/low-R one. Also runs a two-proportion z-test on
    the win-rate difference and flags when either side has too few trades
    (<20) to draw a confident conclusion — don't declare a winner if
    `verdict` says evidence is insufficient, say so plainly instead."""
    job_a, job_b = get_backtest(job_id_a), get_backtest(job_id_b)
    a, b = get_backtest_analytics(job_id_a), get_backtest_analytics(job_id_b)

    warnings = []
    if job_a.get("symbol") != job_b.get("symbol"):
        warnings.append(f"different symbols ({job_a.get('symbol')} vs {job_b.get('symbol')}) — not a fair comparison")
    low_a, low_b = a["trades"] < MIN_TRADES_FOR_CONFIDENCE, b["trades"] < MIN_TRADES_FOR_CONFIDENCE
    if low_a or low_b:
        which = job_a["strategyName"] if low_a and not low_b else job_b["strategyName"] if low_b and not low_a else "both"
        warnings.append(f"{which} under {MIN_TRADES_FOR_CONFIDENCE} closed trades — too few to be confident this isn't noise")

    # Two-proportion z-test on the win-rate difference (95% ~ |z| >= 1.96).
    n1, n2 = a["trades"], b["trades"]
    z = None
    if n1 > 0 and n2 > 0:
        p1, p2 = a["winRate"] / 100, b["winRate"] / 100
        p_pool = (p1 * n1 + p2 * n2) / (n1 + n2)
        se = (p_pool * (1 - p_pool) * (1 / n1 + 1 / n2)) ** 0.5
        z = round((p1 - p2) / se, 2) if se > 0 else 0.0

    metrics = ["trades", "winRate", "profitFactor", "expectancyR", "maxDrawdown", "sharpe", "sqn", "netPnl"]
    side_by_side = {m: {"a": a.get(m), "b": b.get(m)} for m in metrics}

    winner = None  # "a" | "b" | None (tied or insufficient evidence) — machine-readable; `verdict` is the prose form
    if warnings and (low_a or low_b):
        verdict = "insufficient evidence to confidently recommend either — see warnings"
    elif a["expectancyR"] == b["expectancyR"]:
        verdict = "tied on expectancy (R) — no edge either way"
    else:
        winner = "a" if a["expectancyR"] > b["expectancyR"] else "b"
        verdict = f"{winner} ({job_a['strategyName'] if winner == 'a' else job_b['strategyName']}) has higher expectancy"

    return {
        "a": {"jobId": job_id_a, "strategyName": job_a["strategyName"], "symbol": job_a.get("symbol")},
        "b": {"jobId": job_id_b, "strategyName": job_b["strategyName"], "symbol": job_b.get("symbol")},
        "metrics": side_by_side,
        "winRateZScore": z,
        "winRateSignificant": abs(z) >= 1.96 if z is not None else None,
        "warnings": warnings,
        "winner": winner,
        "verdict": verdict,
    }


# --------------------------------------------------------------------------
# Trade-level analysis — the "heavy work"
# --------------------------------------------------------------------------

def _r_multiple(t: dict) -> float | None:
    risk_per_unit = abs(t["entryPrice"] - t["stopPrice"])
    if risk_per_unit <= 0 or not t.get("qty"):
        return None
    return t["pnl"] / (risk_per_unit * t["qty"])


def _replay_features(strategy: dict):
    """Walk every primary bar once through the same FeatureContext + expression
    evaluators the backtest engine uses, recording per-bar market context.
    Returns (bar_times: list[int unix s], features: list[dict]) aligned by
    index — shared by get_trade_features() and find_near_miss_entries()."""
    from engine.expr import Evaluator, walk
    from engine.features import BarRec
    from engine.session import NS, et_to_ns, session_date
    from engine.spec_strategy import SpecRules

    spec = strategy_store.coerce(strategy)
    symbol = (spec.get("instrument") or {}).get("symbol") or spec.get("symbol")
    interval = (spec.get("timeframes") or {}).get("primary") or "1min"
    bars = data_store.bars_to_records(data_store.get_bars(symbol, interval))
    if not bars:
        raise ToolError(f"no bars for {symbol} at {interval}")
    rules = SpecRules(spec)
    ctx = rules.ctx
    direction = spec.get("direction", "long")
    d0 = "short" if direction == "short" else "long"
    trigger = spec["entry"]["trigger"]
    # Sub-conditions: the trigger's top-level AND args (or the trigger itself) plus filters.
    parts = list(trigger.get("args", [])) if isinstance(trigger, dict) and trigger.get("op") == "and" else [trigger]
    parts += list(spec.get("filters") or [])
    part_evals = [Evaluator(pt, ctx, d0) for pt in parts]
    ew = spec["session"]["entryWindow"]
    tf_ns = {"1min": 60, "5min": 300, "15min": 900, "30min": 1800, "1h": 3600, "4h": 14400, "1D": 86400}[interval] * NS
    times, features = [], []
    for bar in bars:
        ts_open = bar["time"] * NS
        vol, delta = bar["volume"], bar["delta"] if bar.get("hasDelta") else 0.0
        buy = (vol + delta) / 2
        rules.on_bar(type("B", (), {})) if False else None
        rec = BarRec(bar["open"], bar["high"], bar["low"], bar["close"], vol, delta, buy, vol - buy, ts_open, ts_open + tf_ns)
        ctx.on_bar(rec)
        for st in rules.states.values():
            st.on_bar(ctx.bar_index)
        for ev in part_evals:
            ev.on_bar()
        d = session_date(ts_open)
        in_window = et_to_ns(d, ew["start"]) <= rec.ts_close < et_to_ns(d, ew["end"])
        truths = [ev.eval() is True for ev in part_evals]
        snap = ctx.snapshot(_FEATURE_NAMES)
        times.append(bar["time"])
        features.append({
            "conditionsTrue": sum(truths), "conditionsTotal": len(truths), "conditions": truths,
            "inSession": in_window, "signal": rules.states[d0].fire(ctx.bar_index) if in_window else False,
            **{k: (round(v, 4) if isinstance(v, float) else v) for k, v in snap.items()},
        })
    return times, features


_FEATURE_NAMES = ["close", "volume", "delta", "rel_volume", "rel_delta", "cvd_session", "cvd_window", "cvd_slope",
                  "delta_divergence", "atr", "rsi", "adx", "vwap", "opening_range_high", "opening_range_low",
                  "session_high", "session_low", "poc", "vah", "val", "time_of_day", "day_of_week", "minutes_to_close",
                  "bars_since_open", "gap_points", "profile_shape"]


# The trader's actual goal, and what "done" means for a tuning run: a majority
# of traded weeks returning at least WEEKLY_TARGET_LOW_PCT, and a positive
# result overall. Weeks are what's judged, not days — a losing day, or a losing
# week, is expected; a strategy that doesn't end up ahead is not.
WEEKLY_TARGET_LOW_PCT = 2.0
WEEKLY_TARGET_HIGH_PCT = 5.0
MIN_WEEKS_FOR_CONFIDENCE = 8


def get_weekly_performance(job_id: str) -> dict:
    """Week-by-week returns for a backtest, scored against the goal: return
    between 2% and 5% in the MAJORITY of weeks traded, and end up positive
    overall. This is the pass/fail check for a strategy — call it on every
    backtest you want to judge, and use `meetsGoal`/`verdict` to decide
    whether to keep tuning or stop. Returns are percentages of account equity
    (starting at 100k, compounding week to week), and only weeks that actually
    traded are counted. Weeks above 5% count as meeting the goal — overshooting
    the band is not a failure — but they're reported separately as
    `weeksAboveBand` because outsized weeks are usually where the risk is."""
    job = get_backtest(job_id)
    trades = sorted(
        (t for t in (job.get("trades") or []) if t.get("exitTime") is not None),
        key=lambda t: t["exitTime"],
    )

    by_week: dict[tuple, float] = {}
    counts: dict[tuple, int] = {}
    for t in trades:
        iso = datetime.fromtimestamp(t["exitTime"], tz=timezone.utc).isocalendar()
        key = (iso.year, iso.week)
        by_week[key] = by_week.get(key, 0.0) + t["pnl"]
        counts[key] = counts.get(key, 0) + 1

    equity = nautilus_runner.STARTING_EQUITY
    weeks = []
    for key in sorted(by_week):
        pnl = by_week[key]
        ret_pct = pnl / equity * 100 if equity > 0 else 0.0
        equity += pnl
        weeks.append({
            "week": f"{key[0]}-W{key[1]:02d}", "trades": counts[key],
            "pnl": round(pnl, 2), "returnPct": round(ret_pct, 2),
            "equityEnd": round(equity, 2),
        })

    n = len(weeks)
    positive = [w for w in weeks if w["returnPct"] > 0]
    at_target = [w for w in weeks if w["returnPct"] >= WEEKLY_TARGET_LOW_PCT]
    in_band = [w for w in at_target if w["returnPct"] <= WEEKLY_TARGET_HIGH_PCT]
    net_return_pct = (equity - nautilus_runner.STARTING_EQUITY) / nautilus_runner.STARTING_EQUITY * 100
    ends_positive = net_return_pct > 0
    majority_at_target = n > 0 and len(at_target) > n / 2
    meets_goal = ends_positive and majority_at_target

    warnings = []
    if n < MIN_WEEKS_FOR_CONFIDENCE:
        warnings.append(
            f"only {n} week(s) traded — under {MIN_WEEKS_FOR_CONFIDENCE}, this is too short a "
            "sample to trust either way"
        )
    if len(at_target) > len(in_band):
        warnings.append(
            f"{len(at_target) - len(in_band)} week(s) returned more than {WEEKLY_TARGET_HIGH_PCT}% — "
            "check whether that is edge or one oversized position"
        )

    if n == 0:
        verdict = "no closed trades — nothing to judge"
    elif meets_goal:
        verdict = (
            f"meets the goal — {len(at_target)}/{n} weeks at or above {WEEKLY_TARGET_LOW_PCT}%, "
            f"{net_return_pct:.1f}% overall"
        )
    elif not ends_positive:
        verdict = f"fails — ends down {net_return_pct:.1f}% overall, so it loses money"
    else:
        verdict = (
            f"fails — positive overall ({net_return_pct:.1f}%) but only {len(at_target)}/{n} weeks "
            f"reached {WEEKLY_TARGET_LOW_PCT}%, short of a majority"
        )

    return {
        "jobId": job_id, "strategyName": job.get("strategyName"),
        "startingEquity": nautilus_runner.STARTING_EQUITY,
        "targetBandPct": [WEEKLY_TARGET_LOW_PCT, WEEKLY_TARGET_HIGH_PCT],
        "weeks": weeks, "weeksTraded": n,
        "positiveWeeks": len(positive),
        "positiveWeekRate": round(len(positive) / n * 100, 1) if n else 0,
        "weeksAtOrAboveTarget": len(at_target),
        "weeksInTargetBand": len(in_band),
        "weeksAboveBand": len(at_target) - len(in_band),
        "targetHitRate": round(len(at_target) / n * 100, 1) if n else 0,
        "netReturnPct": round(net_return_pct, 2),
        "endsPositive": ends_positive,
        "meetsGoal": meets_goal,
        "verdict": verdict,
        "warnings": warnings,
    }


def get_trade_features(job_id: str) -> list[dict]:
    """The core enrichment tool: for every closed trade in a backtest,
    reconstruct the market context at entry — relative volume, ATR14, RSI14,
    hour/day, distance from the 20-bar high/low, AND the Databento MBO order
    flow (deltaBar, cvd20, relDelta20, cvdSession, flowDivergence) — using the
    exact same indicator math the backtest engine used. The flow fields are
    null for symbols with no tick-level side data. This is what
    compare_winners_vs_losers() analyzes — call this first if you want the
    per-trade detail instead of the aggregate comparison."""
    job = get_backtest(job_id)
    strategy_id = job.get("strategyId")
    if not strategy_id:
        raise ToolError("this job has no strategyId — it isn't traceable to a saved strategy")
    strategy = get_strategy(strategy_id)
    times, features = _replay_features(strategy)

    import bisect
    trades = [t for t in (job.get("trades") or []) if t.get("exitTime") is not None]
    out = []
    for t in trades:
        idx = bisect.bisect_right(times, t["entryTime"]) - 1
        idx = max(0, min(idx, len(features) - 1))
        r = _r_multiple(t)
        out.append({
            "tradeId": t["id"],
            "entryTime": t["entryTime"],
            "outcome": "win" if t["pnl"] > 0 else "loss",
            "pnl": t["pnl"],
            "r": round(r, 3) if r is not None else None,
            "exitReason": t["reason"],
            "marketContextAtEntry": features[idx],
        })
    return out


def _mann_whitney_p(a: list[float], b: list[float]) -> float | None:
    """Two-sided Mann-Whitney U p-value (normal approximation with tie correction)."""
    import math

    n1, n2 = len(a), len(b)
    if n1 < 2 or n2 < 2:
        return None
    pooled = sorted([(v, 0) for v in a] + [(v, 1) for v in b])
    ranks = [0.0] * len(pooled)
    i = 0
    tie_term = 0.0
    while i < len(pooled):
        j = i
        while j + 1 < len(pooled) and pooled[j + 1][0] == pooled[i][0]:
            j += 1
        r = (i + j + 2) / 2
        for k in range(i, j + 1):
            ranks[k] = r
        t = j - i + 1
        if t > 1:
            tie_term += t ** 3 - t
        i = j + 1
    r1 = sum(rk for rk, (_, g) in zip(ranks, pooled) if g == 0)
    u1 = r1 - n1 * (n1 + 1) / 2
    mu = n1 * n2 / 2
    n = n1 + n2
    sigma_sq = n1 * n2 / 12 * ((n + 1) - tie_term / (n * (n - 1)))
    if sigma_sq <= 0:
        return 1.0
    z = (u1 - mu) / math.sqrt(sigma_sq)
    return 2 * (1 - 0.5 * (1 + math.erf(abs(z) / math.sqrt(2))))


def compare_winners_vs_losers(job_id: str) -> dict:
    """Statistical comparison of winning vs losing trades' entry context —
    "what do winners have in common." Every numeric primitive in the entry
    feature vector (relative volume/delta, session CVD, ATR, RSI, ADX,
    distance to VWAP/OR/POC, time of day, …) is ranked by effect size
    (Cohen's d: pooled standard deviations between the group means — >0.5
    moderate, >0.8 strong) with a Mann-Whitney p-value. Categorical
    features (entry hour ET, day of week, exit reason, regime tags) get a
    win-rate-by-bucket table. A strong order-flow separation is directly
    actionable: it maps to a filter such as `bar_delta > 0` or
    `rel_volume(20) > 1.5`, which can be tested as a single-variable revision."""
    rows = get_trade_features(job_id)
    if len(rows) < 4:
        raise ToolError(f"only {len(rows)} closed trades — too few for a meaningful comparison (want 10+)")
    job = get_backtest(job_id)
    tags_by_id = {t["id"]: t.get("regimeTags") or [] for t in (job.get("trades") or [])}
    wins = [r for r in rows if r["outcome"] == "win"]
    losses = [r for r in rows if r["outcome"] == "loss"]
    skip = {"conditionsTrue", "conditionsTotal", "conditions", "inSession", "signal", "day_of_week", "close",
            "opening_range_high", "opening_range_low", "session_high", "session_low", "poc", "vah", "val", "vwap"}
    # Distances to levels are comparable across days; raw levels are not.
    def enrich(r):
        m = dict(r["marketContextAtEntry"])
        px = m.get("close")
        for lvl in ("vwap", "opening_range_high", "opening_range_low", "poc", "vah", "val", "session_high", "session_low"):
            if px is not None and m.get(lvl) is not None:
                m[f"dist_{lvl}"] = round(px - m[lvl], 4)
        return m
    W = [enrich(r) for r in wins]
    L = [enrich(r) for r in losses]
    feats = sorted({k for m in W + L for k, v in m.items() if k not in skip and isinstance(v, (int, float)) and not isinstance(v, bool)})
    numeric_comparison = []
    for feat in feats:
        wv = [m[feat] for m in W if isinstance(m.get(feat), (int, float))]
        lv = [m[feat] for m in L if isinstance(m.get(feat), (int, float))]
        if len(wv) < 2 or len(lv) < 2:
            continue
        w_mean, l_mean = statistics.mean(wv), statistics.mean(lv)
        w_std, l_std = statistics.stdev(wv), statistics.stdev(lv)
        pooled = (((len(wv) - 1) * w_std ** 2 + (len(lv) - 1) * l_std ** 2) / (len(wv) + len(lv) - 2)) ** 0.5
        d = (w_mean - l_mean) / pooled if pooled > 0 else 0.0
        numeric_comparison.append({"feature": feat, "winMean": round(w_mean, 3), "lossMean": round(l_mean, 3),
                                   "effectSize": round(d, 3), "pValue": (round(p_, 4) if (p_ := _mann_whitney_p(wv, lv)) is not None else None),
                                   "nWin": len(wv), "nLoss": len(lv)})
    numeric_comparison.sort(key=lambda x: -abs(x["effectSize"]))

    categorical_comparison = {}
    def bucketize(name, key_fn):
        buckets: dict = {}
        for r in rows:
            for k in key_fn(r):
                b = buckets.setdefault(k, {"trades": 0, "wins": 0})
                b["trades"] += 1
                b["wins"] += 1 if r["outcome"] == "win" else 0
        categorical_comparison[name] = sorted(
            [{"value": k, "trades": v["trades"], "winRate": round(v["wins"] / v["trades"] * 100, 1)} for k, v in buckets.items()],
            key=lambda x: -x["trades"])
    from engine.session import NS, ns_to_et

    bucketize("hourEt", lambda r: [ns_to_et(r["entryTime"] * NS).hour])
    bucketize("dayOfWeek", lambda r: [ns_to_et(r["entryTime"] * NS).strftime("%A")])
    bucketize("exitReason", lambda r: [r["exitReason"]])
    bucketize("regime", lambda r: tags_by_id.get(r["tradeId"], []))
    return {"tradeCount": len(rows), "winCount": len(wins), "lossCount": len(losses),
            "numericFeatures": numeric_comparison, "categoricalFeatures": categorical_comparison}


def find_near_miss_entries(strategy_id: str, max_conditions_missing: int = 1, limit: int = 25) -> list[dict]:
    """Bars inside the entry window where all but (up to) max_conditions_missing
    of the entry sub-conditions (the trigger's top-level AND terms plus the
    filters) were true, but the trigger did not fire. Useful for judging
    whether thresholds are too tight; it does not distinguish "a condition
    fell just short" from "already in a position"."""
    strategy = get_strategy(strategy_id)
    spec = strategy_store.coerce(strategy)
    trig = spec["entry"]["trigger"]
    n_parts = (len(trig.get("args", [])) if isinstance(trig, dict) and trig.get("op") == "and" else 1) + len(spec.get("filters") or [])
    if n_parts < 2:
        raise ToolError(
            "near-miss detection needs at least 2 sub-conditions (AND terms or filters) to be meaningful — with only "
            f"{n_parts}, 'missing 1' just means the condition was false, not a close call."
        )
    times, features = _replay_features(strategy)
    near_misses = []
    for t, f in zip(times, features):
        if not f["inSession"] or f.get("signal"):
            continue
        missing = f["conditionsTotal"] - f["conditionsTrue"]
        if 0 < missing <= max_conditions_missing:
            near_misses.append({"time": t, **f})
    near_misses.sort(key=lambda x: -x["time"])
    return near_misses[:limit]


# --------------------------------------------------------------------------
# Findings (persisted alongside the backtest job — see module docstring)
# --------------------------------------------------------------------------

def log_finding(job_id: str, category: str, summary: str, confidence: float | None = None) -> dict:
    """Record an agent finding/hypothesis against a backtest (e.g. "winners
    cluster in the first hour of the session, confidence 0.7") — appended to
    backtests/<job_id>/findings.json so it survives the chat session and
    can be shown in the UI or referenced in a later run."""
    job_dir = JOBS_DIR / job_id
    if not job_dir.exists():
        raise ToolError(f"backtest '{job_id}' not found")
    path = job_dir / "findings.json"
    findings = json.loads(path.read_text()) if path.exists() else []
    entry = {
        "id": uuid.uuid4().hex[:12],
        "category": category,
        "summary": summary,
        "confidence": confidence,
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }
    findings.append(entry)
    path.write_text(json.dumps(findings, indent=2))
    return entry


def get_findings(job_id: str) -> list[dict]:
    path = JOBS_DIR / job_id / "findings.json"
    return json.loads(path.read_text()) if path.exists() else []


# --------------------------------------------------------------------------
# OpenAI/Hermes-compatible function-calling manifest
# --------------------------------------------------------------------------

TOOLS = [
    {"type": "function", "function": {
        "name": "get_spec_schema", "description": get_spec_schema.__doc__,
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "create_strategy", "description": create_strategy.__doc__,
        "parameters": {
            "type": "object",
            "properties": {
                "spec": {"type": "object", "description": "a complete Strategy Spec v2 document (see get_spec_schema)"},
            },
            "required": ["spec"],
        },
    }},
    {"type": "function", "function": {
        "name": "get_strategy", "description": "Fetch a saved strategy by id.",
        "parameters": {"type": "object", "properties": {"strategy_id": {"type": "string"}}, "required": ["strategy_id"]},
    }},
    {"type": "function", "function": {
        "name": "list_strategies", "description": "List all saved strategies.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "propose_strategy_revision", "description": propose_strategy_revision.__doc__,
        "parameters": {
            "type": "object",
            "properties": {
                "base_strategy_id": {"type": "string"},
                "changes": {"type": "object", "description": "fields to override on the base strategy (e.g. new conditions/stop/target)"},
                "rationale": {"type": "string", "description": "why this change, e.g. citing a compare_winners_vs_losers finding"},
                "name": {"type": "string"},
            },
            "required": ["base_strategy_id", "changes", "rationale"],
        },
    }},
    {"type": "function", "function": {
        "name": "update_strategy", "description": update_strategy.__doc__,
        "parameters": {
            "type": "object",
            "properties": {
                "strategy_id": {"type": "string"},
                "changes": {"type": "object", "description": "only the fields to overwrite on the existing strategy (e.g. new conditions/stop/target/session/interval)"},
                "rationale": {"type": "string", "description": "one line on what changed and why"},
            },
            "required": ["strategy_id", "changes"],
        },
    }},
    {"type": "function", "function": {
        "name": "run_backtest", "description": run_backtest.__doc__,
        "parameters": {
            "type": "object",
            "properties": {"strategy_id": {"type": "string"}, "timeout_s": {"type": "number", "description": "default 180"}},
            "required": ["strategy_id"],
        },
    }},
    {"type": "function", "function": {
        "name": "get_backtest", "description": "Full backtest job, including every trade.",
        "parameters": {"type": "object", "properties": {"job_id": {"type": "string"}}, "required": ["job_id"]},
    }},
    {"type": "function", "function": {
        "name": "get_backtest_analytics", "description": get_backtest_analytics.__doc__,
        "parameters": {"type": "object", "properties": {"job_id": {"type": "string"}}, "required": ["job_id"]},
    }},
    {"type": "function", "function": {
        "name": "get_win_rate", "description": get_win_rate.__doc__,
        "parameters": {"type": "object", "properties": {"job_id": {"type": "string"}}, "required": ["job_id"]},
    }},
    {"type": "function", "function": {
        "name": "compare_backtests", "description": compare_backtests.__doc__,
        "parameters": {
            "type": "object",
            "properties": {"job_id_a": {"type": "string"}, "job_id_b": {"type": "string"}},
            "required": ["job_id_a", "job_id_b"],
        },
    }},
    {"type": "function", "function": {
        "name": "get_weekly_performance", "description": get_weekly_performance.__doc__,
        "parameters": {"type": "object", "properties": {"job_id": {"type": "string"}}, "required": ["job_id"]},
    }},
    {"type": "function", "function": {
        "name": "get_trade_features", "description": get_trade_features.__doc__,
        "parameters": {"type": "object", "properties": {"job_id": {"type": "string"}}, "required": ["job_id"]},
    }},
    {"type": "function", "function": {
        "name": "compare_winners_vs_losers", "description": compare_winners_vs_losers.__doc__,
        "parameters": {"type": "object", "properties": {"job_id": {"type": "string"}}, "required": ["job_id"]},
    }},
    {"type": "function", "function": {
        "name": "find_near_miss_entries", "description": find_near_miss_entries.__doc__,
        "parameters": {
            "type": "object",
            "properties": {
                "strategy_id": {"type": "string"},
                "max_conditions_missing": {"type": "integer", "description": "default 1"},
                "limit": {"type": "integer", "description": "default 25"},
            },
            "required": ["strategy_id"],
        },
    }},
    {"type": "function", "function": {
        "name": "log_finding", "description": log_finding.__doc__,
        "parameters": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string"},
                "category": {"type": "string", "description": "e.g. 'pattern', 'risk', 'session-timing'"},
                "summary": {"type": "string"},
                "confidence": {"type": "number", "description": "0..1, optional"},
            },
            "required": ["job_id", "category", "summary"],
        },
    }},
    {"type": "function", "function": {
        "name": "get_findings", "description": "Findings previously logged against a backtest.",
        "parameters": {"type": "object", "properties": {"job_id": {"type": "string"}}, "required": ["job_id"]},
    }},
]

TOOL_FUNCS = {
    "get_spec_schema": get_spec_schema,
    "get_condition_vocabulary": get_condition_vocabulary,
    "create_strategy": create_strategy,
    "get_strategy": get_strategy,
    "list_strategies": list_strategies,
    "propose_strategy_revision": propose_strategy_revision,
    "update_strategy": update_strategy,
    "run_backtest": run_backtest,
    "get_backtest": get_backtest,
    "get_backtest_analytics": get_backtest_analytics,
    "get_win_rate": get_win_rate,
    "compare_backtests": compare_backtests,
    "get_weekly_performance": get_weekly_performance,
    "get_trade_features": get_trade_features,
    "compare_winners_vs_losers": compare_winners_vs_losers,
    "find_near_miss_entries": find_near_miss_entries,
    "log_finding": log_finding,
    "get_findings": get_findings,
}


def call_tool(name: str, arguments: dict) -> dict:
    """Dispatch one tool call by name. Never raises — expected failures
    (ToolError) and unexpected ones alike come back as {"error": "..."} so
    an agent loop can feed the result straight back to the model instead of
    crashing the request."""
    fn = TOOL_FUNCS.get(name)
    if fn is None:
        return {"error": f"unknown tool '{name}'"}
    try:
        result = fn(**arguments)
        return result if isinstance(result, dict) else {"result": result}
    except ToolError as e:
        return {"error": str(e)}
    except TypeError as e:
        return {"error": f"bad arguments for {name}: {e}"}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


# Anthropic Messages API tool format (used when Claude is the model, e.g.
# behind a Hermes agent runtime): flat {name, description, input_schema},
# no "type": "function" wrapper, "parameters" renamed to "input_schema".
# Derived mechanically from TOOLS so the two manifests can't drift apart.
ANTHROPIC_TOOLS = [
    {"name": t["function"]["name"], "description": t["function"]["description"], "input_schema": t["function"]["parameters"]}
    for t in TOOLS
]
