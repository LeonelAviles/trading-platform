"""Similarity between the trader's teaching trades and the compiled
strategy's engine trades over the same window (PLATFORM-SPEC.md Phase 6.5).

Entries match when direction agrees, the entry times are within ±`bars`
primary bars and the entry prices within ±`ticks` ticks. Greedy one-to-one
matching in time order. Precision = matched / engine entries, recall =
matched / user entries; exit similarity is the median absolute exit
difference in ticks and in R over matched pairs; PnL compares the two sides
over the window; unmatched entries are listed on both sides for labelling.
"""

from __future__ import annotations

from statistics import median


def _entry_time(t: dict) -> int:
    v = t.get("entryTime")
    if v is None and t.get("entryTs") is not None:
        v = t["entryTs"] // 1_000_000_000
    return int(v or 0)


def _exit_time(t: dict) -> int | None:
    v = t.get("exitTime")
    if v is None and t.get("exitTs") is not None:
        v = t["exitTs"] // 1_000_000_000
    return int(v) if v is not None else None


def _r(t: dict) -> float | None:
    if t.get("r") is not None:
        return float(t["r"])
    if t.get("rMultiple") is not None:
        return float(t["rMultiple"])
    entry, exit_, stop = t.get("entryPrice"), t.get("exitPrice"), t.get("stopPrice", t.get("stop"))
    if entry is None or exit_ is None or stop in (None, 0) or stop == entry:
        return None
    sign = 1 if t.get("direction") == "long" else -1
    return sign * (exit_ - entry) / abs(entry - stop)


def _pnl(t: dict) -> float:
    for k in ("pnlUsd", "pnl", "netPnl"):
        if t.get(k) is not None:
            return float(t[k])
    return 0.0


def report(user_trades: list[dict], engine_trades: list[dict], *, primary_seconds: int = 60, tick_size: float = 0.25,
           bars: int = 3, ticks: int = 8) -> dict:
    users = sorted(user_trades, key=_entry_time)
    engines = sorted(engine_trades, key=_entry_time)
    used: set[int] = set()
    pairs: list[tuple[dict, dict]] = []
    for u in users:
        ut = _entry_time(u)
        best = None
        for j, e in enumerate(engines):
            if j in used or e.get("direction") != u.get("direction"):
                continue
            dt = abs(_entry_time(e) - ut)
            dp = abs(float(e.get("entryPrice", 0)) - float(u.get("entryPrice", 0))) / tick_size
            if dt <= bars * primary_seconds and dp <= ticks + 1e-9:
                if best is None or dt < best[0]:
                    best = (dt, j)
        if best is not None:
            used.add(best[1])
            pairs.append((u, engines[best[1]]))
    matched = len(pairs)
    precision = matched / len(engines) if engines else None
    recall = matched / len(users) if users else None
    exit_ticks = []
    exit_r = []
    for u, e in pairs:
        if u.get("exitPrice") is not None and e.get("exitPrice") is not None:
            exit_ticks.append(abs(float(u["exitPrice"]) - float(e["exitPrice"])) / tick_size)
        ru, re = _r(u), _r(e)
        if ru is not None and re is not None:
            exit_r.append(abs(ru - re))
    unmatched_user = [u for u in users if not any(u is p[0] for p in pairs)]
    unmatched_engine = [e for j, e in enumerate(engines) if j not in used]
    return {
        "userEntries": len(users), "engineEntries": len(engines), "matched": matched,
        "precision": round(precision, 4) if precision is not None else None,
        "recall": round(recall, 4) if recall is not None else None,
        "exitSimilarity": {"medianExitTickDiff": round(median(exit_ticks), 2) if exit_ticks else None,
                           "medianRDiff": round(median(exit_r), 3) if exit_r else None, "pairs": len(pairs)},
        "pnl": {"user": round(sum(_pnl(u) for u in users), 2), "engine": round(sum(_pnl(e) for e in engines), 2)},
        "matches": [{"userId": u.get("id"), "engineEntryTime": _entry_time(e), "userEntryTime": _entry_time(u),
                     "direction": u.get("direction"), "userEntry": u.get("entryPrice"), "engineEntry": e.get("entryPrice"),
                     "userExit": u.get("exitPrice"), "engineExit": e.get("exitPrice"), "userExitTime": _exit_time(u),
                     "engineExitTime": _exit_time(e)} for u, e in pairs],
        "unmatchedUser": [{"id": u.get("id"), "entryTime": _entry_time(u), "direction": u.get("direction"), "entryPrice": u.get("entryPrice")}
                          for u in unmatched_user],
        "unmatchedEngine": [{"entryTime": _entry_time(e), "direction": e.get("direction"), "entryPrice": e.get("entryPrice"),
                             "exitPrice": e.get("exitPrice"), "pnlUsd": _pnl(e), "exitReason": e.get("exitReason")}
                            for e in unmatched_engine],
        "params": {"bars": bars, "ticks": ticks, "primarySeconds": primary_seconds},
    }
