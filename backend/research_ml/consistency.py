"""Configurable descriptive concentration gate over closed, after-cost trades.

No statistical significance or prop-firm qualification is implied. Session
buckets include zero-trade sessions when supplied, so inactivity stays visible.
"""
import math
from collections import defaultdict
from pydantic import BaseModel, ConfigDict, Field


class PropProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(min_length=1, max_length=120)
    dailyLossLimitUsd: float = Field(gt=0, allow_inf_nan=False)
    maxClosedEquityDrawdownUsd: float = Field(gt=0, allow_inf_nan=False)
    profitTargetUsd: float = Field(gt=0, allow_inf_nan=False)
    minActiveSessions: int = Field(ge=1)


class ConsistencyPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    maxTopThreePositiveShare: float = Field(default=0.5, gt=0, le=1, allow_inf_nan=False)
    minTrades: int = Field(default=30, ge=4)
    minSessions: int = Field(default=5, ge=2)
    requirePositiveWithoutTopThree: bool = True
    minPositiveChronologicalBlocks: int = Field(default=2, ge=0, le=3)
    propProfile: PropProfile | None = None


def assess(trades, sessions=(), policy=None):
    policy = ConsistencyPolicy.model_validate(policy or {})
    closed = sorted((t for t in trades if t.get("exitTime") is not None), key=lambda t: t["exitTime"])
    pnls = [float(t["pnlUsd"]) for t in closed]
    if any(not math.isfinite(x) for x in pnls):
        raise ValueError("finite after-cost trade PnL required")
    wins = sorted((p for p in pnls if p > 0), reverse=True)
    top = sum(wins[:3]); positive = sum(wins); net = sum(pnls)
    share = top / positive if positive else None
    by_day = defaultdict(float, {str(day): 0.0 for day in sessions})
    for trade, pnl in zip(closed, pnls):
        if not trade.get("sessionDate"):
            raise ValueError("sessionDate required for chronological stability")
        by_day[str(trade["sessionDate"])] += pnl
    daily = [{"session": day, "netPnl": round(pnl, 2)} for day, pnl in sorted(by_day.items())]
    # Contiguous thirds by session, including inactive sessions, never rank by return.
    blocks = []
    for index in range(3):
        block = daily[index * len(daily) // 3:(index + 1) * len(daily) // 3]
        blocks.append({"block": index + 1, "sessions": len(block),
            "start": block[0]["session"] if block else None,
            "end": block[-1]["session"] if block else None,
            "netPnl": round(sum(d["netPnl"] for d in block), 2) if block else None})
    positive_blocks = sum(b["netPnl"] is not None and b["netPnl"] > 0 for b in blocks)
    profile = None
    if policy.propProfile:
        peak = equity = drawdown = 0.0
        for pnl in pnls:
            equity += pnl; peak = max(peak, equity); drawdown = max(drawdown, peak-equity)
        profile = {"profile": policy.propProfile.model_dump(),
            "dailyLossWithinLimit": all(d["netPnl"] >= -policy.propProfile.dailyLossLimitUsd for d in daily),
            "closedEquityDrawdownWithinLimit": drawdown <= policy.propProfile.maxClosedEquityDrawdownUsd,
            "profitTargetReached": net >= policy.propProfile.profitTargetUsd,
            "minimumActiveSessionsReached": len({str(t["sessionDate"]) for t in closed}) >= policy.propProfile.minActiveSessions,
            "scope": "historical closed-trade proxies only; intraday equity, trailing limits and firm rules unassessed"}
    reasons = []
    if not wins: reasons.append("no_positive_trades")
    if share is not None and share > policy.maxTopThreePositiveShare: reasons.append("top_three_concentration")
    if policy.requirePositiveWithoutTopThree and net - top <= 0: reasons.append("nonpositive_without_top_three")
    insufficient = len(pnls) < policy.minTrades or len(wins) < 3 or len(daily) < policy.minSessions
    if insufficient: reasons.append("insufficient_sample")
    if positive_blocks < policy.minPositiveChronologicalBlocks: reasons.append("chronological_instability")
    return {"version": "consistency_v1", "policy": policy.model_dump(),
        "status": "insufficient_sample" if insufficient else ("flagged" if reasons else "pass"),
        "passes": not reasons, "reasons": reasons, "tradeCount": len(pnls), "winnerCount": len(wins),
        "topWinnersUsed": min(3, len(wins)), "positiveNetPnl": round(positive, 2),
        "topThreeNetPnl": round(top, 2), "topThreePositiveShare": share,
        "netPnl": round(net, 2), "netExcludingTopThree": round(net - top, 2),
        "chronological": {"sessions": daily, "blocks": blocks,
            "positiveSessions": sum(d["netPnl"] > 0 for d in daily),
            "positiveBlocks": positive_blocks},
        "scope": "configurable descriptive research heuristic; not significance or a prop rule",
        "propFit": "descriptive_profile_checks_only" if profile else "unassessed_no_firm_profile",
        "profileAssessment": profile}
