"""Ingest raw Databento MBO .dbn.zst files into the DuckDB tick store —
decodes straight from the binary format via polars, no CSV/Postgres
involved. Run with the backend stopped (DuckDB allows one writer at a time).

Usage:
    python scripts/ingest_dbn_to_duckdb.py <file1.dbn.zst> [<file2.dbn.zst> ...]
    python scripts/ingest_dbn_to_duckdb.py --all   # every file under market-data/apr-jul-databento

Only symbols that actually feed the continuous ES1! series are ingested:
outright quarterly contracts (ESM6, ESU6, ...) that traded a non-trivial
volume that day. Everything else is dropped at ingest — calendar spreads
(ESM6-ESU6) price a *difference* between two contracts, not a standalone
price, and far-dated/illiquid months carry no usable price signal.

That filter is deliberately volume-based rather than a hardcoded contract
list, because the front month rolls over time (ESM6 -> ESU6 mid-June in
this dataset) and build_continuous.py picks it per-day by comparing
volumes across outrights — so the runner-up contract has to survive
ingestion too, or there'd be nothing to compare against and no way to see
the roll. Measured on the first 12 days ingested: the top two outrights
were 99.8% of all events anyway, so this filter mostly trims noise (many
excluded symbols had literally 2 events and zero volume) rather than
saving dramatic amounts of space.
"""

import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import databento as db
import polars as pl

from duckdb_store import get_connection, refresh_bars_1m
from liquidity_store import get_connection as get_liquidity_connection, materialize_dbn_file

RAW_DIR = Path(__file__).resolve().parent.parent.parent / "market-data" / "apr-jul-databento"
CHUNK_ROWS = 2_000_000
# Rows per INSERT transaction — bounds peak commit memory independently of
# how big a day's file is. See the comment at the insert site.
INSERT_BATCH_ROWS = 1_000_000
INT64_NULL_PRICE = 9223372036854775807
PRICE_SCALE = 1e-9

# Outright quarterly contracts only (ESM6, ESU6, ESZ6...) — excludes
# calendar spreads like ESM6-ESU6. Same pattern build_continuous.py uses.
OUTRIGHT_RE = r"^ES[HMUZ]\d$"
# A contract needs at least this much traded volume in a day to be worth
# storing. Low enough to keep a legitimately-rolling runner-up contract
# (ESU6 traded ~9k over the pre-roll days), high enough to drop the
# long tail of far-dated months with 0-73 volume.
MIN_DAILY_VOLUME = 1_000

COLUMNS = [
    "symbol", "ts_event", "action", "side", "price", "size",
    "order_id", "sequence", "flags", "ts_in_delta",
    "channel_id", "instrument_id", "publisher_id", "rtype",
]


def decode_day(path: Path) -> pl.DataFrame:
    """Every MBO event in the file, streamed in chunks so peak memory
    doesn't scale with file size (some days are 500MB+ compressed)."""
    store = db.DBNStore.from_file(path)
    parts = [pl.from_pandas(chunk) for chunk in store.to_df(price_type="fixed", pretty_ts=False, count=CHUNK_ROWS)]
    df = pl.concat(parts)
    df = df.with_columns(
        pl.when(pl.col("price") == INT64_NULL_PRICE)
          .then(None)
          .otherwise(pl.col("price") * PRICE_SCALE)
          .alias("price"),
        pl.from_epoch(pl.col("ts_event"), time_unit="ns").alias("ts_event"),
    )
    return df.select(COLUMNS)


def relevant_symbols(df: pl.DataFrame) -> list[str]:
    """Outright contracts with real traded volume that day — see module
    docstring for why this is volume-based rather than a fixed list."""
    volumes = (
        df.filter(
            (pl.col("action") == "T") & pl.col("symbol").str.contains(OUTRIGHT_RE)
        )
        .group_by("symbol")
        .agg(pl.col("size").sum().alias("volume"))
        .filter(pl.col("volume") >= MIN_DAILY_VOLUME)
        .sort("volume", descending=True)
    )
    return volumes["symbol"].to_list()


def ingest_file(con, liquidity, path: Path) -> int:
    print(f"[{path.name}] decoding...", flush=True)
    raw = decode_day(path)
    keep = relevant_symbols(raw)
    if not keep:
        print(f"[{path.name}] no outright contract cleared {MIN_DAILY_VOLUME:,} volume — skipped", flush=True)
        return 0

    df = raw.filter(pl.col("symbol").is_in(keep))  # noqa: F841 — DuckDB reads `df` by name below
    dropped = raw.height - df.height
    print(
        f"[{path.name}] {raw.height:,} events -> keeping {df.height:,} "
        f"for {', '.join(keep)} (dropped {dropped:,}), loading...",
        flush=True,
    )
    # Insert in bounded batches rather than one statement per file. A whole
    # file is 15-28M rows, and committing that at once makes DuckDB hold the
    # entire commit in memory — which is what killed the first two backfill
    # attempts on this 8.6 GB host ("Failed to commit: failed to pin block").
    cols = ", ".join(COLUMNS)
    for offset in range(0, df.height, INSERT_BATCH_ROWS):
        batch = df.slice(offset, INSERT_BATCH_ROWS)  # noqa: F841 — read by name below
        con.execute(f"INSERT INTO mbo_events SELECT {cols} FROM batch")
    con.execute(
        "INSERT OR REPLACE INTO ingested_files VALUES (?, now(), ?)",
        [path.name, df.height],
    )

    # Refresh the materialised bars for exactly the minutes this file
    # touched. The charts read bars_1m, not mbo_events, so skipping this
    # would leave freshly ingested days invisible in the UI — a stale read
    # model is a worse failure than a slow one, because nothing looks wrong.
    # Bounded by the file's own span, so the cost is per-day, not per-store.
    lo, hi = df["ts_event"].min(), df["ts_event"].max()
    bars = refresh_bars_1m(con, lo, hi + timedelta(minutes=1))
    liquidity_rows = materialize_dbn_file(liquidity, path, keep[0])
    print(
        f"[{path.name}] done: {df.height:,} events ingested, "
        f"bars_1m now {bars:,} rows, {liquidity_rows:,} liquidity changes",
        flush=True,
    )
    return df.height


def materialized_front_symbol(con, path: Path) -> str:
    """Front contract for a file whose raw insertion already committed.

    If liquidity materialisation was interrupted, the next run can finish
    the missing read model without duplicating raw MBO events.
    """
    match = re.search(r"(20\d{6})", path.name)
    if not match:
        raise ValueError(f"Cannot find YYYYMMDD date in {path.name}")
    day = datetime.strptime(match.group(1), "%Y%m%d")
    row = con.execute(
        """
        SELECT symbol, sum(volume) AS volume
        FROM bars_1m
        WHERE ts >= ? AND ts < ?
        GROUP BY symbol
        ORDER BY volume DESC, symbol
        LIMIT 1
        """,
        [day, day + timedelta(days=1)],
    ).fetchone()
    if row is None:
        raise RuntimeError(f"No minute bars found for {path.name}")
    return row[0]


def main():
    if not sys.argv[1:]:
        print(__doc__)
        sys.exit(1)
    files = (
        sorted(RAW_DIR.glob("2026-*/glbx-mdp3-*.mbo.dbn.zst"))
        if sys.argv[1] == "--all"
        else [Path(p) for p in sys.argv[1:]]
    )

    con = get_connection(read_only=False)
    liquidity = get_liquidity_connection(read_only=False)
    done = {r[0] for r in con.execute("SELECT filename FROM ingested_files").fetchall()}
    liquidity_done = {
        r[0] for r in liquidity.execute("SELECT filename FROM liquidity_files").fetchall()
    }
    liquidity_pending = [p for p in files if p.name in done and p.name not in liquidity_done]
    pending = [p for p in files if p.name not in done]
    if done:
        print(f"resuming: {len(done)} file(s) already ingested, {len(pending)} to go\n", flush=True)

    for i, path in enumerate(liquidity_pending, 1):
        symbol = materialized_front_symbol(con, path)
        print(
            f"=== liquidity recovery [{i}/{len(liquidity_pending)}] "
            f"{path.name}: {symbol} ===",
            flush=True,
        )
        materialize_dbn_file(liquidity, path, symbol)

    total = 0
    for i, path in enumerate(pending, 1):
        print(f"=== [{i}/{len(pending)}] {path.name} ===", flush=True)
        total += ingest_file(con, liquidity, path)
    liquidity.close()
    con.close()
    print(f"\nAll done: {total:,} events ingested across {len(pending)} files.")


if __name__ == "__main__":
    main()
