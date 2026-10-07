"""Save chosen study specs into the platform's SQLite strategy store (backend not running).

usage: save_to_db.py <variant>:<status>:<lineage parent variant or ->  ...
Prints the assigned ids. Description gets the study results appended.
"""
import json
import pathlib
import sys

sys.path.insert(0, "/Users/leonelaviles/Desktop/trading-platform/backend")
import os
os.chdir("/Users/leonelaviles/Desktop/trading-platform/backend")

import strategy_store  # noqa: E402

LAB = pathlib.Path(__file__).parent
ids = {}
for arg in sys.argv[1:]:
    variant, status, parent = arg.split(":")
    spec = json.loads((LAB / "specs" / f"{variant}.json").read_text())
    spec["status"] = status
    spec["execution"]["mode"] = "bars" if variant != "A4_passive_wall" else "ticks"
    notes = []
    for tag in ("is", "oos", "is_ticks"):
        p = LAB / "runs" / f"{variant}_{tag}" / "summary.json"
        if p.exists():
            s = json.loads(p.read_text())
            if s.get("trades"):
                notes.append(f"{tag} {s['window']} {s['mode']}: n {s['trades']}, net ${s['net']:+.0f}, PF {s['pf']}, win {s['win%']}%, exp {s['expR']}R")
            else:
                notes.append(f"{tag} {s['window']}: 0 trades")
    spec["description"] = spec["description"] + " Results (2026-09-07 study, docs/research/2026-09-07-rosato-orderflow-masterclass.md): " + "; ".join(notes) + "."
    if parent != "-" and parent in ids:
        spec["lineage"] = {"parentId": ids[parent], "changedVariable": variant, "rationale": "single-variable child in the Rosato study", "trialIndex": 1}
    spec["meta"]["source"] = "YouTube j9ZuUlVGDr8 — Carmine Rosato order-flow masterclass (2026-08-07)"
    saved = strategy_store.save_strategy(spec)
    ids[variant] = saved["id"]
    print(variant, "->", saved["id"], saved["status"])
