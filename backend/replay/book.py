"""L3 order book rebuilt from Databento MBO records (PLATFORM-SPEC.md §4.11).

`order_id -> (side, price, size)` plus per-level aggregates. Actions:

- `A` add, `M` modify (remove the old order, add the new one — Databento
  re-keys size/price/side under the same id), `C` cancel (partial or full),
- `T` trade and `F` fill: no book change here — the resting side's size
  change arrives as the accompanying `C` record,
- `R` clear (start of a snapshot or a book reset); records flagged
  `F_SNAPSHOT` are plain adds that follow the clear.

Ordering is `ts_recv` (see `liquidity_store.py` for why). Prices are the
fixed-point int64 nanos Databento emits, so keys never suffer float drift;
`top()` converts to floats for the wire.

Seeks: `snapshot()` / `restore()` round-trip the order map through compact
column arrays, which is what `warm.py` stores at checkpoints. The same class
serves `BookMaterializer` semantics (it is a straight extraction of that
loop) so a checkpoint restored here matches the ingest-time aggregates.
"""

from __future__ import annotations

import numpy as np

INT64_NULL_PRICE = 9_223_372_036_854_775_807
F_SNAPSHOT = 32
NS = 1_000_000_000


class L3Book:
    __slots__ = ("orders", "levels", "events", "last_ts")

    def __init__(self):
        self.orders: dict[int, tuple[str, int, int]] = {}
        self.levels: dict[tuple[str, int], int] = {}
        self.events = 0
        self.last_ts: int | None = None

    # -- mutation --------------------------------------------------------------

    def apply(self, action: str, side: str, price: int, size: int, order_id: int) -> bool:
        """Apply one record. Returns True when a level changed."""
        orders, levels = self.orders, self.levels
        self.events += 1
        if action == "A" or action == "M":
            changed = False
            old = orders.pop(order_id, None)
            if old is not None:
                key = (old[0], old[1])
                left = levels.get(key, 0) - old[2]
                if left > 0:
                    levels[key] = left
                else:
                    levels.pop(key, None)
                changed = True
            if size > 0 and (side == "A" or side == "B") and price != INT64_NULL_PRICE:
                key = (side, price)
                orders[order_id] = (side, price, size)
                levels[key] = levels.get(key, 0) + size
                changed = True
            return changed
        if action == "C":
            old = orders.get(order_id)
            if old is None:
                return False
            removed = size if size < old[2] else old[2]
            remaining = old[2] - removed
            key = (old[0], old[1])
            left = levels.get(key, 0) - removed
            if left > 0:
                levels[key] = left
            else:
                levels.pop(key, None)
            if remaining > 0:
                orders[order_id] = (old[0], old[1], remaining)
            else:
                orders.pop(order_id, None)
            return True
        if action == "R":
            had = bool(orders)
            orders.clear()
            levels.clear()
            return had
        return False  # T, F, N: no resting change

    def apply_arrays(self, ts, action, side, price, size, order_id, upto: int | None = None) -> int:
        """Apply a batch of parallel arrays (python lists or numpy). Stops
        before the first event with ts > `upto` when given; returns the number
        of records consumed."""
        n = len(action)
        apply = self.apply
        i = 0
        while i < n:
            t = int(ts[i])
            if upto is not None and t > upto:
                break
            apply(action[i], side[i], int(price[i]), int(size[i]), int(order_id[i]))
            self.last_ts = t
            i += 1
        return i

    # -- queries ---------------------------------------------------------------

    def top(self, depth: int = 20) -> tuple[list[list[float]], list[list[float]]]:
        """(bids desc, asks asc) as [[price, size], ...] with float prices."""
        bids = sorted(((p, v) for (s, p), v in self.levels.items() if s == "B" and v > 0), reverse=True)[:depth]
        asks = sorted(((p, v) for (s, p), v in self.levels.items() if s == "A" and v > 0))[:depth]
        return [[p / 1e9, v] for p, v in bids], [[p / 1e9, v] for p, v in asks]

    def top_nanos(self, depth: int = 50) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
        bids = sorted(((p, v) for (s, p), v in self.levels.items() if s == "B" and v > 0), reverse=True)[:depth]
        asks = sorted(((p, v) for (s, p), v in self.levels.items() if s == "A" and v > 0))[:depth]
        return bids, asks

    def best(self) -> tuple[float | None, float | None]:
        bid = max((p for (s, p), v in self.levels.items() if s == "B" and v > 0), default=None)
        ask = min((p for (s, p), v in self.levels.items() if s == "A" and v > 0), default=None)
        return (bid / 1e9 if bid is not None else None, ask / 1e9 if ask is not None else None)

    def __len__(self) -> int:
        return len(self.orders)

    # -- checkpoints -----------------------------------------------------------

    def snapshot(self) -> dict[str, np.ndarray]:
        n = len(self.orders)
        ids = np.empty(n, dtype=np.int64)
        sides = np.empty(n, dtype="U1")
        prices = np.empty(n, dtype=np.int64)
        sizes = np.empty(n, dtype=np.int32)
        for i, (oid, (s, p, z)) in enumerate(self.orders.items()):
            ids[i] = oid
            sides[i] = s
            prices[i] = p
            sizes[i] = z
        return {"order_id": ids, "side": sides, "price": prices, "size": sizes}

    def restore(self, snap: dict) -> None:
        self.orders.clear()
        self.levels.clear()
        orders, levels = self.orders, self.levels
        for oid, s, p, z in zip(snap["order_id"], snap["side"], snap["price"], snap["size"]):
            s = str(s)
            p = int(p)
            z = int(z)
            orders[int(oid)] = (s, p, z)
            key = (s, p)
            levels[key] = levels.get(key, 0) + z

    def copy(self) -> "L3Book":
        b = L3Book()
        b.orders = dict(self.orders)
        b.levels = dict(self.levels)
        b.events = self.events
        b.last_ts = self.last_ts
        return b
