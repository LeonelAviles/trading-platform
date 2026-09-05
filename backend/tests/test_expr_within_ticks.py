"""`within_ticks(a, b, n)`: `b` is a value (field, primitive or number), only `n` is a count."""

from engine import expr as X
from tests.helpers_bars import bar, make_ctx


def test_check_accepts_a_leaf_for_the_second_argument():
    e = {"op": "not", "args": [{"op": "within_ticks", "args": [
        {"ind": "opening_range_high", "params": {"minutes": 5}}, {"ind": "opening_range_low", "params": {"minutes": 5}}, 39]}]}
    assert X.check(e, ["1min"]) == []
    assert X.check({"op": "within_ticks", "args": [{"field": "high"}, {"field": "low"}, 2]}, ["1min"]) == []
    assert any("must be a number" in err for err in X.check({"op": "within_ticks", "args": [{"field": "high"}, {"field": "low"}, {"field": "close"}]}, ["1min"]))


def test_within_ticks_between_two_fields():
    ctx = make_ctx()
    ev = X.Evaluator({"op": "within_ticks", "args": [{"field": "high"}, {"field": "low"}, 2]}, ctx, "long")
    out = []
    for i, (h, l) in enumerate([(100.5, 100.0), (101.0, 100.0), (100.25, 100.0)]):
        ctx.on_bar(bar(i, l, h, l, h))
        ev.on_bar()
        out.append(ev.eval())
    assert out == [True, False, True]
