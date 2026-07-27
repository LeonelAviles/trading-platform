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
import sys
import uuid
from collections import deque
from datetime import datetime, timezone

import data_store

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
# Indicator/condition state — the closed rule vocabulary, evaluated per bar.
# --------------------------------------------------------------------------

class Indicators:
    def __init__(self, max_lookback: int):
        cap = max(max_lookback + 2, 5)
        self.opens = deque(maxlen=cap)
        self.highs = deque(maxlen=cap)
        self.lows = deque(maxlen=cap)
        self.closes = deque(maxlen=cap)
        self._rsi = {}   # period -> {avg_gain, avg_loss, seed, value}
        self._atr = {}   # period -> {value, seed}
        self.count = 0

    def update(self, o, h, l, c):
        self.opens.append(o)
        self.highs.append(h)
        self.lows.append(l)
        self.closes.append(c)
        self.count += 1
        self._update_rsi()
        self._update_atr()

    def sma(self, n, offset=0):
        """Mean of the n closes ending `offset` bars back (offset=0 = latest)."""
        cl = list(self.closes)
        end = len(cl) - offset
        if end < n:
            return None
        return sum(cl[end - n:end]) / n

    def _update_rsi(self):
        if len(self.closes) < 2:
            return
        change = self.closes[-1] - self.closes[-2]
        gain, loss = max(change, 0.0), max(-change, 0.0)
        for period, st in self._rsi.items():
            if st["avg_gain"] is None:
                st["seed"].append((gain, loss))
                if len(st["seed"]) == period:
                    st["avg_gain"] = sum(g for g, _ in st["seed"]) / period
                    st["avg_loss"] = sum(x for _, x in st["seed"]) / period
            else:
                st["avg_gain"] = (st["avg_gain"] * (period - 1) + gain) / period
                st["avg_loss"] = (st["avg_loss"] * (period - 1) + loss) / period
            if st["avg_gain"] is not None:
                st["value"] = 100.0 if st["avg_loss"] == 0 else 100.0 - 100.0 / (1.0 + st["avg_gain"] / st["avg_loss"])

    def rsi(self, period):
        st = self._rsi.setdefault(period, {"avg_gain": None, "avg_loss": None, "seed": [], "value": None})
        return st["value"]

    def _update_atr(self):
        if len(self.closes) < 2:
            return
        prev_c = self.closes[-2]
        h, l = self.highs[-1], self.lows[-1]
        tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        for period, st in self._atr.items():
            if st["value"] is None:
                st["seed"].append(tr)
                if len(st["seed"]) == period:
                    st["value"] = sum(st["seed"]) / period
            else:
                st["value"] = (st["value"] * (period - 1) + tr) / period

    def atr(self, period):
        st = self._atr.setdefault(period, {"value": None, "seed": []})
        return st["value"]


def _condition_lookback(cond):
    t = cond["type"]
    if t in ("sma_cross_above", "sma_cross_below"):
        return max(int(cond["fast"]), int(cond["slow"])) + 1
    if t in ("rsi_above", "rsi_below"):
        return int(cond["period"]) + 2
    if t in ("breaks_high", "breaks_low"):
        return int(cond["lookback"]) + 1
    if t == "consecutive":
        return int(cond["count"]) + 1
    return 2


def _eval_condition(cond, ind: Indicators):
    t = cond["type"]
    c = ind.closes[-1]
    if t == "price_above":
        return c > float(cond["value"])
    if t == "price_below":
        return c < float(cond["value"])
    if t in ("sma_cross_above", "sma_cross_below"):
        fast, slow = int(cond["fast"]), int(cond["slow"])
        fp, sp = ind.sma(fast, 1), ind.sma(slow, 1)
        fc, sc = ind.sma(fast, 0), ind.sma(slow, 0)
        if None in (fp, sp, fc, sc):
            return False
        return (fp <= sp and fc > sc) if t == "sma_cross_above" else (fp >= sp and fc < sc)
    if t in ("rsi_above", "rsi_below"):
        v = ind.rsi(int(cond["period"]))
        if v is None:
            return False
        return v > float(cond["value"]) if t == "rsi_above" else v < float(cond["value"])
    if t == "breaks_high":
        lb = int(cond["lookback"])
        highs = list(ind.highs)
        if len(highs) <= lb:
            return False
        return c > max(highs[-lb - 1:-1])
    if t == "breaks_low":
        lb = int(cond["lookback"])
        lows = list(ind.lows)
        if len(lows) <= lb:
            return False
        return c < min(lows[-lb - 1:-1])
    if t == "consecutive":
        n, color = int(cond["count"]), cond["color"]
        if len(ind.closes) < n:
            return False
        opens, closes = list(ind.opens)[-n:], list(ind.closes)[-n:]
        if color == "green":
            return all(cl > op for cl, op in zip(closes, opens))
        return all(cl < op for cl, op in zip(closes, opens))
    raise ValueError(f"unknown condition type {t}")


def _session_minutes(hhmm):
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


# --------------------------------------------------------------------------
# The generic strategy
# --------------------------------------------------------------------------

class ConfigStrategy(Strategy):
    def init_params(self, spec, instrument, bar_type):
        self.spec = spec
        self.instrument = instrument
        self.bar_type = bar_type
        self.is_long = spec["direction"] == "long"
        self.conditions = spec["conditions"]
        self.stop_cfg = spec["stop"]
        self.target_cfg = spec["target"]
        self.sizing = spec.get("sizing", {"type": "percent_equity", "value": 95})
        session = spec.get("session", {"start": "13:30", "end": "19:55"})
        self.sess_start = _session_minutes(session["start"])
        self.sess_end = _session_minutes(session["end"])
        self.starting_cash = 100_000.0

        lookback = max([_condition_lookback(c) for c in self.conditions] + [2])
        if self.stop_cfg["type"] == "atr":
            lookback = max(lookback, int(self.stop_cfg.get("period", 14)) + 2)
        self.ind = Indicators(lookback)
        self.min_bars = lookback

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
        self.ind.update(o, h, l, c)
        ts = bar.ts_event

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
        if all(_eval_condition(cond, self.ind) for cond in self.conditions):
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


def run(strategy_path, out_path):
    spec = json.loads(open(strategy_path, encoding="utf-8").read())
    symbol = spec["symbol"]
    interval = spec.get("interval", "1min")

    df = data_store.get_bars(symbol, interval).copy()
    df.columns = [c.lower() for c in df.columns]
    if df.empty:
        raise RuntimeError(f"no bars for {symbol} at {interval}")
    # The simulated venue caps a market fill at the bar's volume; this tool
    # models decision logic, not liquidity, so give bars unbounded volume so
    # orders always fill in full (partial fills would also corrupt netting).
    df["volume"] = 1_000_000_000.0

    engine = BacktestEngine(config=BacktestEngineConfig(trader_id="BACKTEST-001", logging=LoggingConfig(log_level="ERROR")))
    engine.add_venue(
        venue=Venue("SIM"), oms_type=OmsType.NETTING, account_type=AccountType.MARGIN,
        base_currency=USD, starting_balances=[Money(100_000, USD)],
    )
    instrument = TestInstrumentProvider.equity(symbol=symbol, venue="SIM")
    engine.add_instrument(instrument)
    bar_type = BarType.from_str(f"{instrument.id}-1-MINUTE-LAST-EXTERNAL")
    engine.add_data(BarDataWrangler(bar_type, instrument).process(df))

    strat = ConfigStrategy()
    strat.init_params(spec, instrument, bar_type)
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
