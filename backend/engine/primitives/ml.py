"""Frozen historical MBO inference, requiring an exact approved feature row."""
from engine.primitives.base import Primitive, Param, register
from engine.ml_signal import parse_frozen


@register
class FrozenMBOSignal(Primitive):
    name = "frozen_mbo_signal"
    output = "bool"
    mirror = "none"
    params = {"model": Param("str", required=True)}
    doc = "Approved frozen multivariate MBO model; historical snapshot rows only. No live adapter."

    def __init__(self, params=None, tf=None):
        super().__init__(params, tf)
        if tf is not None:
            raise ValueError("frozen MBO inference requires primary 1min bars")
        self.model = parse_frozen(self.p["model"])

    def value(self, ctx):
        row = getattr(ctx, "ml_features", None)
        if row is None or row[0] != ctx.now_ns:
            raise ValueError("approved MBO features unavailable at this bar close")
        return bool(self.model.predict([row[1]])[0])
