"""Pass / fail / untestable against a strategy's risk profile (PLATFORM-SPEC.md §4.6).

`evaluate(job, risk)` where `job` is the validation summary the analytics
layer produces:

    {"inSample": {metrics}, "walkForward": [{metrics}, ...], "outOfSample": {metrics}|None,
     "monteCarlo": {...}|None, "deflatedSharpe": {...}|None}

`passes` requires every non-null criterion. Below `minTradesInSample` the
verdict is `untestable` — never pass, never fail. OOS criteria are only
checked when an OOS run exists (after finalize).
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict

DEFAULT_PASS_CRITERIA = {
    "minTradesInSample": 100,
    "minTradesOutOfSample": 30,
    "minProfitFactor": 1.3,
    "minExpectancyR": 0.15,
    "maxDrawdownPct": 10,
    "minWalkForwardWindowsPositive": 2,
    "minOosProfitFactor": 1.1,
    "maxMonteCarloDrawdown95Pct": 15,
    "minDeflatedSharpeProb": None,
}

DEFAULT_RISK = {
    "proposedBy": "default",
    "rationale": "platform defaults (PLATFORM-SPEC.md §4.6); no agent proposal yet",
    "accountSize": 100000,
    "riskPerTradePct": 0.5,
    "maxContracts": 5,
    "dailyLossLimitPct": 2.0,
    "weeklyLossLimitPct": 5.0,
    "maxTradesPerDay": 5,
    "stopAfterConsecutiveLosses": 3,
    "weeklyTargetPct": None,
    "passCriteria": dict(DEFAULT_PASS_CRITERIA),
}


def with_defaults(risk: dict | None) -> dict:
    out = {**DEFAULT_RISK, **(risk or {})}
    out["passCriteria"] = {**DEFAULT_PASS_CRITERIA, **((risk or {}).get("passCriteria") or {})}
    return out


@dataclass
class Verdict:
    passes: bool
    untestable: bool
    failures: list[str] = field(default_factory=list)
    checks: list[dict] = field(default_factory=list)
    score: float = 0.0
    status: str = "untestable"   # pass | fail | untestable

    def to_dict(self) -> dict:
        return asdict(self)


def _check(checks, name, value, threshold, op, label):
    if threshold is None or value is None:
        checks.append({"name": name, "value": value, "threshold": threshold, "ok": None, "label": label})
        return True
    ok = value >= threshold if op == ">=" else value <= threshold
    checks.append({"name": name, "value": value, "threshold": threshold, "ok": bool(ok), "label": label})
    return bool(ok)


def evaluate(job: dict, risk: dict | None) -> Verdict:
    r = with_defaults(risk)
    pc = r["passCriteria"]
    is_ = job.get("inSample") or {}
    wf = job.get("walkForward") or []
    oos = job.get("outOfSample")
    mc = job.get("monteCarlo")
    dsr = job.get("deflatedSharpe")
    checks: list[dict] = []
    failures: list[str] = []

    n_is = int(is_.get("trades") or 0)
    if n_is < int(pc["minTradesInSample"] or 0):
        v = Verdict(passes=False, untestable=True, status="untestable",
                    failures=[f"untestable: {n_is} in-sample trades < minimum {pc['minTradesInSample']}"],
                    checks=[{"name": "minTradesInSample", "value": n_is, "threshold": pc["minTradesInSample"], "ok": False,
                             "label": "in-sample trades"}])
        return v
    checks.append({"name": "minTradesInSample", "value": n_is, "threshold": pc["minTradesInSample"], "ok": True, "label": "in-sample trades"})

    def fail(msg):
        failures.append(msg)

    if not _check(checks, "minProfitFactor", is_.get("profitFactor"), pc["minProfitFactor"], ">=", "IS profit factor"):
        fail(f"IS profit factor {is_.get('profitFactor')} < {pc['minProfitFactor']}")
    if not _check(checks, "minExpectancyR", is_.get("expectancyR"), pc["minExpectancyR"], ">=", "IS expectancy (R)"):
        fail(f"IS expectancy {is_.get('expectancyR')}R < {pc['minExpectancyR']}R")
    if not _check(checks, "maxDrawdownPct", is_.get("maxDrawdownPct"), pc["maxDrawdownPct"], "<=", "IS max drawdown %"):
        fail(f"IS max drawdown {is_.get('maxDrawdownPct')}% > {pc['maxDrawdownPct']}%")

    positive = sum(1 for w in wf if (w.get("netPnl") or 0) > 0)
    if not _check(checks, "minWalkForwardWindowsPositive", positive if wf else None, pc["minWalkForwardWindowsPositive"], ">=", "walk-forward windows positive"):
        fail(f"only {positive}/{len(wf)} walk-forward windows positive < {pc['minWalkForwardWindowsPositive']}")

    if mc and mc.get("bootstrap") and mc["bootstrap"].get("maxDrawdownPct"):
        v = mc["bootstrap"]["maxDrawdownPct"]["p95"]
        if not _check(checks, "maxMonteCarloDrawdown95Pct", v, pc["maxMonteCarloDrawdown95Pct"], "<=", "Monte Carlo DD p95 %"):
            fail(f"Monte Carlo 95th-percentile drawdown {v:.1f}% > {pc['maxMonteCarloDrawdown95Pct']}%")
    else:
        _check(checks, "maxMonteCarloDrawdown95Pct", None, pc["maxMonteCarloDrawdown95Pct"], "<=", "Monte Carlo DD p95 %")

    if pc.get("minDeflatedSharpeProb") is not None:
        v = (dsr or {}).get("dsr")
        if not _check(checks, "minDeflatedSharpeProb", v, pc["minDeflatedSharpeProb"], ">=", "deflated Sharpe prob"):
            fail(f"deflated Sharpe probability {v} < {pc['minDeflatedSharpeProb']}")
    else:
        _check(checks, "minDeflatedSharpeProb", (dsr or {}).get("dsr"), None, ">=", "deflated Sharpe prob (report only)")

    if oos is not None:
        n_oos = int(oos.get("trades") or 0)
        if not _check(checks, "minTradesOutOfSample", n_oos, pc["minTradesOutOfSample"], ">=", "OOS trades"):
            fail(f"OOS trades {n_oos} < {pc['minTradesOutOfSample']}")
        if not _check(checks, "minOosProfitFactor", oos.get("profitFactor"), pc["minOosProfitFactor"], ">=", "OOS profit factor"):
            fail(f"OOS profit factor {oos.get('profitFactor')} < {pc['minOosProfitFactor']}")
    else:
        checks.append({"name": "outOfSample", "value": None, "threshold": None, "ok": None, "label": "OOS (hidden until finalize)"})

    scored = [c for c in checks if c["ok"] is not None]
    score = sum(1 for c in scored if c["ok"]) / len(scored) if scored else 0.0
    passes = not failures
    return Verdict(passes=passes, untestable=False, failures=failures, checks=checks, score=round(score, 3),
                   status="pass" if passes else "fail")
