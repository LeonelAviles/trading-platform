"""Resting-liquidity book view for backtests, read from the MBO-derived
one-second change stream in data/market/liquidity_1s.duckdb (liquidity_store).

Each stored row is the end-of-second size of one price level that held at
least `liquidity_store.DEFAULT_MIN_SIZE` contracts (a zero row closes the
level), so the view the book primitives see is: 1-second resolution, large
levels only — exactly the "where is the big resting size" question they ask.
A tick-exact book needs the Phase 5 replay engine (replay/book.py).

The feed is advanced to each primary-bar close with rows strictly before the
close second, so a bar never sees liquidity that appeared after it closed.
"""

from __future__ import annotations

from datetime import date

import numpy as np

from engine.session import NS

BOOK_DEPTH = 50   # levels per side handed to the primitives, nearest to the last price


def book_primitives_in(spec: dict) -> list[str]:
    """Names of book-updated primitives the spec references (empty = none)."""
    from engine import expr as expr_mod
    from engine.primitives.base import get_class

    entry = spec.get("entry") or {}
    exprs = [entry.get("trigger")] + [s.get("when") for s in entry.get("sequence") or []] + list(spec.get("filters") or [])
    names: list[str] = []
    for e in exprs:
        if e is None:
            continue
        for name, _, _ in expr_mod.referenced_primitives(e):
            try:
                if get_class(name).update_on == "book" and name not in names:
                    names.append(name)
            except KeyError:
                continue
    return names


def covered_days(symbol: str, days: list[date]) -> set[date]:
    """Session days among `days` for which `symbol` has a materialised liquidity file."""
    import liquidity_store

    con = liquidity_store.get_connection()
    if con is None:
        return set()
    try:
        rows = con.execute("SELECT DISTINCT session_date FROM liquidity_files WHERE symbol = ?", [symbol]).fetchall()
    finally:
        con.close()
    have = {r[0] if isinstance(r[0], date) else date.fromisoformat(str(r[0])[:10]) for r in rows}
    return {d for d in days if d in have}


class LiquidityBookFeed:
    """One session day of level changes for one contract, replayed forward."""

    def __init__(self, ts_s: np.ndarray, price_nanos: np.ndarray, side: np.ndarray, size: np.ndarray):
        self.ts_s = ts_s
        self.price_nanos = price_nanos
        self.side = side
        self.size = size
        self.n = len(ts_s)
        self.pos = 0
        self.levels: dict[tuple[str, int], int] = {}

    @classmethod
    def load(cls, symbol: str, day: date) -> "LiquidityBookFeed | None":
        import liquidity_store

        con = liquidity_store.get_connection()
        if con is None:
            return None
        try:
            rows = con.execute(
                "SELECT ts, price_nanos, side, size FROM liquidity_changes_1s WHERE symbol = ? AND session_date = ? "
                "ORDER BY ts, price_nanos, side",
                [symbol, day],
            ).fetchnumpy()
        finally:
            con.close()
        return cls(np.asarray(rows["ts"], dtype=np.int64), np.asarray(rows["price_nanos"], dtype=np.int64),
                   np.asarray(rows["side"]).astype(str), np.asarray(rows["size"], dtype=np.int64))

    def advance(self, now_ns: int) -> None:
        """Apply every end-of-second row strictly before the second containing `now_ns`."""
        now_s = now_ns // NS
        ts, pn, sd, sz, lv = self.ts_s, self.price_nanos, self.side, self.size, self.levels
        i = self.pos
        while i < self.n and ts[i] < now_s:
            key = (sd[i], int(pn[i]))
            if sz[i] <= 0:
                lv.pop(key, None)
            else:
                lv[key] = int(sz[i])
            i += 1
        self.pos = i

    def view(self, last_price: float, depth: int = BOOK_DEPTH) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
        """(bids, asks) nearest to `last_price`: bids high→low, asks low→high, as (price, size)."""
        bids = [(p / 1e9, float(s)) for (side, p), s in self.levels.items() if side == "B"]
        asks = [(p / 1e9, float(s)) for (side, p), s in self.levels.items() if side == "A"]
        bids = sorted(sorted(bids, key=lambda x: abs(x[0] - last_price))[:depth], key=lambda x: -x[0])
        asks = sorted(sorted(asks, key=lambda x: abs(x[0] - last_price))[:depth], key=lambda x: x[0])
        return bids, asks
