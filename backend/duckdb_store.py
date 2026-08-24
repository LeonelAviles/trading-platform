"""Connection helper for the DuckDB-backed raw MBO tick store.

Replaces the mbo_events/bars/market_data Postgres tables (dropped in
migration 90bc011f231e) after real, measured evidence: the same file took
Postgres/TimescaleDB 18+ minutes (still unfinished, 11.26M rows, 5 live
indexes, a UUID per row) versus 22.2 seconds end-to-end in DuckDB + polars
+ Parquet, at ~3x better compression. See scripts/ingest_dbn_to_duckdb.py
for ingestion.

Schema is a single flat `mbo_events` table — no surrogate keys, no parent
"market_data" row per symbol/day the way the Postgres ERD required. DuckDB
does fast columnar filtering directly on symbol/ts_event, so that
indirection buys nothing here; bars are computed on demand with SQL
(sub-second even over millions of rows — see get_bars()) rather than
pre-materialized and stored.

Concurrency: a read-write connection takes an *exclusive* file lock, so no
other process can open the database at all while it is held — not even
read-only. Several read-only processes can share the file happily, but a
writer and a reader cannot coexist. The app always opens read-only;
scripts/ingest_dbn_to_duckdb.py is the only writer, and the backend must be
stopped before it can run. A consequence worth knowing: a read-only handle
is pinned to the snapshot it opened, so new data only becomes visible after
a restart — which is what lets data_store memoise aggregates for the life of
the process without an invalidation key.
"""

from pathlib import Path

import duckdb

DB_PATH = Path(__file__).resolve().parent.parent / "mbo-data" / "mbo.duckdb"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS mbo_events (
    symbol         VARCHAR NOT NULL,
    ts_event       TIMESTAMP NOT NULL,
    action         VARCHAR(1) NOT NULL,
    side           VARCHAR(1),
    price          DOUBLE,
    size           DOUBLE NOT NULL,
    order_id       BIGINT NOT NULL,
    sequence       BIGINT NOT NULL,
    flags          INTEGER NOT NULL,
    ts_in_delta    BIGINT NOT NULL,
    channel_id     INTEGER,
    instrument_id  BIGINT,
    publisher_id   INTEGER,
    rtype          INTEGER
);
-- Deliberately NO index on (symbol, ts_event). Measured, on this data:
--   * it was the direct cause of repeated ingest OOMs — the crash stack was
--     in AppendToIndexes/BufferChunk, since an ART index has to buffer and
--     merge on every append, which an 8.6 GB host can't absorb;
--   * it cost 6.2 GB of 11 GB total database size for only 36 files;
--   * and it made reads *slower*, not faster — the front-month scan took
--     166s over 250M rows with it vs 1.08s over 408M rows without, because
--     DuckDB's columnar zone-map scan beats index lookups for the
--     full-scan aggregates every read path here actually does.
-- Add one only if a genuinely selective point-lookup workload shows up, and
-- re-measure ingest memory if you do.

-- Materialised 1-minute bars: the read model the charts are actually served
-- from. Deriving bars from ticks at request time meant a full scan of 1.35B
-- rows (~16-100s on this host) to produce ~120k bars, every time; this table
-- is ~300k rows total, so the same query is a few milliseconds.
--
-- Every interval the UI offers (1min..1D) is a whole multiple of a minute,
-- so rolling these up is exactly equivalent to aggregating the raw ticks:
-- open/close come from the first/last minute in the bucket, high/low are
-- max/min of the minute extremes, and volume sums. Anything sub-minute
-- would still have to go back to mbo_events.
--
-- `delta` (signed traded volume: + for a buy aggressor, - for a sell one)
-- rides along so CVD is a running sum over this table too, rather than a
-- second full scan of the ticks.
CREATE TABLE IF NOT EXISTS bars_1m (
    symbol  VARCHAR   NOT NULL,
    ts      TIMESTAMP NOT NULL,   -- minute bucket start, UTC
    open    DOUBLE    NOT NULL,
    high    DOUBLE    NOT NULL,
    low     DOUBLE    NOT NULL,
    close   DOUBLE    NOT NULL,
    volume  DOUBLE    NOT NULL,
    delta   DOUBLE    NOT NULL,
    PRIMARY KEY (symbol, ts)
);

-- One row per successfully ingested source file, so a re-run resumes
-- instead of duplicating. Keyed on filename rather than on the dates found
-- inside: a CME session opens the prior evening, so a file's rows spill
-- into the previous calendar day and date-based dedup would be wrong.
CREATE TABLE IF NOT EXISTS ingested_files (
    filename    VARCHAR PRIMARY KEY,
    ingested_at TIMESTAMP NOT NULL,
    row_count   BIGINT NOT NULL
);
"""

# Bulk-load tuning for a memory-constrained host (this machine has 8.6 GB
# of RAM total, shared with the databento+polars decode side which holds a
# multi-GB frame while the insert runs).
#
# The binding constraint turned out NOT to be these settings but the
# *transaction size*: inserting a whole file (15-28M rows) in one statement
# makes DuckDB hold the entire commit in memory, which dies with "Failed to
# commit: failed to pin block". Capping memory_limit made that fail sooner,
# not later. The actual fix is batching the insert (see INSERT_BATCH_ROWS in
# scripts/ingest_dbn_to_duckdb.py); these just help at the margin.
#
# preserve_insertion_order=false is DuckDB's documented win for bulk loads,
# and is safe here because every read path sorts or buckets by ts_event.
WRITE_PRAGMAS = """
SET preserve_insertion_order = false;
SET threads = 4;
"""


def get_connection(read_only: bool = True) -> duckdb.DuckDBPyConnection:
    DB_PATH.parent.mkdir(exist_ok=True)
    con = duckdb.connect(str(DB_PATH), read_only=read_only)
    if not read_only:
        con.execute(SCHEMA_SQL)
        con.execute(f"SET temp_directory = '{DB_PATH.parent / 'duckdb_tmp'}'")
        con.execute(WRITE_PRAGMAS)
    return con


# The aggregate that defines bars_1m. Kept in one place because both the
# initial backfill and the per-file refresh after an ingest must produce
# byte-identical bars — a bar built one way and refreshed the other would be
# an invisible inconsistency in the read model.
#
# Picking a minute's open and close needs a total order over its trades, and
# ts_event alone does not give one: an aggressor sweeping several price
# levels produces multiple fills sharing a timestamp *and* a `sequence`.
# Insertion order can't break the tie either — bulk loads run with
# preserve_insertion_order=false.
#
# So the tie is broken by sweep direction. `side` on a Databento trade is
# the *aggressor's* side, which was confirmed against this data rather than
# assumed: over one week of ESM6, of the sweeps whose direction can be
# inferred from the preceding print, side='A' started at the high 13,958
# times vs 2,081 at the low, and side='B' started at the low 18,076 times vs
# 12 at the high. So 'A' is a seller walking *down* the book and 'B' a buyer
# walking up it. Ordering each tied group by signed price therefore replays
# the real sweep — first print at the top of book, last at the deepest level
# reached. Deterministic (a rebuild reproduces identical bars) and correct,
# where ordering by ts_event alone was merely arbitrary.
BARS_1M_SELECT = """
    SELECT symbol,
           time_bucket(INTERVAL 1 MINUTE, ts_event)          AS ts,
           first(price ORDER BY ts_event, sequence, _sweep)  AS open,
           max(price)                                        AS high,
           min(price)                                        AS low,
           last(price ORDER BY ts_event, sequence, _sweep)   AS close,
           sum(size)                                         AS volume,
           sum(CASE WHEN side = 'B' THEN size
                    WHEN side = 'A' THEN -size
                    ELSE 0 END)                              AS delta
    FROM (
        SELECT *, CASE WHEN side = 'A' THEN -price ELSE price END AS _sweep
        FROM mbo_events
        WHERE action = 'T' AND price IS NOT NULL {window}
    )
    GROUP BY 1, 2
"""


def refresh_bars_1m(con, lo=None, hi=None) -> int:
    """(Re)build bars_1m over [lo, hi), or the whole store when unbounded.

    Delete-then-insert rather than upsert: a minute that lost its last trade
    (a re-ingest of corrected data, say) has to disappear, and an upsert
    would leave the stale bar behind. The delete is bounded by the same
    window as the insert, so a per-file refresh never touches other days.
    """
    window, bounds, params = "", [], []
    if lo is not None:
        window += " AND ts_event >= ?"
        bounds.append("ts >= ?")
        params.append(lo)
    if hi is not None:
        window += " AND ts_event < ?"
        bounds.append("ts < ?")
        params.append(hi)

    if bounds:
        con.execute(f"DELETE FROM bars_1m WHERE {' AND '.join(bounds)}", params)
    else:
        con.execute("DELETE FROM bars_1m")

    con.execute(f"INSERT INTO bars_1m {BARS_1M_SELECT.format(window=window)}", params)
    return con.execute("SELECT count(*) FROM bars_1m").fetchone()[0]
