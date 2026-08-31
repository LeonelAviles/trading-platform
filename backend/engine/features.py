"""FeatureContext — one implementation of every market feature, three
consumers (PLATFORM-SPEC.md §5 Phase 3 task 3): the Nautilus strategy, the
agent's trade-enrichment tools and the teaching-mode snapshot builder all
run this object over the same bars/trades and read identical numbers.

Inputs
  on_bar(bar)      a closed *primary* bar (open, high, low, close, volume,
                   delta, buy_vol, sell_vol, ts_open, ts_close). Context
                   timeframes are aggregated here from primary bars, so a
                   context primitive only ever sees *closed* context bars.
  on_trade(trade)  (ticks mode) a print: ts, price, size, side ('B' buyer /
                   'A' seller / 'N'). Feeds the forming bar's footprint, the
                   session profile, session CVD and the recent-trades window.
  set_book(bids, asks)  optional top-of-book view for book primitives.

Session state (opening range, initial balance, session high/low, VWAP, prior
day levels, gap) is recomputed from the RTH bars of the New York session
date. In bars mode the profile/footprint are approximated from bar volume
spread across the bar's range (delta split by buy/sell volume); order-flow
primitives that need prints (`update_on == "trade"`) evaluate to None there,
which is what makes the spec's mode requirement explicit.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import date

from engine.primitives.base import get_class
from engine.session import NS, et_to_ns, session_date

TF_MINUTES = {"1min": 1, "5min": 5, "15min": 15, "30min": 30, "1h": 60, "4h": 240, "1D": 1440}


@dataclass(slots=True)
class BarRec:
    open: float
    high: float
    low: float
    close: float
    volume: float
    delta: float
    buy_vol: float
    sell_vol: float
    ts_open: int
    ts_close: int
    index: int = 0

    @property
    def typical(self) -> float:
        return (self.high + self.low + self.close) / 3


@dataclass(slots=True)
class Trade:
    ts: int
    price: float
    size: int
    side: str


class TFSeries:
    """Closed bars of one timeframe, aggregated from primary bars."""

    def __init__(self, tf: str, maxlen: int = 600):
        self.tf = tf
        self.minutes = TF_MINUTES[tf]
        self.ns = self.minutes * 60 * NS
        self.bars: deque[BarRec] = deque(maxlen=maxlen)
        self.partial: BarRec | None = None
        self.count = 0

    def bucket(self, ts_open: int) -> int:
        return (ts_open // self.ns) * self.ns

    def push_primary(self, b: BarRec) -> BarRec | None:
        """Fold a primary bar in; returns the context bar it closed, if any."""
        closed = None
        bk = self.bucket(b.ts_open)
        if self.partial is not None and self.bucket(self.partial.ts_open) != bk:
            closed = self.partial
            self.partial = None
        if self.partial is None:
            self.partial = BarRec(b.open, b.high, b.low, b.close, b.volume, b.delta, b.buy_vol, b.sell_vol,
                                  bk, bk + self.ns, self.count)
        else:
            p = self.partial
            p.high = max(p.high, b.high)
            p.low = min(p.low, b.low)
            p.close = b.close
            p.volume += b.volume
            p.delta += b.delta
            p.buy_vol += b.buy_vol
            p.sell_vol += b.sell_vol
        if closed is not None:
            self.bars.append(closed)
            self.count += 1
        return closed

    def push_closed(self, b: BarRec) -> None:
        b.index = self.count
        self.bars.append(b)
        self.count += 1

    def closes(self, n: int | None = None):
        bars = list(self.bars)
        return [x.close for x in (bars[-n:] if n else bars)]

    def last(self) -> BarRec | None:
        return self.bars[-1] if self.bars else None


class SessionState:
    def __init__(self, d: date, rth_start: str, rth_end: str):
        self.date = d
        self.open_ns = et_to_ns(d, rth_start)
        self.close_ns = et_to_ns(d, rth_end)
        self.bars: list[BarRec] = []     # RTH primary bars
        self.high = self.low = self.open = self.close = None
        self.pv = 0.0
        self.vol = 0.0
        self.cvd = 0.0
        self.profile: dict[float, float] = {}
        self.profile_buy: dict[float, float] = {}
        self.profile_sell: dict[float, float] = {}

    def add_bar(self, b: BarRec, tick: float, bars_mode_profile: bool) -> None:
        if not (self.open_ns <= b.ts_open < self.close_ns):
            return
        self.bars.append(b)
        if self.open is None:
            self.open = b.open
        self.high = b.high if self.high is None else max(self.high, b.high)
        self.low = b.low if self.low is None else min(self.low, b.low)
        self.close = b.close
        self.pv += b.typical * b.volume
        self.vol += b.volume
        self.cvd += b.delta
        if bars_mode_profile and b.volume > 0:
            lo = round(b.low / tick) * tick
            hi = round(b.high / tick) * tick
            n = max(1, int(round((hi - lo) / tick)) + 1)
            per = b.volume / n
            bper = b.buy_vol / n
            sper = b.sell_vol / n
            for i in range(n):
                px = round(lo + i * tick, 6)
                self.profile[px] = self.profile.get(px, 0.0) + per
                self.profile_buy[px] = self.profile_buy.get(px, 0.0) + bper
                self.profile_sell[px] = self.profile_sell.get(px, 0.0) + sper

    def add_trade(self, t: Trade) -> None:
        if not (self.open_ns <= t.ts < self.close_ns):
            return
        px = round(t.price, 6)
        self.profile[px] = self.profile.get(px, 0.0) + t.size
        if t.side == "B":
            self.profile_buy[px] = self.profile_buy.get(px, 0.0) + t.size
        elif t.side == "A":
            self.profile_sell[px] = self.profile_sell.get(px, 0.0) + t.size

    def vwap(self) -> float | None:
        return self.pv / self.vol if self.vol else None

    def range_levels(self, minutes: int) -> tuple[float | None, float | None]:
        end = self.open_ns + minutes * 60 * NS
        hs = [b.high for b in self.bars if b.ts_close <= end]
        ls = [b.low for b in self.bars if b.ts_close <= end]
        if not hs:
            return None, None
        return max(hs), min(ls)

    def range_complete(self, minutes: int, now_ns: int) -> bool:
        return now_ns >= self.open_ns + minutes * 60 * NS


class FeatureContext:
    def __init__(self, primary_tf: str = "1min", context_tfs: list[str] | None = None, *, tick_size: float = 0.25,
                 rth_start: str = "09:30", rth_end: str = "16:00", history: int = 600, trades_window: int = 2000,
                 footprint_bars: int = 30):
        if primary_tf not in TF_MINUTES:
            raise ValueError(f"unsupported primary timeframe {primary_tf}")
        self.primary_tf = primary_tf
        self.tick = tick_size
        self.rth_start, self.rth_end = rth_start, rth_end
        self.series: dict[str, TFSeries] = {primary_tf: TFSeries(primary_tf, history)}
        for tf in context_tfs or []:
            if tf not in TF_MINUTES:
                raise ValueError(f"unsupported context timeframe {tf}")
            if TF_MINUTES[tf] % TF_MINUTES[primary_tf] != 0 or TF_MINUTES[tf] <= TF_MINUTES[primary_tf]:
                raise ValueError(f"context timeframe {tf} must be a coarser multiple of {primary_tf}")
            self.series.setdefault(tf, TFSeries(tf, history))
        self.session: SessionState | None = None
        self.prior: SessionState | None = None
        self.bar: BarRec | None = None          # last closed primary bar
        self.bar_index = -1
        self.now_ns = 0
        self.last_price: float | None = None
        self.trades: deque[Trade] = deque(maxlen=trades_window)
        self.has_trades = False                  # ticks mode once a print arrives
        # Footprint of the forming primary bar and of the last closed ones: price -> [bid_vol, ask_vol]
        self.fp_current: dict[float, list[float]] = {}
        self.fp_current_open: int | None = None
        self.footprints: deque[tuple[BarRec, dict[float, list[float]]]] = deque(maxlen=footprint_bars)
        self.book_bids: list[tuple[float, float]] = []
        self.book_asks: list[tuple[float, float]] = []
        self.book_ts: int | None = None
        self._instances: dict[tuple, object] = {}
        self._by_tf: dict[str, list] = {}
        self._trade_updated: list = []

    # -- primitives ---------------------------------------------------------------
    def primitive(self, name: str, params: dict | None = None, tf: str | None = None):
        params = dict(params or {})
        tf = tf or params.pop("tf", None) or None
        key = (name, tf, tuple(sorted((k, repr(v)) for k, v in params.items())))
        inst = self._instances.get(key)
        if inst is None:
            cls = get_class(name)
            if tf is not None and not cls.tf_capable and tf != self.primary_tf:
                raise ValueError(f"{name} does not support tf")
            if tf is not None and tf not in self.series:
                raise ValueError(f"tf {tf} is not in the strategy's timeframes")
            inst = cls(params, tf if cls.tf_capable else None)
            self._instances[key] = inst
            self._by_tf.setdefault(inst.tf or self.primary_tf, []).append(inst)
            if cls.update_on == "trade":
                self._trade_updated.append(inst)
            # Warm the instance with bars already seen.
            for b in self.series[inst.tf or self.primary_tf].bars:
                inst.on_bar(self, b)
        return inst

    def value(self, name: str, params: dict | None = None, tf: str | None = None):
        return self.primitive(name, params, tf).value(self)

    # -- feeds --------------------------------------------------------------------
    def on_bar(self, b: BarRec) -> None:
        self.bar_index += 1
        b.index = self.bar_index
        self.now_ns = b.ts_close
        self.last_price = b.close
        d = session_date(b.ts_open)
        if self.session is None or self.session.date != d:
            if self.session is not None and self.session.bars:
                self.prior = self.session
            self.session = SessionState(d, self.rth_start, self.rth_end)
        self.session.add_bar(b, self.tick, bars_mode_profile=not self.has_trades)
        # footprint: close the forming one (ticks) or synthesise from the bar (bars mode)
        if self.has_trades:
            fp = self.fp_current if self.fp_current_open == b.ts_open else {}
        else:
            fp = self._synth_footprint(b)
        self.footprints.append((b, fp))
        self.fp_current, self.fp_current_open = {}, None
        self.bar = b
        for tf, s in self.series.items():
            if tf == self.primary_tf:
                s.push_closed(b)
                for inst in self._by_tf.get(tf, []):
                    inst.on_bar(self, b)
            else:
                closed = s.push_primary(b)
                if closed is not None:
                    for inst in self._by_tf.get(tf, []):
                        inst.on_bar(self, closed)

    def on_trade(self, t: Trade) -> None:
        self.has_trades = True
        self.now_ns = max(self.now_ns, t.ts)
        self.last_price = t.price
        self.trades.append(t)
        primary_ns = TF_MINUTES[self.primary_tf] * 60 * NS
        bucket = (t.ts // primary_ns) * primary_ns
        if self.fp_current_open != bucket:
            self.fp_current, self.fp_current_open = {}, bucket
        cell = self.fp_current.setdefault(round(t.price, 6), [0.0, 0.0])
        if t.side == "A":
            cell[0] += t.size
        elif t.side == "B":
            cell[1] += t.size
        d = session_date(t.ts)
        if self.session is None or self.session.date != d:
            if self.session is not None and self.session.bars:
                self.prior = self.session
            self.session = SessionState(d, self.rth_start, self.rth_end)
        self.session.add_trade(t)
        for inst in self._trade_updated:
            inst.on_trade(self, t)

    def set_book(self, bids: list[tuple[float, float]], asks: list[tuple[float, float]], ts: int | None = None) -> None:
        self.book_bids, self.book_asks, self.book_ts = list(bids), list(asks), ts

    # -- helpers ------------------------------------------------------------------
    def _synth_footprint(self, b: BarRec) -> dict[float, list[float]]:
        if b.volume <= 0:
            return {}
        lo = round(b.low / self.tick) * self.tick
        hi = round(b.high / self.tick) * self.tick
        n = max(1, int(round((hi - lo) / self.tick)) + 1)
        return {round(lo + i * self.tick, 6): [b.sell_vol / n, b.buy_vol / n] for i in range(n)}

    def in_rth(self, ts: int | None = None) -> bool:
        ts = self.now_ns if ts is None else ts
        return self.session is not None and self.session.open_ns <= ts < self.session.close_ns

    def minutes_since_open(self) -> float | None:
        if self.session is None:
            return None
        return (self.now_ns - self.session.open_ns) / (60 * NS)

    def bars(self, tf: str | None = None, n: int | None = None) -> list[BarRec]:
        bars = list(self.series[tf or self.primary_tf].bars)
        return bars[-n:] if n else bars

    def snapshot(self, names: list[str] | None = None) -> dict:
        """Every registered primitive with default params (teaching-mode feature vector)."""
        from engine.primitives.base import all_primitives

        out = {}
        for name, cls in all_primitives().items():
            if names and name not in names:
                continue
            if any(p.required for p in cls.params.values()):
                continue
            try:
                v = self.value(name)
            except Exception:
                v = None
            out[name] = v
        return out
