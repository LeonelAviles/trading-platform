"""Order-flow primitives (trade-derived). Footprint-based ones read the closed
primary bars' bid×ask ladders; in bars mode the ladder is a uniform spread of
the bar's buy/sell volume, so imbalance-type primitives are only meaningful
in `ticks`/`l3` mode (they are marked `update_on = "trade"`)."""

from __future__ import annotations

from collections import deque

from engine.primitives.base import Param, Primitive, register
from engine.primitives.profile import value_area


def _deltas(ctx, n):
    b = ctx.bars(None, n)
    return [x.delta for x in b] if len(b) >= n else None


@register
class BarDelta(Primitive):
    """Signed flow of the last closed primary bar (aggressive buys − sells)."""
    name = "bar_delta"
    mirror = "signed"

    def value(self, ctx):
        return ctx.bar.delta if ctx.bar else None


@register
class CVDSession(Primitive):
    """Cumulative delta since the RTH open."""
    name = "cvd_session"
    mirror = "signed"

    def value(self, ctx):
        return ctx.session.cvd if ctx.session else None


@register
class CVDWindow(Primitive):
    """Cumulative delta over the last `n` primary bars."""
    name = "cvd_window"
    params = {"n": Param("int", 20, "bars")}
    mirror = "signed"

    def lookback_bars(self):
        return self.p["n"]

    def value(self, ctx):
        d = _deltas(ctx, self.p["n"])
        return sum(d) if d else None


@register
class CVDSlope(Primitive):
    """Average per-bar delta over the last `n` bars (slope of the CVD line)."""
    name = "cvd_slope"
    params = {"n": Param("int", 5, "bars")}
    mirror = "signed"

    def lookback_bars(self):
        return self.p["n"]

    def value(self, ctx):
        d = _deltas(ctx, self.p["n"])
        return sum(d) / len(d) if d else None


@register
class RelDelta(Primitive):
    """Windowed delta scaled by the mean absolute per-bar delta over `n` bars (unitless, signed)."""
    name = "rel_delta"
    params = {"n": Param("int", 20, "bars")}
    mirror = "signed"

    def lookback_bars(self):
        return self.p["n"]

    def value(self, ctx):
        d = _deltas(ctx, self.p["n"])
        if not d:
            return None
        scale = sum(abs(x) for x in d) / len(d)
        return sum(d) / scale if scale else None


@register
class RelVolume(Primitive):
    """Last bar's volume over the average of the previous `n` bars (1 = average)."""
    name = "rel_volume"
    params = {"n": Param("int", 20, "bars")}
    tf_capable = True

    def lookback_bars(self):
        return self.p["n"] + 1

    def value(self, ctx):
        b = list(self.series(ctx).bars)
        n = self.p["n"]
        if len(b) < n + 1:
            return None
        avg = sum(x.volume for x in b[-n - 1:-1]) / n
        return b[-1].volume / avg if avg else None


@register
class DeltaDivergence(Primitive):
    """Price vs. flow disagreement over `n` bars: +1 bullish (price down, CVD up), −1 bearish (price up, CVD down), 0 none."""
    name = "delta_divergence"
    params = {"n": Param("int", 20, "bars")}
    mirror = "signed"

    def lookback_bars(self):
        return self.p["n"] + 1

    def value(self, ctx):
        b = ctx.bars(None, self.p["n"] + 1)
        if len(b) < self.p["n"] + 1:
            return None
        price_up = b[-1].close > b[0].close
        cvd = sum(x.delta for x in b[1:])
        if price_up and cvd < 0:
            return -1.0
        if not price_up and cvd > 0:
            return 1.0
        return 0.0


def _last_footprint(ctx):
    return ctx.footprints[-1] if ctx.footprints else (None, None)


def _diagonal_imbalances(fp: dict, tick: float, ratio: float, min_vol: int, side: str) -> list[float]:
    """Prices with a diagonal imbalance. `ask` (buying): ask volume at P vs bid
    volume at P − tick; `bid` (selling): bid volume at P vs ask volume at P + tick."""
    out = []
    for p, (bid, ask) in fp.items():
        if side == "ask":
            other = fp.get(round(p - tick, 6))
            if other is None:
                continue
            a, b = ask, other[0]
        else:
            other = fp.get(round(p + tick, 6))
            if other is None:
                continue
            a, b = bid, other[1]
        if a >= min_vol and a >= ratio * max(b, 1e-9):
            out.append(p)
    return sorted(out)


@register
class FootprintImbalance(Primitive):
    """Number of diagonal imbalances of `ratio`× (min `min_volume` contracts) on `side` in the last closed bar."""
    name = "footprint_imbalance"
    params = {"side": Param("str", "ask", "ask (buying) | bid (selling)", choices=("ask", "bid")),
              "ratio": Param("float", 3.0, "imbalance ratio"), "min_volume": Param("int", 5, "contracts")}
    update_on = "trade"

    def value(self, ctx):
        bar, fp = _last_footprint(ctx)
        if fp is None:
            return None
        return float(len(_diagonal_imbalances(fp, ctx.tick, self.p["ratio"], self.p["min_volume"], self.p["side"])))


@register
class StackedImbalances(Primitive):
    """1 when at least `count` consecutive price levels carry a diagonal imbalance on `side` in the last closed bar."""
    name = "stacked_imbalances"
    params = {"side": Param("str", "ask", "ask | bid", choices=("ask", "bid")), "count": Param("int", 3, "consecutive levels"),
              "ratio": Param("float", 3.0, "imbalance ratio"), "min_volume": Param("int", 5, "contracts")}
    output, update_on = "bool", "trade"

    def value(self, ctx):
        bar, fp = _last_footprint(ctx)
        if fp is None:
            return None
        prices = _diagonal_imbalances(fp, ctx.tick, self.p["ratio"], self.p["min_volume"], self.p["side"])
        run = best = 0
        prev = None
        for p in prices:
            run = run + 1 if prev is not None and abs(p - prev - ctx.tick) < 1e-9 else 1
            best = max(best, run)
            prev = p
        return best >= self.p["count"]


@register
class Absorption(Primitive):
    """1 when the last closed bar traded at least `min_volume` contracts aggressively into `side`
    (bid: sellers hitting bids; ask: buyers lifting offers) while the bar's range stayed within
    `max_range_ticks` — heavy volume that failed to move price."""
    name = "absorption"
    params = {"side": Param("str", "bid", "bid | ask", choices=("bid", "ask")), "min_volume": Param("int", 500, "contracts"),
              "max_range_ticks": Param("int", 3, "bar range cap in ticks")}
    output, update_on = "bool", "trade"

    def value(self, ctx):
        b = ctx.bar
        if b is None:
            return None
        vol = b.sell_vol if self.p["side"] == "bid" else b.buy_vol
        rng = (b.high - b.low) / ctx.tick
        return vol >= self.p["min_volume"] and rng <= self.p["max_range_ticks"]


@register
class Exhaustion(Primitive):
    """1 when the last closed bar's extreme shows a collapsing tail on `side`: the top (ask) or bottom (bid)
    two levels of the footprint hold ≤ `tail_max` contracts each while the bar traded ≥ `min_volume`."""
    name = "exhaustion"
    params = {"side": Param("str", "ask", "ask (at the high) | bid (at the low)", choices=("ask", "bid")),
              "tail_max": Param("int", 3, "contracts per tail level"), "min_volume": Param("int", 100, "bar volume")}
    output, update_on = "bool", "trade"

    def value(self, ctx):
        bar, fp = _last_footprint(ctx)
        if fp is None or bar is None or bar.volume < self.p["min_volume"] or len(fp) < 3:
            return None if fp is None else False
        prices = sorted(fp)
        tail = prices[-2:] if self.p["side"] == "ask" else prices[:2]
        idx = 1 if self.p["side"] == "ask" else 0
        return all(fp[p][idx] <= self.p["tail_max"] for p in tail)


@register
class POCMigration(Primitive):
    """Bar-POC drift over the last `n` closed bars in ticks (signed: + rising)."""
    name = "poc_migration"
    params = {"n": Param("int", 5, "bars")}
    mirror, update_on = "signed", "trade"

    def value(self, ctx):
        fps = list(ctx.footprints)[-self.p["n"]:]
        if len(fps) < self.p["n"]:
            return None
        pocs = []
        for _, fp in fps:
            if not fp:
                return None
            pocs.append(max(fp.items(), key=lambda kv: kv[1][0] + kv[1][1])[0])
        return (pocs[-1] - pocs[0]) / ctx.tick


@register
class LargePrint(Primitive):
    """Signed size of the largest single print of at least `min_size` contracts within the last `within_bars`
    primary bars (+ buyer, − seller); 0 when none."""
    name = "large_print"
    params = {"min_size": Param("int", 50, "contracts"), "within_bars": Param("int", 3, "bars")}
    mirror, update_on = "signed", "trade"

    def value(self, ctx):
        if not ctx.trades or ctx.bar is None:
            return None
        from engine.session import NS
        from engine.features import TF_MINUTES

        span = self.p["within_bars"] * TF_MINUTES[ctx.primary_tf] * 60 * NS
        lo = ctx.now_ns - span
        best = 0
        for t in reversed(ctx.trades):
            if t.ts < lo:
                break
            if t.size >= self.p["min_size"] and t.size > abs(best):
                best = t.size if t.side == "B" else -t.size
        return float(best)
