"""The strategy condition/indicator vocabulary (see strategy_spec.py),
evaluated per bar. Pure Python, no NautilusTrader import — split out of
nautilus_backtest.py so it can be shared with agent_tools.py without pulling
the (subprocess-only, deliberately isolated from the API process) Nautilus
BacktestEngine into the live API process.

Both nautilus_backtest.py (the real backtest) and agent_tools.py (trade
enrichment / near-miss detection) import this, so "what the engine actually
saw at entry" and "what the agent analyzes" are guaranteed to be the exact
same computation, not a re-implementation that could drift.
"""

from collections import deque


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


def condition_lookback(cond):
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


def eval_condition(cond, ind: Indicators):
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


def session_minutes(hhmm):
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)
