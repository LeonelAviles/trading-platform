"""Content-addressed discovery with explicit user control and durable results.

No worker dispatch, model tools, live/paper adapters, scheduler or recovery
reruns. SQLite reservations enforce one consumption across API processes.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.metadata
import json
import platform
from datetime import timedelta
from pathlib import Path

import pandas as pd

import database
import data_store
from config.instruments import CONFIG_PATH, load_instruments
from engine import validation
from engine.session import et_to_ns, NS
from market.paths import get_paths
from models import AgentThread, MLExperiment, new_id, utc_now
from research_agent import memory
from research_ml import core
from research_ml.schema import Plan, parse_plan

MAX_SNAPSHOT_BYTES = 12_000_000


def provenance() -> dict:
    backend = Path(__file__).resolve().parents[1]
    files = sorted(set((backend / "research_ml").glob("*.py")) |
                   set((backend / "engine").rglob("*.py")) |
                   {backend / "data_store.py", backend / "config/instruments.py"})
    return {"codeSha256": memory.digest({str(p.relative_to(backend)): hashlib.sha256(p.read_bytes()).hexdigest()
                                         for p in files}),
            "configSha256": hashlib.sha256(CONFIG_PATH.read_bytes()).hexdigest(),
            "python": platform.python_version(), "machine": platform.machine(),
            "versions": {p: importlib.metadata.version(p) for p in ("numpy", "pandas", "nautilus_trader")}}


def _scope(plan):
    split = validation._splits("ES") or {}
    ins, oos = set(split.get("inSample", [])), set(split.get("outOfSample", []))
    if not ins or ins & oos:
        raise ValueError("valid frozen ES in-sample sessions are required")
    endpoints = [str(plan.start), str(plan.trainEnd), str(plan.validationEnd), str(plan.testEnd)]
    if any(d not in ins for d in endpoints) or any(endpoints[0] <= d <= endpoints[-1] for d in oos):
        raise ValueError("all dates must be inside frozen in-sample data; final holdout is unavailable to discovery")
    return sorted(d for d in ins if endpoints[0] <= d <= endpoints[-1])


def _snapshot_path(experiment_id):
    # IDs come only from persisted rows/server-generated IDs, never input paths.
    return get_paths().research_cache_dir / "ml" / f"{experiment_id}.json"


def _row(db, thread_id, experiment_id):
    row = db.get(MLExperiment, experiment_id)
    if not row or row.thread_id != thread_id:
        raise memory.ResearchConflict("experiment not found in this thread")
    return row


def _public(row):
    return {"id": row.id, "threadId": row.thread_id, "digest": row.digest,
            "document": row.document_json, "decision": row.decision_json,
            "status": row.status, "result": row.result_json, "createdAt": row.created_at}


def history(thread_id):
    with database.session_scope() as db:
        if not db.get(AgentThread, thread_id):
            raise ValueError("thread not found")
        rows = db.query(MLExperiment).filter_by(thread_id=thread_id).all()
        # Includes interrupted/failed runs: never silently reset search history.
        attempts = db.query(MLExperiment).filter(MLExperiment.status.in_(("running", "candidate", "no_edge", "blocked"))).count()
        return {"experiments": [_public(r) for r in rows], "recordedAttempts": attempts,
                "trialCountScope": "persisted ML run reservations; excludes Nautilus and external trials",
                "externalTrials": "unknown", "significance": "not established"}


def propose(thread_id: str, plan: Plan):
    sessions = _scope(plan)
    with database.session_scope() as db:
        if not db.get(AgentThread, thread_id):
            raise ValueError("thread not found")
        if db.query(MLExperiment).filter_by(thread_id=thread_id).first() or memory.thread_stopped(db, thread_id):
            raise memory.ResearchConflict("one discovery plan per thread; start a new thread to revise or restart")
    # The store's bounds are inclusive UNIX seconds. Request the final second
    # of testEnd so the next ET midnight cannot enter the approved snapshot.
    end_second = et_to_ns(plan.testEnd + timedelta(days=1), "00:00") // NS - 1
    bars = data_store.get_bars("ES1!", "1min", et_to_ns(plan.start, "00:00") // NS, end_second)
    bars = core.validate_bars(bars)
    days = set(bars.index.tz_convert("America/New_York").strftime("%Y-%m-%d"))
    if not days <= set(sessions):
        raise ValueError("snapshot contains sessions outside approved in-sample range")
    if not set(sessions) <= days:
        raise ValueError("snapshot is missing frozen sessions; repair ingestion before research")
    tick = load_instruments().roots["ES"].tick_size
    # Check target feasibility before asking for approval, without fitting.
    partitions = core.samples(bars, plan, tick)
    payload = {"timestamps": bars.index.as_unit("ns").asi8.tolist(), "columns": core.COLUMNS,
               "values": bars.to_numpy().tolist()}
    content = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode()
    if len(content) > MAX_SNAPSHOT_BYTES:
        raise ValueError("snapshot exceeds 12 MB")
    eid = new_id()
    document = {"plan": plan.model_dump(mode="json"), "provenance": provenance(),
                "dataset": {"sha256": hashlib.sha256(content).hexdigest(), "rows": len(bars),
                            "sessions": sessions, "source": "existing_ES_Parquet_via_data_store",
                            "identityPolicy": "immutable_derived_OHLCV_snapshot",
                            "rawDataQuality": "unverified", "tickSize": tick},
                "partitionCounts": {k: len(v) for k, v in partitions.items()},
                "scope": "ES historical discovery only; internal OOS within IS; no final holdout"}
    path = _snapshot_path(eid)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as snapshot:
        snapshot.write(content)
    try:
        with database.session_scope() as db:
            memory._write_reservation(db)
            if db.query(MLExperiment).filter_by(thread_id=thread_id).first() or memory.thread_stopped(db, thread_id):
                raise memory.ResearchConflict("thread already contains a discovery plan or stop decision")
            row = MLExperiment(id=eid, thread_id=thread_id, digest=memory.digest(document),
                               document_json=document, status="proposed")
            db.add(row)
            db.flush()
            result = _public(row)
        return result
    except Exception:
        path.unlink(missing_ok=True)
        raise


def decide(thread_id, experiment_id, expected_digest, action, reason):
    if action not in {"approve", "reject_stop"} or not reason.strip():
        raise ValueError("approve or reject_stop and an explicit reason are required")
    with database.session_scope() as db:
        memory._write_reservation(db)
        row = _row(db, thread_id, experiment_id)
        if row.digest != expected_digest or memory.digest(row.document_json) != row.digest:
            raise memory.ResearchConflict("exact experiment digest mismatch")
        if row.decision_json:
            previous = row.decision_json
            if (previous["action"], previous["reason"], previous["digest"]) != (action, reason, expected_digest):
                raise memory.ResearchConflict("decision is immutable")
            return _public(row)
        if memory.thread_stopped(db, thread_id):
            raise memory.ResearchConflict("thread was stopped")
        row.decision_json = {"action": action, "reason": reason, "digest": expected_digest, "at": utc_now()}
        row.status = "approved" if action == "approve" else "stopped"
        db.flush()
        return _public(row)


def run(thread_id, experiment_id):
    with database.session_scope() as db:
        memory._write_reservation(db)
        row = _row(db, thread_id, experiment_id)
        if row.status in {"candidate", "no_edge", "blocked"}:
            return _public(row)
        if row.status != "approved" or not row.decision_json or row.decision_json["action"] != "approve":
            raise memory.ResearchConflict("explicit approval required; running/stopped experiments cannot rerun")
        document = copy.deepcopy(row.document_json)
        if memory.digest(document) != row.digest or row.decision_json["digest"] != row.digest:
            raise memory.ResearchConflict("approved experiment changed")
        if memory.thread_stopped(db, thread_id):
            raise memory.ResearchConflict("thread was stopped")
        row.status = "running"  # committed before any model can fit
    try:
        plan = parse_plan(document["plan"])
        if provenance() != document["provenance"] or _scope(plan) != document["dataset"]["sessions"]:
            raise ValueError("code/config/dependencies/frozen sessions changed; new approval required")
        path = _snapshot_path(experiment_id)
        with path.open("rb") as snapshot:
            content = snapshot.read(MAX_SNAPSHOT_BYTES + 1)
        if len(content) > MAX_SNAPSHOT_BYTES:
            raise ValueError("snapshot exceeds 12 MB")
        if hashlib.sha256(content).hexdigest() != document["dataset"]["sha256"]:
            raise ValueError("approved snapshot changed")
        payload = json.loads(content)
        bars = pd.DataFrame(payload["values"], columns=payload["columns"],
                            index=pd.to_datetime(payload["timestamps"], unit="ns", utc=True))
        result = core.discover(bars, plan, document["dataset"]["tickSize"])
        result["experimentDigest"] = memory.digest(document)
        result["resultDigest"] = memory.digest(result)
        status = result["outcome"]
    except Exception as exc:
        status, result = "blocked", {"outcome": "blocked", "reason": str(exc), "significance": "not established"}
    with database.session_scope() as db:
        memory._write_reservation(db)
        row = _row(db, thread_id, experiment_id)
        row.status, row.result_json = status, result
        db.flush()
        return _public(row)


def candidate_proposal(thread_id, experiment_id, strategy, test_plan):
    """Export learned trigger into the existing approval workflow, never queue."""
    with database.session_scope() as db:
        row = _row(db, thread_id, experiment_id)
        if row.status != "candidate":
            raise memory.ResearchConflict("only a completed candidate can be exported")
        document, result = copy.deepcopy(row.document_json), copy.deepcopy(row.result_json)
        experiment_digest = row.digest
        digest = result.pop("resultDigest", None)
        if memory.digest(result) != digest or memory.digest(document) != row.digest:
            raise memory.ResearchConflict("experiment artifact changed")
    plan = document["plan"]
    raw = copy.deepcopy(strategy)
    if raw.get("instrument") != {"root": "ES", "symbol": "ES1!"} or raw.get("timeframes") != {"primary": "1min", "context": []}:
        raise ValueError("export requires explicit ES1! and 1min timeframes with no context")
    if raw.get("direction") != plan["direction"]:
        raise ValueError("strategy direction must match discovery")
    if not isinstance(raw.get("entry"), dict) or raw["entry"].get("sequence") != [] or raw.get("filters") != []:
        raise ValueError("export requires an explicit entry with no sequence and no filters")
    raw["entry"]["trigger"] = result["model"]["trigger"]
    if not isinstance(raw.get("meta", {}), dict):
        raise ValueError("strategy meta must be an object")
    raw.setdefault("meta", {})["mlDiscovery"] = {"experimentId": experiment_id, "experimentDigest": experiment_digest,
                                                  "resultDigest": digest, "method": plan["method"], "target": plan["target"],
                                                  "horizonBars": plan["horizonBars"],
                                                  "interpretation": plan.get("interpretation"),
                                                  "warning": "signal export only; exits/sizing/costs require separate Nautilus validation"}
    return memory.propose(thread_id, plan["hypothesis"], raw, test_plan,
                          ["ML discovery", "chronological internal OOS"], [])
