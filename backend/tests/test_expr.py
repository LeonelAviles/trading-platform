import pytest

from engine import expr as X
from tests.helpers_bars import bar, make_ctx


def _eval_series(expr, closes, direction="long", deltas=None):
    ctx = make_ctx()
    ev = X.Evaluator(expr, ctx, direction)
    out = []
    prev = closes[0]
    for i, c in enumerate(closes):
        ctx.on_bar(bar(i, prev, max(prev, c), min(prev, c), c, delta=(deltas[i] if deltas else 0.0)))
        ev.on_bar()
        out.append(ev.eval())
        prev = c
    return out


def test_check_reports_problems():
    errs = X.check({"op": "gt", "args": [{"ind": "nope"}, 1]}, ["1min"])
    assert errs and "unknown primitive" in errs[0]
    assert any("tf '15min'" in e for e in X.check({"op": "gt", "args": [{"ind": "ema", "params": {"period": 9, "tf": "15min"}}, 1]}, ["1min"]))
    assert any("takes 2" in e for e in X.check({"op": "gt", "args": [1]}, ["1min"]))
    assert any("boolean" in e for e in X.check({"op": "and", "args": [{"field": "close"}, True]}, ["1min"]))
    assert any("unknown operator" in e for e in X.check({"op": "xor", "args": [True, False]}, ["1min"]))
    assert any("must be a number" in e for e in X.check({"op": "rising", "args": [{"field": "close"}, {"field": "open"}]}, ["1min"]))
    assert X.check({"op": "and", "args": [{"op": "gt", "args": [{"field": "close"}, {"ind": "opening_range_high", "params": {"minutes": 15}}]},
                                          {"op": "gt", "args": [{"ind": "ema", "params": {"period": 9, "tf": "15min"}}, {"ind": "ema", "params": {"period": 21, "tf": "15min"}}]}]},
                   ["1min", "15min"]) == []


def test_compare_and_logic():
    e = {"op": "and", "args": [{"op": "gt", "args": [{"field": "close"}, 12]}, {"op": "not", "args": [{"op": "lt", "args": [{"field": "close"}, 10]}]}]}
    assert _eval_series(e, [10, 11, 12, 13, 14]) == [False, False, False, True, True]
    e = {"op": "between", "args": [{"field": "close"}, 11, 13]}
    assert _eval_series(e, [10, 11, 12, 13, 14]) == [False, True, True, True, False]
    e = {"op": "within_ticks", "args": [{"field": "close"}, 12, 2]}
    assert _eval_series(e, [11, 11.5, 12.5, 13]) == [False, True, True, False]
    e = {"op": "gt", "args": [{"field": "close"}, {"ind": "sma", "params": {"period": 3}}]}
    assert _eval_series(e, [10, 11, 12, 13]) == [None, None, True, True]


def test_cross_and_rising():
    e = {"op": "cross_above", "args": [{"field": "close"}, {"ind": "sma", "params": {"period": 3}}]}
    assert _eval_series(e, [12, 11, 10, 9, 12]) == [False, False, False, False, True]
    e = {"op": "cross_below", "args": [{"field": "close"}, {"ind": "sma", "params": {"period": 3}}]}
    assert _eval_series(e, [10, 11, 12, 13, 9])[-1] is True
    e = {"op": "rising", "args": [{"field": "close"}, 2]}
    assert _eval_series(e, [10, 11, 12, 11, 12, 13]) == [False, False, True, False, False, True]
    e = {"op": "falling", "args": [{"field": "close"}, 2]}
    assert _eval_series(e, [12, 11, 10])[-1] is True


def test_held_touched_bars_since():
    e = {"op": "held_above", "args": [10.5, 3]}
    assert _eval_series(e, [10, 11, 12, 13, 10, 11]) == [False, False, False, True, False, False]
    e = {"op": "touched", "args": [12, 1, 2]}
    # bar 1 closes 12.25 (touch), bar 2 opens there (touch again), then 3 bars away -> stale after 2 bars
    assert _eval_series(e, [10, 12.25, 14, 16, 18, 20]) == [False, True, True, True, True, False]
    e = {"op": "gte", "args": [{"op": "bars_since", "args": [{"op": "gt", "args": [{"field": "close"}, 15]}]}, 2]}
    assert _eval_series(e, [16, 10, 10, 10, 16, 10]) == [False, False, True, True, False, False]


def test_retest_sequence():
    e = {"op": "retest", "args": [12, 2, 5]}
    ctx = make_ctx()
    ev = X.Evaluator(e, ctx)
    out = []
    bars = [(11, 11.5, 10.5, 11), (11, 12, 10.75, 11.5), (11.5, 13.5, 11.5, 13), (13, 13.25, 12.25, 12.75), (12.75, 14, 12.5, 14)]
    for i, (o, h, l, c) in enumerate(bars):
        ctx.on_bar(bar(i, o, h, l, c))
        ev.on_bar()
        out.append(ev.eval())
    assert out == [False, False, False, True, False]
    ctx = make_ctx()
    ev = X.Evaluator(e, ctx)
    out = []
    for i, (o, h, l, c) in enumerate([(11, 11.5, 10.5, 11), (11, 13, 11, 13), (13, 15, 13, 15), (15, 16, 14.5, 16)]):
        ctx.on_bar(bar(i, o, h, l, c))
        ev.on_bar()
        out.append(ev.eval())
    assert out == [False, False, False, False]
    ev = X.Evaluator({"op": "retest", "args": [{"ind": "opening_range_high", "params": {"minutes": 1}}, 2, 5]}, make_ctx(), "short")
    assert ev.expr["args"][0]["ind"] == "opening_range_low"


def test_mirror_rules():
    long = {"op": "and", "args": [
        {"op": "gt", "args": [{"field": "close"}, {"ind": "opening_range_high", "params": {"minutes": 15}}]},
        {"op": "gt", "args": [{"ind": "rel_volume", "params": {"n": 20}}, 1.5]},
        {"op": "gt", "args": [{"ind": "bar_delta"}, 0]},
        {"op": "gt", "args": [{"ind": "rsi", "params": {"period": 14}}, 70]},
        {"op": "cross_above", "args": [{"ind": "ema", "params": {"period": 9}}, {"ind": "ema", "params": {"period": 21}}]},
        {"op": "gte", "args": [{"ind": "stacked_imbalances", "params": {"side": "ask", "count": 3}}, 1]},
    ]}
    short = X.mirror(long)
    a = short["args"]
    assert a[0] == {"op": "lt", "args": [{"field": "close"}, {"ind": "opening_range_low", "params": {"minutes": 15}}]}
    assert a[1] == {"op": "gt", "args": [{"ind": "rel_volume", "params": {"n": 20}}, 1.5]}
    assert a[2] == {"op": "lt", "args": [{"ind": "bar_delta"}, 0]}
    assert a[3] == {"op": "lt", "args": [{"ind": "rsi", "params": {"period": 14}}, 30]}
    assert a[4]["op"] == "cross_below"
    assert a[5] == {"op": "gte", "args": [{"ind": "stacked_imbalances", "params": {"side": "bid", "count": 3}}, 1]}


def test_direction_both_mirrors_on_symmetric_series():
    e = {"op": "gt", "args": [{"field": "close"}, {"ind": "highest", "params": {"n": 3}}]}
    closes = [10, 10.5, 11, 12, 11, 10, 9, 8]
    longs = _eval_series(e, closes, "long")
    mirrored = [2 * 10 - c for c in closes]
    shorts = _eval_series(e, mirrored, "short")
    assert longs == shorts and True in longs
