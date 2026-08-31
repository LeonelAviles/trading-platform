"""Volume-profile primitives over the RTH session (trade-derived; bar volume spread over the bar range in bars mode)."""

from __future__ import annotations

from engine.primitives.base import Param, Primitive, register


def value_area(profile: dict[float, float], fraction: float = 0.70):
    if not profile:
        return None, None, None
    bins = sorted(profile.items())
    total = sum(v for _, v in bins)
    if total <= 0:
        return None, None, None
    i = max(range(len(bins)), key=lambda k: bins[k][1])
    poc = bins[i][0]
    lo = hi = i
    acc = bins[i][1]
    while acc < fraction * total and (lo > 0 or hi < len(bins) - 1):
        up = bins[hi + 1][1] if hi < len(bins) - 1 else -1
        dn = bins[lo - 1][1] if lo > 0 else -1
        if up >= dn:
            hi += 1
            acc += up
        else:
            lo -= 1
            acc += dn
    return poc, bins[hi][0], bins[lo][0]


class _VA(Primitive):
    output, mirror = "level", "price"
    idx = 0

    def value(self, ctx):
        if ctx.session is None:
            return None
        return value_area(ctx.session.profile)[self.idx]


@register
class POC(_VA):
    """Session point of control: the price with the most traded volume so far."""
    name, idx = "poc", 0


@register
class VAH(_VA):
    """Session value-area high (70 % of volume around the POC)."""
    name, idx, mirror_name = "vah", 1, "val"


@register
class VAL(_VA):
    """Session value-area low (70 % of volume around the POC)."""
    name, idx, mirror_name = "val", 2, "vah"


@register
class VolumeAtPrice(Primitive):
    """Session volume traded within `ticks` of `price` (defaults to the last close)."""
    name = "volume_at_price"
    params = {"price": Param("price", None, "level; default = last close"), "ticks": Param("int", 2, "half-width in ticks")}

    def value(self, ctx):
        if ctx.session is None:
            return None
        px = self.p["price"] if self.p["price"] is not None else ctx.last_price
        if px is None:
            return None
        w = self.p["ticks"] * ctx.tick + 1e-9
        return sum(v for p, v in ctx.session.profile.items() if abs(p - px) <= w)


@register
class ProfileShape(Primitive):
    """Session profile shape: 'P' (volume concentrated high), 'b' (low), 'D'/'normal' (balanced). Numeric:
    P=1, b=-1, D=0 — computed from where the POC sits inside the value area."""
    name = "profile_shape"
    mirror = "signed"

    def value(self, ctx):
        if ctx.session is None or not ctx.session.profile:
            return None
        poc, vah, val = value_area(ctx.session.profile)
        if poc is None or vah == val:
            return 0.0
        pos = (poc - val) / (vah - val)
        return 1.0 if pos >= 0.66 else -1.0 if pos <= 0.34 else 0.0
