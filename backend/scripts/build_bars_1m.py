"""Backfill the materialised 1-minute bar table (duckdb_store.bars_1m).

The charts read bars from this table instead of aggregating raw ticks per
request — 1.35B tick rows down to ~300k bar rows. Ingestion keeps it up to
date automatically (see ingest_dbn_to_duckdb.py); this script exists for the
one-time backfill of a store ingested before the table existed, and for
rebuilding it from scratch if it is ever suspected of drift.

Run with the backend stopped — DuckDB allows only one writer, and a
read-only reader blocks it.

Usage:
    python scripts/build_bars_1m.py
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from duckdb_store import get_connection, refresh_bars_1m


def main():
    con = get_connection(read_only=False)

    trades = con.execute(
        "SELECT count(*) FROM mbo_events WHERE action = 'T'"
    ).fetchone()[0]
    if not trades:
        print("No trades in mbo_events — nothing to build.")
        return

    print(f"Aggregating {trades:,} trades into 1-minute bars…")
    t0 = time.time()
    rows = refresh_bars_1m(con)
    print(f"bars_1m: {rows:,} rows in {time.time() - t0:.1f}s")

    span = con.execute("SELECT min(ts), max(ts) FROM bars_1m").fetchone()
    print(f"covering {span[0]} .. {span[1]}")


if __name__ == "__main__":
    main()
