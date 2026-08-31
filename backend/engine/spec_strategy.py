"""SpecRules — the DSL v2 rule source for the execution layer (PLATFORM-SPEC.md §5 Phase 3 task 5).

Runs a `FeatureContext` over the primary bars (and prints in ticks mode),
compiles `entry.sequence[].when`, `entry.trigger` and `filters[]` into
evaluators — one set per direction; `direction: both` mirrors the long tree
for the short side — and answers the execution layer's questions:

  signal(bar)                       'long' | 'short' | None on the primary bar close
  stop_target(direction, ref, bar)  structure stops / level targets, else None (distance types
                                    are handled by the execution layer)
  trailing / breakeven / scale-out parameters are read by the execution layer from the spec

Sequence semantics: each step must become true, in order, each within
`withinBars` primary bars of the previous step; the trigger is then armed
until it fires or the last step's window expires. Filters are ANDed with the
trigger on the same bar.
"""

from __future__ import annotations

from engine.expr import Evaluator
from engine.features import FeatureContext, Trade
from engine.rules import Bar, RuleSource
from engine.session import NS


class _DirectionState:
    def __init__(self, spec: dict, ctx: FeatureContext, direction: str):
        self.direction = direction
        self.trigger = Evaluator(spec["entry"]["trigger"], ctx, direction)
        self.filters = [Evaluator(f, ctx, direction) for f in spec.get("filters") or []]
        self.steps = [(Evaluator(s["when"], ctx, direction), int(s.get("withinBars", 20))) for s in spec["entry"].get("sequence") or []]
        self.step = 0            # next step to satisfy
        self.step_bar = None     # bar index when the previous step completed

    def on_bar(self, bar_index: int):
        self.trigger.on_bar()
        for f in self.filters:
            f.on_bar()
        for ev, _ in self.steps:
            ev.on_bar()
        if self.steps:
            # Expire an in-progress sequence.
            if self.step > 0 and self.step_bar is not None and bar_index - self.step_bar > self.steps[self.step - 1][1] and self.step < len(self.steps) + 1:
                self.step, self.step_bar = 0, None
            if self.step < len(self.steps):
                ev, _ = self.steps[self.step]
                if ev.eval() is True:
                    self.step += 1
                    self.step_bar = bar_index

    def armed(self, bar_index: int) -> bool:
        if not self.steps:
            return True
        if self.step < len(self.steps):
            return False
        within = self.steps[-1][1]
        return bar_index - (self.step_bar or bar_index) <= within

    def fire(self, bar_index: int) -> bool:
        if not self.armed(bar_index):
            return False
        if self.trigger.eval() is not True:
            return False
        if any(f.eval() is not True for f in self.filters):
            return False
        self.step, self.step_bar = 0, None
        return True


class SpecRules(RuleSource):
    def __init__(self, spec: dict, *, tick_size: float | None = None, rth_start: str = "09:30", rth_end: str = "16:00"):
        self.spec = spec
        tfs = spec.get("timeframes") or {}
        self.primary = tfs.get("primary", "1min")
        if tick_size is None:
            from config.instruments import load_instruments

            root = load_instruments().root_for_symbol((spec.get("instrument") or {}).get("symbol", "ES1!"))
            tick_size = root.tick_size if root else 0.25
        self.ctx = FeatureContext(self.primary, list(tfs.get("context") or []), tick_size=tick_size, rth_start=rth_start, rth_end=rth_end)
        d = spec.get("direction", "long")
        self.directions = ("long", "short") if d == "both" else (d,)
        self.states = {x: _DirectionState(spec, self.ctx, x) for x in self.directions}
        self.warmup_bars = self._warmup()
        self._atr_cache = {}

    def _warmup(self) -> int:
        from engine.expr import referenced_primitives
        from engine.primitives.base import get_class

        need = 2
        exprs = [self.spec["entry"]["trigger"]] + [s["when"] for s in self.spec["entry"].get("sequence") or []] + list(self.spec.get("filters") or [])
        for e in exprs:
            for name, params, tf in referenced_primitives(e):
                try:
                    need = max(need, get_class(name)(params, tf).lookback_bars())
                except Exception:
                    pass
        return need

    # -- feeds ----------------------------------------------------------------
    def on_trade(self, ts: int, price: float, size: int, side: str) -> None:
        self.ctx.on_trade(Trade(int(ts), float(price), int(size), side))

    def on_bar(self, bar: Bar) -> None:
        from engine.features import BarRec

        rec = BarRec(bar.open, bar.high, bar.low, bar.close, bar.volume, bar.delta, bar.buy_vol, bar.sell_vol, bar.ts_open, bar.ts_close)
        self.ctx.on_bar(rec)
        for st in self.states.values():
            st.on_bar(self.ctx.bar_index)

    def signal(self, bar: Bar) -> str | None:
        if self.ctx.bar_index + 1 < self.warmup_bars:
            return None
        for d in self.directions:
            if self.states[d].fire(self.ctx.bar_index):
                return d
        return None

    # -- exits ----------------------------------------------------------------
    def atr(self, period: int) -> float | None:
        return self.ctx.value("atr", {"period": period})

    def _level(self, name: str, direction: str):
        ctx = self.ctx
        sess = ctx.session
        b = ctx.bar
        mapping = {
            "or_low": lambda: ctx.value("opening_range_low", {"minutes": 15}),
            "or_high": lambda: ctx.value("opening_range_high", {"minutes": 15}),
            "session_low": lambda: sess.low if sess else None,
            "session_high": lambda: sess.high if sess else None,
            "bar_low": lambda: b.low if b else None,
            "bar_high": lambda: b.high if b else None,
            "swing_low": lambda: ctx.value("swing_low", {"n": 3}),
            "swing_high": lambda: ctx.value("swing_high", {"n": 3}),
            "vah": lambda: ctx.value("vah"), "val": lambda: ctx.value("val"), "poc": lambda: ctx.value("poc"),
            "prior_day_high": lambda: ctx.value("prior_day_high"), "prior_day_low": lambda: ctx.value("prior_day_low"),
            "vwap": lambda: ctx.value("vwap"),
        }
        if name not in mapping:
            return None
        return mapping[name]()

    @staticmethod
    def _mirror_level(name: str) -> str:
        pairs = {"or_low": "or_high", "or_high": "or_low", "session_low": "session_high", "session_high": "session_low",
                 "bar_low": "bar_high", "bar_high": "bar_low", "swing_low": "swing_high", "swing_high": "swing_low",
                 "vah": "val", "val": "vah", "prior_day_high": "prior_day_low", "prior_day_low": "prior_day_high"}
        return pairs.get(name, name)

    def stop_target(self, direction: str, entry_ref: float, bar: Bar):
        ex = self.spec.get("exit") or {}
        st, tg = ex.get("stop") or {}, ex.get("target") or {}
        tick = self.ctx.tick
        sign = 1 if direction == "long" else -1
        stop = target = None
        if st.get("type") == "structure":
            name = st.get("structure")
            if direction == "short":
                name = self._mirror_level(name)
            lvl = self._level(name, direction)
            if lvl is None:
                return None
            stop = lvl - sign * int(st.get("bufferTicks", 2)) * tick
            if (sign > 0 and stop >= entry_ref) or (sign < 0 and stop <= entry_ref):
                return None
        if tg.get("type") == "level":
            name = tg.get("level")
            if direction == "short":
                name = self._mirror_level(name)
            lvl = self._level(name, direction)
            if lvl is None or (sign > 0 and lvl <= entry_ref) or (sign < 0 and lvl >= entry_ref):
                return None
            target = lvl
        if stop is None and target is None:
            return None
        if stop is None:
            # distance stop + level target: compute the stop the standard way
            from engine import pnl as P

            spec_ = P.ContractSpec(tick, 0, 0)
            t = st.get("type", "ticks")
            if t == "ticks":
                dist = float(st.get("value", 20)) * tick
            elif t == "points":
                dist = float(st.get("value", 5))
            elif t == "percent":
                dist = entry_ref * float(st.get("value", 0.3)) / 100
            else:
                a = self.atr(int(st.get("period", 14)))
                if a is None:
                    return None
                dist = a * float(st.get("value", 1.5))
            stop = P.round_to_tick(entry_ref - sign * dist, spec_)
        if target is None:
            risk = abs(entry_ref - stop)
            t = tg.get("type", "rr")
            if t == "rr":
                target = entry_ref + sign * risk * float(tg.get("value", 2.0))
            elif t == "ticks":
                target = entry_ref + sign * float(tg.get("value", 40)) * tick
            elif t == "points":
                target = entry_ref + sign * float(tg.get("value", 10))
            else:
                target = entry_ref + sign * risk * 2.0
        return (round(round(stop / tick) * tick, 6), round(round(target / tick) * tick, 6))
