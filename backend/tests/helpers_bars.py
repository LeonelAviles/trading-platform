"""Hand-built bar/trade streams for primitive and expression tests."""

from datetime import date

from engine.features import BarRec, FeatureContext, Trade
from engine.session import NS, et_to_ns

D = date(2026, 7, 15)


def make_ctx(primary="1min", context=None, **kw) -> FeatureContext:
    return FeatureContext(primary, context or [], tick_size=0.25, **kw)


def bar(i, o, h=None, l=None, c=None, v=100, delta=0.0, d=D, start="09:30", minutes=1):
    """Primary bar i (0-based from `start` ET) with OHLC; defaults derive from open/close."""
    c = c if c is not None else o
    h = h if h is not None else max(o, c)
    l = l if l is not None else min(o, c)
    ts_open = et_to_ns(d, start) + i * minutes * 60 * NS
    buy = (v + delta) / 2
    return BarRec(float(o), float(h), float(l), float(c), float(v), float(delta), float(buy), float(v - buy), ts_open, ts_open + minutes * 60 * NS)


def feed_closes(ctx, closes, start="09:30", d=D, v=100, deltas=None):
    prev = closes[0]
    for i, c in enumerate(closes):
        dl = deltas[i] if deltas else 0.0
        ctx.on_bar(bar(i, prev, max(prev, c), min(prev, c), c, v=v, delta=dl, d=d, start=start))
        prev = c
    return ctx


def trade(ts_offset_s, price, size, side, d=D, start="09:30"):
    return Trade(et_to_ns(d, start) + int(ts_offset_s * NS), float(price), int(size), side)
