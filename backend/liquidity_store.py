"""Sparse, materialised one-second resting-liquidity read model.

The raw MBO table is the archival source of truth, but it is the wrong shape
for an interactive chart: finding the book at a viewport start requires
replaying millions of order messages.  This module reads a much smaller
change stream instead.  Each row is the end-of-second size of a price level
that is large enough to be a heatmap candidate; a zero row closes a level.

Timestamps come from DBN ``ts_recv`` (feed order), not ``ts_event``.  Initial
historical snapshots intentionally contain old order-event timestamps while
all being received at the start of the requested day.  Using ``ts_event``
therefore makes a snapshot appear to have existed hours or days too early.
"""

from __future__ import annotations

from datetime import datetime, timezone
from heapq import nlargest
from operator import itemgetter
from pathlib import Path

import databento as db
import duckdb
import pandas as pd


from market.paths import get_paths


def default_db_path() -> Path:
    """data/market/liquidity_1s.duckdb (multi-root; Phase 1 moved it out of mbo-data/)."""
    return get_paths().liquidity_db

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS liquidity_changes_1s (
    session_date DATE       NOT NULL,
    symbol       VARCHAR    NOT NULL,
    ts           BIGINT     NOT NULL,
    price_nanos  BIGINT     NOT NULL,
    side         VARCHAR(1) NOT NULL,
    size         INTEGER    NOT NULL,
    PRIMARY KEY (session_date, symbol, ts, price_nanos, side)
);

CREATE TABLE IF NOT EXISTS liquidity_scale_1m (
    session_date DATE      NOT NULL,
    symbol       VARCHAR   NOT NULL,
    ts           BIGINT    NOT NULL,
    scale_max    INTEGER   NOT NULL,
    PRIMARY KEY (session_date, symbol, ts)
);

CREATE TABLE IF NOT EXISTS liquidity_files (
    filename      VARCHAR PRIMARY KEY,
    session_date  DATE      NOT NULL,
    symbol        VARCHAR   NOT NULL,
    min_size      INTEGER   NOT NULL,
    row_count     BIGINT    NOT NULL,
    built_at      TIMESTAMP NOT NULL
);
"""

COLOR_GAMMA = 1.35
MIN_INTENSITY = 0.90
MIN_SIZE_RATIO = MIN_INTENSITY ** (1 / COLOR_GAMMA)
MAX_CANDIDATE_LEVELS = 64
SCALE_SAMPLE_SECONDS = 60
# ES whole-book p95 is commonly around 60 contracts in this dataset.  Fifty
# keeps the complete orange/red candidate set while still discarding the vast
# majority of ordinary depth before it reaches disk.
DEFAULT_MIN_SIZE = 50
INT64_NULL_PRICE = 9_223_372_036_854_775_807


def get_connection(*, read_only: bool = True, path: Path | None = None):
    database = path or default_db_path()
    if read_only and not database.exists():
        return None
    database.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(database), read_only=read_only)
    if not read_only:
        con.execute(SCHEMA_SQL)
        con.execute("SET preserve_insertion_order = false")
        con.execute("SET threads = 4")
    return con


def materialize_dbn_file(
    con,
    path: Path,
    symbol: str,
    *,
    min_size: int = DEFAULT_MIN_SIZE,
    chunk_rows: int = 2_000_000,
) -> int:
    """Build sparse one-second changes for one symbol in one DBN file.

    The loop maintains both order-id state and aggregated price levels.  A
    row is written only when an above-threshold level changes at the end of
    a second, or when it drops below the threshold (size zero closes it).
    This run-length representation turns ~17M front-contract messages in a
    busy day into roughly 25K rows without losing a hot level's lifetime.
    """
    book: dict[int, tuple[str, int, int]] = {}
    levels: dict[tuple[str, int], int] = {}
    published: dict[tuple[str, int], int] = {}
    dirty: set[tuple[str, int]] = set()
    rows: list[tuple[object, str, int, int, str, int]] = []
    scale_rows: list[tuple[object, str, int, int]] = []
    current_second: int | None = None
    sampled_minute: int | None = None

    def flush(second: int):
        for key in dirty:
            value = levels.get(key, 0)
            previous = published.get(key)
            if value >= min_size:
                if previous != value:
                    side, price_nanos = key
                    rows.append((session_date, symbol, second, price_nanos, side, value))
                    published[key] = value
            elif previous is not None:
                side, price_nanos = key
                rows.append((session_date, symbol, second, price_nanos, side, 0))
                published.pop(key, None)
        dirty.clear()

    def sample_scale(second: int):
        nonlocal sampled_minute
        minute = second - (second % 60)
        if sampled_minute == minute:
            return
        sizes = sorted(size for size in levels.values() if size > 0)
        scale = sizes[min(len(sizes) - 1, int(0.95 * len(sizes)))] if sizes else 1
        scale_rows.append((session_date, symbol, minute, scale))
        sampled_minute = minute

    store = db.DBNStore.from_file(path)
    session_date = datetime.fromtimestamp(store.metadata.start / 1_000_000_000, timezone.utc).date()
    for frame in store.to_df(price_type="fixed", pretty_ts=False, count=chunk_rows):
        receive_seconds = frame.index.to_numpy(dtype="int64", copy=False) // 1_000_000_000
        mask = frame["symbol"].to_numpy(copy=False) == symbol
        if not mask.any():
            continue
        selected = frame.loc[mask]
        for second, action, side, price, size, order_id in zip(
            receive_seconds[mask],
            selected["action"].to_numpy(copy=False),
            selected["side"].to_numpy(copy=False),
            selected["price"].to_numpy(copy=False),
            selected["size"].to_numpy(copy=False),
            selected["order_id"].to_numpy(copy=False),
        ):
            second = int(second)
            order_id = int(order_id)
            size = int(size)
            price_nanos = int(price)
            if current_second is None:
                current_second = second
            elif second != current_second:
                flush(current_second)
                if second // 60 != current_second // 60:
                    sample_scale(current_second)
                current_second = second

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
                dirty.update(published)
                book.clear()
                levels.clear()

    if current_second is not None:
        flush(current_second)
        sample_scale(current_second)

    con.execute("BEGIN")
    try:
        con.execute(
            "DELETE FROM liquidity_changes_1s WHERE session_date = ? AND symbol = ?",
            [session_date, symbol],
        )
        con.execute(
            "DELETE FROM liquidity_scale_1m WHERE session_date = ? AND symbol = ?",
            [session_date, symbol],
        )
        if rows:
            output = pd.DataFrame(
                rows,
                columns=["session_date", "symbol", "ts", "price_nanos", "side", "size"],
            )
            con.register("liquidity_output", output)
            con.execute("INSERT INTO liquidity_changes_1s SELECT * FROM liquidity_output")
            con.unregister("liquidity_output")
        if scale_rows:
            scales = pd.DataFrame(
                scale_rows,
                columns=["session_date", "symbol", "ts", "scale_max"],
            )
            con.register("liquidity_scales", scales)
            con.execute("INSERT INTO liquidity_scale_1m SELECT * FROM liquidity_scales")
            con.unregister("liquidity_scales")
        con.execute(
            "INSERT OR REPLACE INTO liquidity_files VALUES (?, ?, ?, ?, ?, now())",
            [path.name, session_date, symbol, min_size, len(rows)],
        )
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return len(rows)


def _query_materialized(
    windows: list[tuple[str, int, int]],
    start: int,
    end: int,
) -> tuple[list[tuple[int, int, str, int]], list[tuple[int, int]]]:
    """Return materialised changes from the UTC day containing ``start``.

    Replaying from that day's midnight is cheap (tens of thousands of sparse
    rows rather than millions of raw MBO messages) and establishes the exact
    state at an arbitrary viewport start.  Daily snapshot boundaries reset
    the state in ``get_heatmap`` below.
    """
    con = get_connection()
    if con is None:
        return [], []
    try:
        clauses: list[str] = []
        params: list[object] = []
        replay_start = (start // 86_400) * 86_400
        for symbol, lo, hi in windows:
            bounded_lo = max(replay_start, lo)
            bounded_hi = min(end + 1, hi)
            if bounded_lo >= bounded_hi:
                continue
            clauses.append("(symbol = ? AND ts >= ? AND ts < ?)")
            params.extend([symbol, bounded_lo, bounded_hi])
        if not clauses:
            return [], []
        changes = con.execute(
            f"""
            SELECT ts, price_nanos, side, size
            FROM liquidity_changes_1s
            WHERE {' OR '.join(clauses)}
            ORDER BY ts, price_nanos, side
            """,
            params,
        ).fetchall()
        scales = con.execute(
            f"""
            SELECT ts, scale_max
            FROM liquidity_scale_1m
            WHERE {' OR '.join(clauses)}
            ORDER BY ts
            """,
            params,
        ).fetchall()
        return changes, scales
    finally:
        con.close()


def get_heatmap(
    windows: list[tuple[str, int, int]],
    start: int,
    end: int,
    bucket_seconds: int,
    min_price: float | None = None,
    max_price: float | None = None,
) -> dict:
    """Render chart buckets from the sparse one-second change stream."""
    changes, scales = _query_materialized(windows, start, end)
    price_min_nanos = round(min_price * 1_000_000_000) if min_price is not None else None
    price_max_nanos = round(max_price * 1_000_000_000) if max_price is not None else None

    active: dict[tuple[int, str], int] = {}
    buckets: list[dict] = []
    scale_candidates: list[int] = []
    change_index = 0
    scale_index = 0
    active_day: int | None = None
    scale_day: int | None = None
    active_scale = 1
    last_scale_sample: int | None = None
    bucket_start = start - (start % bucket_seconds)

    for bucket_time in range(bucket_start, end + 1, bucket_seconds):
        # A row stamped at second S is the book at the end of S.  For wider
        # buckets sample the end of the interval, matching the old replay API.
        sample_time = min(end, bucket_time + bucket_seconds - 1)
        sample_day = sample_time // 86_400
        while change_index < len(changes) and changes[change_index][0] <= sample_time:
            ts, price_nanos, side, size = changes[change_index]
            row_day = ts // 86_400
            if active_day != row_day:
                active.clear()
                active_day = row_day
            key = (int(price_nanos), side)
            if size > 0:
                active[key] = int(size)
            else:
                active.pop(key, None)
            change_index += 1
        while scale_index < len(scales) and scales[scale_index][0] <= sample_time:
            scale_ts, scale_value = scales[scale_index]
            row_day = scale_ts // 86_400
            if scale_day != row_day:
                active_scale = 1
                scale_day = row_day
            active_scale = int(scale_value)
            scale_index += 1
        if active_day != sample_day:
            # DBN files start with a fresh snapshot at UTC midnight.  Never
            # leak a level from yesterday into a day with no matching row.
            active.clear()
            active_day = sample_day
        if scale_day != sample_day:
            active_scale = 1
            scale_day = sample_day

        if last_scale_sample is None or sample_time - last_scale_sample >= SCALE_SAMPLE_SECONDS:
            scale_candidates.append(active_scale)
            last_scale_sample = sample_time

        visible = [
            (price_nanos, size, side)
            for (price_nanos, side), size in active.items()
            if (
                price_min_nanos is None
                or price_min_nanos <= price_nanos <= price_max_nanos
            )
        ]
        strongest = nlargest(MAX_CANDIDATE_LEVELS, visible, key=itemgetter(1))
        buckets.append({
            "t": bucket_time,
            "levels": [
                {"p": round(price_nanos / 1_000_000_000, 4), "s": size, "side": side}
                for price_nanos, size, side in strongest
            ],
        })

    scale_candidates.sort()
    scale_max = (
        scale_candidates[min(len(scale_candidates) - 1, int(0.95 * len(scale_candidates)))]
        if scale_candidates
        else 1
    )
    display_min = scale_max * MIN_SIZE_RATIO
    for bucket in buckets:
        bucket["levels"] = [level for level in bucket["levels"] if level["s"] >= display_min]
    return {
        "bucketSeconds": bucket_seconds,
        "scaleMax": scale_max,
        "displayMin": display_min,
        "buckets": buckets,
        "source": "materialized liquidity_changes_1s",
    }


def has_coverage(windows: list[tuple[str, int, int]]) -> bool:
    """Whether at least one requested symbol/day has been materialised."""
    con = get_connection()
    if con is None:
        return False
    try:
        for symbol, lo, hi in windows:
            row = con.execute(
                """
                SELECT 1 FROM liquidity_files
                WHERE symbol = ? AND session_date >= to_timestamp(?)::DATE
                  AND session_date < to_timestamp(?)::DATE + INTERVAL 1 DAY
                LIMIT 1
                """,
                [symbol, lo, hi],
            ).fetchone()
            if row:
                return True
        return False
    finally:
        con.close()
