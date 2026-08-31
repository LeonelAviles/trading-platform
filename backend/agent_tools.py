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
from condition_engine import Indicators, condition_lookback, eval_condition, session_minutes

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

def get_condition_vocabulary() -> dict:
    """The exact entry-condition/stop/target vocabulary the backtest engine
    understands, read live from strategy_spec.py — call this before
    building a strategy so the rule set you generate is guaranteed valid."""
    return {
        "conditions": {
            name: {k: v.__name__ for k, v in defn["params"].items()}
            for name, defn in strategy_spec.CONDITION_DEFS.items()
        },
        "stopTypes": sorted(strategy_spec.STOP_TYPES),
        "targetTypes": sorted(strategy_spec.TARGET_TYPES),
        "intervals": list(strategy_spec.INTERVALS),
        "notes": (
            "conditions are ANDed together (all must be true on the same bar). "
            "stop.type == 'atr' uses stop.period/stop.mult (default 14/1.5) to compute the stop, "
            "NOT stop.value — but validation still requires stop.value to be present and numeric "
            "regardless of type, so always include one anyway (e.g. same as mult, it's just unused). "
            "sizing defaults to {type: percent_equity, value: 95}. "
            "session times are 'HH:MM' 24h UTC, default 13:30-19:55. "
            "interval is the bar size every condition is evaluated on — one of `intervals` "
            "above, default '1min'. A strategy runs on ONE interval; there are no "
            "multi-timeframe conditions. "
            "ORDER FLOW: delta_above/delta_below/cvd_rising/cvd_falling/rel_volume_above "
            "come from Databento MBO ticks (aggressive buys minus aggressive sells per bar), "
            "not from price. delta_above/below take `lookback` bars and a UNITLESS `value`: "
            "cumulative delta over the window divided by the average absolute per-bar delta, "
            "so 1.0 means one average bar's worth of one-sided flow (try 0.5-2.0, not raw "
            "contract counts). cvd_rising/falling just test the sign of cumulative delta over "
            "`lookback` bars. rel_volume_above compares this bar's size to the `lookback`-bar "
            "average (1.5 = 50% busier than normal). These never fire for symbols with no "
            "tick-level side data, so check get_trade_features' flow fields are non-null first."
        ),
    }


def _strategy_file(strategy_id: str) -> Path:
    return STRATEGIES_DIR / f"{strategy_id}.json"


def _save_one(strategy: dict) -> dict:
    errors = strategy_spec.validate_strategy(strategy)
    if errors:
        raise ToolError("; ".join(errors))
    if not strategy.get("id"):
        strategy["id"] = uuid.uuid4().hex[:12]
    STRATEGIES_DIR.mkdir(exist_ok=True)
    _strategy_file(strategy["id"]).write_text(json.dumps(strategy, indent=2), encoding="utf-8")
    return strategy


def create_strategy(
    name: str, symbol: str, direction: str, conditions: list[dict],
    stop: dict, target: dict, sizing: dict | None = None, session: dict | None = None,
    interval: str = "1min",
) -> dict:
    """Validate and save a new strategy. direction is 'long', 'short', or
    'both' (saves two sibling strategies sharing a directionGroup id, since
    the engine is single-directional per run)."""
    base = {
        "name": name, "symbol": symbol, "conditions": conditions,
        "stop": stop, "target": target, "interval": interval,
    }
    if sizing is not None:
        base["sizing"] = sizing
    if session is not None:
        base["session"] = session

    if direction == "both":
        group = uuid.uuid4().hex[:12]
        long_s = _save_one({**base, "name": f"{name} (long)", "direction": "long", "directionGroup": group})
        short_s = _save_one({**base, "name": f"{name} (short)", "direction": "short", "directionGroup": group})
        return {"directionGroup": group, "long": long_s, "short": short_s}

    if direction not in ("long", "short"):
        raise ToolError("direction must be 'long', 'short', or 'both'")
    return _save_one({**base, "direction": direction})


def get_strategy(strategy_id: str) -> dict:
    f = _strategy_file(strategy_id)
    if not f.exists():
        raise ToolError(f"strategy '{strategy_id}' not found")
    return json.loads(f.read_text(encoding="utf-8"))


def list_strategies() -> list[dict]:
    if not STRATEGIES_DIR.exists():
        return []
    return sorted(
        (json.loads(f.read_text(encoding="utf-8")) for f in STRATEGIES_DIR.glob("*.json")),
        key=lambda s: s.get("name", ""),
    )


def propose_strategy_revision(base_strategy_id: str, changes: dict, rationale: str, name: str | None = None) -> dict:
    """Clone an existing strategy with changes applied (e.g. new conditions/
    stop/target from a winners-vs-losers finding) and save it as a new
    strategy, so it can be backtested and compared against the original
    without overwriting it. `changes` is shallow-merged onto the base spec."""
    base = get_strategy(base_strategy_id)
    revised = {**base, **changes}
    revised["id"] = None
    revised["name"] = name or f"{base['name']} (revised)"
    revised["basedOn"] = base_strategy_id
    revised["rationale"] = rationale
    return _save_one(revised)


def update_strategy(strategy_id: str, changes: dict, rationale: str | None = None) -> dict:
    """Edit a saved strategy IN PLACE — same id, same file, no second copy in
    the trader's list. This is what to use when the trader asks you to change
    the strategy they already have ("make the target 3R", "move the session to
    the US open", "tighten the stop"): they want their strategy fixed, and the
    next run_backtest on this id picks the change up. `changes` is
    shallow-merged onto the current spec, so pass only the fields you are
    changing.

    Destructive — the previous values are gone and cannot be compared against.
    For your OWN A/B experiments, where the point is to keep the original and
    rank the two, use propose_strategy_revision instead."""
    current = get_strategy(strategy_id)
    updated = {**current, **changes}
    # `changes` must not be able to move the strategy to a different id (that
    # would write a copy under a new file and leave the original stale).
    updated["id"] = strategy_id
    if rationale:
        updated["rationale"] = rationale
    return _save_one(updated)


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


def _replay_features(symbol: str, interval: str, conditions: list[dict], stop_cfg: dict, session: dict):
    """Walk every bar once with the same Indicators/eval_condition machinery
    nautilus_backtest.py's real engine uses, recording per-bar market
    context. Returns (bar_times: list[int], features: list[dict]) aligned
    by index — shared by get_trade_features() and find_near_miss_entries()
    so both see identical numbers for identical bars."""
    bars = data_store.bars_to_records(data_store.get_bars(symbol, interval))
    if not bars:
        raise ToolError(f"no bars for {symbol} at {interval}")

    lookback = max([condition_lookback(c) for c in conditions] + [22])
    if stop_cfg.get("type") == "atr":
        lookback = max(lookback, int(stop_cfg.get("period", 14)) + 2)
    ind = Indicators(lookback)

    sess_start = session_minutes(session["start"])
    sess_end = session_minutes(session["end"])

    has_flow = bool(bars[0].get("hasDelta"))
    volumes: list[float] = []
    times: list[int] = []
    features: list[dict] = []
    session_delta = 0.0
    prev_in_session = False
    for bar in bars:
        ind.update(
            bar["open"], bar["high"], bar["low"], bar["close"],
            bar["volume"], bar["delta"] if has_flow else None,
        )
        volumes.append(bar["volume"])
        vol_window = volumes[-20:]
        rel_volume = bar["volume"] / (sum(vol_window) / len(vol_window)) if vol_window and sum(vol_window) else None

        dt = datetime.fromtimestamp(bar["time"], tz=timezone.utc)
        minutes = dt.hour * 60 + dt.minute
        highs20, lows20 = list(ind.highs)[-20:], list(ind.lows)[-20:]

        conds_true = sum(1 for c in conditions if eval_condition(c, ind)) if ind.count >= 2 else 0
        in_session = sess_start <= minutes < sess_end
        # Session-cumulative delta: resets at each session open, which is what
        # a trader means by "CVD today" — a rolling window can't express it.
        if in_session:
            session_delta = session_delta + bar["delta"] if prev_in_session else bar["delta"]
        prev_in_session = in_session

        # Order flow at this bar, all MBO-derived. Price-only fields could
        # never separate "broke the high on real buying" from "broke the high
        # on no participation" — these are the fields that can.
        flow_feats = {
            "deltaBar": None, "cvd20": None, "relDelta20": None,
            "cvdSession": None, "flowDivergence": None,
        }
        if has_flow:
            cvd20 = ind.delta_sum(20)
            price_up = len(ind.closes) > 20 and bar["close"] > list(ind.closes)[-21]
            flow_feats = {
                "deltaBar": round(bar["delta"], 2),
                "cvd20": round(cvd20, 2) if cvd20 is not None else None,
                "relDelta20": round(ind.rel_delta(20), 3) if ind.rel_delta(20) is not None else None,
                "cvdSession": round(session_delta, 2) if in_session else None,
                # Price and flow disagreeing over the last 20 bars — the
                # classic "move without participation behind it".
                "flowDivergence": (
                    None if cvd20 is None
                    else "bearish" if price_up and cvd20 < 0
                    else "bullish" if not price_up and cvd20 > 0
                    else "none"
                ),
            }

        times.append(bar["time"])
        features.append({
            "conditionsTrue": conds_true,
            "conditionsTotal": len(conditions),
            "inSession": in_session,
            **flow_feats,
            "relVolume20": round(rel_volume, 3) if rel_volume is not None else None,
            "atr14": round(ind.atr(14), 4) if ind.atr(14) is not None else None,
            "rsi14": round(ind.rsi(14), 1) if ind.rsi(14) is not None else None,
            "hourUtc": dt.hour,
            "dayOfWeek": dt.strftime("%A"),
            "distFrom20High": round(max(highs20) - bar["close"], 4) if highs20 else None,
            "distFrom20Low": round(bar["close"] - min(lows20), 4) if lows20 else None,
        })
    return times, features


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
    session = strategy.get("session", {"start": "13:30", "end": "19:55"})

    times, features = _replay_features(
        strategy["symbol"], strategy.get("interval", "1min"),
        strategy["conditions"], strategy["stop"], session,
    )

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


def compare_winners_vs_losers(job_id: str) -> dict:
    """Statistical comparison of winning vs losing trades' entry context —
    answers "what do winners have in common." Numeric features (relVolume20,
    atr14, rsi14, distFrom20High/Low, and the MBO order flow: deltaBar, cvd20,
    relDelta20, cvdSession) are ranked by effect size (Cohen's d:
    how many pooled standard deviations apart the two groups' means are —
    >0.5 is a moderately strong separation, >0.8 is strong). Categorical
    features (hourUtc, dayOfWeek, flowDivergence, exitReason) get a
    win-rate-by-bucket breakdown instead. A strong order-flow separation is
    directly actionable: delta_above/cvd_rising/rel_volume_above are real
    entry conditions, so a finding there can be tested as a revision."""
    rows = get_trade_features(job_id)
    if len(rows) < 4:
        raise ToolError(f"only {len(rows)} closed trades — too few for a meaningful comparison (want 10+)")

    wins = [r for r in rows if r["outcome"] == "win"]
    losses = [r for r in rows if r["outcome"] == "loss"]

    numeric_features = [
        "relVolume20", "atr14", "rsi14", "distFrom20High", "distFrom20Low",
        # Order flow — null for symbols without MBO side data, in which case
        # the len() guard below drops them from the ranking automatically.
        "deltaBar", "cvd20", "relDelta20", "cvdSession",
    ]
    numeric_comparison = []
    for feat in numeric_features:
        wv = [r["marketContextAtEntry"][feat] for r in wins if r["marketContextAtEntry"][feat] is not None]
        lv = [r["marketContextAtEntry"][feat] for r in losses if r["marketContextAtEntry"][feat] is not None]
        if len(wv) < 2 or len(lv) < 2:
            continue
        w_mean, l_mean = statistics.mean(wv), statistics.mean(lv)
        w_std, l_std = statistics.stdev(wv), statistics.stdev(lv)
        pooled_std = (((len(wv) - 1) * w_std ** 2 + (len(lv) - 1) * l_std ** 2) / (len(wv) + len(lv) - 2)) ** 0.5
        effect_size = (w_mean - l_mean) / pooled_std if pooled_std > 0 else 0.0
        numeric_comparison.append({
            "feature": feat,
            "winMean": round(w_mean, 3), "lossMean": round(l_mean, 3),
            "effectSize": round(effect_size, 3),
        })
    numeric_comparison.sort(key=lambda x: -abs(x["effectSize"]))

    categorical_comparison = {}
    for feat in ("hourUtc", "dayOfWeek", "flowDivergence", "exitReason"):
        key_fn = (lambda r: r["exitReason"]) if feat == "exitReason" else (lambda r, f=feat: r["marketContextAtEntry"][f])
        buckets: dict = {}
        for r in rows:
            k = key_fn(r)
            b = buckets.setdefault(k, {"trades": 0, "wins": 0})
            b["trades"] += 1
            b["wins"] += 1 if r["outcome"] == "win" else 0
        categorical_comparison[feat] = sorted(
            [{"value": k, "trades": v["trades"], "winRate": round(v["wins"] / v["trades"] * 100, 1)} for k, v in buckets.items()],
            key=lambda x: -x["trades"],
        )

    return {
        "tradeCount": len(rows), "winCount": len(wins), "lossCount": len(losses),
        "numericFeatures": numeric_comparison,
        "categoricalFeatures": categorical_comparison,
    }


def find_near_miss_entries(strategy_id: str, max_conditions_missing: int = 1, limit: int = 25) -> list[dict]:
    """Bars where entry almost triggered — all but (up to) max_conditions_missing
    of the entry conditions were true, during the trading session, but the
    strategy didn't actually enter (either a condition fell just short, or
    the strategy was already in a position — this doesn't distinguish the
    two). Useful for judging whether thresholds are too tight, not a bug
    detector for the backtest engine itself."""
    strategy = get_strategy(strategy_id)
    conditions = strategy["conditions"]
    if len(conditions) < 2:
        raise ToolError(
            "near-miss detection needs at least 2 conditions to be meaningful — with only "
            f"{len(conditions)}, 'missing 1' just means the condition was false, not a close call."
        )
    session = strategy.get("session", {"start": "13:30", "end": "19:55"})

    times, features = _replay_features(strategy["symbol"], strategy.get("interval", "1min"), conditions, strategy["stop"], session)

    near_misses = []
    for t, f in zip(times, features):
        if not f["inSession"]:
            continue
        missing = f["conditionsTotal"] - f["conditionsTrue"]
        if 0 < missing <= max_conditions_missing:
            near_misses.append({"time": t, "conditionsTrue": f["conditionsTrue"], "conditionsTotal": f["conditionsTotal"], **f})
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
        "name": "get_condition_vocabulary", "description": get_condition_vocabulary.__doc__,
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "create_strategy", "description": create_strategy.__doc__,
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "symbol": {"type": "string"},
                "direction": {"type": "string", "enum": ["long", "short", "both"]},
                "conditions": {"type": "array", "items": {"type": "object"}, "description": "entry conditions (ANDed); see get_condition_vocabulary"},
                "stop": {"type": "object", "description": "{type: percent|fixed_points|atr, value, period?, mult?}"},
                "target": {"type": "object", "description": "{type: rr|percent|fixed_points, value}"},
                "sizing": {"type": "object", "description": "optional {type: fixed_qty|percent_equity, value}"},
                "session": {"type": "object", "description": "optional {start: 'HH:MM', end: 'HH:MM'} UTC"},
                "interval": {"type": "string", "description": "bar interval — one of get_condition_vocabulary's `intervals`, default '1min'"},
            },
            "required": ["name", "symbol", "direction", "conditions", "stop", "target"],
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
