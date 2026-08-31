"""Rule sources the execution layer can run (PLATFORM-SPEC.md §5 Phase 2).

The execution layer (`backtest_worker.ExecStrategy`) owns orders, exits,
sessions, costs and constraints; a rule source only says *when* to enter
and, optionally, *where* the stop/target sit. Phase 2 ships two:

- `V1Rules` — the legacy flat condition list (`condition_engine`), so the
  two saved strategies run on the new engine unchanged;
- `TestOpenCloseRules` — enter at the first primary bar after the entry
  window opens, exit at flatten: the hand-checkable acceptance strategy.

Phase 3 adds `SpecRules` (DSL v2 expression tree over the primitive registry).
"""

from __future__ import annotations

from datetime import date

from condition_engine import Indicators, condition_lookback, eval_condition
from engine.session import et_to_ns, session_date


class Bar:
    __slots__ = ("open", "high", "low", "close", "volume", "delta", "buy_vol", "sell_vol", "ts_close", "ts_open", "index")

    def __init__(self, open, high, low, close, volume, delta, buy_vol, sell_vol, ts_close, ts_open, index):
        self.open, self.high, self.low, self.close = open, high, low, close
        self.volume, self.delta, self.buy_vol, self.sell_vol = volume, delta, buy_vol, sell_vol
        self.ts_close, self.ts_open, self.index = ts_close, ts_open, index


class RuleSource:
    warmup_bars: int = 0
    directions: tuple[str, ...] = ("long",)

    def on_bar(self, bar: Bar) -> None:
        pass

    def signal(self, bar: Bar) -> str | None:
        """'long' | 'short' | None, evaluated on the primary bar close."""
        return None

    def stop_target(self, direction: str, entry_ref: float, bar: Bar) -> tuple[float | None, float | None] | None:
        """Override the spec's exit levels; None -> the execution layer computes them."""
        return None

    def atr(self, period: int) -> float | None:
        return None


class TestOpenCloseRules(RuleSource):
    """Long on the first primary bar that closes inside the entry window
    each session; the execution layer flattens at `flattenAt`."""

    def __init__(self, entry_start: str = "09:30"):
        self.entry_start = entry_start
        self._done: set[date] = set()

    def signal(self, bar: Bar) -> str | None:
        d = session_date(bar.ts_close)
        if d in self._done:
            return None
        if bar.ts_close >= et_to_ns(d, self.entry_start):
            self._done.add(d)
            return "long"
        return None


class V1Rules(RuleSource):
    """Legacy v1 conditions ANDed, single direction; ATR-based stops read
    the same Indicators instance the conditions use."""

    def __init__(self, v1: dict):
        self.v1 = v1
        self.direction = v1["direction"]
        self.directions = (self.direction,)
        self.conditions = v1["conditions"]
        stop = v1.get("stop", {})
        lookback = max([condition_lookback(c) for c in self.conditions] + [2])
        if stop.get("type") == "atr":
            lookback = max(lookback, int(stop.get("period", 14)) + 2)
        self.ind = Indicators(lookback)
        self.warmup_bars = lookback
        self.has_flow = True

    def on_bar(self, bar: Bar) -> None:
        self.ind.update(bar.open, bar.high, bar.low, bar.close, bar.volume, bar.delta)

    def signal(self, bar: Bar) -> str | None:
        if self.ind.count < self.warmup_bars:
            return None
        if all(eval_condition(c, self.ind) for c in self.conditions):
            return self.direction
        return None

    def atr(self, period: int) -> float | None:
        return self.ind.atr(period)


def build_rules(spec: dict) -> RuleSource:
    rules = spec.get("rules") or {}
    kind = rules.get("kind") or ("v1" if "conditions" in spec else "test_open_close")
    if kind == "v1":
        return V1Rules(rules.get("v1") or spec)
    if kind == "test_open_close":
        return TestOpenCloseRules(entry_start=(spec.get("session", {}).get("entryWindow", {}) or {}).get("start", "09:30"))
    if kind == "spec_v2":
        from engine.spec_strategy import SpecRules  # Phase 3

        return SpecRules(spec)
    raise ValueError(f"unknown rules kind {kind!r}")
