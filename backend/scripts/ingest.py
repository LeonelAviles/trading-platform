"""Ingest raw Databento files into the tiered Parquet layout (PLATFORM-SPEC.md §4.1, §5 Phase 1).

    python scripts/ingest.py --all                     # organize market-data/ into raw/, ingest everything new
    python scripts/ingest.py <file.dbn.zst> [...]      # specific files (already under market-data/raw)
    python scripts/ingest.py --finalize-only           # just recompute front_month / splits / regimes

Options:
    --schema mbo|trades|ohlcv-1m   restrict to one schema (default: all supported)
    --roots ES,NQ                  restrict to these roots (default: every root in instruments.yaml)
    --no-book                      skip the L3 pass (no liquidity heatmap / book checkpoints)
    --rebuild                      re-ingest even if the manifest says the file is up to date
    --rebuild-liquidity            re-run the book pass for days already in liquidity_1s.duckdb
    --recompute-splits             re-freeze the 70/30 IS/OOS split (see market.ingest.recompute_splits)
    --limit N                      stop after N files (useful for a first smoke run)
    --dry-run                      list what would happen

Safe to run while the backend is up: outputs are written atomically per
partition and DuckDB reads Parquet directly. The legacy liquidity.duckdb
is copied into place on first run so the 105 already-materialised days are
not re-processed.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from market import ingest as ing  # noqa: E402
from market.paths import get_paths  # noqa: E402

LEGACY_LIQUIDITY = BACKEND_DIR.parent / "mbo-data" / "liquidity.duckdb"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="*")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--schema", choices=ing.SUPPORTED_SCHEMAS)
    ap.add_argument("--roots")
    ap.add_argument("--no-book", action="store_true")
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--rebuild-liquidity", action="store_true")
    ap.add_argument("--recompute-splits", action="store_true")
    ap.add_argument("--finalize-only", action="store_true")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    paths = get_paths()
    paths.ensure_dirs()
    roots = set(args.roots.split(",")) if args.roots else None

    if not args.finalize_only:
        if args.all:
            moves = ing.organize_raw(paths, dry_run=args.dry_run)
            for src, dst in moves:
                print(f"organize: {src.relative_to(paths.market_data_dir)} -> {dst.relative_to(paths.market_data_dir)}")
            schemas = (args.schema,) if args.schema else ing.SUPPORTED_SCHEMAS
            files = ing.list_raw_files(paths, schemas, roots)
        else:
            files = [Path(f).resolve() for f in args.files]
            if args.schema:
                files = [f for f in files if ing.schema_of(f) == args.schema]
        if args.limit:
            files = files[: args.limit]
        if not files and not args.all:
            ap.print_help()
            return 1

        if not args.no_book and LEGACY_LIQUIDITY.exists() and not paths.liquidity_db.exists():
            print(f"copying legacy {LEGACY_LIQUIDITY} -> {paths.liquidity_db} ({LEGACY_LIQUIDITY.stat().st_size / 1e9:.1f} GB)")
            if not args.dry_run:
                paths.liquidity_db.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(LEGACY_LIQUIDITY, paths.liquidity_db)

        print(f"{len(files)} file(s) to consider under {paths.raw_dir}")
        if args.dry_run:
            for f in files:
                print("  ", f.relative_to(paths.raw_dir))
            return 0
        ing.ingest_files(files, paths=paths, roots=roots, book=not args.no_book,
                         rebuild=args.rebuild, rebuild_liquidity=args.rebuild_liquidity)

    if args.dry_run:
        return 0
    summary = ing.finalize(paths, force_splits=args.recompute_splits)
    print(f"finalize: {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
