"""Strategy package export / import (PLATFORM-SPEC.md §5 Phase 7, task 2).

`build(strategy_id)` returns a zip with everything a forward-test executor (out
of scope here) would need to run and audit the strategy:

    spec.json                 the Strategy Spec v2 as stored
    risk.json                 the risk profile (proposedBy, limits, pass criteria)
    validation_report.json    IS / WF / OOS / Monte Carlo / DSR / verdict (engine.validation.report)
    lineage.json              the tree the strategy belongs to, champion marked
    nautilus_config.json      ImportableStrategyConfig stub pointing at the execution strategy
    manifest.json             package version, export time, platform commit, file list

`import_package(data)` re-creates the strategy from `spec.json` + `risk.json`
(new id when the id is taken by a different document; the parent link is kept
only when the parent exists locally) and returns what it did."""

from __future__ import annotations

import io
import json
import subprocess
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import strategy_store
from engine import validation

PACKAGE_VERSION = 1
REPO_ROOT = Path(__file__).resolve().parent.parent


class PackageError(ValueError):
    pass


def _commit() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, timeout=5).stdout.strip() or None
    except Exception:
        return None


def nautilus_config(spec: dict) -> dict:
    """The contract a forward-test executor consumes: NautilusTrader's
    `ImportableStrategyConfig` shape (strategy_path, config_path, config) with
    the parameters `engine.backtest_worker` derives from a spec."""
    from engine import backtest_worker as bw

    inst = spec.get("instrument") or {}
    tfs = spec.get("timeframes") or {}
    return {
        "strategy_path": "engine.backtest_worker:ExecStrategy",
        "config_path": "engine.backtest_worker:ExecStrategyConfig",
        "config": {
            "spec_path": "spec.json",
            "strategy_id": spec.get("id"),
            "instrument_id": f"{inst.get('symbol')}.{inst.get('venue') or 'CME'}",
            "bar_type": f"{inst.get('symbol')}.{inst.get('venue') or 'CME'}-{bw.TF_MINUTES.get(tfs.get('primary', '1min'), 1)}-MINUTE-LAST-EXTERNAL",
            "mode": (spec.get("execution") or {}).get("mode") or "bars",
            "params": bw.exec_params(spec),
        },
        "note": "Forward testing is out of scope for the platform; this stub names the strategy class and config the executor should load.",
    }


def build(strategy_id: str) -> bytes:
    spec = strategy_store.get_strategy(strategy_id)
    if spec is None:
        raise PackageError(f"strategy '{strategy_id}' not found")
    risk = spec.get("risk") or {}
    report = validation.report(strategy_id, risk=risk, trial_index=max(1, int((spec.get("lineage") or {}).get("trialIndex") or 1)))
    try:
        lineage = strategy_store.lineage(strategy_id)
    except strategy_store.StrategyError:
        lineage = None
    files = {
        "spec.json": spec,
        "risk.json": risk,
        "validation_report.json": report,
        "lineage.json": lineage,
        "nautilus_config.json": nautilus_config(spec),
    }
    manifest = {
        "packageVersion": PACKAGE_VERSION, "strategyId": strategy_id, "name": spec.get("name"), "status": spec.get("status"),
        "exportedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"), "platformCommit": _commit(),
        "files": sorted(files),
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, indent=1))
        for name, payload in files.items():
            zf.writestr(name, json.dumps(payload, indent=1, default=str))
    return buf.getvalue()


def read(data: bytes) -> dict[str, object]:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise PackageError(f"not a strategy package: {e}")
    out = {}
    for name in zf.namelist():
        if name.endswith(".json"):
            try:
                out[name] = json.loads(zf.read(name))
            except json.JSONDecodeError as e:
                raise PackageError(f"{name}: {e}")
    if "spec.json" not in out:
        raise PackageError("package has no spec.json")
    return out


def import_package(data: bytes, *, keep_id: bool = True) -> dict:
    files = read(data)
    spec = dict(files["spec.json"])
    if files.get("risk.json"):
        spec["risk"] = files["risk.json"]
    original_id = spec.get("id")
    for k in ("createdAt", "updatedAt"):
        spec.pop(k, None)
    new_id = original_id if keep_id and original_id and strategy_store.get_strategy(original_id) is None else uuid.uuid4().hex[:12]
    spec["id"] = new_id
    parent = (spec.get("lineage") or {}).get("parentId")
    parent_kept = bool(parent and strategy_store.get_strategy(parent))
    try:
        saved = strategy_store.save_strategy(spec, strategy_id=new_id)
    except strategy_store.StrategyError as e:
        raise PackageError(str(e))
    manifest = files.get("manifest.json") or {}
    return {
        "strategy": saved, "id": new_id, "originalId": original_id, "renamedId": new_id != original_id,
        "parentKept": parent_kept, "packageVersion": manifest.get("packageVersion"), "exportedAt": manifest.get("exportedAt"),
        "files": sorted(files),
        "validationReport": files.get("validation_report.json"),
    }
