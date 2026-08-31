#!/usr/bin/env python
"""Warm (or list / evict) the tick-replay cache from the command line.

    python scripts/warm_replay.py ES 2026-06-12        # decode one day (~2 min for ES)
    python scripts/warm_replay.py --list
    python scripts/warm_replay.py --evict              # apply the LRU cap now

The web UI warms a day on first replay; this is for pre-warming a few
sessions before a teaching sitting. Only this script, scripts/ingest.py and
the in-app warmer read raw .dbn.zst files (PLATFORM-SPEC.md §4.1).
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from replay import warm  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", nargs="?")
    ap.add_argument("date", nargs="?")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--evict", action="store_true")
    a = ap.parse_args()
    if a.list:
        print(json.dumps(warm.list_cached(), indent=1))
        return 0
    if a.evict:
        print("evicted:", warm.evict())
        return 0
    if not (a.root and a.date):
        ap.error("root and date are required (or --list / --evict)")
    t0 = time.time()
    last = [-1]

    def progress(pct: int) -> None:
        if pct // 10 != last[0] // 10:
            print(f"  {pct:3d}%  {time.time() - t0:5.0f}s", flush=True)
        last[0] = pct

    meta = warm.warm_day(a.root, a.date, progress=progress)
    print(json.dumps({k: v for k, v in meta.items() if k != "checkpoints"}, indent=1))
    print("checkpoints:", {s: len(v) for s, v in meta["checkpoints"].items()})
    return 0


if __name__ == "__main__":
    sys.exit(main())
