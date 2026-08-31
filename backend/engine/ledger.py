"""Trade ledger — turns fills/positions into the v2 trade record (PLATFORM-SPEC.md §4.3).

Record (times in unix seconds for the chart; legacy aliases kept so the
review page and the agent tools keep working):

  {id, direction, contracts, entryTime, entryPrice, exitTime, exitPrice, stopPrice, targetPrice,
   exitReason, pnlPoints, pnlTicks, pnlUsd (after commission), grossPnlUsd, commissionUsd,
   slippageTicks, r, mae, mfe (ticks, adverse/favourable excursion), barsHeld, sessionDate,
   regimeTags[], entryContextId,
   pnl (=pnlUsd), reason (=exitReason), qty (=contracts)}
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from engine import pnl as P
from engine.session import NS, session_date


@dataclass
class OpenTrade:
    direction: str
    contracts: int
    entry_ts: int                      # ns
    entry_price: float
    ref_price: float                   # signal reference (bar close) for slippage
    stop_price: float | None
    target_price: float | None
    entry_bar_index: int
    mae_price: float = 0.0             # worst price seen
    mfe_price: float = 0.0             # best price seen
    commission: float = 0.0
    entry_context_id: str | None = None
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    initial_risk: float | None = None
    breakeven_done: bool = False
    scaled: set = field(default_factory=set)

    def __post_init__(self):
        self.mae_price = self.entry_price
        self.mfe_price = self.entry_price

    def observe(self, high: float, low: float) -> None:
        if self.direction == "long":
            self.mae_price = min(self.mae_price, low)
            self.mfe_price = max(self.mfe_price, high)
        else:
            self.mae_price = max(self.mae_price, high)
            self.mfe_price = min(self.mfe_price, low)


class Ledger:
    def __init__(self, spec: P.ContractSpec, regime_tags: dict | None = None):
        self.spec = spec
        self.trades: list[dict] = []
        self.regime_tags = regime_tags or {}

    def close(self, t: OpenTrade, exit_ts: int, exit_price: float, reason: str, bar_index: int,
              exit_ref_price: float | None = None, commission: float | None = None) -> dict:
        s = self.spec
        sign = P.direction_sign(t.direction)
        points = P.pnl_points(t.entry_price, exit_price, t.direction)
        gross = points * s.multiplier * t.contracts
        comm = commission if commission is not None else (t.commission or P.commission_usd(t.contracts, s))
        entry_slip = (t.entry_price - t.ref_price) * sign / s.tick_size
        exit_slip = ((exit_ref_price - exit_price) * sign / s.tick_size) if exit_ref_price is not None else 0.0
        sd = session_date(t.entry_ts)
        rec = {
            "id": t.id, "direction": t.direction, "contracts": t.contracts,
            "entryTime": int(t.entry_ts // NS), "entryPrice": round(t.entry_price, 4),
            "exitTime": int(exit_ts // NS), "exitPrice": round(exit_price, 4),
            "stopPrice": round(t.stop_price, 4) if t.stop_price is not None else None,
            "targetPrice": round(t.target_price, 4) if t.target_price is not None else None,
            "exitReason": reason,
            "pnlPoints": round(points, 4), "pnlTicks": round(points / s.tick_size, 2),
            "grossPnlUsd": round(gross, 2), "commissionUsd": round(comm, 2), "pnlUsd": round(gross - comm, 2),
            "slippageTicks": round(entry_slip + exit_slip, 2),
            "r": (round(v, 3) if (v := P.r_multiple(t.entry_price, exit_price, t.stop_price, t.direction)) is not None else None),
            "mae": round(abs(t.mae_price - t.entry_price) / s.tick_size, 2),
            "mfe": round(abs(t.mfe_price - t.entry_price) / s.tick_size, 2),
            "barsHeld": int(max(0, bar_index - t.entry_bar_index)),
            "sessionDate": sd.isoformat(),
            "regimeTags": list(self.regime_tags.get(sd.isoformat(), [])),
            "entryContextId": t.entry_context_id,
        }
        rec["pnl"] = rec["pnlUsd"]
        rec["reason"] = reason
        rec["qty"] = t.contracts
        self.trades.append(rec)
        return rec


def daily_returns(trades: list[dict], session_dates: list[str], account_size: float) -> list[dict]:
    """Per session: PnL in $ and % of account, zero for sessions without trades."""
    by_day: dict[str, float] = {d: 0.0 for d in session_dates}
    for t in trades:
        by_day[t["sessionDate"]] = by_day.get(t["sessionDate"], 0.0) + t["pnlUsd"]
    return [{"date": d, "pnlUsd": round(v, 2), "returnPct": round(v / account_size * 100, 6) if account_size else 0.0}
            for d, v in sorted(by_day.items())]
