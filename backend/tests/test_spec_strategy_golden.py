"""Golden tests for the DSL v2 rule source (PLATFORM-SPEC.md §5 Phase 3 acceptance a, b, d, f)."""

from datetime import date

import pytest

from engine.rules import Bar
from engine.spec_strategy import SpecRules
from engine.session import NS, et_to_ns
from tests.helpers_bars import D

ORB = {
    "schemaVersion": 2, "name": "ORB 15m — breakout", "instrument": {"root": "ES", "symbol": "ES1!"},
    "timeframes": {"primary": "1min", "context": []}, "direction": "both",
    "session": {"entryWindow": {"start": "09:45", "end": "11:30"}, "flattenAt": "15:58"},
    "entry": {"trigger": {"op": "gt", "args": [{"field": "close"}, {"ind": "opening_range_high", "params": {"minutes": 15}}]},
              "orderType": "market", "timeoutBars": 1},
    "filters": [],
    "exit": {"stop": {"type": "structure", "structure": "or_low", "bufferTicks": 2}, "target": {"type": "rr", "value": 2.0}},
    "sizing": {"type": "fixed_risk", "value": 0.5, "maxContracts": 5},
    "constraints": {"maxTradesPerDay": 1}, "execution": {"mode": "bars"},
}


def _bar(i, o, h, l, c, d=D, v=100, delta=0.0, start="09:30"):
    ts_open = et_to_ns(d, start) + i * 60 * NS
    buy = (v + delta) / 2
    return Bar(float(o), float(h), float(l), float(c), float(v), float(delta), float(buy), float(v - buy), ts_open + 60 * NS, ts_open, i)


def _run(spec, bars):
    rules = SpecRules(spec, tick_size=0.25)
    out = []
    for b in bars:
        rules.on_bar(b)
        out.append(rules.signal(b))
    return rules, out


def test_a_orb_enters_on_first_close_above_range_after_it_forms():
    # 15 minutes of range 100–101 (never a close above 101 could count before 09:45),
    # then bar 15 closes at 101.5 (above OR high) -> the very first eligible bar fires long.
    bars = [_bar(i, 100.5, 101, 100, 100.5 + (0.25 if i % 2 else -0.25)) for i in range(15)]
    bars.append(_bar(15, 100.75, 101.75, 100.5, 101.5))
    bars.append(_bar(16, 101.5, 102.5, 101.25, 102.25))
    rules, sig = _run(ORB, bars)
    assert sig[:15] == [None] * 15
    assert sig[15] == "long"
    # Structure stop: OR low − 2 ticks; target = 2R.
    stop, target = rules.stop_target("long", 101.5, bars[15])
    assert stop == 100 - 0.5 and target == pytest.approx(101.5 + 2 * (101.5 - 99.5))


def test_a_orb_never_fires_inside_the_range_window():
    # Highs poke above the eventual OR high during the first 15 minutes — still no signal.
    bars = [_bar(i, 100, 101 + (i == 5) * 0.5, 99.5, 100.25 + (i == 5) * 1.0) for i in range(15)]
    bars += [_bar(15 + k, 100, 100.5, 99.5, 100) for k in range(5)]   # below OR high afterwards
    _, sig = _run(ORB, bars)
    assert set(sig) == {None}


def test_f_direction_both_mirrors_short_side():
    bars = [_bar(i, 100.5, 101, 100, 100.5 + (0.25 if i % 2 else -0.25)) for i in range(15)]
    bars.append(_bar(15, 100.25, 100.5, 99.25, 99.5))      # closes below OR low
    rules, sig = _run(ORB, bars)
    assert sig[15] == "short"
    stop, target = rules.stop_target("short", 99.5, bars[15])
    assert stop == 101 + 0.5 and target == pytest.approx(99.5 - 2 * (101.5 - 99.5))


def test_b_retest_fires_only_after_break_return_hold():
    spec = {**ORB, "direction": "long",
            "entry": {"sequence": [{"when": {"op": "gt", "args": [{"field": "close"}, {"ind": "opening_range_high", "params": {"minutes": 15}}]}, "withinBars": 30}],
                      "trigger": {"op": "retest", "args": [{"ind": "opening_range_high", "params": {"minutes": 15}}, 4, 20]},
                      "orderType": "market", "timeoutBars": 1}}
    base = [_bar(i, 100.5, 101, 100, 100.5 + (0.25 if i % 2 else -0.25)) for i in range(15)]
    # break (bar 15), run (16), pull back to within 4 ticks of 101 and close above (17) -> fire at 17
    seq = base + [_bar(15, 100.75, 101.75, 100.5, 101.5), _bar(16, 102.25, 102.5, 102.25, 102.25), _bar(17, 102.25, 102.5, 101.25, 101.75), _bar(18, 101.75, 103, 101.5, 102.75)]
    _, sig = _run(spec, seq)
    assert sig[15] is None and sig[16] is None and sig[17] == "long" and sig[18] is None
    # break and run without returning: never fires
    run = base + [_bar(15, 100.75, 101.75, 100.5, 101.5), _bar(16, 102.5, 103, 102.25, 103), _bar(17, 103, 104, 102.75, 104)]
    _, sig = _run(spec, run)
    assert set(sig) == {None}


def test_d_context_timeframe_filter_reads_closed_bars():
    spec = {**ORB, "direction": "long", "timeframes": {"primary": "1min", "context": ["5min"]},
            "entry": {"trigger": {"op": "gt", "args": [{"field": "close"}, 100]}, "orderType": "market", "timeoutBars": 1},
            "filters": [{"op": "gt", "args": [{"field": "close", "tf": "5min"}, 101]}],
            "exit": {"stop": {"type": "ticks", "value": 8}, "target": {"type": "rr", "value": 2}}}
    # 1-minute closes: 100.25 for 5 bars (5m bar 1 closes 100.25), then 102s. The 5-minute close only
    # exceeds 101 once the SECOND 5-minute bar closes (after bar index 9), so signals start at bar 10.
    bars = [_bar(i, 100, 100.5, 99.75, 100.25) for i in range(5)] + [_bar(5 + k, 102, 102.5, 101.75, 102.25) for k in range(8)]
    rules, sig = _run(spec, bars)
    firsts = [i for i, s in enumerate(sig) if s == "long"]
    assert firsts and firsts[0] == 10


def test_warmup_and_required_mode():
    rules = SpecRules({**ORB, "direction": "long", "filters": [{"op": "gt", "args": [{"ind": "sma", "params": {"period": 30}}, 1]}]}, tick_size=0.25)
    assert rules.warmup_bars == 30
