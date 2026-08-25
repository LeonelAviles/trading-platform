"""Worker: runs one strategy through a NautilusTrader backtest and writes the
resulting trades as JSON.

Invoked as a subprocess by nautilus_runner (isolated from the API process):

    python nautilus_backtest.py <strategy.json> <trades_out.json>

The strategy JSON is the same document the builder UI produces; its condition
vocabulary (see strategy_spec.py) is interpreted at runtime here — no code
generation needed since Nautilus is an in-process library.

Trade record schema (unchanged, shared with the demo path):
  { id, direction, qty, entryTime, entryPrice, exitTime, exitPrice,
    stopPrice, targetPrice, pnl, reason }
"""

import json
import re
import sys
import uuid
from datetime import datetime, timezone

import pandas as pd

import data_store
from condition_engine import Indicators, condition_lookback, eval_condition, session_minutes

from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import AccountType, OmsType, OrderSide
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.objects import Money, Quantity
from nautilus_trader.persistence.wranglers import BarDataWrangler
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from nautilus_trader.trading.strategy import Strategy


# --------------------------------------------------------------------------
# The generic strategy
# --------------------------------------------------------------------------

class ConfigStrategy(Strategy):
    def init_params(self, spec, instrument, bar_type, flow=None, has_flow=False):
        self.spec = spec
        self.instrument = instrument
        self.bar_type = bar_type
        self.is_long = spec["direction"] == "long"
        self.conditions = spec["conditions"]
        self.stop_cfg = spec["stop"]
        self.target_cfg = spec["target"]
        self.sizing = spec.get("sizing", {"type": "percent_equity", "value": 95})
        session = spec.get("session", {"start": "13:30", "end": "19:55"})
        self.sess_start = session_minutes(session["start"])
        self.sess_end = session_minutes(session["end"])
        self.starting_cash = 100_000.0

        lookback = max([condition_lookback(c) for c in self.conditions] + [2])
        if self.stop_cfg["type"] == "atr":
            lookback = max(lookback, int(self.stop_cfg.get("period", 14)) + 2)
        self.ind = Indicators(lookback)
        self.min_bars = lookback
        # {bar open unix seconds: (volume, delta)} — see run().
        self.flow = flow or {}
        self.has_flow = has_flow

        # position state
        self.in_pos = False
        self.opening = False
        self.closing = False
        self.entry_px = None
        self.entry_ts = None
        self.qty = 0
        self.stop_price = None
        self.target_price = None
        self.pending_reason = None
        self.trades = []

    def on_start(self):
        self.subscribe_bars(self.bar_type)

    def _in_session(self, ts_ns):
        dt = datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc)
        minutes = dt.hour * 60 + dt.minute
        return self.sess_start <= minutes < self.sess_end

    def _compute_levels(self, c):
        st, tg = self.stop_cfg, self.target_cfg
        sign = -1 if self.is_long else 1
        if st["type"] == "percent":
            stop = c * (1 + sign * float(st["value"]) / 100.0)
        elif st["type"] == "fixed_points":
            stop = c + sign * float(st["value"])
        else:  # atr
            atr = self.ind.atr(int(st.get("period", 14)))
            stop = c + sign * (atr or 0) * float(st.get("mult", 1.5))
        risk = abs(c - stop)
        tsign = 1 if self.is_long else -1
        if tg["type"] == "rr":
            target = c + tsign * risk * float(tg["value"])
        elif tg["type"] == "percent":
            target = c * (1 + tsign * float(tg["value"]) / 100.0)
        else:
            target = c + tsign * float(tg["value"])
        return stop, target

    def _size(self, c):
        if self.sizing["type"] == "fixed_qty":
            return max(1, int(self.sizing["value"]))
        return max(1, int(self.starting_cash * float(self.sizing["value"]) / 100.0 / c))

    def _submit(self, side, qty):
        self.submit_order(self.order_factory.market(self.instrument.id, side, Quantity.from_int(qty)))

    def on_bar(self, bar):
        o, h, l, c = float(bar.open), float(bar.high), float(bar.low), float(bar.close)
        ts = bar.ts_event
        # bar.volume is the 1e9 fill-guarantee placeholder, so real size and
        # delta come from the flow map instead. None (not 0) when this symbol
        # has no side data, which is what stops flow conditions firing blind.
        v, d = self.flow.get(int(ts / 1e9), (None, None)) if self.has_flow else (None, None)
        self.ind.update(o, h, l, c, v, d)

        if self.in_pos and not self.closing:
            reason = None
            if self.is_long:
                if l <= self.stop_price:
                    reason = "stop"
                elif h >= self.target_price:
                    reason = "target"
            else:
                if h >= self.stop_price:
                    reason = "stop"
                elif l <= self.target_price:
                    reason = "target"
            if reason is None and not self._in_session(ts):
                reason = "session_end"
            if reason:
                self.pending_reason = reason
                self.closing = True
                self._submit(OrderSide.SELL if self.is_long else OrderSide.BUY, self.qty)
            return

        if self.in_pos or self.opening or self.closing:
            return
        if self.ind.count < self.min_bars or not self._in_session(ts):
            return
        if self.stop_cfg["type"] == "atr" and self.ind.atr(int(self.stop_cfg.get("period", 14))) is None:
            return
        if all(eval_condition(cond, self.ind) for cond in self.conditions):
            self.stop_price, self.target_price = self._compute_levels(c)
            self.qty = self._size(c)
            self.opening = True
            self._submit(OrderSide.BUY if self.is_long else OrderSide.SELL, self.qty)

    def on_order_filled(self, fill):
        if self.opening:
            # Keep the intended size (self.qty) rather than fill.last_qty, so
            # the exit order closes exactly the position under NETTING.
            self.entry_px = float(fill.last_px)
            self.entry_ts = fill.ts_event
            self.opening = False
            self.in_pos = True
        elif self.closing:
            exit_px = float(fill.last_px)
            sign = 1 if self.is_long else -1
            self.trades.append({
                "id": str(uuid.uuid4()),
                "direction": self.spec["direction"],
                "qty": self.qty,
                "entryTime": int(self.entry_ts / 1e9),
                "entryPrice": round(self.entry_px, 4),
                "exitTime": int(fill.ts_event / 1e9),
                "exitPrice": round(exit_px, 4),
                "stopPrice": round(self.stop_price, 4),
                "targetPrice": round(self.target_price, 4),
                "reason": self.pending_reason,
                "pnl": round(sign * (exit_px - self.entry_px) * self.qty, 2),
            })
            self.in_pos = False
            self.closing = False
            self.entry_px = self.entry_ts = None

    def on_stop(self):
        # Close an open position at the last bar so the trade is recorded.
        if self.in_pos and not self.closing and self.ind.closes:
            last_c = self.ind.closes[-1]
            sign = 1 if self.is_long else -1
            self.trades.append({
                "id": str(uuid.uuid4()),
                "direction": self.spec["direction"],
                "qty": self.qty,
                "entryTime": int(self.entry_ts / 1e9),
                "entryPrice": round(self.entry_px, 4),
                "exitTime": int(self.last_ts / 1e9),
                "exitPrice": round(last_c, 4),
                "stopPrice": round(self.stop_price, 4),
                "targetPrice": round(self.target_price, 4),
                "reason": "end_of_data",
                "pnl": round(sign * (last_c - self.entry_px) * self.qty, 2),
            })


_BAR_UNITS = {"min": "MINUTE", "t": "MINUTE", "h": "HOUR", "d": "DAY"}


def _bar_type_str(instrument_id, interval: str) -> str:
    """Map one of our pandas-offset-alias interval strings (1min, 5min,
    15min, 30min, 1h, 4h, 1D — see CandlestickPage.jsx's timeframe buttons)
    to a real Nautilus BarType spec, instead of hardcoding 1-MINUTE
    regardless of the strategy's actual interval."""
    m = re.fullmatch(r"(\d*)([a-zA-Z]+)", interval.strip())
    unit = m and _BAR_UNITS.get(m.group(2).lower())
    if not m or unit is None:
        raise ValueError(f"unsupported interval {interval!r} — expected e.g. '1min', '15min', '1h', '1D'")
    step = int(m.group(1)) if m.group(1) else 1
    return f"{instrument_id}-{step}-{unit}-LAST-EXTERNAL"


def run(strategy_path, out_path):
    spec = json.loads(open(strategy_path, encoding="utf-8").read())
    symbol = spec["symbol"]
    interval = spec.get("interval", "1min")

    df = data_store.get_bars(symbol, interval).copy()
    df.columns = [c.lower() for c in df.columns]
    if df.empty:
        raise RuntimeError(f"no bars for {symbol} at {interval}")
    # Real traded size and MBO order flow, keyed by bar open time (BarDataWrangler
    # uses the frame's index as ts_event verbatim). Captured BEFORE volume is
    # overwritten below, and handed to the strategy out-of-band because a
    # Nautilus Bar carries no delta field at all.
    flow = {
        int(ts.timestamp()): (float(v), float(d))
        for ts, v, d in zip(df.index, df["volume"], df.get("delta", pd.Series(0.0, index=df.index)))
    }
    has_flow = "delta" in df.columns
    # The simulated venue caps a market fill at the bar's volume; this tool
    # models decision logic, not liquidity, so give bars unbounded volume so
    # orders always fill in full (partial fills would also corrupt netting).
    df["volume"] = 1_000_000_000.0

    engine = BacktestEngine(config=BacktestEngineConfig(trader_id="BACKTEST-001", logging=LoggingConfig(log_level="ERROR")))
    engine.add_venue(
        venue=Venue("SIM"), oms_type=OmsType.NETTING, account_type=AccountType.MARGIN,
        # 100k — mirrored as nautilus_runner.STARTING_EQUITY, which is what
        # weekly/percentage performance is measured against. Keep them in sync.
        base_currency=USD, starting_balances=[Money(100_000, USD)],
    )
    instrument = TestInstrumentProvider.equity(symbol=symbol, venue="SIM")
    engine.add_instrument(instrument)
    bar_type = BarType.from_str(_bar_type_str(instrument.id, interval))
    engine.add_data(BarDataWrangler(bar_type, instrument).process(df))

    strat = ConfigStrategy()
    strat.init_params(spec, instrument, bar_type, flow=flow, has_flow=has_flow)
    # last bar timestamp, for on_stop's forced close
    strat.last_ts = int(df.index[-1].timestamp() * 1e9)
    engine.add_strategy(strat)
    engine.run()

    trades = strat.trades
    engine.dispose()

    wins = [t for t in trades if t["pnl"] > 0]
    summary = {
        "trades": len(trades),
        "wins": len(wins),
        "winRate": round(len(wins) / len(trades) * 100, 1) if trades else 0,
        "totalPnl": round(sum(t["pnl"] for t in trades), 2),
    }
    json.dump({"trades": trades, "summary": summary}, open(out_path, "w", encoding="utf-8"))


if __name__ == "__main__":
    run(sys.argv[1], sys.argv[2])
