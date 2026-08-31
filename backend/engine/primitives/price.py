"""Price primitives: fields, moving averages, oscillators, volatility, bands."""

from __future__ import annotations

from engine.primitives.base import Param, Primitive, register


def _closes(inst, ctx):
    return inst.series(ctx).closes()


class _Field(Primitive):
    output = "price"
    tf_capable = True
    mirror = "price"
    field = "close"

    def value(self, ctx):
        b = self.series(ctx).last()
        return None if b is None else getattr(b, self.field)


@register
class Open(_Field):
    """Open of the last closed bar (on `tf`)."""
    name, field = "open", "open"


@register
class High(_Field):
    """High of the last closed bar (on `tf`)."""
    name, field = "high", "high"


@register
class Low(_Field):
    """Low of the last closed bar (on `tf`)."""
    name, field = "low", "low"


@register
class Close(_Field):
    """Close of the last closed bar (on `tf`)."""
    name, field = "close", "close"


@register
class Volume(_Field):
    """Traded contracts in the last closed bar (on `tf`)."""
    name, field, output, mirror = "volume", "volume", "number", "none"


@register
class Delta(_Field):
    """Signed flow of the last closed bar: aggressive buys − aggressive sells (contracts)."""
    name, field, output, mirror = "delta", "delta", "number", "signed"


@register
class SMA(Primitive):
    """Simple moving average of closes over `period` bars."""
    name = "sma"
    params = {"period": Param("int", 20, "bars")}
    output, tf_capable, mirror = "price", True, "price"

    def lookback_bars(self):
        return self.p["period"]

    def value(self, ctx):
        c = _closes(self, ctx)
        n = self.p["period"]
        return sum(c[-n:]) / n if len(c) >= n else None


@register
class EMA(Primitive):
    """Exponential moving average of closes (`period`), seeded with the SMA."""
    name = "ema"
    params = {"period": Param("int", 20, "bars")}
    output, tf_capable, mirror = "price", True, "price"

    def __init__(self, params=None, tf=None):
        super().__init__(params, tf)
        self._v = None
        self._seed = []

    def lookback_bars(self):
        return self.p["period"]

    def on_bar(self, ctx, bar):
        n = self.p["period"]
        if self._v is None:
            self._seed.append(bar.close)
            if len(self._seed) == n:
                self._v = sum(self._seed) / n
        else:
            k = 2 / (n + 1)
            self._v = bar.close * k + self._v * (1 - k)

    def value(self, ctx):
        return self._v


@register
class VWAP(Primitive):
    """Session VWAP (RTH, typical price × volume), from the New York session open."""
    name = "vwap"
    output, mirror = "price", "price"

    def value(self, ctx):
        return ctx.session.vwap() if ctx.session else None


@register
class RSI(Primitive):
    """Wilder RSI of closes over `period` bars (0–100)."""
    name = "rsi"
    params = {"period": Param("int", 14, "bars")}
    tf_capable, mirror = True, "rsi"

    def __init__(self, params=None, tf=None):
        super().__init__(params, tf)
        self._prev = None
        self._g = self._l = None
        self._seed = []
        self._v = None

    def lookback_bars(self):
        return self.p["period"] + 1

    def on_bar(self, ctx, bar):
        n = self.p["period"]
        if self._prev is None:
            self._prev = bar.close
            return
        ch = bar.close - self._prev
        self._prev = bar.close
        g, l = max(ch, 0.0), max(-ch, 0.0)
        if self._g is None:
            self._seed.append((g, l))
            if len(self._seed) == n:
                self._g = sum(x for x, _ in self._seed) / n
                self._l = sum(x for _, x in self._seed) / n
        else:
            self._g = (self._g * (n - 1) + g) / n
            self._l = (self._l * (n - 1) + l) / n
        if self._g is not None:
            self._v = 100.0 if self._l == 0 else 100.0 - 100.0 / (1.0 + self._g / self._l)

    def value(self, ctx):
        return self._v


@register
class ATR(Primitive):
    """Wilder average true range over `period` bars (points)."""
    name = "atr"
    params = {"period": Param("int", 14, "bars")}
    tf_capable = True

    def __init__(self, params=None, tf=None):
        super().__init__(params, tf)
        self._prev_close = None
        self._seed = []
        self._v = None

    def lookback_bars(self):
        return self.p["period"] + 1

    def on_bar(self, ctx, bar):
        n = self.p["period"]
        if self._prev_close is None:
            self._prev_close = bar.close
            return
        tr = max(bar.high - bar.low, abs(bar.high - self._prev_close), abs(bar.low - self._prev_close))
        self._prev_close = bar.close
        if self._v is None:
            self._seed.append(tr)
            if len(self._seed) == n:
                self._v = sum(self._seed) / n
        else:
            self._v = (self._v * (n - 1) + tr) / n

    def value(self, ctx):
        return self._v


@register
class ADX(Primitive):
    """Wilder ADX over `period` bars (trend strength, 0–100)."""
    name = "adx"
    params = {"period": Param("int", 14, "bars")}
    tf_capable = True

    def __init__(self, params=None, tf=None):
        super().__init__(params, tf)
        self._prev = None
        self._tr = self._pdm = self._mdm = None
        self._dx_seed = []
        self._adx = None
        self._n = 0
        self._seed = []

    def lookback_bars(self):
        return 2 * self.p["period"] + 1

    def on_bar(self, ctx, bar):
        n = self.p["period"]
        if self._prev is None:
            self._prev = bar
            return
        p = self._prev
        tr = max(bar.high - bar.low, abs(bar.high - p.close), abs(bar.low - p.close))
        up, dn = bar.high - p.high, p.low - bar.low
        pdm = up if up > dn and up > 0 else 0.0
        mdm = dn if dn > up and dn > 0 else 0.0
        self._prev = bar
        if self._tr is None:
            self._seed.append((tr, pdm, mdm))
            if len(self._seed) == n:
                self._tr = sum(x[0] for x in self._seed)
                self._pdm = sum(x[1] for x in self._seed)
                self._mdm = sum(x[2] for x in self._seed)
            else:
                return
        else:
            self._tr = self._tr - self._tr / n + tr
            self._pdm = self._pdm - self._pdm / n + pdm
            self._mdm = self._mdm - self._mdm / n + mdm
        if self._tr == 0:
            return
        pdi, mdi = 100 * self._pdm / self._tr, 100 * self._mdm / self._tr
        dx = 100 * abs(pdi - mdi) / (pdi + mdi) if (pdi + mdi) else 0.0
        if self._adx is None:
            self._dx_seed.append(dx)
            if len(self._dx_seed) == n:
                self._adx = sum(self._dx_seed) / n
        else:
            self._adx = (self._adx * (n - 1) + dx) / n

    def value(self, ctx):
        return self._adx


class _Bollinger(Primitive):
    params = {"period": Param("int", 20, "bars"), "stdev": Param("float", 2.0, "band width in standard deviations")}
    output, tf_capable, mirror = "price", True, "price"
    sign = 1

    def lookback_bars(self):
        return self.p["period"]

    def value(self, ctx):
        c = _closes(self, ctx)
        n = self.p["period"]
        if len(c) < n:
            return None
        w = c[-n:]
        m = sum(w) / n
        sd = (sum((x - m) ** 2 for x in w) / n) ** 0.5
        return m + self.sign * self.p["stdev"] * sd


@register
class BollingerUpper(_Bollinger):
    """Bollinger upper band: SMA(period) + stdev × σ."""
    name, sign, mirror_name = "bollinger_upper", 1, "bollinger_lower"


@register
class BollingerLower(_Bollinger):
    """Bollinger lower band: SMA(period) − stdev × σ."""
    name, sign, mirror_name = "bollinger_lower", -1, "bollinger_upper"


@register
class Highest(Primitive):
    """Highest high of the previous `n` closed bars (excluding the current one when `exclude_current`)."""
    name = "highest"
    params = {"n": Param("int", 20, "bars"), "exclude_current": Param("bool", True, "leave the last closed bar out")}
    output, tf_capable, mirror, mirror_name = "price", True, "price", "lowest"

    def lookback_bars(self):
        return self.p["n"] + 1

    def value(self, ctx):
        bars = self.series(ctx).bars
        b = list(bars)[:-1] if self.p["exclude_current"] else list(bars)
        if len(b) < self.p["n"]:
            return None
        return max(x.high for x in b[-self.p["n"]:])


@register
class Lowest(Primitive):
    """Lowest low of the previous `n` closed bars."""
    name = "lowest"
    params = {"n": Param("int", 20, "bars"), "exclude_current": Param("bool", True, "leave the last closed bar out")}
    output, tf_capable, mirror, mirror_name = "price", True, "price", "highest"

    def lookback_bars(self):
        return self.p["n"] + 1

    def value(self, ctx):
        bars = self.series(ctx).bars
        b = list(bars)[:-1] if self.p["exclude_current"] else list(bars)
        if len(b) < self.p["n"]:
            return None
        return min(x.low for x in b[-self.p["n"]:])
