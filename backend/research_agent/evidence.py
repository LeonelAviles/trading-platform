"""Read-only evidence analysis. Never schedules simulations or selects a winner."""
from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np

import database
from engine import jobs
from models import Backtest, ResearchProposal

ALLOWED_WINDOWS = {"is", "wf1", "wf2", "wf3"}


def _artifact(job_id: str) -> tuple[dict, list[dict], str]:
    with database.session_scope() as db:
        row = db.get(Backtest, job_id)
        if not row or row.status != "done" or row.window_kind not in ALLOWED_WINDOWS:
            raise ValueError("evidence requires a completed IS/WF job; OOS/full are unavailable to research")
        meta = jobs._row_to_job(row)
        path = row.trades_path
    if meta.get("symbol") != "ES1!":
        raise ValueError("research evidence currently supports ES1! only")
    from pathlib import Path
    artifact = Path(path) if path else jobs._job_dir(job_id) / "trades.json"
    if not artifact.is_absolute():
        artifact = jobs.REPO_ROOT / artifact
    if not artifact.is_file():
        raise ValueError("trade artifact unavailable; an absent file is not zero trades")
    raw = artifact.read_bytes()
    data = json.loads(raw)
    if not isinstance(data.get("trades"), list):
        raise ValueError("trade evidence unavailable: malformed artifact")
    # Do not normalize legacy records here: that invents zero MAE/MFE and fees.
    return meta, data["trades"], hashlib.sha256(raw).hexdigest()


def trade_evidence(job_id: str, offset: int, limit: int) -> dict:
    if offset < 0 or not 1 <= limit <= 200:
        raise ValueError("offset must be nonnegative and limit must be 1..200")
    job, trades, artifact_hash = _artifact(job_id)
    fields = ("id", "direction", "contracts", "entryTime", "entryPrice", "exitTime", "exitPrice", "stopPrice",
              "targetPrice", "exitReason", "pnlUsd", "r", "commissionUsd", "slippageTicks", "mae", "mfe",
              "barsHeld", "sessionDate", "regimeTags", "entryContextId")
    return {"job": job, "artifactSha256": artifact_hash, "total": len(trades), "offset": offset,
            "trades": [{**{k: t.get(k) for k in fields},
                        "missingFields": [k for k in fields if k not in t or t[k] is None],
                        "entrySnapshot": {"status": "unavailable", "reason": "snapshot retrieval/capture is not implemented"}}
                       for t in trades[offset:offset + limit]],
            "regimeProvenance": "full-session hindsight or unknown; not an entry-time feature"}


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _summary(trades: list[dict]) -> dict:
    pnl = [t["pnlUsd"] for t in trades if _finite(t.get("pnlUsd"))]
    rs = [t["r"] for t in trades if _finite(t.get("r"))]
    return {"trades": len(trades), "pnlObservations": len(pnl), "rObservations": len(rs),
            "netPnlUsd": sum(pnl) if pnl else None, "meanPnlUsd": float(np.mean(pnl)) if pnl else None,
            "meanR": float(np.mean(rs)) if rs else None,
            "winRate": sum(p > 0 for p in pnl) / len(pnl) if pnl else None}


def _select(trades: list[dict], selector: dict) -> list[dict]:
    allowed = {"direction", "exitReason", "regime", "sessionFrom", "sessionTo", "entryHourEt"}
    if set(selector) - allowed:
        raise ValueError(f"unsupported selector; use {sorted(allowed)}")
    def matches(t):
        for key, value in selector.items():
            if key == "regime" and value not in (t.get("regimeTags") or []):
                return False
            if key in {"direction", "exitReason"} and t.get(key) != value:
                return False
            if key == "sessionFrom" and (not t.get("sessionDate") or t["sessionDate"] < value):
                return False
            if key == "sessionTo" and (not t.get("sessionDate") or t["sessionDate"] > value):
                return False
            if key == "entryHourEt":
                if not _finite(t.get("entryTime")) or datetime.fromtimestamp(t["entryTime"], ZoneInfo("America/New_York")).hour != value:
                    return False
        return True
    return [t for t in trades if matches(t)]


def _trial_context() -> dict:
    with database.session_scope() as db:
        count = db.query(ResearchProposal).filter(ResearchProposal.strategy_id.is_not(None)).count()
    return {"recordedExperiments": count, "externalAndLegacyTrials": "unknown",
            "postHocComparisons": "not enumerated; repeated descriptive queries are not independent tests",
            "significance": "not established; no multiplicity-adjusted claim"}


def compare_groups(job_id: str, group_a: dict, group_b: dict, uncertainty: dict | None) -> dict:
    job, trades, artifact_hash = _artifact(job_id)
    a, b = _select(trades, group_a), _select(trades, group_b)
    result = {"jobId": job_id, "window": job["windowKind"], "artifactSha256": artifact_hash,
              "groupA": {"selector": group_a, **_summary(a)}, "groupB": {"selector": group_b, **_summary(b)},
              "overlapTrades": len({id(t) for t in a} & {id(t) for t in b}),
              "uncertainty": {"status": "unresolved", "reason": "explicit uncertainty policy not supplied"},
              "repeatedTesting": _trial_context(), "regimeProvenance": "hindsight/unknown; descriptive stratification only"}
    if uncertainty is None:
        return result
    if set(uncertainty) != {"method", "confidence", "resamples", "seed", "minSessions"}:
        raise ValueError("uncertainty requires method, confidence, resamples, seed, minSessions")
    if uncertainty["method"] != "session_cluster_bootstrap":
        raise ValueError("only session_cluster_bootstrap is supported; no IID trade tests")
    confidence, count, seed, minimum = (uncertainty[k] for k in ("confidence", "resamples", "seed", "minSessions"))
    if not _finite(confidence) or not 0 < confidence < 1 or type(count) is not int or not 100 <= count <= 10000 or type(seed) is not int or seed < 0 or type(minimum) is not int or minimum < 2:
        raise ValueError("invalid bootstrap policy; confidence in (0,1), resamples 100..10000, seed >=0, minSessions >=2")
    # Pair the same resampled sessions across both groups, preserving intraday
    # dependence and overlapping group membership. Sessions without trades in a
    # group remain zero-count clusters, not fabricated zero-return observations.
    if any(not t.get("sessionDate") or not _finite(t.get("pnlUsd")) for t in a + b):
        result["uncertainty"] = {"status": "unavailable", "reason": "missing session or PnL evidence", "policy": uncertainty}
        return result
    sessions = sorted({t["sessionDate"] for t in a + b})
    if min(len({t["sessionDate"] for t in group}) for group in (a, b)) < minimum:
        result["uncertainty"] = {"status": "unavailable", "reason": "insufficient sessions under requested policy", "policy": uncertainty}
        return result
    sums, counts = [], []
    for group in (a, b):
        buckets = defaultdict(list)
        for t in group:
            buckets[t["sessionDate"]].append(t["pnlUsd"])
        sums.append(np.array([sum(buckets[s]) for s in sessions]))
        counts.append(np.array([len(buckets[s]) for s in sessions]))
    rng = np.random.default_rng(seed)
    differences = []
    for _ in range(count):
        sample = rng.integers(0, len(sessions), len(sessions))
        na, nb = counts[0][sample].sum(), counts[1][sample].sum()
        if na and nb:
            differences.append(float(sums[0][sample].sum() / na - sums[1][sample].sum() / nb))
    if len(differences) != count:
        result["uncertainty"] = {"status": "unavailable", "reason": "resampling produced empty groups", "policy": uncertainty}
        return result
    alpha = (1 - confidence) / 2
    result["uncertainty"] = {"status": "descriptive", "policy": uncertainty, "sessions": len(sessions),
                             "meanPnlDifferenceIntervalUsd": np.quantile(differences, [alpha, 1 - alpha]).tolist(),
                             "assumptions": "sessions exchangeable; inter-session dependence and post-selection bias not corrected"}
    return result


def stability(job_ids: list[str], extra_costs_usd: list[float]) -> dict:
    if not 1 <= len(job_ids) <= 20 or len(set(job_ids)) != len(job_ids):
        raise ValueError("provide 1..20 distinct existing jobs")
    if len(extra_costs_usd) > 20 or any(not _finite(v) or v < 0 for v in extra_costs_usd):
        raise ValueError("cost scenarios must contain at most 20 nonnegative finite USD costs per trade")
    results = []
    for job_id in job_ids:
        job, trades, artifact_hash = _artifact(job_id)
        regimes, months = defaultdict(list), defaultdict(list)
        for t in trades:
            for tag in t.get("regimeTags") or ["unavailable"]:
                regimes[tag].append(t)
            months[str(t.get("sessionDate") or "unavailable")[:7]].append(t)
        stats = _summary(trades)
        results.append({"jobId": job_id, "strategyId": job["strategyId"], "window": job["windowKind"],
                        "dateFrom": job["dateFrom"], "dateTo": job["dateTo"], "mode": job["mode"],
                        "artifactSha256": artifact_hash, "summary": stats,
                        "byMonth": {k: _summary(v) for k, v in sorted(months.items())},
                        "byRegime": {k: _summary(v) for k, v in sorted(regimes.items())},
                        "costSensitivity": [{"additionalUsdPerTrade": cost,
                                             "netPnlUsd": stats["netPnlUsd"] - cost * len(trades)
                                             if stats["netPnlUsd"] is not None and stats["pnlObservations"] == len(trades) else None}
                                            for cost in extra_costs_usd]})
    return {"results": results, "repeatedTesting": _trial_context(),
            "limitations": ["Descriptive existing results; no new simulations or parameter sweeps.",
                            "Cost sensitivity is arithmetic on unchanged fills, not an execution model.",
                            "Regime labels may use the full session and overlap; not entry-time evidence.",
                            "IS/WF may overlap; jobs are not independent replications or matched comparisons.",
                            "No robustness threshold or significance policy is selected."]}
