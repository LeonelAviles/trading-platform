"""Separate account lenses over verified research trades, without firm defaults."""
from collections import defaultdict
import math

import database
from models import Backtest, MLExperiment, ResearchEvidence, ResearchProposal
from research_agent import evidence
from research_ml.schema import AccountPolicy


def assess(trades, policy: AccountPolicy | None):
    if policy is None:
        return {"status": "unassessed", "reason": "explicit account policy required"}
    if not trades or any(any(not isinstance(t.get(k), (int, float)) or isinstance(t[k], bool) or not math.isfinite(t[k])
                             for k in ("pnlUsd", "exitTime", "commissionUsd")) or not t.get("sessionDate") for t in trades):
        return {"status": "unassessed", "reason": "complete net PnL, fees, timestamps and sessions required"}
    equity = peak = policy.startingEquity
    worst = 0.0
    daily = defaultdict(float)
    for trade in sorted(trades, key=lambda t: t["exitTime"]):
        equity += trade["pnlUsd"]  # engine pnlUsd already includes commission
        peak = max(peak, equity)
        worst = max(worst, (peak - equity) / peak * 100)
        daily[trade["sessionDate"]] += trade["pnlUsd"]
    observed = {"netPnlUsd": sum(t["pnlUsd"] for t in trades), "trades": len(trades),
                "closedTradeDrawdownPct": worst, "dailyNetLossUsd": max(0.0, -min(daily.values()))}
    if not all(math.isfinite(v) for v in observed.values()):
        return {"status": "unassessed", "reason": "non-finite aggregate evidence"}
    failures = []
    for metric, limit, passes in (
        ("netPnlUsd", policy.minNetPnlUsd, observed["netPnlUsd"] >= policy.minNetPnlUsd),
        ("trades", policy.minTrades, len(trades) >= policy.minTrades),
        ("closedTradeDrawdownPct", policy.maxClosedTradeDrawdownPct, worst <= policy.maxClosedTradeDrawdownPct),
        ("dailyNetLossUsd", policy.maxDailyNetLossUsd, observed["dailyNetLossUsd"] <= policy.maxDailyNetLossUsd),
    ):
        if not passes:
            failures.append({"metric": metric, "observed": observed[metric], "limit": limit})
    return {"status": "criteria_failed" if failures else "criteria_met", "policy": policy.model_dump(),
            "observed": observed, "failures": failures,
            "scope": "closed-trade and daily-net metrics only; not prop-firm eligibility or live suitability",
            "unassessed": ["intraday/unrealized loss", "trailing floor rules", "consistency/payout rules", "future returns"]}


def report(thread_id, experiment_id, job_id, prop_firm, personal):
    with database.session_scope() as db:
        proposal = (db.query(ResearchProposal).join(ResearchEvidence)
                    .filter(ResearchEvidence.job_id == job_id, ResearchProposal.thread_id == thread_id).one_or_none())
        experiment = db.get(MLExperiment, experiment_id)
        reference = (proposal.document_json.get("strategy", {}).get("meta", {}).get("mlDiscovery", {})
                     if proposal else {})
        if (not experiment or experiment.thread_id != thread_id or experiment.status != "candidate" or
                reference.get("experimentId") != experiment_id or
                reference.get("experimentDigest") != experiment.digest or
                reference.get("resultDigest") != (experiment.result_json or {}).get("resultDigest")):
            raise ValueError("job is not approved evidence for this experiment and thread")
        job = db.get(Backtest, job_id)
        account = (job.metrics_json or {}).get("accountSize")
        if any(p is not None and p.startingEquity != account for p in (prop_firm, personal)):
            raise ValueError("account equity must match the actual Nautilus run; rescaling requires a new approved test")
    _, artifact, artifact_hash = evidence.verified_artifact(job_id)
    return {"jobId": job_id, "artifactSha256": artifact_hash,
            "propFirm": assess(artifact["trades"], prop_firm), "personal": assess(artifact["trades"], personal)}
