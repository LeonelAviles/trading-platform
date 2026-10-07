"""Observational analytics for Stratos Research — typed, descriptive, no trades.

`level_event_stats` answers "what happens after price breaks level X" straight
from the stored 1-minute RTH bars: how often the level breaks, whether the
session closes beyond it or back inside, how far price follows through and
pulls back, and how those outcomes change when the breakout bar's order flow
is conditioned (aggressor share, volume, bar range). The event is always the
session's FIRST breach — the same definition as the `first_above` /
`first_below` spec operators — so chat answers and strategy triggers agree.

Nothing here simulates entries or exits, invokes Nautilus, or reads OOS; the
tool layer clamps every request to the frozen in-sample window.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import numpy as np

import data_store
from config.instruments import load_instruments
from engine import session as sess

# level -> inferred breakout direction (None: caller must say above/below)
FIXED_LEVELS = {
    "prior_day_high": "above",
    "prior_day_low": "below",
    "prior_day_close": None,
    "opening_range_high": "above",
    "opening_range_low": "below",
}
LEVELS = tuple(FIXED_LEVELS) + ("vwap",)
# where excursion measurement stops (reversal boundary)
MEASURE_UNTIL = ("session_close", "level", "range_mid", "range_opposite")
TARGETS_POINTS = (5.0, 10.0, 20.0, 40.0)
HORIZONS_MIN = (15, 30, 60)


def _rth_sessions(symbol: str):
    """[(session_date, RTH 1-min DataFrame)], tick size, RTH open minute-of-day."""
    ins = load_instruments()
    spec = ins.root_for_symbol(symbol)
    if spec is None:
        raise ValueError(f"unknown symbol '{symbol}'")
    bars = data_store.get_bars(symbol, "1min")
    local = bars.copy()
    local["sessionDate"] = local.index.tz_convert(sess.ET).date
    local["localTime"] = local.index.tz_convert(sess.ET).time
    start_t = sess.parse_hhmm(ins.session.rth_start)
    end_t = sess.parse_hhmm(ins.session.rth_end)
    local = local[(local["localTime"] >= start_t) & (local["localTime"] < end_t)]
    groups = [(d, g) for d, g in local.groupby("sessionDate", sort=True)]
    return groups, float(spec.tick_size), start_t.hour * 60 + start_t.minute


def _pct(n: int, d: int):
    return round(100.0 * n / d, 1) if d else None


def _dist(vals):
    if not vals:
        return None
    a = np.asarray(vals, dtype=float)
    return {"mean": round(float(a.mean()), 2), "median": round(float(np.median(a)), 2),
            "p25": round(float(np.percentile(a, 25)), 2), "p75": round(float(np.percentile(a, 75)), 2),
            "max": round(float(a.max()), 2)}


def _aggregate(events: list[dict]) -> dict:
    n = len(events)
    out = {"events": n}
    if n == 0:
        return out
    out["closedBeyondLevelPct"] = _pct(sum(e["closedBeyond"] for e in events), n)
    out["closedBackInsidePct"] = _pct(sum(not e["closedBeyond"] for e in events), n)
    out["followThroughPoints"] = _dist([e["mfePoints"] for e in events])
    out["pullbackAgainstPoints"] = _dist([e["maePoints"] for e in events])
    out["reachedPointsPct"] = {f"{t:g}": _pct(sum(e["mfePoints"] >= t for e in events), n) for t in TARGETS_POINTS}
    out["pulledBackPointsPct"] = {f"{t:g}": _pct(sum(e["maePoints"] >= t for e in events), n) for t in TARGETS_POINTS}
    out["minutesAfterOpen"] = _dist([e["minuteAfterOpen"] for e in events])
    out["breachEpisodesPerSession"] = _dist([e["episodes"] for e in events])
    if "reversed" in events[0]:
        reversed_events = [e for e in events if e["reversed"]]
        out["reversedToBoundaryPct"] = _pct(len(reversed_events), n)
        out["followThroughBeforeReversalPoints"] = _dist([e["mfePoints"] for e in reversed_events])
        out["followThroughWhenNeverReversedPoints"] = _dist([e["mfePoints"] for e in events if not e["reversed"]])
        out["minutesToReversal"] = _dist([e["minutesToReversal"] for e in reversed_events])
    horizons = {}
    for m in HORIZONS_MIN:
        vals = [e["horizons"][str(m)] for e in events if e["horizons"].get(str(m)) is not None]
        if vals:
            horizons[f"{m}min"] = {"medianPoints": round(float(np.median(vals)), 2),
                                   "stillBeyondPct": _pct(sum(v > 0 for v in vals), len(vals)),
                                   "samples": len(vals)}
    out["afterEvent"] = horizons
    return out


def level_event_stats(symbol: str, level: str, date_from: date | None = None, date_to: date | None = None, *,
                      direction: str | None = None, or_minutes: int = 15, measure_until: str = "session_close",
                      min_aggressor_share: float | None = None, min_volume: float | None = None,
                      max_range_ticks: float | None = None) -> dict:
    if level not in LEVELS:
        raise ValueError(f"level must be one of {list(LEVELS)}")
    if measure_until not in MEASURE_UNTIL:
        raise ValueError(f"measure_until must be one of {list(MEASURE_UNTIL)}")
    if measure_until in ("range_mid", "range_opposite") and level in ("prior_day_close", "vwap"):
        raise ValueError(f"measure_until '{measure_until}' needs a range level (prior-day high/low or opening range)")
    direction = direction or FIXED_LEVELS.get(level)
    if direction not in ("above", "below"):
        raise ValueError(f"level '{level}' needs an explicit direction 'above' or 'below'")
    sessions, tick, open_minute = _rth_sessions(symbol)

    events: list[dict] = []
    considered = with_level = 0
    first_date = last_date = None
    prior: dict | None = None
    for d, g in sessions:
        in_range = not (date_from and d < date_from or date_to and d > date_to)
        highs = g["high"].to_numpy(dtype=float)
        lows = g["low"].to_numpy(dtype=float)
        closes = g["close"].to_numpy(dtype=float)
        this_session = {"high": float(highs.max()), "low": float(lows.min()), "close": float(closes[-1])}
        if not in_range or len(g) < 2 or (level.startswith("prior_day") and prior is None):
            prior = this_session
            continue
        considered += 1
        first_date = first_date or d
        last_date = d
        vols = g["volume"].to_numpy(dtype=float)
        buys = g["buy_vol"].to_numpy(dtype=float) if "buy_vol" in g.columns else np.zeros(len(g))
        sells = g["sell_vol"].to_numpy(dtype=float) if "sell_vol" in g.columns else np.zeros(len(g))
        minutes = np.array([t.hour * 60 + t.minute for t in g["localTime"]], dtype=int) - open_minute

        lvl = None
        lvl_series = None
        start_i = 0
        range_hi = range_lo = None
        if level.startswith("prior_day"):
            with_level += 1
            lvl = prior[level.removeprefix("prior_day_")]
            range_hi, range_lo = prior["high"], prior["low"]
        elif level.startswith("opening_range"):
            in_or = minutes < or_minutes
            start_i = int(in_or.sum())
            if start_i == 0 or start_i == len(g):
                prior = this_session
                continue
            with_level += 1
            range_hi, range_lo = float(highs[:start_i].max()), float(lows[:start_i].min())
            lvl = range_hi if level.endswith("high") else range_lo
        else:  # running session VWAP of the 1-minute bars (close-weighted)
            with_level += 1
            cum_v = np.maximum(vols.cumsum(), 1e-9)
            lvl_series = (closes * vols).cumsum() / cum_v
            start_i = 1

        idx = None
        episodes = 0
        if lvl_series is None:
            beyond = highs > lvl if direction == "above" else lows < lvl
            beyond[:start_i] = False
            inside = True
            for i in range(start_i, len(g)):
                if inside and beyond[i]:
                    episodes += 1
                    inside = False
                    if idx is None:
                        idx = i
                elif not inside and (closes[i] < lvl if direction == "above" else closes[i] > lvl):
                    inside = True
        else:
            above = closes > lvl_series
            state = above[0]
            for i in range(start_i, len(g)):
                if above[i] != state:
                    state = above[i]
                    if state == (direction == "above"):
                        episodes += 1
                        if idx is None:
                            idx = i
        if idx is None:
            prior = this_session
            continue

        # Excursions are measured from the level itself (a running VWAP has no
        # fixed level, so from the crossing bar's close instead).
        ref = float(lvl) if lvl_series is None else float(closes[idx])
        sign = 1.0 if direction == "above" else -1.0
        # Reversal boundary: the first bar AFTER the event whose range touches
        # it ends the measurement window. The event bar itself never counts —
        # its opposite-side extreme is normally the pre-break approach.
        rev_j = None
        if measure_until != "session_close":
            if measure_until == "level":
                bound = ref
            elif measure_until == "range_mid":
                bound = (range_hi + range_lo) / 2.0
            else:  # range_opposite
                bound = range_lo if direction == "above" else range_hi
            for j in range(idx + 1, len(g)):
                if lvl_series is not None:  # vwap: a close back across the running level
                    hit = closes[j] <= lvl_series[j] if direction == "above" else closes[j] >= lvl_series[j]
                else:
                    hit = lows[j] <= bound if direction == "above" else highs[j] >= bound
                if hit:
                    rev_j = j
                    break
        stop = rev_j if rev_j is not None else len(g)

        # Favorable includes the event bar (its beyond-level extreme can only
        # occur at/after the breach) and excludes the boundary-touching bar
        # (its favorable extreme may postdate the touch); adverse starts on
        # the bar after the event, inside the same window.
        mfe = max(0.0, float(highs[idx:stop].max()) - ref) if direction == "above" else max(0.0, ref - float(lows[idx:stop].min()))
        if idx + 1 < stop:
            post = slice(idx + 1, stop)
            mae = max(0.0, ref - float(lows[post].min())) if direction == "above" else max(0.0, float(highs[post].max()) - ref)
        else:
            mae = 0.0
        horizons = {}
        for m in HORIZONS_MIN:
            j = int(np.searchsorted(minutes, minutes[idx] + m, side="right")) - 1
            if j > idx:
                horizons[str(m)] = round(sign * (float(closes[j]) - ref), 2)
        vol = float(vols[idx])
        share = (float(buys[idx]) if direction == "above" else float(sells[idx])) / vol if vol > 0 else None
        events.append({
            "date": d.isoformat(),
            "minuteAfterOpen": int(minutes[idx]),
            "aggressorShare": round(share, 3) if share is not None else None,
            "volume": vol,
            "rangeTicks": round((float(highs[idx]) - float(lows[idx])) / tick, 1),
            "mfePoints": round(mfe, 2),
            "maePoints": round(mae, 2),
            "closedBeyond": bool(closes[-1] > ref) if direction == "above" else bool(closes[-1] < ref),
            "horizons": horizons,
            "episodes": episodes,
        })
        if measure_until != "session_close":
            events[-1]["reversed"] = rev_j is not None
            events[-1]["minutesToReversal"] = int(minutes[rev_j] - minutes[idx]) if rev_j is not None else None
        prior = this_session

    result = {
        "symbol": symbol, "level": level, "direction": direction,
        "measureUntil": measure_until,
        "orMinutes": or_minutes if level.startswith("opening_range") else None,
        "dateFrom": first_date.isoformat() if first_date else None,
        "dateTo": last_date.isoformat() if last_date else None,
        "sessions": considered,
        "sessionsWithLevel": with_level,
        "breakSessions": len(events),
        "breakRatePct": _pct(len(events), with_level),
        "definitions": {
            "event": "the session's FIRST RTH breach of the level (same as the first_above/first_below spec operators); later re-breaks are counted in breachEpisodesPerSession but never re-measured",
            "followThroughPoints": "maximum favorable excursion beyond the level in the breakout direction, event bar through the session close",
            "pullbackAgainstPoints": "maximum excursion back through the level against the breakout, from the bar after the event through the session close (the event bar's opposite extreme is its pre-break approach)",
            "closedBeyondLevelPct": "sessions whose RTH close finished beyond the level in the breakout direction",
            "afterEvent": "signed distance of the close N minutes after the event from the level (positive = still beyond)",
            "aggressorShare": "breakout-direction aggressive volume share of the event bar (buy share for above, sell share for below)",
            "vwapCaveat": "vwap events are close-through crossings of the running session VWAP, measured from the crossing bar's close",
            "measureUntil": ("excursions run from the event through the session close, or — when measure_until is "
                             "'level', 'range_mid', or 'range_opposite' — only until the first later bar that touches "
                             "that reversal boundary (back to the broken level, the source range's midpoint, or its "
                             "other side; the source range is the opening range or the prior day's range). "
                             "followThroughBeforeReversalPoints covers events that hit the boundary; "
                             "followThroughWhenNeverReversedPoints covers those that never did"),
        },
        "all": _aggregate(events),
    }
    if min_aggressor_share is not None or min_volume is not None or max_range_ticks is not None:
        subset = [e for e in events
                  if (min_aggressor_share is None or (e["aggressorShare"] is not None and e["aggressorShare"] >= min_aggressor_share))
                  and (min_volume is None or e["volume"] >= min_volume)
                  and (max_range_ticks is None or e["rangeTicks"] <= max_range_ticks)]
        result["filter"] = {"minAggressorShare": min_aggressor_share, "minVolume": min_volume,
                           "maxRangeTicks": max_range_ticks}
        result["filtered"] = _aggregate(subset)
    result["recentEvents"] = events[-20:]
    return result


def data_coverage() -> dict:
    """What the agent may analyze: bar ranges per symbol, evidence windows, book-liquidity days."""
    from engine import validation

    ins = load_instruments()
    symbols = []
    for symbol in data_store.list_symbols():
        start, end = data_store.data_range(symbol)
        spec = ins.root_for_symbol(symbol)
        windows = {}
        for kind, (a, b) in (validation.windows(spec.root) if spec else {}).items():
            windows[kind] = {"dateFrom": a, "dateTo": b}
            if kind == "oos":
                windows[kind]["resultsHidden"] = True
        symbols.append({
            "symbol": symbol, "root": spec.root if spec else None,
            "barsFrom": datetime.fromtimestamp(start, tz=timezone.utc).date().isoformat(),
            "barsTo": datetime.fromtimestamp(end, tz=timezone.utc).date().isoformat(),
            "orderFlowInBars": True,  # every 1-minute bar carries delta/buy_vol/sell_vol from MBO
            "windows": windows,
        })
    return {
        "symbols": symbols,
        "bookLiquidityDays": _book_days(),
        "notes": "Observational analysis is clamped to the in-sample window; later sessions are frozen for out-of-sample validation and are not queryable here.",
    }


def _book_days() -> dict:
    """Days with a materialised resting-liquidity (MBO book) file, per outright symbol."""
    try:
        import liquidity_store

        con = liquidity_store.get_connection()
        if con is None:
            return {}
        rows = con.execute(
            "SELECT symbol, count(DISTINCT session_date), min(session_date), max(session_date) "
            "FROM liquidity_files GROUP BY symbol ORDER BY symbol").fetchall()
        return {str(r[0]): {"days": int(r[1]), "from": str(r[2]), "to": str(r[3])} for r in rows}
    except Exception:
        return {}
