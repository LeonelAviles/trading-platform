"""Structure primitives: swings, opening range, initial balance, session and prior-day levels, candles."""

from __future__ import annotations

from engine.primitives.base import Param, Primitive, register


@register
class SwingHigh(Primitive):
    """Most recent confirmed swing high: a bar whose high exceeds the `n` bars on each side."""
    name = "swing_high"
    params = {"n": Param("int", 3, "bars on each side")}
    output, tf_capable, mirror, mirror_name = "price", True, "price", "swing_low"

    def lookback_bars(self):
        return 2 * self.p["n"] + 1

    def value(self, ctx):
        b = list(self.series(ctx).bars)
        n = self.p["n"]
        for i in range(len(b) - 1 - n, n - 1, -1):
            h = b[i].high
            if all(b[j].high < h for j in range(i - n, i)) and all(b[j].high <= h for j in range(i + 1, i + n + 1)):
                return h
        return None


@register
class SwingLow(Primitive):
    """Most recent confirmed swing low: a bar whose low is below the `n` bars on each side."""
    name = "swing_low"
    params = {"n": Param("int", 3, "bars on each side")}
    output, tf_capable, mirror, mirror_name = "price", True, "price", "swing_high"

    def lookback_bars(self):
        return 2 * self.p["n"] + 1

    def value(self, ctx):
        b = list(self.series(ctx).bars)
        n = self.p["n"]
        for i in range(len(b) - 1 - n, n - 1, -1):
            lo = b[i].low
            if all(b[j].low > lo for j in range(i - n, i)) and all(b[j].low >= lo for j in range(i + 1, i + n + 1)):
                return lo
        return None


class _Range(Primitive):
    params = {"minutes": Param("int", 15, "minutes after the RTH open")}
    output, mirror = "level", "price"
    which = "high"

    def value(self, ctx):
        s = ctx.session
        if s is None or not s.range_complete(self.p["minutes"], ctx.now_ns):
            return None
        hi, lo = s.range_levels(self.p["minutes"])
        return hi if self.which == "high" else lo


@register
class OpeningRangeHigh(_Range):
    """High of the first `minutes` of RTH; None until that range has closed."""
    name, which, mirror_name = "opening_range_high", "high", "opening_range_low"


@register
class OpeningRangeLow(_Range):
    """Low of the first `minutes` of RTH; None until that range has closed."""
    name, which, mirror_name = "opening_range_low", "low", "opening_range_high"


@register
class InitialBalanceHigh(_Range):
    """High of the first 60 minutes of RTH (initial balance)."""
    name, which, mirror_name = "initial_balance_high", "high", "initial_balance_low"
    params = {"minutes": Param("int", 60, "minutes after the RTH open")}


@register
class InitialBalanceLow(_Range):
    """Low of the first 60 minutes of RTH (initial balance)."""
    name, which, mirror_name = "initial_balance_low", "low", "initial_balance_high"
    params = {"minutes": Param("int", 60, "minutes after the RTH open")}


@register
class SessionHigh(Primitive):
    """Highest high of the current RTH session so far."""
    name = "session_high"
    output, mirror, mirror_name = "level", "price", "session_low"

    def value(self, ctx):
        return ctx.session.high if ctx.session else None


@register
class SessionLow(Primitive):
    """Lowest low of the current RTH session so far."""
    name = "session_low"
    output, mirror, mirror_name = "level", "price", "session_high"

    def value(self, ctx):
        return ctx.session.low if ctx.session else None


@register
class PriorDayHigh(Primitive):
    """RTH high of the previous session."""
    name = "prior_day_high"
    output, mirror, mirror_name = "level", "price", "prior_day_low"

    def value(self, ctx):
        return ctx.prior.high if ctx.prior else None


@register
class PriorDayLow(Primitive):
    """RTH low of the previous session."""
    name = "prior_day_low"
    output, mirror, mirror_name = "level", "price", "prior_day_high"

    def value(self, ctx):
        return ctx.prior.low if ctx.prior else None


@register
class PriorDayClose(Primitive):
    """RTH close of the previous session."""
    name = "prior_day_close"
    output, mirror = "level", "price"

    def value(self, ctx):
        return ctx.prior.close if ctx.prior else None


@register
class GapPoints(Primitive):
    """Today's RTH open minus the prior session's RTH close (points, signed)."""
    name = "gap_points"
    mirror = "signed"

    def value(self, ctx):
        if ctx.session is None or ctx.session.open is None or ctx.prior is None or ctx.prior.close is None:
            return None
        return ctx.session.open - ctx.prior.close


@register
class Consecutive(Primitive):
    """1 when the last `count` closed bars are all `color` (green: close > open, red: close < open), else 0."""
    name = "consecutive"
    params = {"count": Param("int", 3, "bars"), "color": Param("str", "green", "green | red", choices=("green", "red"))}
    output, tf_capable = "bool", True

    def lookback_bars(self):
        return self.p["count"]

    def value(self, ctx):
        b = list(self.series(ctx).bars)[-self.p["count"]:]
        if len(b) < self.p["count"]:
            return None
        if self.p["color"] == "green":
            return all(x.close > x.open for x in b)
        return all(x.close < x.open for x in b)


@register
class CandlePattern(Primitive):
    """1 when the last closed bar forms `pattern`: engulfing (body covers the prior body, opposite colour),
    pin (wick ≥ 2× body on one side), inside (range inside the prior bar's range)."""
    name = "candle_pattern"
    params = {"pattern": Param("str", "engulfing", "engulfing | pin | inside", choices=("engulfing", "pin", "inside"))}
    output, tf_capable = "bool", True

    def lookback_bars(self):
        return 2

    def value(self, ctx):
        b = list(self.series(ctx).bars)
        if len(b) < 2:
            return None
        cur, prev = b[-1], b[-2]
        pat = self.p["pattern"]
        if pat == "engulfing":
            up = cur.close > cur.open and prev.close < prev.open and cur.open <= prev.close and cur.close >= prev.open
            dn = cur.close < cur.open and prev.close > prev.open and cur.open >= prev.close and cur.close <= prev.open
            return up or dn
        if pat == "inside":
            return cur.high <= prev.high and cur.low >= prev.low
        body = abs(cur.close - cur.open)
        upper = cur.high - max(cur.open, cur.close)
        lower = min(cur.open, cur.close) - cur.low
        return body > 0 and (upper >= 2 * body or lower >= 2 * body) or (body == 0 and (upper > 0 or lower > 0))
