"""Write the NautilusTrader ParquetDataCatalog from the Parquet tiers (PLATFORM-SPEC.md §5 Phase 1 task 3).

    python scripts/build_catalog.py [--roots ES,NQ] [--from 2026-04-01 --to 2026-07-31] [--rebuild] [--limit N]

Incremental: days whose partitions have not changed since the last build are
skipped (data/market/catalog/catalog_manifest.json). Run after scripts/ingest.py.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from market import catalog as cat  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--roots")
    ap.add_argument("--from", dest="date_from")
    ap.add_argument("--to", dest="date_to")
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--limit", type=int)
    args = ap.parse_args(argv)
    roots = set(args.roots.split(",")) if args.roots else None
    dates = None
    if args.date_from or args.date_to:
        dates = (date.fromisoformat(args.date_from or "2000-01-01"), date.fromisoformat(args.date_to or "2100-01-01"))
    summary = cat.build(roots=roots, dates=dates, rebuild=args.rebuild, limit=args.limit)
    print(f"catalog: {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
