"""Validate a spec, run it through the engine worker (subprocess), summarise.

usage: run.py <spec.json> <from> <to> <mode> [tag]
Writes runs/<variant>[_tag]/{out.json,summary.json,log.txt}.
"""
import json
import pathlib
import subprocess
import sys
from collections import Counter, defaultdict

ROOT = pathlib.Path("/Users/leonelaviles/Desktop/trading-platform")
LAB = pathlib.Path(__file__).parent
PY = ROOT / ".venv/bin/python"


def summarize(result: dict) -> dict:
    tr = result["trades"]
    n = len(tr)
    if n == 0:
        return {"trades": 0}
    pnl = [t["pnlUsd"] for t in tr]
    gw = sum(p for p in pnl if p > 0)
    gl = -sum(p for p in pnl if p < 0)
    rs = [t.get("r") for t in tr if t.get("r") is not None]
    eq = 0.0
    peak = 0.0
    dd = 0.0
    for p in pnl:
        eq += p
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    months = defaultdict(float)
    for t in tr:
        months[t["sessionDate"][:7]] += t["pnlUsd"]
    by_dir = defaultdict(lambda: [0, 0.0])
    for t in tr:
        by_dir[t["direction"]][0] += 1
        by_dir[t["direction"]][1] += t["pnlUsd"]
    stop_ticks = [abs(t["entryPrice"] - t["stopPrice"]) / 0.25 for t in tr if t.get("stopPrice")]
    return {
        "trades": n,
        "net": round(sum(pnl), 0),
        "pf": round(gw / gl, 2) if gl else None,
        "win%": round(100 * sum(1 for p in pnl if p > 0) / n, 1),
        "expR": round(sum(rs) / len(rs), 3) if rs else None,
        "avg$": round(sum(pnl) / n, 1),
        "maxDD$": round(dd, 0),
        "exits": dict(Counter(t["exitReason"] for t in tr)),
        "months": {k: round(v, 0) for k, v in sorted(months.items())},
        "byDir": {k: [v[0], round(v[1], 0)] for k, v in by_dir.items()},
        "medStopTicks": round(sorted(stop_ticks)[len(stop_ticks) // 2], 1) if stop_ticks else None,
        "avgBarsHeld": round(sum(t.get("barsHeld") or 0 for t in tr) / n, 1),
        "sessions": result["meta"]["sessions"],
        "seconds": result["meta"]["seconds"],
    }


def main():
    spec_path, d0, d1, mode = sys.argv[1:5]
    tag = sys.argv[5] if len(sys.argv) > 5 else mode
    spec = json.loads(pathlib.Path(spec_path).read_text())
    variant = spec["meta"].get("variant", pathlib.Path(spec_path).stem)
    out_dir = LAB / "runs" / f"{variant}_{tag}"
    out_dir.mkdir(parents=True, exist_ok=True)
    spec["execution"]["mode"] = mode
    (out_dir / "spec.json").write_text(json.dumps(spec))
    # validate
    chk = subprocess.run([str(PY), "-c",
                          "import json,sys; from engine.spec import validate_spec, required_mode; s=json.load(open(sys.argv[1])); "
                          "e=validate_spec(s); print(json.dumps({'errors': e, 'requiredMode': required_mode(s)}))",
                          str(out_dir / "spec.json")], cwd=ROOT / "backend", capture_output=True, text=True)
    if chk.returncode != 0:
        print(variant, "VALIDATE CRASH", chk.stderr[-2000:])
        sys.exit(1)
    v = json.loads(chk.stdout.strip().splitlines()[-1])
    if v["errors"]:
        print(variant, "INVALID", v["errors"])
        sys.exit(1)
    if v["requiredMode"] == "ticks" and mode == "bars":
        print(variant, "needs ticks mode; refusing bars run")
        sys.exit(1)
    log = open(out_dir / "log.txt", "w")
    r = subprocess.run([str(PY), "-m", "engine.backtest_worker", str(out_dir / "spec.json"), d0, d1, mode, str(out_dir / "out.json")],
                       cwd=ROOT / "backend", stdout=log, stderr=subprocess.STDOUT, text=True)
    log.close()
    if r.returncode != 0:
        print(variant, "WORKER FAILED; tail:", open(out_dir / "log.txt").read()[-1500:])
        sys.exit(1)
    result = json.loads((out_dir / "out.json").read_text())
    s = summarize(result)
    s["variant"] = variant
    s["window"] = f"{d0}..{d1}"
    s["mode"] = mode
    (out_dir / "summary.json").write_text(json.dumps(s, indent=1))
    print(json.dumps(s))


if __name__ == "__main__":
    main()
