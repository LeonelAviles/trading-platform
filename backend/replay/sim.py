"""Teaching-order simulation inside a replay session (PLATFORM-SPEC.md §4.11).

Fills follow the `ticks` backtest mode (§4.3, `engine/backtest_worker.py`
with `FillModel(prob_slippage=1.0)`): a market order fills on the next
print one tick against the trader; a stop is a market order triggered by
the first print at or through the stop price (again one tick of slippage);
a target is a limit that fills at its price once a print trades *through*
it. Stop wins when a single print would trigger both. PnL uses
`engine.pnl` so a teaching trade's number is the backtester's number.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field

from engine.pnl import ContractSpec, apply_slippage, net_pnl_usd, r_multiple, round_to_tick

NS = 1_000_000_000


@dataclass
class OpenPosition:
    id: str
    direction: str            # long | short
    contracts: int
    entry_ts: int
    entry_price: float
    stop: float | None
    target: float | None
    note: str | None = None
    confidence: int | None = None
    stop_ticks: int | None = None
    target_ticks: int | None = None

    def unrealized(self, price: float, spec: ContractSpec) -> float:
        return net_pnl_usd(self.entry_price, price, self.direction, self.contracts, spec)

    def to_dict(self, last: float | None, spec: ContractSpec) -> dict:
        return {
            "id": self.id, "direction": self.direction, "contracts": self.contracts,
            "entryTime": self.entry_ts // NS, "entryTs": self.entry_ts, "entryPrice": self.entry_price,
            "stop": self.stop, "target": self.target, "last": last,
            "unrealizedPnl": round(self.unrealized(last, spec), 2) if last is not None else None,
            "unrealizedTicks": round((last - self.entry_price) / spec.tick_size * (1 if self.direction == "long" else -1), 2)
            if last is not None else None,
        }


@dataclass
class OrderSim:
    spec: ContractSpec
    slippage_ticks: float = 1.0
    position: OpenPosition | None = None
    pending: dict | None = None          # market order waiting for the next print
    pending_exit: str | None = None      # "flatten" waiting for the next print
    trades: list[dict] = field(default_factory=list)
    last_price: float | None = None

    # -- commands --------------------------------------------------------------

    def submit(self, side: str, contracts: int = 1, stop_ticks: int | None = None, target_ticks: int | None = None,
               note: str | None = None, confidence: int | None = None) -> dict:
        if side not in ("buy", "sell"):
            raise ValueError("side must be buy or sell")
        if self.position is not None:
            raise ValueError("a position is already open — flatten first")
        if self.pending is not None:
            raise ValueError("an order is already pending")
        self.pending = {
            "direction": "long" if side == "buy" else "short", "contracts": max(1, int(contracts)),
            "stop_ticks": int(stop_ticks) if stop_ticks else None, "target_ticks": int(target_ticks) if target_ticks else None,
            "note": note, "confidence": confidence,
        }
        return self.pending

    def flatten(self) -> bool:
        if self.pending is not None and self.position is None:
            self.pending = None
            return True
        if self.position is None:
            return False
        self.pending_exit = "flatten"
        return True

    def modify(self, stop: float | None = None, target: float | None = None) -> bool:
        if self.position is None:
            return False
        if stop is not None:
            self.position.stop = round_to_tick(float(stop), self.spec)
        if target is not None:
            self.position.target = round_to_tick(float(target), self.spec)
        return True

    # -- tape ------------------------------------------------------------------

    def on_trade(self, ts: int, price: float) -> list[dict]:
        """Feed one print. Returns the events it produced: `fill` (entry),
        `exit` (closed trade), each as a dict with a `kind`."""
        out: list[dict] = []
        self.last_price = price
        if self.pending is not None:
            o = self.pending
            self.pending = None
            fill = apply_slippage(price, o["direction"], self.slippage_ticks, self.spec, entering=True)
            sign = 1 if o["direction"] == "long" else -1
            stop = round_to_tick(fill - sign * o["stop_ticks"] * self.spec.tick_size, self.spec) if o["stop_ticks"] else None
            target = round_to_tick(fill + sign * o["target_ticks"] * self.spec.tick_size, self.spec) if o["target_ticks"] else None
            self.position = OpenPosition(
                id=secrets.token_hex(6), direction=o["direction"], contracts=o["contracts"], entry_ts=ts,
                entry_price=fill, stop=stop, target=target, note=o["note"], confidence=o["confidence"],
                stop_ticks=o["stop_ticks"], target_ticks=o["target_ticks"],
            )
            out.append({"kind": "fill", "position": self.position.to_dict(price, self.spec)})
            return out  # the entry print never also exits
        pos = self.position
        if pos is None:
            return out
        if self.pending_exit is not None:
            self.pending_exit = None
            exit_px = apply_slippage(price, pos.direction, self.slippage_ticks, self.spec, entering=False)
            out.append(self._close(ts, exit_px, "flatten"))
            return out
        long = pos.direction == "long"
        stop_hit = pos.stop is not None and ((long and price <= pos.stop) or (not long and price >= pos.stop))
        if stop_hit:
            exit_px = apply_slippage(pos.stop, pos.direction, self.slippage_ticks, self.spec, entering=False)
            out.append(self._close(ts, exit_px, "stop"))
            return out
        target_hit = pos.target is not None and ((long and price > pos.target) or (not long and price < pos.target))
        if target_hit:
            out.append(self._close(ts, pos.target, "target"))
        return out

    def _close(self, ts: int, exit_px: float, reason: str) -> dict:
        pos = self.position
        self.position = None
        pnl = net_pnl_usd(pos.entry_price, exit_px, pos.direction, pos.contracts, self.spec)
        trade = {
            "id": pos.id, "direction": pos.direction, "contracts": pos.contracts, "qty": pos.contracts,
            "entryTime": pos.entry_ts // NS, "entryTs": pos.entry_ts, "entryPrice": pos.entry_price,
            "exitTime": ts // NS, "exitTs": ts, "exitPrice": exit_px, "stop": pos.stop, "target": pos.target,
            "stopTicks": pos.stop_ticks, "targetTicks": pos.target_ticks,
            "pnl": round(pnl, 2), "pnlUsd": round(pnl, 2), "r": r_multiple(pos.entry_price, exit_px, pos.stop, pos.direction),
            "reason": reason, "exitReason": reason, "note": pos.note, "confidence": pos.confidence, "source": "teaching",
        }
        self.trades.append(trade)
        return {"kind": "exit", "trade": trade}
