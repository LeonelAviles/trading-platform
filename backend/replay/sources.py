"""Where a replay session reads from.

`DaySource` — production: MBO from the replay cache (book layers), trades
from `data/market/trades` (never needs the cache), bar history / volume at
price / approximate books from `data_store`. Everything is streamed in
100k-row batches (§9 memory note).

`FrameSource` — tests: one in-memory MBO frame (the synthetic generator's
output) with the same interface, checkpoints computed on the fly.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Iterator

import numpy as np
import pandas as pd

from replay.book import L3Book

NS = 1_000_000_000
BATCH = 100_000

TF_SECONDS = {
    "1s": 1, "5s": 5, "15s": 15, "30s": 30,
    "1min": 60, "5min": 300, "15min": 900, "30min": 1800, "1h": 3600, "4h": 14400, "1D": 86400,
}


class Batch:
    """Column arrays for one slice of events, all the same length."""
    __slots__ = ("ts", "action", "side", "price", "size", "order_id", "n")

    def __init__(self, ts, action, side, price, size, order_id):
        self.ts = ts
        self.action = action
        self.side = side
        self.price = price       # float prices for trades-only batches, int nanos for MBO
        self.size = size
        self.order_id = order_id
        self.n = len(ts)


def _arrow_batches(cur, size: int):
    reader = cur.to_arrow_reader(size) if hasattr(cur, "to_arrow_reader") else cur.fetch_record_batch(size)
    yield from reader


def _trade_batch(ts, price, size, side) -> Batch:
    n = len(ts)
    return Batch(ts, ["T"] * n, list(side), price, size, [0] * n)


class Source:
    symbol: str
    root: str
    day: date
    tick_size: float
    first_ts: int
    last_ts: int

    def iter_mbo(self, from_ts: int) -> Iterator[Batch]: ...
    def iter_trades(self, from_ts: int) -> Iterator[Batch]: ...
    def checkpoint(self, ts: int) -> tuple[int | None, dict | None]: ...
    def bars_before(self, tf: str, before_s: int) -> list[dict]: ...
    def trades_before(self, ts: int, n: int) -> list[dict]: ...
    def volume_at_price_before(self, ts: int) -> dict[float, int]: ...
    def approx_book(self, ts: int, depth: int = 20) -> tuple[list, list] | None: ...
    def has_mbo(self) -> bool: ...


# ----------------------------------------------------------------------------

class DaySource(Source):
    def __init__(self, symbol: str, day: date, *, paths=None):
        import data_store
        from replay import warm

        self._warm = warm
        self._ds = data_store
        self.paths = paths or warm.get_paths()
        spec, cont = data_store.resolve(symbol)
        self.root = spec.root
        self.tick_size = spec.tick_size
        self.spec = spec
        self.day = day
        self.symbol = data_store.front_symbol_for(symbol, day) or symbol
        self.requested_symbol = symbol
        d0 = int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp())
        self.first_ts = d0 * NS
        self.last_ts = (d0 + 86400) * NS - 1
        self._con = None

    def _duck(self):
        if self._con is None:
            import duckdb

            self._con = duckdb.connect()
            self._con.execute("SET TimeZone='UTC'")
            self._con.execute("SET memory_limit='512MB'")
            self._con.execute("SET threads=2")
            self._con.execute("SET preserve_insertion_order=true")
        return self._con

    def close(self):
        if self._con is not None:
            self._con.close()
            self._con = None

    def has_mbo(self) -> bool:
        return self._warm.is_cached(self.root, self.day, self.paths)

    def _mbo_path(self):
        return self._warm.day_dir(self.root, self.day, self.paths) / "mbo.parquet"

    def _trades_path(self):
        return self.paths.partition(self.paths.trades_dir, self.root, str(self.day)) / "part.parquet"

    def iter_mbo(self, from_ts: int) -> Iterator[Batch]:
        con = self._duck()
        cur = con.execute(
            f"SELECT ts_recv, action, side, price, size, order_id FROM read_parquet('{self._mbo_path()}') "
            "WHERE symbol = ? AND ts_recv >= ?", [self.symbol, int(from_ts)],
        )
        for rb in _arrow_batches(cur, BATCH):
            yield Batch(
                rb.column("ts_recv").to_numpy(), rb.column("action").to_pylist(), rb.column("side").to_pylist(),
                rb.column("price").to_numpy(), rb.column("size").to_numpy(), rb.column("order_id").to_numpy(),
            )

    def iter_trades(self, from_ts: int) -> Iterator[Batch]:
        p = self._trades_path()
        if not p.exists():
            return
        con = self._duck()
        cur = con.execute(
            f"SELECT ts_recv, price, size, side FROM read_parquet('{p}') WHERE symbol = ? AND ts_recv >= ? "
            "ORDER BY ts_recv, sequence", [self.symbol, int(from_ts)],
        )
        for rb in _arrow_batches(cur, BATCH):
            # DOUBLE prices carry float noise from ingest; snap to 4 decimals so
            # bar/footprint keys match the book-mode path (int nanos / 1e9).
            yield _trade_batch(rb.column("ts_recv").to_numpy(), np.round(rb.column("price").to_numpy(), 4),
                               rb.column("size").to_numpy(), rb.column("side").to_pylist())

    def checkpoint(self, ts: int) -> tuple[int | None, dict | None]:
        return self._warm.load_checkpoint(self.root, self.day, self.symbol, ts, self.paths)

    def bars_before(self, tf: str, before_s: int) -> list[dict]:
        from fastapi import HTTPException

        try:
            bars = self._ds.get_bars(self.symbol, tf, self.first_ts // NS, before_s - 1)
        except HTTPException:
            return []
        recs = []
        times = bars.index.values.astype("datetime64[ns]").astype("int64") // NS
        for t, o, h, lo, c, v, d, bv, sv in zip(times, bars["open"], bars["high"], bars["low"], bars["close"],
                                              bars["volume"], bars["delta"], bars["buy_vol"], bars["sell_vol"]):
            if int(t) >= before_s:
                continue
            recs.append({"time": int(t), "open": float(o), "high": float(h), "low": float(lo), "close": float(c),
                         "volume": int(v), "delta": int(d), "buyVol": int(bv), "sellVol": int(sv)})
        return recs

    def trades_before(self, ts: int, n: int = 300) -> list[dict]:
        p = self._trades_path()
        if not p.exists():
            return []
        df = self._duck().execute(
            f"SELECT ts_recv, price, size, side FROM read_parquet('{p}') WHERE symbol = ? AND ts_recv < ? "
            "ORDER BY ts_recv DESC, sequence DESC LIMIT ?", [self.symbol, int(ts), int(n)],
        ).df()
        return [{"ts": int(t), "price": round(float(px), 4), "size": int(z), "side": str(s)}
                for t, px, z, s in zip(df["ts_recv"][::-1], df["price"][::-1], df["size"][::-1], df["side"][::-1])]

    def volume_at_price_before(self, ts: int) -> dict[float, int]:
        p = self._trades_path()
        if not p.exists():
            return {}
        df = self._duck().execute(
            f"SELECT price, sum(size)::BIGINT AS v FROM read_parquet('{p}') WHERE symbol = ? AND ts_recv < ? GROUP BY 1",
            [self.symbol, int(ts)],
        ).df()
        return {round(float(px), 4): int(v) for px, v in zip(df["price"], df["v"])}

    def approx_book(self, ts: int, depth: int = 20):
        try:
            snap = self._ds.order_book_snapshot(self.symbol, ts // NS, depth=depth)
        except Exception:
            return None
        if not snap["bids"] and not snap["asks"]:
            return None
        return ([[b["price"], b["size"]] for b in snap["bids"]], [[a["price"], a["size"]] for a in snap["asks"]])


# ----------------------------------------------------------------------------

class FrameSource(Source):
    """In-memory source over one synthetic MBO frame (tests)."""

    def __init__(self, mbo: pd.DataFrame, symbol: str, *, tick_size: float = 0.25, root: str = "ES",
                 checkpoint_every_s: int = 60):
        df = mbo[mbo["symbol"] == symbol].sort_values(["ts_recv", "sequence"], kind="stable").reset_index(drop=True)
        self.df = df
        self.symbol = symbol
        self.root = root
        self.tick_size = tick_size
        self.first_ts = int(df["ts_recv"].iloc[0])
        self.last_ts = int(df["ts_recv"].iloc[-1])
        self.day = datetime.fromtimestamp(self.first_ts / NS, tz=timezone.utc).date()
        self._ts = df["ts_recv"].to_numpy(dtype="int64")
        self._price_nanos = np.round(df["price"].to_numpy(dtype="float64") * 1e9).astype("int64")
        self._trades = df[df["action"] == "T"].reset_index(drop=True)
        self._ckpt_every = checkpoint_every_s
        self._ckpts: dict[int, dict] = {}

    def has_mbo(self) -> bool:
        return True

    def iter_mbo(self, from_ts: int) -> Iterator[Batch]:
        i = int(np.searchsorted(self._ts, from_ts, side="left"))
        df = self.df
        while i < len(df):
            j = min(i + BATCH, len(df))
            g = df.iloc[i:j]
            yield Batch(self._ts[i:j], g["action"].tolist(), g["side"].tolist(), self._price_nanos[i:j],
                        g["size"].to_numpy(), g["order_id"].to_numpy())
            i = j

    def iter_trades(self, from_ts: int) -> Iterator[Batch]:
        t = self._trades
        ts = t["ts_recv"].to_numpy(dtype="int64")
        i = int(np.searchsorted(ts, from_ts, side="left"))
        while i < len(t):
            j = min(i + BATCH, len(t))
            g = t.iloc[i:j]
            yield _trade_batch(ts[i:j], g["price"].to_numpy(), g["size"].to_numpy(), g["side"].tolist())
            i = j

    def checkpoint(self, ts: int) -> tuple[int | None, dict | None]:
        sec = (ts // NS) // self._ckpt_every * self._ckpt_every
        if sec * NS <= self.first_ts:
            return None, None
        snap = self._ckpts.get(sec)
        if snap is None:
            book = L3Book()
            for b in self.iter_mbo(self.first_ts):
                book.apply_arrays(b.ts, b.action, b.side, b.price, b.size, b.order_id, upto=sec * NS - 1)
                if int(b.ts[-1]) >= sec * NS:
                    break
            snap = self._ckpts[sec] = book.snapshot()
        return sec * NS, snap

    def bars_before(self, tf: str, before_s: int) -> list[dict]:
        step = TF_SECONDS[tf]
        t = self._trades
        ts_s = t["ts_recv"].to_numpy(dtype="int64") // NS
        sel = ts_s < before_s
        if not sel.any():
            return []
        g = t[sel].copy()
        g["bar"] = (ts_s[sel] // step) * step
        out = []
        for bar, x in g.groupby("bar", sort=True):
            buy = int(x.loc[x["side"] == "B", "size"].sum())
            sell = int(x.loc[x["side"] == "A", "size"].sum())
            out.append({"time": int(bar), "open": float(x["price"].iloc[0]), "high": float(x["price"].max()),
                        "low": float(x["price"].min()), "close": float(x["price"].iloc[-1]),
                        "volume": int(x["size"].sum()), "delta": buy - sell, "buyVol": buy, "sellVol": sell})
        return out

    def trades_before(self, ts: int, n: int = 300) -> list[dict]:
        t = self._trades[self._trades["ts_recv"] < ts].tail(n)
        return [{"ts": int(a), "price": float(b), "size": int(c), "side": str(d)}
                for a, b, c, d in zip(t["ts_recv"], t["price"], t["size"], t["side"])]

    def volume_at_price_before(self, ts: int) -> dict[float, int]:
        t = self._trades[self._trades["ts_recv"] < ts]
        return {float(p): int(v) for p, v in t.groupby("price")["size"].sum().items()}

    def approx_book(self, ts: int, depth: int = 20):
        sec, snap = self.checkpoint(ts)
        if snap is None:
            return None
        b = L3Book()
        b.restore(snap)
        return b.top(depth)
