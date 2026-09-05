"""`or_high` / `or_low` structure stops and level targets follow the spec's opening-range length
(the length of the `opening_range_*` primitive the rules reference), defaulting to 15 minutes."""

from engine.spec_strategy import SpecRules
from tests.helpers_bars import bar

BASE = {
    "schemaVersion": 2, "name": "or-minutes", "direction": "long",
    "timeframes": {"primary": "1min", "context": []},
    "session": {"entryWindow": {"start": "09:35", "end": "10:30"}, "flattenAt": "15:58"},
    "exit": {"stop": {"type": "ticks", "value": 80}, "target": {"type": "level", "level": "or_high"}},
}


def _spec(minutes):
    return {**BASE, "entry": {"trigger": {"op": "lt", "args": [{"field": "close"}, {"ind": "opening_range_low", "params": {"minutes": minutes}}]}}}


def _feed(rules):
    # 09:30–09:34 range 100–104, then a drop to 96 at 09:40 (the 5-min range is 100–104, the 15-min range 96–104).
    closes = [100, 102, 104, 103, 101] + [100, 99, 98, 97, 96, 97, 98, 99, 100, 101, 102]
    prev = closes[0]
    for i, c in enumerate(closes):
        rules.on_bar(_b(i, prev, c))
        prev = c


def _b(i, o, c):
    from engine.rules import Bar

    b = bar(i, o, max(o, c), min(o, c), c)
    return Bar(b.open, b.high, b.low, b.close, b.volume, b.delta, b.buy_vol, b.sell_vol, b.ts_close, b.ts_open, i)


def test_level_uses_the_referenced_opening_range_length():
    rules = SpecRules(_spec(5), tick_size=0.25)
    assert rules.or_minutes == 5
    _feed(rules)
    assert rules._level("or_high", "long") == 104.0
    assert rules._level("or_low", "long") == 100.0


def test_level_defaults_to_15_minutes():
    spec = {**BASE, "entry": {"trigger": {"op": "gt", "args": [{"field": "close"}, {"ind": "vwap"}]}}}
    rules = SpecRules(spec, tick_size=0.25)
    assert rules.or_minutes == 15
    _feed(rules)
    assert rules._level("or_low", "long") == 96.0


def test_stop_target_returns_the_short_range_level():
    rules = SpecRules(_spec(5), tick_size=0.25)
    _feed(rules)
    stop, target = rules.stop_target("long", 97.0, None)
    assert target == 104.0 and stop == 97.0 - 20.0
