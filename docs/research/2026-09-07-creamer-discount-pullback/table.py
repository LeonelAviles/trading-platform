"""Print a markdown table of every runs/*/summary.json (optionally filtered by tag)."""
import json
import pathlib
import sys

LAB = pathlib.Path(__file__).parent
tag = sys.argv[1] if len(sys.argv) > 1 else None
rows = []
for p in sorted((LAB / "runs").glob("*/summary.json")):
    s = json.loads(p.read_text())
    if tag and not p.parent.name.endswith("_" + tag):
        continue
    rows.append(s)
print("| variant | window | n | net $ | PF | win % | exp R | max DD $ | med stop t | exits | long / short | months |")
print("|---|---|---|---|---|---|---|---|---|---|---|---|")
for s in rows:
    if s.get("trades", 0) == 0:
        print(f"| {s.get('variant')} | {s.get('window')} | 0 | | | | | | | | | |")
        continue
    ex = " ".join(f"{k[:6]} {v}" for k, v in sorted(s["exits"].items()))
    bd = " / ".join(f"{v[0]} ({v[1]:+.0f})" for k, v in sorted(s["byDir"].items()))
    mo = " ".join(f"{k[5:]} {v:+.0f}" for k, v in s["months"].items())
    print(f"| {s['variant']} | {s['window']} | {s['trades']} | {s['net']:+.0f} | {s['pf']} | {s['win%']} | {s['expR']} | "
          f"{s['maxDD$']:.0f} | {s['medStopTicks']} | {ex} | {bd} | {mo} |")
