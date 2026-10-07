"""Relational research memory and the sole agent validation controller.

User decisions enter through HTTP, never through an LLM tool. SQLite's write
reservation covers approval consumption, workflow readiness and every job row.
Workers see a complete batch only after commit; recovery handles lost dispatch.
"""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import date

from sqlalchemy import text

import database
from config.instruments import CONFIG_PATH
from engine import jobs, spec, validation
from market.paths import get_paths
from models import (AgentRun, AgentThread, Backtest, KnowledgeChunk, ResearchDecision,
                    ResearchEvidence, ResearchProposal, Strategy, KnowledgeSource, new_id, utc_now)
from research_agent import workflow
from research_agent.readiness import executable_errors


class ResearchConflict(ValueError):
    pass


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def dataset_context() -> dict:
    """Read-only inventory, not snapshot capture or a claim of raw-data quality."""
    paths = get_paths()
    def file_hash(path):
        return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
    inventory = {}
    for label, base in (("bars", paths.bars_1m_dir), ("ticks", paths.trades_dir)):
        inventory[label] = [
            {"path": str(p.relative_to(base)), "bytes": p.stat().st_size, "mtimeNs": p.stat().st_mtime_ns}
            for p in sorted((base / "root=ES").glob("date=*/*.parquet"))
        ]
    return {
        "windows": {k: list(v) for k, v in validation.windows("ES").items() if k in {"is", "wf1", "wf2", "wf3"}},
        "splitsSha256": file_hash(paths.splits), "manifestSha256": file_hash(paths.manifest),
        "frontMonthSha256": file_hash(paths.front_month),
        "engineConfig": CONFIG_PATH.read_text(),
        "engineCodeSha256": digest({str(p.relative_to(jobs.BACKEND_DIR)): file_hash(p)
                                     for p in sorted((jobs.BACKEND_DIR / "engine").rglob("*.py"))}),
        "inventory": inventory,
        "identityPolicy": "metadata_inventory_not_immutable_snapshot",
        "rawDataQuality": "unverified", "entrySnapshots": "unavailable",
    }


def plan_errors(plan: dict, strategy: dict, dataset: dict) -> list[str]:
    errors = []
    fields = {"windows", "objective", "comparison", "uncertaintyPolicy", "multipleTestingPolicy", "dataPolicy"}
    if set(plan) != fields:
        errors.append(f"testPlan requires exactly {sorted(fields)}")
    for field in fields - {"windows", "dataPolicy"}:
        if not isinstance(plan.get(field), str) or not plan[field].strip():
            errors.append(f"testPlan.{field}: explicit policy required (descriptive-only is allowed)")
    if plan.get("dataPolicy") != dataset["identityPolicy"]:
        errors.append("testPlan.dataPolicy must explicitly accept metadata_inventory_not_immutable_snapshot")
    selected = plan.get("windows")
    if not isinstance(selected, list) or not selected or "is" not in selected or any(not isinstance(k, str) for k in selected) or len(set(selected)) != len(selected):
        errors.append("testPlan.windows must select unique frozen windows including is")
        selected = []
    for kind in selected:
        if kind not in dataset["windows"]:
            errors.append(f"testPlan.windows: {kind} unavailable or outside IS/WF scope")
    if not dataset["splitsSha256"] or not dataset["frontMonthSha256"]:
        errors.append("dataset unavailable: frozen splits and front-month mapping required")
    mode = (strategy.get("execution") or {}).get("mode")
    for tier in ("bars", "ticks") if mode == "ticks" else ("bars",):
        if not dataset["inventory"][tier]:
            errors.append(f"dataset unavailable: no ES {tier} partitions")
    return errors


def _public(db, row) -> dict:
    decision = db.query(ResearchDecision).filter_by(proposal_id=row.id).one_or_none()
    linked_jobs = (db.query(Backtest).join(ResearchEvidence, ResearchEvidence.job_id == Backtest.id)
                   .filter(ResearchEvidence.proposal_id == row.id).order_by(Backtest.window_kind).all())
    evidence = []
    for job in linked_jobs:
        evidence.append({"jobId": job.id, "window": job.window_kind, "status": job.status,
                         "message": job.message, "dateFrom": job.date_from, "dateTo": job.date_to})
    return {"id": row.id, "threadId": row.thread_id, "parentId": row.parent_id, "digest": row.digest,
            "document": row.document_json, "blockers": row.blockers_json, "status": row.status,
            "strategyId": row.strategy_id, "runId": row.run_id, "createdAt": row.created_at,
            "decision": {"action": decision.action, "reason": decision.reason, "digest": decision.digest,
                         "at": decision.created_at} if decision else None, "evidence": evidence}


def get(proposal_id: str, thread_id: str) -> dict:
    with database.session_scope() as db:
        row = db.get(ResearchProposal, proposal_id)
        if not row or row.thread_id != thread_id:
            raise ValueError("proposal not found in this thread")
        return _public(db, row)


def history(thread_id: str, limit: int = 20, offset: int = 0) -> dict:
    if not 1 <= limit <= 100 or offset < 0:
        raise ValueError("history limit must be 1..100 and offset nonnegative")
    with database.session_scope() as db:
        query = db.query(ResearchProposal).filter_by(thread_id=thread_id)
        total = query.count()
        rows = query.order_by(ResearchProposal.created_at.desc(), ResearchProposal.id).offset(offset).limit(limit).all()
        proposals = [_public(db, r) for r in rows]
        # Global lower bound: splitting one search across conversations must not reset the counter.
        attempts = db.query(ResearchProposal).filter(ResearchProposal.strategy_id.is_not(None)).count()
    return {"proposals": proposals, "total": total, "offset": offset, "limit": limit, "recordedAttempts": attempts, "trialCountScope": "all recorded research proposals",
            "externalAndLegacyTrials": "unknown", "significance": "not established"}


def propose(thread_id: str, hypothesis: str, strategy: dict, test_plan: dict,
            concepts: list[str], passage_ids: list[str], parent_id: str | None = None) -> dict:
    if not hypothesis.strip():
        raise ValueError("hypothesis is required")
    if not isinstance(strategy, dict) or not isinstance(test_plan, dict):
        raise ValueError("strategy and testPlan must be objects")
    if len(json.dumps({"hypothesis": hypothesis, "strategy": strategy, "testPlan": test_plan}, allow_nan=False).encode()) > 256_000:
        raise ValueError("proposal input exceeds 256 KB")
    if len(concepts) > 100 or any(not isinstance(c, str) or len(c) > 500 for c in concepts) or len(passage_ids) > 100:
        raise ValueError("at most 100 concepts (500 characters each) and passages are supported")
    raw = copy.deepcopy(strategy)
    for field in ("id", "createdAt", "updatedAt", "status", "origin"):
        raw.pop(field, None)
    dataset = dataset_context()
    blockers = executable_errors(raw) + plan_errors(test_plan, raw, dataset)
    with database.session_scope() as db:
        if not db.get(AgentThread, thread_id):
            raise ValueError("thread not found")
        if parent_id:
            parent = db.get(ResearchProposal, parent_id)
            if not parent or parent.thread_id != thread_id:
                raise ValueError("parent proposal not found in this thread")
        passages = []
        for pid in dict.fromkeys(passage_ids):
            chunk = db.get(KnowledgeChunk, pid)
            if not chunk:
                raise ValueError(f"passage {pid} unavailable")
            source = db.get(KnowledgeSource, chunk.source_id)
            passages.append({"sourceRevision": source.revision, "sourceUrl": source.url, "sourceName": source.name, "id": pid, "sourceId": chunk.source_id, "contentHash": chunk.content_hash,
                             "content": chunk.content, "path": chunk.path, "heading": chunk.heading})
        document = {"hypothesis": hypothesis, "strategy": raw, "testPlan": test_plan,
                    "concepts": concepts, "passages": passages, "dataset": dataset,
                    "scope": "ES historical research only"}
        row = ResearchProposal(id=new_id(), thread_id=thread_id, parent_id=parent_id,
                               digest=digest(document), document_json=document, blockers_json=blockers,
                               status="draft" if blockers else "proposed")
        db.add(row)
        db.flush()
        return _public(db, row)


def _write_reservation(db):
    # This platform's metadata store is SQLite. Do not pretend a process lock
    # provides cross-process approval consumption or transaction isolation.
    if db.bind.dialect.name != "sqlite":
        raise ResearchConflict("research approval controller currently requires SQLite")
    db.execute(text("BEGIN IMMEDIATE"))


def decide(proposal_id: str, thread_id: str, expected_digest: str, action: str, reason: str) -> dict:
    if action not in {"approve", "reject_stop", "reject_revise"}:
        raise ValueError("decision must be approve, reject_stop or reject_revise")
    if not reason.strip():
        raise ValueError("a decision reason is required")
    with database.session_scope() as db:
        _write_reservation(db)
        row = db.get(ResearchProposal, proposal_id)
        if not row or row.thread_id != thread_id or row.digest != expected_digest:
            raise ResearchConflict("proposal/thread/digest mismatch; review the exact proposal")
        previous = db.query(ResearchDecision).filter_by(proposal_id=proposal_id).one_or_none()
        if previous:
            if (previous.action, previous.reason, previous.digest) != (action, reason, expected_digest):
                raise ResearchConflict("decision is immutable; create a new proposal")
            return _public(db, row)
        if action == "approve" and row.blockers_json:
            raise ResearchConflict("draft is not executable: " + "; ".join(row.blockers_json))
        db.add(ResearchDecision(proposal_id=proposal_id, digest=expected_digest, action=action, reason=reason))
        row.status = "approved" if action == "approve" else action
        if action == "reject_stop":
            for run in db.query(AgentRun).filter_by(thread_id=thread_id).all():
                if run.status in {"analyzing", "awaiting_approval", "blocked"}:
                    run.status = "stopped"
        db.flush()
        return _public(db, row)


def enqueue(proposal_id: str, thread_id: str) -> dict:
    staged_dirs = []
    try:
        with database.session_scope() as db:
            _write_reservation(db)
            row = db.get(ResearchProposal, proposal_id)
            if not row or row.thread_id != thread_id:
                raise ResearchConflict("proposal not found in this thread")
            if row.status == "queued":
                return _public(db, row)  # durable idempotency; recovery owns dispatch
            decision = db.query(ResearchDecision).filter_by(proposal_id=proposal_id).one_or_none()
            if not decision or decision.action != "approve" or decision.digest != row.digest:
                raise ResearchConflict("explicit user approval of this exact proposal is required")
            document = row.document_json
            if digest(document) != row.digest:
                raise ResearchConflict("proposal content changed")
            stopped = (db.query(ResearchDecision).join(ResearchProposal, ResearchProposal.id == ResearchDecision.proposal_id)
                       .filter(ResearchProposal.thread_id == thread_id, ResearchDecision.action == "reject_stop").first())
            if stopped:
                raise ResearchConflict("user stopped this research thread; start a new thread for new research")
            current_data = dataset_context()
            if current_data != document["dataset"]:
                raise ResearchConflict("dataset/config changed; a new proposal and approval are required")
            errors = executable_errors(document["strategy"]) + plan_errors(document["testPlan"], document["strategy"], current_data)
            if errors:
                raise ResearchConflict("; ".join(errors))
            active = (db.query(AgentRun).filter(AgentRun.thread_id == thread_id,
                      AgentRun.status.in_(("running", "analyzing", "awaiting_approval", "blocked")))
                      .order_by(AgentRun.created_at.desc()).first())
            if active and active.status == "running":
                raise ResearchConflict("wait for the current validation batch")
            strategy = spec.normalize(document["strategy"])
            strategy["id"] = new_id()
            strategy["status"] = "testing"
            strategy["origin"] = {"type": "agent", "sourceId": row.id}
            if active:
                state = copy.deepcopy(active.state_json)
                current_candidate = (state.get("candidates") or [{}])[-1]
                if current_candidate.get("status") not in {"complete", "blocked"}:
                    raise ResearchConflict("wait for the completed candidate evidence to be recorded")
                if int(state.get("changeCount", 0)) >= workflow.MAX_CHANGES or int(state.get("consecutiveNonImprovements", 0)) >= workflow.MAX_NON_IMPROVEMENTS:
                    raise ResearchConflict("research stopping budget reached")
                parent = db.get(ResearchProposal, row.parent_id) if row.parent_id else None
                if not parent or parent.strategy_id != state.get("currentStrategyId"):
                    raise ResearchConflict("child must link to the current experiment proposal")
                current = db.get(Strategy, parent.strategy_id).spec_json
                changed = workflow._diff_paths(workflow._core(current), workflow._core(strategy))
                lineage = strategy.get("lineage") or {}
                if len(changed) != 1 or lineage.get("changedVariable") != changed[0] or not lineage.get("rationale"):
                    raise ResearchConflict("child must declare exactly one changed variable and rationale")
                if lineage.get("parentId") != parent.strategy_id or lineage.get("trialIndex") != int((current.get("lineage") or {}).get("trialIndex") or 0) + 1:
                    raise ResearchConflict("child lineage must explicitly name parent strategy and increment trialIndex")
                state["changeCount"] = int(state.get("changeCount", 0)) + 1
                run = active
            else:
                # A completed/stopped thread requires a new conversation to restart;
                # rejected proposals are still retained in this thread's history.
                if db.query(AgentRun).filter_by(thread_id=thread_id).first():
                    raise ResearchConflict("workflow is closed; start a new research thread")
                run = AgentRun(id=new_id(), thread_id=thread_id, kind="research", status="running",
                               input_json={"proposalId": row.id})
                db.add(run)
                state = {"rootStrategyId": strategy["id"], "changeCount": 0, "consecutiveNonImprovements": 0,
                         "bestStrategyId": None, "bestScore": -1, "candidates": [], "events": []}
            db.add(Strategy(id=strategy["id"], name=strategy["name"], status="testing", origin_type="agent",
                            origin_id=row.id, parent_id=(strategy.get("lineage") or {}).get("parentId"),
                            spec_json=strategy, risk_json=strategy["risk"]))
            db.flush()
            batch = []
            for kind in document["testPlan"]["windows"]:
                job_id = new_id()
                job_dir = jobs._job_dir(job_id)
                staged_dirs.append(job_dir)
                job_dir.mkdir(parents=True, exist_ok=False)
                (job_dir / "strategy.json").write_text(json.dumps(strategy, indent=2))
                start, end = current_data["windows"][kind]
                date.fromisoformat(start)
                date.fromisoformat(end)
                job = Backtest(id=job_id, strategy_id=strategy["id"], agent_run_id=run.id,
                               mode=strategy["execution"]["mode"], window_kind=kind, date_from=start, date_to=end,
                               status="queued", message="user-approved research",
                               trades_path=jobs._rel(job_dir / "trades.json"),
                               metrics_json={"strategyName": strategy["name"], "symbol": "ES1!",
                                             "interval": strategy["timeframes"]["primary"],
                                             "accountSize": strategy["risk"]["accountSize"],
                                             "researchProposalId": row.id, "strategyDigest": digest(strategy)})
                db.add(job)
                db.flush()
                db.add(ResearchEvidence(proposal_id=row.id, job_id=job_id))
                batch.append({"id": job_id, "windowKind": kind, "status": "queued"})
            state["candidates"].append(workflow._candidate(strategy, batch))
            state.update(currentStrategyId=strategy["id"], chartJobId=batch[0]["id"])
            state["events"].append(workflow._event("approved", "User-approved validation queued", row.digest))
            run.state_json, run.status, run.updated_at = state, "running", utc_now()
            row.strategy_id, row.run_id, row.status = strategy["id"], run.id, "queued"
            db.flush()
            result = _public(db, row)
    except Exception:
        import shutil
        for path in staged_dirs:
            shutil.rmtree(path, ignore_errors=True)
        raise
    # No worker can race ahead of approval/workflow/evidence persistence.
    for item in result["evidence"]:
        jobs.start(item["jobId"])
    return result


def verify_job_inputs(job_id: str, strategy: dict) -> None:
    """Fail as a data/technical error if approved inputs drift while queued."""
    with database.session_scope() as db:
        job = db.get(Backtest, job_id)
        pid = (job.metrics_json or {}).get("researchProposalId")
        if not pid:
            from models import Strategy
            saved = db.get(Strategy, job.strategy_id) if job.strategy_id else None
            if job.agent_run_id or (saved and saved.origin_type == "agent") or (strategy.get("origin") or {}).get("type") == "agent":
                raise ResearchConflict("legacy agent job has no exact approval; create and approve a new proposal")
            return
        proposal = db.get(ResearchProposal, pid)
        if digest(strategy) != job.metrics_json["strategyDigest"]:
            raise ResearchConflict("approved strategy artifact changed")
        if dataset_context() != proposal.document_json["dataset"]:
            raise ResearchConflict("approved dataset/config changed before execution")
