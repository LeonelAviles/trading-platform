"""Compile a teaching session into a strategy (PLATFORM-SPEC.md Phase 6.5).

The reasoning model runs as a `teaching_compile` agent run
(`agent/flows.TeachingCompileFlow`); this module holds the deterministic
parts: the prompt payload, the evaluation of a candidate over the replayed
window (Nautilus via `validation.run_teaching_window`, then the similarity
report), refinements as lineage children, and the full in-sample run.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone

import strategy_store
from chart_time import format_et
from engine import jobs
from engine import validation
from teaching import similarity, store

NS = 1_000_000_000
MAX_REFINEMENTS = 3


def _date_of(ts_ns: int | None, fallback: str | None) -> date | None:
    if ts_ns:
        return datetime.fromtimestamp(ts_ns / NS, tz=timezone.utc).date()
    return date.fromisoformat(fallback[:10]) if fallback else None


def window(detail: dict) -> tuple[date, date]:
    trades = detail.get("trades") or []
    if trades:
        d0 = _date_of(trades[0]["entryTs"], detail.get("dateFrom"))
        d1 = _date_of(trades[-1].get("exitTs") or trades[-1]["entryTs"], detail.get("dateTo") or detail.get("dateFrom"))
        return d0, d1
    d = date.fromisoformat((detail.get("dateFrom") or str(date.today()))[:10])
    return d, d


def typical_ticks(trades: list[dict], tick: float) -> tuple[int, int]:
    stops = [abs(t["entryPrice"] - t["stopPrice"]) / tick for t in trades if t.get("stopPrice")]
    targets = [abs(t["targetPrice"] - t["entryPrice"]) / tick for t in trades if t.get("targetPrice")]
    med = lambda xs, d: int(round(sorted(xs)[len(xs) // 2])) if xs else d  # noqa: E731
    return med(stops, 20), med(targets, 40)


def typical_bars(trades: list[dict], primary_seconds: int = 60) -> tuple[int | None, int | None]:
    """(median hold in bars, median spacing between entries in bars)."""
    holds = [max(1, round(((t["exitTs"] - t["entryTs"]) / 1e9) / primary_seconds)) for t in trades if t.get("exitTs") and t.get("entryTs")]
    entries = sorted(t["entryTime"] for t in trades if t.get("entryTime") is not None)
    gaps = [max(1, round((b - a) / primary_seconds)) for a, b in zip(entries, entries[1:])]
    med = lambda xs: int(sorted(xs)[len(xs) // 2]) if xs else None  # noqa: E731
    return med(holds), med(gaps)


def prompt_payload(detail: dict, tick: float) -> dict:
    trades = detail.get("trades") or []
    tags = {e["payload"].get("tradeId"): e["payload"].get("tags") for e in detail.get("events") or [] if e["type"] == "setup_tags"}
    hyps = [e for e in detail.get("events") or [] if e["type"] == "hypothesis_update"]
    labels = [e["payload"] for e in detail.get("events") or [] if e["type"] == "skipped_setup_label"]
    marks = [e["payload"] for e in detail.get("events") or [] if e["type"] == "skipped_setup" and e["payload"].get("source") == "user"]
    stop_t, target_t = typical_ticks(trades, tick)
    hold_bars, spacing_bars = typical_bars(trades)
    flattens = sum(1 for t in trades if t.get("exitReason") == "flatten")
    d0, d1 = window(detail)
    return {
        "sessionId": detail["id"], "symbol": detail["symbol"], "root": detail["root"],
        "window": {"from": str(d0), "to": str(d1)},
        "typicalStopTicks": stop_t, "typicalTargetTicks": target_t,
        "typicalHoldBars": hold_bars, "typicalSpacingBars": spacing_bars, "flattenExits": flattens,
        "trades": [{"id": t["id"], "direction": t["direction"], "entryTimeET": format_et(t["entryTime"]), "entryTime": t["entryTime"],
                    "entryPrice": t["entryPrice"], "stop": t["stopPrice"], "target": t["targetPrice"], "exitPrice": t["exitPrice"],
                    "exitReason": t["exitReason"], "pnlUsd": t["pnlUsd"], "confidence": t["confidence"], "note": t["note"],
                    "tags": tags.get(t["id"])} for t in trades],
        "hypothesis": hyps[-1]["payload"] if hyps else None,
        "questions": [{"kind": q["kind"], "question": q["question"], "answer": q["answer"]} for q in detail.get("questions") or []],
        "skippedLabels": labels, "userMarks": marks,
    }


def evaluate(session_id: str, strategy_id: str, *, mode: str | None = None) -> dict:
    """Run the strategy over the replayed window, compute similarity, store it."""
    detail = store.session_detail(session_id)
    if detail is None:
        raise KeyError(session_id)
    d0, d1 = window(detail)
    job = validation.run_teaching_window(strategy_id, d0, d1, mode=mode)
    if job["status"] != "done":
        return {"error": f"teaching-window backtest failed: {job.get('message')}", "jobId": job["id"]}
    strategy = strategy_store.get_strategy(strategy_id) or {}
    from config.instruments import load_instruments

    root = load_instruments().root_for_symbol(strategy.get("instrument", {}).get("symbol", detail["symbol"]))
    tick = root.tick_size if root else 0.25
    primary = {"1min": 60, "5min": 300, "15min": 900}.get((strategy.get("timeframes") or {}).get("primary", "1min"), 60)
    user = detail["trades"]
    engine_trades = [jobs.normalize_trade(t) for t in job["trades"]]
    rep = similarity.report(user, engine_trades, primary_seconds=primary, tick_size=tick)
    rep.update({"strategyId": strategy_id, "jobId": job["id"], "window": {"from": str(d0), "to": str(d1)},
                "engineMetrics": {k: job.get("summary", {}).get(k) for k in ("netPnl", "trades", "profitFactor", "winRate")} if job.get("summary") else None})
    return rep


def record_candidate(session_id: str, report: dict, *, kind: str = "compiled", rationale: str | None = None) -> dict:
    """Store a candidate's similarity on the session; the first one becomes
    the compiled strategy, later ones go under `refinements`."""
    sess = store.get_session(session_id) or {}
    sim = dict(sess.get("similarity") or {})
    entry = {**report, "kind": kind, "rationale": rationale}
    if kind == "compiled" or not sim:
        sim = {**entry, "refinements": sim.get("refinements") or []}
        store.update_session(session_id, compiled_strategy_id=report.get("strategyId"), similarity_json=sim)
    else:
        sim.setdefault("refinements", []).append(entry)
        store.update_session(session_id, similarity_json=sim)
    return sim


def start_is_run(strategy_id: str) -> str | None:
    strategy = strategy_store.get_strategy(strategy_id)
    if not strategy:
        return None
    try:
        return jobs.start_backtest(strategy, window_kind="is")["id"]
    except Exception:
        return None


def label_false_positive(session_id: str, entry_time: int, label: str, reason: str | None = None) -> dict:
    return store.add_event(session_id, int(entry_time) * NS, "fp_label", {"entryTime": int(entry_time), "label": label, "reason": reason})


def pick(session_id: str, strategy_id: str) -> dict:
    return store.update_session(session_id, compiled_strategy_id=strategy_id)


def start_compile_run(session_id: str) -> dict:
    from agent import runs

    detail = store.session_detail(session_id)
    if detail is None:
        raise KeyError(session_id)
    store.update_session(session_id, status="compiling", date_to=detail.get("dateTo") or str(window(detail)[1]))
    run = runs.start_run("teaching_compile", {"sessionId": session_id})
    store.add_event(session_id, 0, "compile_started", {"runId": run["id"], "at": datetime.now(timezone.utc).isoformat()})
    return run


def summary_text(detail: dict) -> str:
    return json.dumps(prompt_payload(detail, 0.25), default=str)
