"""Synthetic market-data generator for tests (PLATFORM-SPEC.md §5 Phase 0).

Produces one RTH session of Databento-shaped MBO events for a few outright
symbols without touching real data:

- price is a tick-quantised random walk (default ES: tick 0.25);
- trades arrive as a Poisson process; each trade has an aggressor side and
  consumes resting size at the touch (so the book and the tape agree);
- adds/cancels keep a plausible ladder of `depth` levels per side around the
  mid, with sizes drawn from a lognormal.

Events carry the columns later phases consume (`ts_event`, `ts_recv`,
`symbol`, `action`, `side`, `price`, `size`, `order_id`, `sequence`,
`flags`), and `trades()` / `bars_1m()` derive the two hot tables the data
layer will materialise, so engine tests can assert against the same numbers
`scripts/ingest.py` will produce.

Everything is seeded; the same arguments always give the same session.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ET = ZoneInfo("America/New_York")
NS = 1_000_000_000


@dataclass
class SynthConfig:
    session_date: date = date(2026, 6, 12)
    symbols: tuple[str, ...] = ("ESM6", "ESU6")
    start_price: float = 5300.0
    tick_size: float = 0.25
    rth_start: str = "09:30"
    rth_end: str = "16:00"
    # Trades per second (Poisson rate) per symbol; the second symbol gets a
    # fraction so the front-month-by-volume choice is unambiguous.
    trade_rate_per_s: float = 2.0
    secondary_volume_fraction: float = 0.05
    depth: int = 10
    seed: int = 7
    # Book churn: resting adds/cancels per second per symbol.
    book_rate_per_s: float = 6.0
    trend_per_hour: float = 0.0  # points drift per hour (0 = pure random walk)
    volatility_ticks_per_s: float = 0.6
    extra: dict = field(default_factory=dict)


def _session_bounds_ns(cfg: SynthConfig) -> tuple[int, int]:
    d = cfg.session_date
    h0, m0 = map(int, cfg.rth_start.split(":"))
    h1, m1 = map(int, cfg.rth_end.split(":"))
    start = datetime(d.year, d.month, d.day, h0, m0, tzinfo=ET)
    end = datetime(d.year, d.month, d.day, h1, m1, tzinfo=ET)
    return int(start.timestamp()) * NS, int(end.timestamp()) * NS


class _Book:
    """Minimal L3 book the generator keeps consistent with its own tape."""

    def __init__(self, cfg: SynthConfig, rng: np.random.Generator, mid: float):
        self.cfg = cfg
        self.rng = rng
        self.orders: dict[int, tuple[str, float, int]] = {}  # id -> (side, price, size)
        self.next_id = 1
        self.seed_levels(mid)

    def _size(self) -> int:
        return int(max(1, round(self.rng.lognormal(mean=2.3, sigma=0.7))))

    def seed_levels(self, mid: float):
        tick = self.cfg.tick_size
        events = []
        for i in range(1, self.cfg.depth + 1):
            for side, px in (("B", mid - i * tick), ("A", mid + i * tick)):
                for _ in range(2):
                    events.append(self.add(side, px))
        return events

    def add(self, side: str, price: float, size: int | None = None):
        oid = self.next_id
        self.next_id += 1
        sz = size or self._size()
        self.orders[oid] = (side, price, sz)
        return ("A", side, price, sz, oid)

    def cancel_random(self, side: str):
        ids = [oid for oid, (s, _, _) in self.orders.items() if s == side]
        if len(ids) <= self.cfg.depth:  # keep a ladder
            return None
        oid = int(self.rng.choice(ids))
        s, px, sz = self.orders.pop(oid)
        return ("C", s, px, sz, oid)

    def best(self, side: str) -> float | None:
        px = [p for (s, p, _) in self.orders.values() if s == side]
        if not px:
            return None
        return max(px) if side == "B" else min(px)

    def consume(self, side: str, price: float, size: int) -> list[tuple]:
        """Fill resting orders at `price` on `side`; returns fill events."""
        out = []
        remaining = size
        for oid, (s, p, sz) in list(self.orders.items()):
            if remaining <= 0:
                break
            if s != side or p != price:
                continue
            take = min(sz, remaining)
            remaining -= take
            if take == sz:
                self.orders.pop(oid)
            else:
                self.orders[oid] = (s, p, sz - take)
            out.append(("F", s, p, take, oid))
        return out

    def levels(self, depth: int) -> tuple[list[tuple[float, int]], list[tuple[float, int]]]:
        agg: dict[tuple[str, float], int] = {}
        for s, p, sz in self.orders.values():
            agg[(s, p)] = agg.get((s, p), 0) + sz
        bids = sorted(((p, v) for (s, p), v in agg.items() if s == "B"), reverse=True)[:depth]
        asks = sorted(((p, v) for (s, p), v in agg.items() if s == "A"))[:depth]
        return bids, asks


def generate_mbo(cfg: SynthConfig | None = None) -> pd.DataFrame:
    """One session of MBO events for every symbol in `cfg.symbols`."""
    cfg = cfg or SynthConfig()
    rng = np.random.default_rng(cfg.seed)
    t0, t1 = _session_bounds_ns(cfg)
    rows: list[tuple] = []
    seq = 0

    for idx, symbol in enumerate(cfg.symbols):
        rate = cfg.trade_rate_per_s * (1.0 if idx == 0 else cfg.secondary_volume_fraction)
        price = cfg.start_price + idx * 5.0  # deferred month at a small premium
        book = _Book(cfg, rng, price)
        seed_ts = t0 - 60 * NS
        for i, (act, side, px, sz, oid) in enumerate(book.seed_levels(price)):
            seq += 1
            rows.append((seed_ts + i, seed_ts + i, symbol, act, side, px, sz, oid, seq, 0))

        # Merge two Poisson streams: trades and book churn.
        n_sec = (t1 - t0) // NS
        trade_times = np.sort(rng.uniform(0, n_sec, size=rng.poisson(rate * n_sec)))
        churn_times = np.sort(rng.uniform(0, n_sec, size=rng.poisson(cfg.book_rate_per_s * n_sec)))
        events = [(t, "trade") for t in trade_times] + [(t, "churn") for t in churn_times]
        events.sort()

        drift_per_s = cfg.trend_per_hour / 3600.0
        for t_s, kind in events:
            ts = t0 + int(t_s * NS)
            if kind == "churn":
                side = "B" if rng.random() < 0.5 else "A"
                if rng.random() < 0.5:
                    ev = book.cancel_random(side)
                else:
                    best = book.best(side)
                    if best is None:
                        continue
                    offset = int(rng.integers(0, cfg.depth)) * cfg.tick_size
                    px = best - offset if side == "B" else best + offset
                    ev = book.add(side, round(px / cfg.tick_size) * cfg.tick_size)
                if ev is None:
                    continue
                act, side, px, sz, oid = ev
                seq += 1
                rows.append((ts, ts, symbol, act, side, px, sz, oid, seq, 0))
                continue

            # Trade: aggressor side biased by drift and mean-reverting noise.
            p_buy = 0.5 + np.tanh(drift_per_s * 200) * 0.2
            aggressor_buy = rng.random() < p_buy
            resting_side = "A" if aggressor_buy else "B"
            best = book.best(resting_side)
            if best is None:
                continue
            size = int(max(1, round(rng.lognormal(mean=1.2, sigma=0.8))))
            fills = book.consume(resting_side, best, size)
            filled = sum(f[3] for f in fills)
            if filled == 0:
                continue
            for act, s, px, sz, oid in fills:
                seq += 1
                rows.append((ts, ts, symbol, "F", s, px, sz, oid, seq, 0))
            seq += 1
            # Databento convention: the T record's side is the aggressor's.
            rows.append((ts, ts, symbol, "T", "B" if aggressor_buy else "A", best, filled, 0, seq, 0))
            # ...and the resting size actually leaves the book via C records
            # (consumers apply C and ignore F, exactly like the real feed).
            for act, s, px, sz, oid in fills:
                seq += 1
                rows.append((ts, ts, symbol, "C", s, px, sz, oid, seq, 0))

            # Random walk driven by aggressor flow. The spread is kept at one
            # tick: whichever side fell behind after a sweep gets a fresh
            # resting order one tick inside the other side's best, so the
            # mid moves with the flow and the two ladders never drift apart.
            bb, ba = book.best("B"), book.best("A")
            tick = cfg.tick_size
            if ba is None and bb is not None:
                ev = book.add("A", round((bb + tick) / tick) * tick)
            elif bb is None and ba is not None:
                ev = book.add("B", round((ba - tick) / tick) * tick)
            elif bb is not None and ba is not None and ba - bb > tick + 1e-9:
                ev = book.add("B", round((ba - tick) / tick) * tick) if aggressor_buy else book.add("A", round((bb + tick) / tick) * tick)
            else:
                ev = None
            if ev is not None:
                act, s, p, sz, oid = ev
                seq += 1
                rows.append((ts, ts, symbol, act, s, p, sz, oid, seq, 0))

    df = pd.DataFrame(
        rows,
        columns=["ts_event", "ts_recv", "symbol", "action", "side", "price", "size", "order_id", "sequence", "flags"],
    )
    # Per-symbol generation gives per-symbol sequences; the feed's sequence is
    # a single counter across the whole channel, so renumber after the sort.
    df = df.sort_values(["ts_recv", "sequence"], kind="stable").reset_index(drop=True)
    df["sequence"] = np.arange(1, len(df) + 1, dtype="int64")
    df["ts_event"] = df["ts_event"].astype("int64")
    df["ts_recv"] = df["ts_recv"].astype("int64")
    df["price"] = df["price"].astype("float64")
    df["size"] = df["size"].astype("int64")
    return df


def trades(mbo: pd.DataFrame) -> pd.DataFrame:
    """Trade prints only — the shape of data/market/trades (§4.1)."""
    t = mbo[mbo["action"] == "T"][["ts_event", "ts_recv", "symbol", "price", "size", "side", "sequence"]]
    return t.reset_index(drop=True)


def bars_1m(trade_df: pd.DataFrame) -> pd.DataFrame:
    """1-minute OHLCV + delta/buy/sell volume per symbol from trades."""
    if trade_df.empty:
        return pd.DataFrame(columns=["symbol", "ts", "open", "high", "low", "close", "volume", "delta", "buy_vol", "sell_vol", "trades"])
    df = trade_df.copy()
    df["ts"] = (df["ts_event"] // (60 * NS)) * (60 * NS)
    df["buy_vol"] = np.where(df["side"] == "B", df["size"], 0)
    df["sell_vol"] = np.where(df["side"] == "A", df["size"], 0)
    g = df.groupby(["symbol", "ts"], sort=True)
    out = g.agg(
        open=("price", "first"), high=("price", "max"), low=("price", "min"), close=("price", "last"),
        volume=("size", "sum"), buy_vol=("buy_vol", "sum"), sell_vol=("sell_vol", "sum"), trades=("price", "size"),
    ).reset_index()
    out["delta"] = out["buy_vol"] - out["sell_vol"]
    return out[["symbol", "ts", "open", "high", "low", "close", "volume", "delta", "buy_vol", "sell_vol", "trades"]]


def book_at(mbo: pd.DataFrame, symbol: str, ts_ns: int, depth: int = 10):
    """Brute-force L3 book reconstruction at `ts_ns` — the reference the
    Phase 5 `replay/book.py` tests compare against."""
    orders: dict[int, tuple[str, float, int]] = {}
    sub = mbo[(mbo["symbol"] == symbol) & (mbo["ts_recv"] <= ts_ns)]
    for act, side, px, sz, oid in zip(sub["action"], sub["side"], sub["price"], sub["size"], sub["order_id"]):
        if act == "A":
            orders[oid] = (side, px, sz)
        elif act == "C":
            cur = orders.get(oid)
            if cur is not None:
                left = cur[2] - sz
                if left <= 0:
                    orders.pop(oid, None)
                else:
                    orders[oid] = (cur[0], cur[1], left)
        elif act == "F":
            pass  # size change arrives as the accompanying C record
        elif act == "R":
            orders.clear()
    agg: dict[tuple[str, float], int] = {}
    for s, p, sz in orders.values():
        agg[(s, p)] = agg.get((s, p), 0) + sz
    bids = sorted(((p, v) for (s, p), v in agg.items() if s == "B"), reverse=True)[:depth]
    asks = sorted(((p, v) for (s, p), v in agg.items() if s == "A"))[:depth]
    return bids, asks


def session_bounds_ns(cfg: SynthConfig | None = None) -> tuple[int, int]:
    return _session_bounds_ns(cfg or SynthConfig())
