"""One-pass L3 book state machine used by ingest (PLATFORM-SPEC.md §4.1).

Fed MBO chunks for a single symbol, it produces two read models at once:

1. the sparse one-second resting-liquidity change stream + per-minute scale
   the heatmap already consumes (`liquidity_store` schema, unchanged), and
2. `book_checkpoints`: every 60 s, the top `CHECKPOINT_DEPTH` aggregated
   levels per side plus the order-map size, so a replay can seek near a
   timestamp without replaying from midnight.

The loop is a straight port of the legacy `liquidity_store.materialize_dbn_file`
(same actions, same `ts_recv` ordering — see that module's docstring for why
`ts_recv`), split into `feed(frame)` / `finish()` so a day's file is decoded
once for trades, bars, liquidity and checkpoints together.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

INT64_NULL_PRICE = 9_223_372_036_854_775_807
DEFAULT_MIN_SIZE = 50
CHECKPOINT_SECONDS = 60
CHECKPOINT_DEPTH = 50


class BookMaterializer:
    def __init__(self, symbol: str, session_date: date, *, min_size: int = DEFAULT_MIN_SIZE,
                 checkpoints: bool = True):
        self.symbol = symbol
        self.session_date = session_date
        self.min_size = min_size
        self.want_checkpoints = checkpoints
        self.book: dict[int, tuple[str, int, int]] = {}
        self.levels: dict[tuple[str, int], int] = {}
        self.published: dict[tuple[str, int], int] = {}
        self.dirty: set[tuple[str, int]] = set()
        self.rows: list[tuple] = []
        self.scale_rows: list[tuple] = []
        self.checkpoint_rows: list[tuple] = []
        self.current_second: int | None = None
        self.sampled_minute: int | None = None
        self.checkpointed_minute: int | None = None
        self.events = 0

    # -- per-second bookkeeping (identical to the legacy materializer) -------

    def _flush(self, second: int):
        for key in self.dirty:
            value = self.levels.get(key, 0)
            previous = self.published.get(key)
            if value >= self.min_size:
                if previous != value:
                    side, price_nanos = key
                    self.rows.append((self.session_date, self.symbol, second, price_nanos, side, value))
                    self.published[key] = value
            elif previous is not None:
                side, price_nanos = key
                self.rows.append((self.session_date, self.symbol, second, price_nanos, side, 0))
                self.published.pop(key, None)
        self.dirty.clear()

    def _sample_scale(self, second: int):
        minute = second - (second % 60)
        if self.sampled_minute == minute:
            return
        sizes = sorted(size for size in self.levels.values() if size > 0)
        scale = sizes[min(len(sizes) - 1, int(0.95 * len(sizes)))] if sizes else 1
        self.scale_rows.append((self.session_date, self.symbol, minute, scale))
        self.sampled_minute = minute

    def _checkpoint(self, second: int):
        """Snapshot the book as of the END of `second` (all its events applied)."""
        if not self.want_checkpoints:
            return
        minute = second - (second % CHECKPOINT_SECONDS)
        if self.checkpointed_minute == minute:
            return
        self.checkpointed_minute = minute
        n_orders = len(self.book)
        bids = sorted(((p, v) for (s, p), v in self.levels.items() if s == "B" and v > 0), reverse=True)[:CHECKPOINT_DEPTH]
        asks = sorted(((p, v) for (s, p), v in self.levels.items() if s == "A" and v > 0))[:CHECKPOINT_DEPTH]
        for p, v in bids:
            self.checkpoint_rows.append((second, self.symbol, "B", p, v, n_orders))
        for p, v in asks:
            self.checkpoint_rows.append((second, self.symbol, "A", p, v, n_orders))

    # -- public API -----------------------------------------------------------

    def feed(self, frame: pd.DataFrame, ts_recv: np.ndarray | None = None) -> None:
        """`frame` holds only this symbol's rows, in feed order. `ts_recv`
        (int64 ns) defaults to the frame index, which is how databento's
        `to_df` hands chunks over."""
        if frame.empty:
            return
        recv = ts_recv if ts_recv is not None else frame.index.to_numpy(dtype="int64", copy=False)
        seconds = recv // 1_000_000_000
        book, levels, dirty = self.book, self.levels, self.dirty
        for second, action, side, price, size, order_id in zip(
            seconds,
            frame["action"].to_numpy(copy=False),
            frame["side"].to_numpy(copy=False),
            frame["price"].to_numpy(copy=False),
            frame["size"].to_numpy(copy=False),
            frame["order_id"].to_numpy(copy=False),
        ):
            second = int(second)
            order_id = int(order_id)
            size = int(size)
            price_nanos = int(price)
            self.events += 1
            if self.current_second is None:
                self.current_second = second
            elif second != self.current_second:
                self._flush(self.current_second)
                if second // 60 != self.current_second // 60:
                    self._sample_scale(self.current_second)
                    self._checkpoint(self.current_second)
                self.current_second = second

            if action in ("A", "M"):
                old = book.pop(order_id, None)
                if old is not None:
                    old_side, old_price, old_size = old
                    old_key = (old_side, old_price)
                    levels[old_key] = levels.get(old_key, 0) - old_size
                    dirty.add(old_key)
                if size > 0 and side in ("A", "B") and price_nanos != INT64_NULL_PRICE:
                    key = (side, price_nanos)
                    book[order_id] = (side, price_nanos, size)
                    levels[key] = levels.get(key, 0) + size
                    dirty.add(key)
            elif action == "C":
                old = book.get(order_id)
                if old is not None:
                    old_side, old_price, old_size = old
                    removed = min(size, old_size)
                    remaining = old_size - removed
                    key = (old_side, old_price)
                    levels[key] = levels.get(key, 0) - removed
                    dirty.add(key)
                    if remaining > 0:
                        book[order_id] = (old_side, old_price, remaining)
                    else:
                        book.pop(order_id, None)
            elif action == "R":
                dirty.update(self.published)
                book.clear()
                levels.clear()

    def finish(self) -> None:
        if self.current_second is not None:
            self._flush(self.current_second)
            self._sample_scale(self.current_second)
            self._checkpoint(self.current_second)

    def liquidity_frames(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        changes = pd.DataFrame(self.rows, columns=["session_date", "symbol", "ts", "price_nanos", "side", "size"])
        scales = pd.DataFrame(self.scale_rows, columns=["session_date", "symbol", "ts", "scale_max"])
        return changes, scales

    def checkpoint_frame(self) -> pd.DataFrame:
        df = pd.DataFrame(self.checkpoint_rows, columns=["ts", "symbol", "side", "price_nanos", "size", "n_orders"])
        if df.empty:
            return pd.DataFrame(columns=["ts", "symbol", "side", "price", "size", "n_orders"])
        df["price"] = df["price_nanos"] / 1e9
        return df[["ts", "symbol", "side", "price", "size", "n_orders"]].astype(
            {"ts": "int64", "size": "int64", "n_orders": "int64"}
        )

    def commit_liquidity(self, con) -> int:
        """Write the change stream into a `liquidity_store` connection
        (replacing this symbol/day) — same transaction shape as the legacy
        materializer so an interrupted run leaves no partial day."""
        changes, scales = self.liquidity_frames()
        con.execute("BEGIN")
        try:
            con.execute("DELETE FROM liquidity_changes_1s WHERE session_date = ? AND symbol = ?",
                        [self.session_date, self.symbol])
            con.execute("DELETE FROM liquidity_scale_1m WHERE session_date = ? AND symbol = ?",
                        [self.session_date, self.symbol])
            if not changes.empty:
                con.register("liquidity_output", changes)
                con.execute("INSERT INTO liquidity_changes_1s SELECT * FROM liquidity_output")
                con.unregister("liquidity_output")
            if not scales.empty:
                con.register("liquidity_scales", scales)
                con.execute("INSERT INTO liquidity_scale_1m SELECT * FROM liquidity_scales")
                con.unregister("liquidity_scales")
            con.execute(
                "INSERT OR REPLACE INTO liquidity_files VALUES (?, ?, ?, ?, ?, now())",
                [f"{self.session_date.isoformat()}:{self.symbol}", self.session_date, self.symbol, self.min_size, len(changes)],
            )
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        return len(changes)
