"""Build the sparse one-second liquidity read model from source DBN files.

Usage:
    python scripts/build_liquidity_1s.py <file1.dbn.zst> [<file2> ...]
    python scripts/build_liquidity_1s.py --all
    python scripts/build_liquidity_1s.py --all --rebuild
    python scripts/build_liquidity_1s.py --all --database=../mbo-data/liquidity.build.duckdb

The source DBN files are required because their ``ts_recv`` index preserves
feed order.  The older raw DuckDB table intentionally did not store that
field, and its ``ts_event`` cannot correctly order historical snapshots.
"""

import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from duckdb_store import get_connection as get_market_connection
from liquidity_store import DEFAULT_MIN_SIZE, get_connection, materialize_dbn_file


RAW_DIR = Path(__file__).resolve().parent.parent.parent / "market-data" / "apr-jul-databento"
DATE_RE = re.compile(r"(20\d{6})")


def file_day(path: Path):
    match = DATE_RE.search(path.name)
    if not match:
        raise ValueError(f"Cannot find YYYYMMDD date in {path.name}")
    return datetime.strptime(match.group(1), "%Y%m%d").replace(tzinfo=timezone.utc)


def front_symbol(market, path: Path) -> str:
    day = file_day(path)
    row = market.execute(
        """
        SELECT symbol, sum(volume) AS volume
        FROM bars_1m
        WHERE ts >= ? AND ts < ?
        GROUP BY symbol
        ORDER BY volume DESC, symbol
        LIMIT 1
        """,
        [day.replace(tzinfo=None), (day + timedelta(days=1)).replace(tzinfo=None)],
    ).fetchone()
    if row is None:
        raise RuntimeError(f"No minute bars found for {path.name}")
    return row[0]


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        raise SystemExit(1)
    rebuild = "--rebuild" in args
    args = [arg for arg in args if arg != "--rebuild"]
    database_arg = next((arg for arg in args if arg.startswith("--database=")), None)
    args = [arg for arg in args if not arg.startswith("--database=")]
    database = Path(database_arg.split("=", 1)[1]).resolve() if database_arg else None
    files = (
        sorted(RAW_DIR.glob("2026-*/glbx-mdp3-*.mbo.dbn.zst"))
        if args == ["--all"]
        else [Path(arg) for arg in args]
    )

    market = get_market_connection(read_only=True)
    liquidity = get_connection(read_only=False, path=database)
    if rebuild:
        liquidity.execute("DELETE FROM liquidity_changes_1s")
        liquidity.execute("DELETE FROM liquidity_scale_1m")
        liquidity.execute("DELETE FROM liquidity_files")
    done = {row[0] for row in liquidity.execute("SELECT filename FROM liquidity_files").fetchall()}
    pending = [path for path in files if path.name not in done]
    print(f"liquidity_1s: {len(done)} built, {len(pending)} pending", flush=True)

    started = time.time()
    total_rows = 0
    for index, path in enumerate(pending, 1):
        symbol = front_symbol(market, path)
        item_started = time.time()
        print(f"[{index}/{len(pending)}] {path.name}: {symbol}", flush=True)
        rows = materialize_dbn_file(liquidity, path, symbol, min_size=DEFAULT_MIN_SIZE)
        total_rows += rows
        print(f"  {rows:,} changes in {time.time() - item_started:.1f}s", flush=True)

    count = liquidity.execute("SELECT count(*) FROM liquidity_changes_1s").fetchone()[0]
    market.close()
    liquidity.close()
    print(
        f"done: {total_rows:,} new changes, {count:,} total rows, "
        f"{time.time() - started:.1f}s",
        flush=True,
    )


if __name__ == "__main__":
    main()
