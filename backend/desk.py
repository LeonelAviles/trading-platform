"""Desk summary (PLATFORM-SPEC.md §5 Phase 7): one payload for the `/` page —
candidates with their latest verdict, what is testing right now and data
coverage. Everything here is a read over the metadata DB
and the on-disk tiers; nothing is computed that a tile could not show in one
glance."""

from __future__ import annotations

import strategy_store
from engine import jobs, validation

DESK_STATUSES = ("candidate", "forward_test", "live")


def regime_notes(is_metrics: dict | None) -> list[str]:
    """Short, readable regime notes from the in-sample `byRegime` breakdown:
    the best and the worst regime by expectancy, when they differ."""
    by = ((is_metrics or {}).get("analytics") or {}).get("byRegime") or (is_metrics or {}).get("byRegime") or {}
    rows = [(k, v) for k, v in by.items() if isinstance(v, dict) and v.get("expectancyR") is not None]
    if not rows:
        return []
    rows.sort(key=lambda kv: kv[1]["expectancyR"], reverse=True)
    best, worst = rows[0], rows[-1]
    notes = [f"best in {best[0]} ({best[1]['expectancyR']:+.2f} R, PF {best[1].get('profitFactor') or 0:.2f})"]
    if worst[0] != best[0]:
        notes.append(f"worst in {worst[0]} ({worst[1]['expectancyR']:+.2f} R)")
    return notes


def candidate_card(spec: dict) -> dict:
    """The candidate tile: verdict, OOS profit factor, Monte Carlo DD95 and
    regime notes from the latest validation rows of the strategy."""
    rep = validation.report(spec["id"], risk=spec.get("risk"), trial_index=max(1, int((spec.get("lineage") or {}).get("trialIndex") or 1)))
    oos = rep.get("outOfSample") or {}
    mc = ((rep.get("monteCarlo") or {}).get("bootstrap") or {})
    is_m = rep.get("inSample") or {}
    return {
        "id": spec["id"], "name": spec["name"], "status": spec["status"], "symbol": (spec.get("instrument") or {}).get("symbol"),
        "direction": spec.get("direction"), "origin": spec.get("origin"), "parentId": (spec.get("lineage") or {}).get("parentId"),
        "verdict": rep.get("verdict"),
        "inSample": {k: is_m.get(k) for k in ("trades", "profitFactor", "expectancyR", "maxDrawdownPct")} if is_m else None,
        "oosProfitFactor": oos.get("profitFactor"), "oosTrades": oos.get("trades"), "oosAvailable": rep.get("oosAvailable"),
        "monteCarloDd95Pct": (mc.get("maxDrawdownPct") or {}).get("p95"),
        "walkForwardPositive": sum(1 for w in rep.get("walkForward") or [] if (w.get("netPnl") or 0) > 0),
        "walkForwardWindows": len(rep.get("walkForward") or []),
        "regimeNotes": regime_notes(is_m), "updatedAt": spec.get("updatedAt"),
    }


def _safe(fn, default):
    try:
        return fn()
    except Exception as e:  # a missing tier must not blank the whole desk
        return default if not isinstance(default, dict) else {**default, "error": str(e)}


def summary() -> dict:
    strategies = strategy_store.list_strategies()
    candidates = [candidate_card(s) for s in strategies if s.get("status") in DESK_STATUSES]
    candidates.sort(key=lambda c: (c["status"] != "live", c["status"] != "forward_test", -(c.get("inSample") or {}).get("expectancyR", -9) or 0))

    backtests = jobs.list_jobs()
    running_jobs = [b for b in backtests if b["status"] in ("queued", "running")]
    by_status: dict[str, int] = {}
    for s in strategies:
        by_status[s.get("status") or "draft"] = by_status.get(s.get("status") or "draft", 0) + 1

    roots = []
    for s in strategies:
        if (s.get("lineage") or {}).get("parentId"):
            continue
        lin = _safe(lambda s=s: strategy_store.lineage(s["id"]), None)
        if not lin:
            continue
        size = _count(lin["tree"])
        if size > 1 or s.get("status") in DESK_STATUSES:
            roots.append({"rootId": lin["rootId"], "name": s["name"], "nodes": size, "champion": lin.get("champion"), "tree": lin["tree"]})

    return {
        "candidates": candidates,
        "strategies": {"total": len(strategies), "byStatus": by_status},
        "testing": {"backtests": running_jobs, "recentBacktests": backtests[:8]},
        "coverage": _safe(_coverage, {"roots": {}, "replayCache": [], "sizes": {}}),
        "lineage": roots,
    }


def _coverage() -> dict:
    import data_store

    cov = data_store.coverage()
    for r in cov.get("roots", {}).values():
        r.pop("dates", None)   # the desk wants counts and ranges, not 70 dates
    return cov


def _count(node: dict) -> int:
    return 1 + sum(_count(c) for c in node.get("children") or [])
