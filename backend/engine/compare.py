"""Side-by-side comparison of two finished backtests (the strategy page's
"Compare two nodes"). Ranks by expectancy (R), not raw win rate, since
expectancy accounts for risk sizing; runs a two-proportion z-test on the
win-rate difference and flags when either side has too few trades (<20) to
draw a confident conclusion."""

from __future__ import annotations

from engine import jobs

MIN_TRADES_FOR_CONFIDENCE = 20


class CompareError(ValueError):
    pass


def _job(job_id: str) -> dict:
    job = jobs.get_job(job_id)
    if job is None:
        raise CompareError(f"backtest '{job_id}' not found")
    return job


def compare_backtests(job_id_a: str, job_id_b: str) -> dict:
    job_a, job_b = _job(job_id_a), _job(job_id_b)
    a, b = jobs.strategy_analytics(job_a), jobs.strategy_analytics(job_b)

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

    winner = None  # "a" | "b" | None (tied or insufficient evidence); `verdict` is the prose form
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
