"""Book primitives (top-of-book view from the liquidity store in backtests, the live book in replay).
They evaluate to None when no book view has been set."""

from __future__ import annotations

from engine.primitives.base import Param, Primitive, register


@register
class LargeRestingSizeNear(Primitive):
    """Largest resting size on `side` within `within_ticks` of the last price, if ≥ `min_size` (else 0)."""
    name = "large_resting_size_near"
    params = {"side": Param("str", "bid", "bid | ask", choices=("bid", "ask")), "min_size": Param("int", 200, "contracts"),
              "within_ticks": Param("int", 5, "ticks from last")}
    update_on = "book"

    def value(self, ctx):
        levels = ctx.book_bids if self.p["side"] == "bid" else ctx.book_asks
        if not levels or ctx.last_price is None:
            return None
        w = self.p["within_ticks"] * ctx.tick + 1e-9
        best = max((s for p, s in levels if abs(p - ctx.last_price) <= w), default=0)
        return float(best) if best >= self.p["min_size"] else 0.0


@register
class RestingSizeAt(Primitive):
    """Resting size at `price` on `side` (0 when the level is empty)."""
    name = "resting_size_at"
    params = {"price": Param("price", None, "level", required=True), "side": Param("str", "bid", "bid | ask", choices=("bid", "ask"))}
    update_on = "book"

    def value(self, ctx):
        levels = ctx.book_bids if self.p["side"] == "bid" else ctx.book_asks
        if not levels:
            return None
        return float(sum(s for p, s in levels if abs(p - self.p["price"]) < 1e-9))


@register
class BookImbalance(Primitive):
    """Bid size over ask size across the top `levels` levels (>1 = more resting bids)."""
    name = "book_imbalance"
    params = {"levels": Param("int", 5, "levels per side")}
    update_on = "book"

    def value(self, ctx):
        if not ctx.book_bids or not ctx.book_asks:
            return None
        n = self.p["levels"]
        bids = sum(s for _, s in sorted(ctx.book_bids, key=lambda x: -x[0])[:n])
        asks = sum(s for _, s in sorted(ctx.book_asks, key=lambda x: x[0])[:n])
        return bids / asks if asks else None
