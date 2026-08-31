"""One active `ReplaySession` (PLATFORM-SPEC.md §4.11).

Event-time scheduler: exchange time advances at `speed` × wall time. Each
frame (~16 ms wall) applies every event whose `ts_recv` falls before the
frame's exchange-time horizon, then flushes coalesced messages:

- `clock` at most every 100 ms, `trades` once per frame, `book` ≤10 Hz,
  partial `bar` ≤4 Hz per timeframe (closed bars go out immediately),
  `footprint` ≤2 Hz, `position` ≤4 Hz.

Layers decide what is read: with the book layer on and speed ≤ 25× the
session walks the day's full MBO (replay cache) and keeps an `L3Book`;
otherwise it walks only prints from `data/market/trades` — bars, footprint,
CVD and fills come from prints in both paths, so they never degrade. Above
25× the book is served from the ingest-time 60-second checkpoints once per
wall second, flagged `approx` ("book approximate" in the UI).

Seeks restore the nearest order-map checkpoint ≤ T and replay forward to T
silently; bar history before the earliest partial bar comes from the
materialised 1-minute bars, the rest from prints. `clock`/`sleep` are
injectable so tests drive the scheduler with a fake clock.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from engine.pnl import ContractSpec
from replay.book import L3Book
from replay.sim import OrderSim
from replay.sources import TF_SECONDS, Batch, Source

NS = 1_000_000_000
SPEEDS = (0.25, 0.5, 1, 2, 5, 10, 25, 100)
BOOK_MAX_SPEED = 25
FRAME_S = 0.016
CLOCK_MIN_S = 0.1
BOOK_MIN_S = 0.1
PARTIAL_MIN_S = 0.25
FOOTPRINT_MIN_S = 0.5
POSITION_MIN_S = 0.25
APPROX_BOOK_S = 1.0
BOOK_DEPTH = 20
LAST_TRADES = 300
MAX_EVENTS_PER_FRAME = 60_000

Send = Callable[[dict], Awaitable[None]]


@dataclass
class Layers:
    book: bool = True
    trades: bool = True
    bars: list[str] = field(default_factory=lambda: ["1min", "5min", "15min"])

    @classmethod
    def from_dict(cls, d: dict | None) -> "Layers":
        d = d or {}
        bars = [tf for tf in (d.get("bars") or ["1min", "5min", "15min"]) if tf in TF_SECONDS] or ["1min"]
        return cls(book=bool(d.get("book", True)), trades=bool(d.get("trades", True)), bars=bars)


class _Bar:
    __slots__ = ("time", "open", "high", "low", "close", "volume", "delta", "buy", "sell")

    def __init__(self, t: int, price: float):
        self.time = t
        self.open = self.high = self.low = self.close = price
        self.volume = 0
        self.delta = 0
        self.buy = 0
        self.sell = 0

    def add(self, price: float, size: int, buy: bool):
        if price > self.high:
            self.high = price
        elif price < self.low:
            self.low = price
        self.close = price
        self.volume += size
        if buy:
            self.buy += size
            self.delta += size
        else:
            self.sell += size
            self.delta -= size

    def to_dict(self, cvd: int | None = None) -> dict:
        d = {"time": self.time, "open": self.open, "high": self.high, "low": self.low, "close": self.close,
             "volume": self.volume, "delta": self.delta, "buyVol": self.buy, "sellVol": self.sell}
        if cvd is not None:
            d["cvd"] = cvd
        return d


class ReplaySession:
    def __init__(self, source: Source, *, from_ts: int, speed: float = 1.0, layers: Layers | None = None,
                 send: Send, spec: ContractSpec | None = None, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep, teaching_session_id: str | None = None,
                 on_trade_closed: Callable[[dict], None] | None = None):
        self.src = source
        self.layers = layers or Layers()
        self.send = send
        self.clock = clock
        self.sleep = sleep
        self.speed = float(speed) if speed in SPEEDS else 1.0
        self.spec = spec or ContractSpec(source.tick_size, source.tick_size * 50, 50, 0.0)
        self.sim = OrderSim(self.spec)
        self.teaching_session_id = teaching_session_id
        self.on_trade_closed = on_trade_closed
        self.primary_tf = self.layers.bars[0]
        self.book = L3Book()
        self.from_ts = max(int(from_ts), source.first_ts)
        self.clock_ts = self.from_ts
        self.paused = True
        self.stopped = False
        self.ended = False
        self.book_mode = False
        self.commands: asyncio.Queue = asyncio.Queue()
        self._wake = asyncio.Event()
        # replay state
        self.bars: dict[str, list[_Bar]] = {tf: [] for tf in self.layers.bars}
        self.closed_history: dict[str, list[dict]] = {tf: [] for tf in self.layers.bars}
        self.cvd = 0
        self.vap: dict[float, int] = {}
        self.footprint: dict[float, list[int]] = {}
        self.footprint_time: int | None = None
        self.last_trades: list[dict] = []
        self.last_price: float | None = None
        self.events_applied = 0
        self.trades_applied = 0
        # iteration
        self._iter = None
        self._batch: Batch | None = None
        self._i = 0
        # outbound coalescing
        self._pending_trades: list[dict] = []
        self._pending_sim: list[dict] = []
        self._pending_fp_closed: list[dict] = []
        self._pending_closed: list[tuple[str, dict]] = []
        self._dirty_partial: set[str] = set()
        self._book_dirty = False
        self._fp_dirty = False
        self._pos_dirty = False
        self._last_sent = {"clock": -1e9, "book": -1e9, "fp": -1e9, "pos": -1e9, "approx": -1e9}
        self._last_partial: dict[str, float] = {}
        self._last_clock_sent = None
        self._anchor_ts = self.from_ts
        self._anchor_wall = self.clock()
        self.stats = {"frames": 0, "messages": 0}

    # -- public control ---------------------------------------------------------

    def command(self, msg: dict) -> None:
        self.commands.put_nowait(msg)
        self._wake.set()

    def stop(self) -> None:
        self.stopped = True
        self._wake.set()

    # -- messaging --------------------------------------------------------------

    async def _emit(self, msg: dict) -> None:
        self.stats["messages"] += 1
        await self.send(msg)

    # -- preparation / seek -----------------------------------------------------

    def _want_book(self) -> bool:
        return self.layers.book and self.speed <= BOOK_MAX_SPEED and self.src.has_mbo()

    def _reset_state(self) -> None:
        self.bars = {tf: [] for tf in self.layers.bars}
        self.closed_history = {tf: [] for tf in self.layers.bars}
        self.cvd = 0
        self.vap = {}
        self.footprint = {}
        self.footprint_time = None
        self.last_trades = []
        self._pending_trades = []
        self._pending_closed = []
        self._pending_fp_closed = []
        self._dirty_partial = set()

    def _prepare(self, ts: int) -> None:
        """Rebuild every derived state as of `ts` (exclusive) without emitting."""
        ts = max(int(ts), self.src.first_ts)
        self._reset_state()
        # earliest partial-bar start over the requested timeframes
        t0 = min(((ts // NS) // TF_SECONDS[tf]) * TF_SECONDS[tf] for tf in self.layers.bars)
        for tf in self.layers.bars:
            self.closed_history[tf] = self.src.bars_before(tf, t0)
        one_min = self.src.bars_before("1min", t0) if "1min" in self.layers.bars else None
        base = one_min if one_min is not None else self.src.bars_before("1min", t0)
        self.cvd = sum(int(b["delta"]) for b in base)
        self.vap = self.src.volume_at_price_before(t0 * NS)
        # prints from t0 up to ts build the partial bars / footprint / cvd
        for b in self.src.iter_trades(t0 * NS):
            n = self._apply_trades_batch(b, upto=ts - 1, silent=True)
            if n < b.n:
                break
        # a bar whose window ended before `ts` is closed, not partial
        for tf, lst in self.bars.items():
            if lst and lst[-1].time + TF_SECONDS[tf] <= ts // NS:
                self.closed_history[tf].append(lst.pop().to_dict(self.cvd))
                if tf == self.primary_tf:
                    self.footprint = {}
                    self.footprint_time = None
        self.last_trades = self.src.trades_before(ts, LAST_TRADES)
        if self.last_trades:
            self.last_price = self.last_trades[-1]["price"]
        # book
        self.book = L3Book()
        self.book_mode = self._want_book()
        if self.book_mode:
            self._rebuild_book(ts)
        self.clock_ts = ts
        self._open_iter(ts)
        self._anchor_ts = ts
        self._anchor_wall = self.clock()
        self.ended = False

    def _rebuild_book(self, ts: int) -> None:
        start, snap = self.src.checkpoint(ts)
        self.book = L3Book()
        if snap is not None:
            self.book.restore(snap)
        for b in self.src.iter_mbo(start if start is not None else self.src.first_ts):
            n = self.book.apply_arrays(b.ts, b.action, b.side, b.price, b.size, b.order_id, upto=ts - 1)
            if n < b.n:
                break

    def _open_iter(self, ts: int) -> None:
        self._iter = self.src.iter_mbo(ts) if self.book_mode else self.src.iter_trades(ts)
        self._batch = None
        self._i = 0

    def _ready_message(self) -> dict:
        bids, asks = self.book.top(BOOK_DEPTH) if self.book_mode else (self.src.approx_book(self.clock_ts) or ([], []))
        return {
            "type": "ready", "clock": self.clock_ts, "speed": self.speed, "paused": self.paused,
            "symbol": self.src.symbol, "root": self.src.root, "date": str(self.src.day), "tickSize": self.src.tick_size,
            "dayStart": self.src.first_ts, "dayEnd": self.src.last_ts,
            "bookMode": "l3" if self.book_mode else ("approx" if self.layers.book else "off"),
            "book": {"ts": self.clock_ts, "bids": bids, "asks": asks},
            "bars": {tf: self.closed_history[tf] + [b.to_dict() for b in self.bars[tf]] for tf in self.layers.bars},
            "lastTrades": self.last_trades[-LAST_TRADES:],
            "cvd": self.cvd,
            "volumeAtPrice": sorted([[p, v] for p, v in self.vap.items()]),
            "footprint": self._footprint_payload(),
            "position": self.sim.position.to_dict(self.last_price, self.spec) if self.sim.position else None,
            "trades": self.sim.trades,
        }

    # -- event application ------------------------------------------------------

    def _on_trade(self, ts: int, price: float, size: int, side: str, silent: bool) -> None:
        buy = side == "B"
        sec = ts // NS
        for tf, lst in self.bars.items():
            step = TF_SECONDS[tf]
            bt = (sec // step) * step
            cur = lst[-1] if lst else None
            if cur is None or bt > cur.time:
                if cur is not None:
                    if not silent:
                        self._pending_closed.append((tf, cur.to_dict(self.cvd)))
                    else:
                        self.closed_history[tf].append(cur.to_dict(self.cvd))
                    lst.pop()
                nb = _Bar(bt, price)
                lst.append(nb)
                cur = nb
                if tf == self.primary_tf:
                    if not silent and self.footprint_time is not None and self.footprint:
                        self._pending_fp_closed.append(self._footprint_payload(closed=True))
                    self.footprint = {}
                    self.footprint_time = bt
            cur.add(price, size, buy)
            if not silent:
                self._dirty_partial.add(tf)
        self.cvd += size if buy else -size
        self.vap[price] = self.vap.get(price, 0) + size
        fp = self.footprint.get(price)
        if fp is None:
            fp = self.footprint[price] = [0, 0]
        fp[1 if buy else 0] += size
        self.last_price = price
        self.trades_applied += 1
        if not silent:
            item = {"ts": ts, "price": price, "size": size, "side": side}
            self._pending_trades.append(item)
            self.last_trades.append(item)
            if len(self.last_trades) > LAST_TRADES:
                del self.last_trades[: len(self.last_trades) - LAST_TRADES]
            self._fp_dirty = True
            for ev in self.sim.on_trade(ts, price):
                self._pending_sim.append(ev)
            if self.sim.position is not None:
                self._pos_dirty = True

    def _apply_trades_batch(self, b: Batch, upto: int, silent: bool = False, start: int = 0,
                            max_trades: int | None = None) -> int:
        """Apply prints of `b[start:]` with ts <= upto. Returns the index reached."""
        ts, price, size, side = b.ts, b.price, b.size, b.side
        i = start
        n = b.n
        count = 0
        while i < n:
            t = int(ts[i])
            if t > upto:
                break
            self._on_trade(t, float(price[i]), int(size[i]), side[i], silent)
            i += 1
            count += 1
            if max_trades is not None and count >= max_trades:
                break
        return i

    def _apply_mbo_batch(self, b: Batch, upto: int, start: int = 0, max_trades: int | None = None,
                         max_events: int | None = None) -> int:
        ts, action, side, price, size, oid = b.ts, b.action, b.side, b.price, b.size, b.order_id
        book = self.book
        i = start
        n = b.n
        trades = 0
        events = 0
        while i < n:
            t = int(ts[i])
            if t > upto:
                break
            a = action[i]
            if a == "T":
                self._on_trade(t, round(int(price[i]) / 1e9, 4), int(size[i]), side[i], False)
                trades += 1
            elif a != "F":
                if book.apply(a, side[i], int(price[i]), int(size[i]), int(oid[i])):
                    self._book_dirty = True
            i += 1
            events += 1
            if max_trades is not None and trades >= max_trades:
                break
            if max_events is not None and events >= max_events:
                break
        book.last_ts = int(ts[i - 1]) if i > start else book.last_ts
        return i

    def _next_batch(self) -> bool:
        if self._iter is None:
            return False
        try:
            self._batch = next(self._iter)
        except StopIteration:
            self._batch = None
            return False
        self._i = 0
        return self._batch.n > 0 or self._next_batch()

    def _advance(self, upto: int, *, max_trades: int | None = None) -> int:
        """Apply events with ts <= upto (bounded per frame). Returns trades applied."""
        before = self.trades_applied
        budget = MAX_EVENTS_PER_FRAME
        while budget > 0:
            if self._batch is None or self._i >= self._batch.n:
                if not self._next_batch():
                    self.ended = True
                    break
            b = self._batch
            start = self._i
            remaining = None if max_trades is None else max_trades - (self.trades_applied - before)
            if remaining is not None and remaining <= 0:
                break
            if self.book_mode:
                self._i = self._apply_mbo_batch(b, upto, start, max_trades=remaining, max_events=budget)
            else:
                self._i = self._apply_trades_batch(b, upto, False, start, max_trades=remaining)
            consumed = self._i - start
            self.events_applied += consumed
            budget -= consumed
            if self._i < b.n and (remaining is None or self.trades_applied - before < remaining):
                # stopped at the horizon
                break
        applied = self.trades_applied - before
        if self.book_mode and self.book.last_ts is not None and self.book.last_ts > self.clock_ts:
            self.clock_ts = self.book.last_ts
        return applied

    def _peek_ts(self) -> int | None:
        if self._batch is None or self._i >= self._batch.n:
            if not self._next_batch():
                return None
        return int(self._batch.ts[self._i])

    # -- flushing ---------------------------------------------------------------

    def _footprint_payload(self, closed: bool = False) -> dict | None:
        if self.footprint_time is None:
            return None
        levels = [{"price": p, "bid": v[0], "ask": v[1]} for p, v in sorted(self.footprint.items())]
        vol = sum(v[0] + v[1] for v in self.footprint.values())
        poc = max(self.footprint.items(), key=lambda kv: kv[1][0] + kv[1][1])[0] if self.footprint else None
        return {"type": "footprint", "tf": self.primary_tf, "time": self.footprint_time, "levels": levels,
                "volume": vol, "delta": sum(v[1] - v[0] for v in self.footprint.values()), "poc": poc, "closed": closed}

    async def _flush(self, force: bool = False) -> None:
        now = self.clock()
        if self._pending_trades and self.layers.trades:
            await self._emit({"type": "trades", "items": self._pending_trades})
        self._pending_trades = []
        if self._pending_closed:
            for tf, bar in self._pending_closed:
                self.closed_history[tf].append(bar)
                await self._emit({"type": "bar", "tf": tf, "bar": bar, "closed": True})
            self._pending_closed = []
        if self._pending_fp_closed:
            for fp in self._pending_fp_closed:
                await self._emit(fp)
            self._pending_fp_closed = []
        for tf in list(self._dirty_partial):
            if force or now - self._last_partial.get(tf, -1e9) >= PARTIAL_MIN_S:
                cur = self.bars[tf][-1] if self.bars[tf] else None
                if cur is not None:
                    await self._emit({"type": "bar", "tf": tf, "bar": cur.to_dict(self.cvd), "closed": False})
                self._last_partial[tf] = now
                self._dirty_partial.discard(tf)
        if self.book_mode and self._book_dirty and (force or now - self._last_sent["book"] >= BOOK_MIN_S):
            bids, asks = self.book.top(BOOK_DEPTH)
            await self._emit({"type": "book", "ts": self.clock_ts, "bids": bids, "asks": asks, "approx": False})
            self._last_sent["book"] = now
            self._book_dirty = False
        elif not self.book_mode and self.layers.book and (force or now - self._last_sent["approx"] >= APPROX_BOOK_S):
            top = self.src.approx_book(self.clock_ts)
            if top is not None:
                await self._emit({"type": "book", "ts": self.clock_ts, "bids": top[0], "asks": top[1], "approx": True})
            self._last_sent["approx"] = now
        if self._fp_dirty and (force or now - self._last_sent["fp"] >= FOOTPRINT_MIN_S):
            fp = self._footprint_payload()
            if fp:
                await self._emit(fp)
            self._last_sent["fp"] = now
            self._fp_dirty = False
        for ev in self._pending_sim:
            if ev["kind"] == "fill":
                await self._emit({"type": "fill", "position": ev["position"], "trade": None})
            else:
                await self._emit({"type": "fill", "trade": ev["trade"], "position": None})
                if self.on_trade_closed:
                    self.on_trade_closed(ev["trade"])
            self._pos_dirty = True
        self._pending_sim = []
        if self._pos_dirty and (force or now - self._last_sent["pos"] >= POSITION_MIN_S):
            pos = self.sim.position
            await self._emit({"type": "position", "position": pos.to_dict(self.last_price, self.spec) if pos else None})
            self._last_sent["pos"] = now
            self._pos_dirty = False
        if self.clock_ts != self._last_clock_sent and (force or now - self._last_sent["clock"] >= CLOCK_MIN_S):
            await self._emit({"type": "clock", "ts": self.clock_ts, "paused": self.paused, "speed": self.speed})
            self._last_sent["clock"] = now
            self._last_clock_sent = self.clock_ts

    # -- commands ----------------------------------------------------------------

    def _reanchor(self) -> None:
        self._anchor_ts = self.clock_ts
        self._anchor_wall = self.clock()

    async def _handle(self, msg: dict) -> None:
        t = msg.get("type")
        if t == "pause":
            self.paused = True
            await self._flush(force=True)
        elif t == "resume":
            if self.ended:
                return
            self.paused = False
            self._reanchor()
            await self._flush(force=True)
        elif t == "speed":
            v = float(msg.get("value", 1))
            if v not in SPEEDS:
                await self._emit({"type": "error", "message": f"speed must be one of {list(SPEEDS)}"})
                return
            self.speed = v
            want = self._want_book()
            if want != self.book_mode:
                await self._switch_book_mode(want)
            self._reanchor()
            await self._emit({"type": "mode", "bookMode": "l3" if self.book_mode else ("approx" if self.layers.book else "off"),
                              "speed": self.speed})
        elif t == "step":
            unit = msg.get("unit", "tick")
            n = max(1, int(msg.get("n", 1)))
            self.paused = True
            for _ in range(n):
                await self._step(unit)
            self._reanchor()
            await self._flush(force=True)
        elif t == "seek":
            ts = int(msg.get("ts"))
            if ts < self.src.first_ts or ts > self.src.last_ts:
                await self._emit({"type": "error", "message": "seek outside the replayed day"})
                return
            was_paused = self.paused
            self.paused = True
            await self._flush(force=True)
            self._prepare(ts)
            await self._emit(self._ready_message())
            self._last_clock_sent = self.clock_ts
            self.paused = was_paused
            self._reanchor()
        elif t == "order":
            try:
                self.sim.submit(msg.get("side"), msg.get("contracts", 1), msg.get("stopTicks"), msg.get("targetTicks"),
                                msg.get("note"), msg.get("confidence"))
            except ValueError as e:
                await self._emit({"type": "error", "message": str(e)})
        elif t == "flatten":
            if not self.sim.flatten():
                await self._emit({"type": "error", "message": "no open position"})
        elif t == "modify":
            self.sim.modify(msg.get("stopPrice"), msg.get("targetPrice"))
            self._pos_dirty = True
            await self._flush(force=True)
        elif t == "mark":
            await self._emit({"type": "marked", "kind": msg.get("kind"), "ts": self.clock_ts, "payload": msg.get("payload")})
        elif t == "stop":
            self.stop()

    async def _switch_book_mode(self, want: bool) -> None:
        if want:
            self._rebuild_book(self.clock_ts)
            self.book_mode = True
            self._book_dirty = True
        else:
            self.book_mode = False
        self._open_iter(self.clock_ts + 1 if self.events_applied else self.clock_ts)

    async def _step(self, unit: str) -> None:
        if self.ended:
            return
        if unit == "bar":
            step = TF_SECONDS[self.primary_tf]
            cur = self.bars[self.primary_tf][-1] if self.bars[self.primary_tf] else None
            target_bar = (cur.time + step) if cur else ((self.clock_ts // NS) // step * step + step)
            # apply prints until the primary bar closes (first print of the next bar)
            while not self.ended:
                nxt = self._peek_ts()
                if nxt is None:
                    self.ended = True
                    break
                self._advance(nxt, max_trades=1)
                if self.bars[self.primary_tf] and self.bars[self.primary_tf][-1].time >= target_bar:
                    break
            self.clock_ts = max(self.clock_ts, min(target_bar * NS, self.src.last_ts))
        else:
            nxt = self._peek_ts()
            if nxt is None:
                self.ended = True
                return
            # one print (in book mode this consumes the book events before it too)
            while not self.ended:
                nxt = self._peek_ts()
                if nxt is None:
                    self.ended = True
                    break
                before = self.trades_applied
                self._advance(nxt, max_trades=1)
                if self.trades_applied > before:
                    break
            self.clock_ts = max(self.clock_ts, nxt)

    # -- main loop --------------------------------------------------------------

    async def run(self) -> None:
        self._prepare(self.from_ts)
        await self._emit(self._ready_message())
        self._last_clock_sent = self.clock_ts
        try:
            while not self.stopped:
                while not self.commands.empty():
                    await self._handle(self.commands.get_nowait())
                if self.stopped:
                    break
                if self.paused or self.ended:
                    if self.ended and not self.paused:
                        self.paused = True
                        await self._flush(force=True)
                        await self._emit({"type": "end", "ts": self.clock_ts})
                    self._wake.clear()
                    await self._wait_command()
                    continue
                now = self.clock()
                horizon = self._anchor_ts + int((now - self._anchor_wall) * self.speed * NS)
                if horizon > self.src.last_ts:
                    horizon = self.src.last_ts
                self._advance(horizon)
                self.clock_ts = max(self.clock_ts, min(horizon, self.src.last_ts))
                self.stats["frames"] += 1
                await self._flush()
                nxt = self._peek_ts()
                if nxt is None:
                    self.ended = True
                    continue
                if nxt <= horizon:
                    await self.sleep(0)         # more due now (bounded frame) — yield only
                else:
                    wait = (nxt - self._anchor_ts) / NS / self.speed - (self.clock() - self._anchor_wall)
                    await self.sleep(min(max(wait, FRAME_S), 0.25))
        finally:
            await self._flush(force=True)

    async def _wait_command(self) -> None:
        # Poll with the injectable sleep so a fake clock can drive tests, but
        # wake immediately when a command arrives.
        while not self.stopped and self.commands.empty():
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=0.25)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()
