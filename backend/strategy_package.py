"""Strategy package export / import (PLATFORM-SPEC.md §5 Phase 7, task 2).

`build(strategy_id)` returns a zip with everything a forward-test executor (out
of scope here) would need to run and audit the strategy:

    spec.json                 the Strategy Spec v2 as stored
    risk.json                 the risk profile (proposedBy, limits, pass criteria)
    validation_report.json    IS / WF / OOS / Monte Carlo / DSR / verdict (engine.validation.report)
    lineage.json              the tree the strategy belongs to, champion marked
    evidence/findings.json    agent findings logged against the strategy's backtests
    evidence/knowledge.json   knowledge facts recorded from this strategy or its run
    evidence/agent_run.json   the originating agent run's report and citations (prompt origin)
    evidence/teaching.json    the originating teaching session's similarity report (teaching origin)
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

import database
import strategy_store
from engine import jobs, validation
from models import KnowledgeFact

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


def evidence(spec: dict) -> dict:
    sid = spec["id"]
    origin = spec.get("origin") or {}
    findings = []
    for job in jobs.list_jobs():
        if job.get("strategyId") != sid:
            continue
        for f in _findings_for(job["id"]):
            findings.append({**f, "backtestId": job["id"], "windowKind": job.get("windowKind"), "mode": job.get("mode")})
    ref_ids = {sid, origin.get("sourceId")} - {None}
    with database.session_scope() as db:
        facts = [
            {"id": r.id, "kind": r.kind, "text": r.text, "tags": r.tags_json, "credibility": r.credibility,
             "source": {"id": r.source_id, "title": r.source_title, "url": r.source_url}, "refId": r.ref_id, "createdAt": r.created_at}
            for r in db.query(KnowledgeFact).filter(KnowledgeFact.ref_id.in_(ref_ids), KnowledgeFact.invalid_at.is_(None)).all()
        ]
    out = {"findings": findings, "knowledge": facts}
    if origin.get("type") == "prompt" and origin.get("sourceId"):
        from agent import runs as agent_runs

        run = agent_runs.get(origin["sourceId"])
        if run:
            out["agent_run"] = {"id": run["id"], "kind": run["kind"], "status": run["status"], "input": run.get("input"),
                                "report": run.get("report"), "costUsd": run.get("costUsd")}
    if origin.get("type") == "teaching" and origin.get("sourceId"):
        from teaching import store as teaching_store

        sess = teaching_store.get_session(origin["sourceId"])
        if sess:
            out["teaching"] = sess
    return out


def _findings_for(job_id: str) -> list[dict]:
    path = jobs._job_dir(job_id) / "findings.json"
    try:
        return json.loads(path.read_text()) if path.exists() else []
    except Exception:
        return []


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
    ev = evidence(spec)
    files = {
        "spec.json": spec,
        "risk.json": risk,
        "validation_report.json": report,
        "lineage.json": lineage,
        "evidence/findings.json": ev["findings"],
        "evidence/knowledge.json": ev["knowledge"],
        "nautilus_config.json": nautilus_config(spec),
    }
    if "agent_run" in ev:
        files["evidence/agent_run.json"] = ev["agent_run"]
    if "teaching" in ev:
        files["evidence/teaching.json"] = ev["teaching"]
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
