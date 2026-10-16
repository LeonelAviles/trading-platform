"""Local immutable research-run evidence, separate from model approvals."""
import json
import hashlib
import re
from pathlib import Path
from market.paths import get_paths
from research_ml.consistency import assess

MAX_REPORT_BYTES = 2_000_000


def directory():
    return get_paths().data_dir / "research_runs"


def read(run_id):
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", run_id):
        raise ValueError("invalid research run ID")
    path = directory() / f"{run_id}.json"
    with path.open("rb") as stream:
        content = stream.read(MAX_REPORT_BYTES + 1)
    if len(content) > MAX_REPORT_BYTES:
        raise ValueError("research report exceeds size limit")
    report = json.loads(content)
    digest = report.get("reportDigest")
    unsigned = {k: v for k, v in report.items() if k != "reportDigest"}
    observed = hashlib.sha256(json.dumps(unsigned, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    if digest != observed:
        raise ValueError("research report digest mismatch")
    if report.get("id") != run_id:
        raise ValueError("research report identity mismatch")
    return report


def inventory():
    reports = []
    for path in sorted(directory().glob("*.json"), reverse=True)[:50]:
        try:
            report = read(path.stem)
            public = {k: v for k, v in report.items() if k != "trades"}
            trades = [t for t in report.get("trades", []) if t.get("exitTime") is not None]
            wins = sum(t["pnlUsd"] > 0 for t in trades)
            public["derivedAfterCostSummary"] = {"trades": len(trades), "wins": wins,
                "winRate": round(wins / len(trades) * 100, 1) if trades else 0.0,
                "netPnl": round(sum(t["pnlUsd"] for t in trades), 2),
                "scope": "derived from recorded after-cost trades; original report remains unchanged"}
            reports.append(public)
        except (ValueError, OSError):
            reports.append({"id": path.stem, "status": "unreadable", "reason": "local report unavailable or invalid"})
    return {"runs": reports, "scope": "recorded research and diagnostic execution; no trading permission"}


def reassess(run_id, policy):
    report = read(run_id)
    return assess(report.get("trades", []), report.get("sessions", []), policy)


def save(report):
    """Publish once; all later retries get distinct run IDs, never overwrite."""
    run_id = report["id"]
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", run_id):
        raise ValueError("invalid research run ID")
    unsigned = {k: v for k, v in report.items() if k != "reportDigest"}
    report["reportDigest"] = hashlib.sha256(json.dumps(unsigned, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    content = json.dumps(report, sort_keys=True, allow_nan=False, indent=2).encode()
    if len(content) > MAX_REPORT_BYTES:
        raise ValueError("research report exceeds size limit")
    directory().mkdir(parents=True, exist_ok=True)
    with (directory() / f"{run_id}.json").open("xb") as stream:
        stream.write(content)
    return report


def save_artifact(run_id, name, value):
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", run_id) or not re.fullmatch(r"[a-z_]{1,30}", name):
        raise ValueError("invalid artifact identity")
    content = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    if len(content) > MAX_REPORT_BYTES:
        raise ValueError("research artifact exceeds size limit")
    folder = directory() / "artifacts"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{run_id}-{name}.json"
    with path.open("xb") as stream:
        stream.write(content)
    return {"relativePath": str(path.relative_to(get_paths().data_dir)),
            "sha256": hashlib.sha256(content).hexdigest()}
