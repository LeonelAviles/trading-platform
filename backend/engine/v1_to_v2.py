"""Convert a legacy v1 strategy document to Spec v2 (PLATFORM-SPEC.md §5 Phase 3 task 2).

v1: flat ANDed `conditions` from a closed vocabulary, `stop` (%/points/ATR),
`target` (rr/%/points), `interval`, `session` in UTC, `sizing`
(percent_equity | fixed_qty), `riskPerTradePercent`.
"""

from __future__ import annotations

from datetime import date

from engine.session import parse_hhmm, utc_hhmm_to_et

_CONVERT_DATE = date(2026, 6, 15)   # v1 strategies were built on Apr–Jul (EDT) data


def _cond(c: dict) -> dict:
    t = c["type"]
    if t == "price_above":
        return {"op": "gt", "args": [{"field": "close"}, float(c["value"])]}
    if t == "price_below":
        return {"op": "lt", "args": [{"field": "close"}, float(c["value"])]}
    if t in ("sma_cross_above", "sma_cross_below"):
        op = "cross_above" if t.endswith("above") else "cross_below"
        return {"op": op, "args": [{"ind": "sma", "params": {"period": int(c["fast"])}}, {"ind": "sma", "params": {"period": int(c["slow"])}}]}
    if t in ("rsi_above", "rsi_below"):
        return {"op": "gt" if t.endswith("above") else "lt", "args": [{"ind": "rsi", "params": {"period": int(c["period"])}}, float(c["value"])]}
    if t == "breaks_high":
        return {"op": "gt", "args": [{"field": "close"}, {"ind": "highest", "params": {"n": int(c["lookback"])}}]}
    if t == "breaks_low":
        return {"op": "lt", "args": [{"field": "close"}, {"ind": "lowest", "params": {"n": int(c["lookback"])}}]}
    if t == "consecutive":
        return {"op": "eq", "args": [{"ind": "consecutive", "params": {"count": int(c["count"]), "color": c["color"]}}, 1]}
    if t in ("delta_above", "delta_below"):
        return {"op": "gt" if t.endswith("above") else "lt", "args": [{"ind": "rel_delta", "params": {"n": int(c["lookback"])}}, float(c["value"])]}
    if t in ("cvd_rising", "cvd_falling"):
        return {"op": "gt" if t.endswith("rising") else "lt", "args": [{"ind": "cvd_window", "params": {"n": int(c["lookback"])}}, 0]}
    if t == "rel_volume_above":
        return {"op": "gt", "args": [{"ind": "rel_volume", "params": {"n": int(c["lookback"])}}, float(c["value"])]}
    raise ValueError(f"unknown v1 condition type {t!r}")


def _session(v1: dict) -> dict:
    s = v1.get("session") or {"start": "13:30", "end": "19:55"}
    start = utc_hhmm_to_et(s["start"], _CONVERT_DATE)
    end = utc_hhmm_to_et(s["end"], _CONVERT_DATE)
    start = max(start, "09:30", key=parse_hhmm)
    end = min(end, "15:58", key=parse_hhmm)
    if parse_hhmm(end) <= parse_hhmm(start):
        start, end = "09:30", "15:30"
    return {"entryWindow": {"start": start, "end": end}, "noTradeWindows": [], "flattenAt": "15:58"}


def _stop(v1: dict) -> dict:
    st = v1.get("stop") or {"type": "percent", "value": 0.3}
    t = st.get("type")
    if t == "percent":
        return {"type": "percent", "value": float(st["value"])}
    if t == "fixed_points":
        return {"type": "points", "value": float(st["value"])}
    if t == "atr":
        return {"type": "atr", "value": float(st.get("mult", st.get("value", 1.5))), "period": int(st.get("period", 14))}
    return {"type": "ticks", "value": 20}


def _target(v1: dict) -> dict:
    tg = v1.get("target") or {"type": "rr", "value": 2.0}
    t = tg.get("type")
    if t == "rr":
        return {"type": "rr", "value": float(tg["value"])}
    if t == "fixed_points":
        return {"type": "points", "value": float(tg["value"])}
    if t == "percent":
        # v2 has no percent target: express it in points relative to a 5000-ish price is wrong,
        # so keep it as an R multiple of the stop when both are percents, else points.
        st = v1.get("stop") or {}
        if st.get("type") == "percent" and float(st.get("value", 0)) > 0:
            return {"type": "rr", "value": round(float(tg["value"]) / float(st["value"]), 4)}
        return {"type": "points", "value": float(tg["value"])}
    return {"type": "rr", "value": 2.0}


def convert_v1_to_v2(v1: dict) -> dict:
    conds = [_cond(c) for c in v1.get("conditions", [])]
    trigger = conds[0] if len(conds) == 1 else {"op": "and", "args": conds} if conds else True
    sizing_v1 = v1.get("sizing") or {"type": "percent_equity", "value": 95}
    risk_pct = float(v1.get("riskPerTradePercent") or 0.5)
    if sizing_v1.get("type") == "fixed_qty":
        sizing = {"type": "fixed_contracts", "value": int(sizing_v1.get("value", 1)), "maxContracts": max(1, int(sizing_v1.get("value", 1)))}
    else:
        sizing = {"type": "fixed_risk", "value": risk_pct, "maxContracts": 5}
    from config.instruments import load_instruments

    root = load_instruments().root_for_symbol(v1.get("symbol", "ES1!"))
    spec = {
        "schemaVersion": 2,
        "id": v1.get("id"),
        "name": v1.get("name", "untitled"),
        "description": v1.get("description"),
        "origin": {"type": "manual", "sourceId": None},
        "lineage": {"parentId": v1.get("basedOn"), "changedVariable": None, "rationale": v1.get("rationale"), "trialIndex": 0},
        "status": "draft",
        "instrument": {"root": root.root if root else "ES", "symbol": v1.get("symbol", "ES1!")},
        "timeframes": {"primary": v1.get("interval") or "1min", "context": []},
        "direction": v1.get("direction", "long"),
        "session": _session(v1),
        "entry": {"trigger": trigger, "sequence": [], "orderType": "market", "limitOffsetTicks": 0, "stopOffsetTicks": 1, "timeoutBars": 1},
        "filters": [],
        "exit": {"stop": _stop(v1), "target": _target(v1), "trailing": None, "breakeven": None, "timeStop": None, "scaleOut": []},
        "sizing": sizing,
        "constraints": {"maxTradesPerDay": 5, "cooldownBars": 0, "stopAfterConsecutiveLosses": 3, "maxConcurrentPositions": 1},
        "execution": {"mode": "bars", "slippageTicksOverride": None},
        "risk": {"proposedBy": "default", "rationale": "converted from v1; platform defaults", "riskPerTradePct": risk_pct},
        "meta": {"convertedFrom": "v1", "directionGroup": v1.get("directionGroup")},
    }
    return spec
