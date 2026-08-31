"""Backtest analytics over v2 trade records (PLATFORM-SPEC.md §5 Phase 2 task 4).

`compute(trades, daily_returns, account_size)` returns the dashboard payload:
legacy keys the review page already reads (`netPnl`, `winRate`,
`profitFactor`, `expectancyR`, `maxDrawdown`, `sharpe`, `sqn`, `equityCurve`,
`distribution`, `monthly`, `exitReasons`, `recentTrades`) plus dollar
metrics, daily-return Sharpe/Sortino/Calmar, MAE/MFE, time in trade and the
per-regime / per-hour / per-exit tables.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timezone

from engine.monte_carlo import max_drawdown
from engine.session import ns_to_et, NS

TRADING_DAYS = 252


def _pf(gross_win: float, gross_loss: float):
    if gross_loss != 0:
        return round(abs(gross_win / gross_loss), 3)
    return None if gross_win > 0 else 0.0


def _bucket(trades: list[dict]) -> dict:
    """Metrics for one group of trades (used by the per-tag tables)."""
    if not trades:
        return {"trades": 0, "netPnl": 0.0, "winRate": 0.0, "profitFactor": 0.0, "expectancyR": 0.0, "avgPnl": 0.0}
    wins = [t for t in trades if t["pnlUsd"] > 0]
    gw = sum(t["pnlUsd"] for t in wins)
    gl = sum(t["pnlUsd"] for t in trades if t["pnlUsd"] < 0)
    rs = [t["r"] for t in trades if t.get("r") is not None]
    net = sum(t["pnlUsd"] for t in trades)
    return {
        "trades": len(trades), "netPnl": round(net, 2), "winRate": round(len(wins) / len(trades) * 100, 1),
        "profitFactor": _pf(gw, gl), "expectancyR": round(sum(rs) / len(rs), 3) if rs else None,
        "avgPnl": round(net / len(trades), 2),
    }


def compute(trades: list[dict], daily: list[dict] | None = None, account_size: float = 100_000.0) -> dict:
    trades = sorted((t for t in trades if t.get("exitTime") is not None), key=lambda t: t["exitTime"])
    daily = daily or []
    if not trades:
        return {
            "trades": 0, "netPnl": 0.0, "grossPnl": 0.0, "commission": 0.0, "winRate": 0.0, "profitFactor": 0.0,
            "expectancyR": 0.0, "expectancyUsd": 0.0, "maxDrawdown": 0.0, "maxDrawdownPct": 0.0, "sharpe": 0.0,
            "sortino": 0.0, "calmar": 0.0, "sqn": 0.0, "avgWin": 0.0, "avgLoss": 0.0, "largestWin": 0.0,
            "largestLoss": 0.0, "avgSlippageTicks": 0.0, "equityCurve": [], "distribution": [], "monthly": [],
            "exitReasons": [], "byRegime": {}, "byHour": [], "maeMfe": {}, "timeInTrade": {}, "recentTrades": [],
            "sessions": len(daily), "sessionsTraded": 0,
        }

    pnls = [t["pnlUsd"] for t in trades]
    rs = [t["r"] for t in trades if t.get("r") is not None]
    wins = [t for t in trades if t["pnlUsd"] > 0]
    losses = [t for t in trades if t["pnlUsd"] < 0]
    gross_win = sum(t["pnlUsd"] for t in wins)
    gross_loss = sum(t["pnlUsd"] for t in losses)
    net = sum(pnls)
    commission = sum(t.get("commissionUsd", 0.0) for t in trades)
    dd = max_drawdown(pnls)

    # Equity curve per closed trade.
    curve, cum, cum_r = [], 0.0, 0.0
    for t in trades:
        cum += t["pnlUsd"]
        cum_r += t["r"] or 0.0
        curve.append({"time": t["exitTime"], "cumPnl": round(cum, 2), "cumR": round(cum_r, 3), "pnl": t["pnlUsd"]})

    # Daily-return ratios (annualised √252).
    rets = [d["returnPct"] / 100 for d in daily] if daily else []
    sharpe = sortino = 0.0
    if len(rets) >= 2:
        mean = sum(rets) / len(rets)
        var = sum((x - mean) ** 2 for x in rets) / (len(rets) - 1)
        if var > 0:
            sharpe = mean / math.sqrt(var) * math.sqrt(TRADING_DAYS)
        downside = [min(0.0, x) ** 2 for x in rets]
        dvar = sum(downside) / (len(rets) - 1)
        if dvar > 0:
            sortino = mean / math.sqrt(dvar) * math.sqrt(TRADING_DAYS)
    dd_pct = dd / account_size * 100 if account_size else 0.0
    ann_return_pct = (net / account_size * 100) * (TRADING_DAYS / max(1, len(rets))) if rets and account_size else 0.0
    calmar = ann_return_pct / dd_pct if dd_pct > 0 else 0.0

    sqn = 0.0
    if len(rs) >= 2:
        mean_r = sum(rs) / len(rs)
        var_r = sum((r - mean_r) ** 2 for r in rs) / (len(rs) - 1)
        if var_r > 0:
            sqn = mean_r / math.sqrt(var_r) * math.sqrt(len(rs))

    buckets: dict[float, int] = defaultdict(int)
    for r in rs:
        buckets[int(max(-3.0, min(3.0, r)) // 0.5) * 0.5] += 1
    distribution = [{"bucket": b, "count": buckets[b]} for b in sorted(buckets)]

    monthly_map: dict[str, list] = defaultdict(list)
    for t in trades:
        ts = datetime.fromtimestamp(t["exitTime"], tz=timezone.utc)
        monthly_map[f"{ts.year}-{ts.month:02d}"].append(t)
    monthly = []
    for key in sorted(monthly_map):
        m = monthly_map[key]
        mrs = [t["r"] for t in m if t.get("r") is not None]
        monthly.append({"year": int(key[:4]), "month": int(key[5:]), "trades": len(m),
                        "winRate": round(sum(1 for t in m if t["pnlUsd"] > 0) / len(m) * 100, 1),
                        "netPnl": round(sum(t["pnlUsd"] for t in m), 2),
                        "avgR": round(sum(mrs) / len(mrs), 2) if mrs else None})

    reasons: dict[str, list] = defaultdict(list)
    for t in trades:
        reasons[t["exitReason"]].append(t)
    exit_reasons = [{"reason": k, "count": len(v), **{kk: vv for kk, vv in _bucket(v).items() if kk != "trades"}}
                    for k, v in sorted(reasons.items(), key=lambda kv: -len(kv[1]))]

    by_tag: dict[str, list] = defaultdict(list)
    for t in trades:
        for tag in t.get("regimeTags") or []:
            by_tag[tag].append(t)
    by_regime = {tag: _bucket(v) for tag, v in sorted(by_tag.items())}

    by_hour_map: dict[int, list] = defaultdict(list)
    for t in trades:
        by_hour_map[ns_to_et(t["entryTime"] * NS).hour].append(t)
    by_hour = [{"hourEt": h, **_bucket(v)} for h, v in sorted(by_hour_map.items())]

    def _avg(xs):
        return round(sum(xs) / len(xs), 2) if xs else 0.0

    mae_mfe = {
        "avgMaeTicks": _avg([t["mae"] for t in trades]), "avgMfeTicks": _avg([t["mfe"] for t in trades]),
        "winnersAvgMaeTicks": _avg([t["mae"] for t in wins]), "losersAvgMfeTicks": _avg([t["mfe"] for t in losses]),
    }
    minutes = [(t["exitTime"] - t["entryTime"]) / 60 for t in trades]
    time_in_trade = {"avgBars": _avg([t["barsHeld"] for t in trades]), "avgMinutes": _avg(minutes),
                     "maxMinutes": round(max(minutes), 1) if minutes else 0.0}

    return {
        "trades": len(trades), "wins": len(wins), "losses": len(losses),
        "netPnl": round(net, 2), "grossPnl": round(net + commission, 2), "commission": round(commission, 2),
        "winRate": round(len(wins) / len(trades) * 100, 1),
        "profitFactor": _pf(gross_win, gross_loss),
        "expectancyR": round(sum(rs) / len(rs), 3) if rs else 0.0,
        "expectancyUsd": round(net / len(trades), 2),
        "maxDrawdown": round(-dd, 2), "maxDrawdownUsd": round(dd, 2), "maxDrawdownPct": round(dd_pct, 3),
        "sharpe": round(sharpe, 3), "sortino": round(sortino, 3), "calmar": round(calmar, 3), "sqn": round(sqn, 2),
        "annualizedReturnPct": round(ann_return_pct, 2),
        "avgWin": round(gross_win / len(wins), 2) if wins else 0.0,
        "avgLoss": round(gross_loss / len(losses), 2) if losses else 0.0,
        "largestWin": round(max((t["pnlUsd"] for t in wins), default=0.0), 2),
        "largestLoss": round(min((t["pnlUsd"] for t in losses), default=0.0), 2),
        "avgSlippageTicks": _avg([t.get("slippageTicks", 0.0) for t in trades]),
        "avgContracts": _avg([t["contracts"] for t in trades]),
        "equityCurve": curve, "distribution": distribution, "monthly": monthly, "exitReasons": exit_reasons,
        "byRegime": by_regime, "byHour": by_hour, "maeMfe": mae_mfe, "timeInTrade": time_in_trade,
        "sessions": len(daily), "sessionsTraded": len({t["sessionDate"] for t in trades}),
        "recentTrades": trades[-25:][::-1],
    }
