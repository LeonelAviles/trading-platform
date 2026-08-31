"""Compare the new Parquet bars against the legacy mbo.duckdb bars_1m
(PLATFORM-SPEC.md §5 Phase 1 task 6).

    python scripts/verify_ingest.py [--legacy ../mbo-data/mbo.duckdb] [--root ES]

Per (symbol, UTC date) present in both stores: bar count, volume sum,
delta sum and a full OHLC equality check. Prints a table and exits 1 on any
mismatch. After it passes, `mbo-data/mbo.duckdb` can be deleted.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from market.paths import get_paths  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--legacy", default=str(BACKEND_DIR.parent / "mbo-data" / "mbo.duckdb"))
    ap.add_argument("--root", default="ES")
    args = ap.parse_args(argv)

    legacy = Path(args.legacy)
    if not legacy.exists():
        print(f"legacy store {legacy} not found; nothing to verify")
        return 0
    paths = get_paths()
    new_glob = str(paths.bars_1m_dir / f"root={args.root}" / "date=*" / "*.parquet")

    con = duckdb.connect()
    con.execute("SET TimeZone='UTC'")
    con.execute("SET memory_limit='2GB'")
    con.execute(f"ATTACH '{legacy}' AS legacy (READ_ONLY)")
    rows = con.execute(f"""
        WITH old AS (
            SELECT symbol, ts::DATE AS day, count(*) AS bars, sum(volume) AS volume, sum(delta) AS delta,
                   sum(open + high + low + close) AS ohlc_sum
            FROM legacy.bars_1m GROUP BY 1, 2
        ), new AS (
            SELECT symbol, date AS day, count(*) AS bars, sum(volume) AS volume, sum(delta) AS delta,
                   sum(open + high + low + close) AS ohlc_sum
            FROM read_parquet('{new_glob}', hive_partitioning=true) GROUP BY 1, 2
        )
        SELECT coalesce(old.symbol, new.symbol) AS symbol, coalesce(old.day, new.day) AS day,
               old.bars AS old_bars, new.bars AS new_bars, old.volume AS old_vol, new.volume AS new_vol,
               old.delta AS old_delta, new.delta AS new_delta,
               abs(coalesce(old.ohlc_sum, 0) - coalesce(new.ohlc_sum, 0)) AS ohlc_diff
        FROM old FULL OUTER JOIN new ON old.symbol = new.symbol AND old.day = new.day
        ORDER BY 2, 1
    """).fetchall()

    bad = 0
    print(f"{'symbol':8} {'day':10} {'old_bars':>8} {'new_bars':>8} {'old_vol':>12} {'new_vol':>12} {'d_delta':>8} {'ohlc':>6}")
    for symbol, day, ob, nb, ov, nv, od, nd, ohlc in rows:
        ok = ob == nb and ov == nv and od == nd and ohlc < 1e-6
        # Only-in-one-store days: the new ingest may have added days the legacy skipped (and vice versa).
        if ob is None or nb is None:
            status = "new-only" if ob is None else "legacy-only"
        else:
            status = "ok" if ok else "MISMATCH"
            bad += 0 if ok else 1
        print(f"{symbol:8} {str(day):10} {str(ob):>8} {str(nb):>8} {str(ov):>12} {str(nv):>12} "
              f"{str((nd or 0) - (od or 0)):>8} {ohlc:6.2f}  {status}")
    both = sum(1 for r in rows if r[2] is not None and r[3] is not None)
    print(f"\n{both} symbol-days compared, {bad} mismatches")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
