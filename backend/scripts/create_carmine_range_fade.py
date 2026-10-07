"""Export a CLC-inspired research draft; --save also adds it to Strategies.

Run from the repo root with .venv/bin/python backend/scripts/create_carmine_range_fade.py.
No parameter here is fitted to historical returns. See the linked research note.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

SOURCE = "https://www.youtube.com/watch?v=j9ZuUlVGDr8"
OUTPUT = ROOT / "docs/research/carmine-clc-range-fade/spec.json"


def ind(name, **params):
    return {"ind": name, "params": params}


def op(name, *args):
    return {"op": name, "args": list(args)}


def build_spec():
    lo = ind("opening_range_low", minutes=60)
    hi = ind("opening_range_high", minutes=60)
    close = {"field": "close"}
    balanced = op("lt", ind("adx", period=14), 25)
    location = op("and", op("within_ticks", {"field": "low"}, lo, 8),
                  op("within_ticks", close, lo, 8))
    # Both sides use the engine's directional mirroring. These are LONG rules.
    spec = {
        "schemaVersion": 2,
        "id": "carmine-clc-range-fade-v1",
        "name": "Carmine CLC — range fade (1m approximation)",
        "description": "Research approximation of Carmine Rosato's balanced-market fade. "
            "Uses the first RTH hour and ADX as automatic context/location proxies, "
            "then heavy opposing aggression in a narrow bar followed by a delta flip. "
            "Signals are evaluated at 1-minute closes, NOT his 20-tick range / immediate "
            "order-flow entries. No profitability claim; all numerical thresholds are assumptions.",
        "origin": {"type": "manual", "sourceId": SOURCE},
        "status": "draft",
        "instrument": {"root": "ES", "symbol": "ES1!"},
        "timeframes": {"primary": "1min", "context": []},
        "direction": "both",
        "session": {"entryWindow": {"start": "10:30", "end": "15:30"}, "flattenAt": "15:58"},
        "entry": {
            "sequence": [{"when": op("and", balanced, location,
                op("not", op("within_ticks", hi, lo, 24)),
                ind("absorption", side="bid", min_volume=500, max_range_ticks=8),
                op("gte", ind("aggressor_share", side="bid"), 0.65),
                op("gte", ind("rel_volume", n=20), 1.5)), "withinBars": 3}],
            "trigger": op("and", op("gt", ind("bar_delta"), 0),
                          op("gt", close, {"field": "open"})),
            "orderType": "market", "timeoutBars": 1,
        },
        "filters": [balanced, location, op("gt", close, lo), op("lt", close, hi)],
        "exit": {"stop": {"type": "structure", "structure": "session_low", "bufferTicks": 2},
                 "target": {"type": "level", "level": "or_high"}},
        "sizing": {"type": "fixed_contracts", "value": 1, "maxContracts": 1},
        "constraints": {"maxTradesPerDay": 3, "cooldownBars": 5,
                        "stopAfterConsecutiveLosses": 2, "maxConcurrentPositions": 1},
        "execution": {"mode": "ticks"},
        "risk": {"proposedBy": "default", "accountSize": 100000,
                 "maxContracts": 1, "maxTradesPerDay": 3, "stopAfterConsecutiveLosses": 2,
                 "rationale": "Research defaults, not the presenter's account or sizing rules."},
        "meta": {"sourceUrl": SOURCE, "sourceTitle": "This Is My EXACT Profitable Trading Strategy (FULL Course)",
                 "author": "Carmine Rosato", "replication": "approximation",
                 "sourceReviewed": "English automatic captions; chart visuals not verified",
                 "researchNote": "docs/research/carmine-clc-range-fade.md",
                 "assumptions": [
                     "First 60 RTH minutes replace manually identified multi-session supply/demand zones.",
                     "ADX(14) < 25 on 1m bars replaces discretionary balance classification.",
                     "Range > 24 ticks; setup and confirmation low/close within 8 ticks of the edge.",
                     "Absorption proxy: >=500 opposing contracts, >=65% opposing share, range <=8 ticks, relative volume >=1.5 over prior 20 bars.",
                     "Positive delta and bullish close within 3 bars; mirrored for shorts.",
                     "Session extreme plus 2 ticks is the invalidation proxy; opposite initial range edge is the target.",
                     "1m close evaluation, tick execution; no 20-tick range bars or passive-fill/iceberg identification.",
                     "One contract, 3 trades/day, 5-bar cooldown, stop after 2 losses; entry 10:30-15:30 ET, flatten 15:58.",
                 ]},
    }
    from engine.spec import normalize, validate_spec
    errors = validate_spec(spec)
    if errors:
        raise ValueError("; ".join(errors))
    return normalize(spec)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--save", action="store_true", help="Add a new draft to the local platform; refuses to overwrite")
    args = parser.parse_args()
    spec = build_spec()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(spec, indent=2) + "\n")
    print(f"Exported {OUTPUT}")
    if args.save:
        import strategy_store
        if strategy_store.get_strategy(spec["id"]) is not None:
            raise SystemExit("Draft already exists; left the saved strategy unchanged.")
        saved = strategy_store.save_strategy(spec)
        print(f"Saved /strategies/{saved['id']}")


if __name__ == "__main__":
    main()
