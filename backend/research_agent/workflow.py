"""Durable orchestration for one-at-a-time Stratos strategy research."""

from __future__ import annotations

import copy
import threading
from typing import Any

import database
import strategy_store
from engine import spec as spec_mod, validation
from models import AgentRun, Backtest, ResearchProposal, new_id, utc_now

MAX_CHANGES = 5
MAX_NON_IMPROVEMENTS = 3
TERMINAL = {"done", "error"}
_resume_lock = threading.Lock()
_resuming: set[str] = set()


def _event(kind: str, title: str, detail: str = "") -> dict:
    return {"at": utc_now(), "type": kind, "title": title, "detail": detail}


def _public(row: AgentRun) -> dict:
    state = dict(row.state_json or {})
    return {
        "id": row.id,
        "threadId": row.thread_id,
        "status": row.status,
        "rootStrategyId": state.get("rootStrategyId"),
        "currentStrategyId": state.get("currentStrategyId"),
        "chartJobId": state.get("chartJobId"),
        "changeCount": state.get("changeCount", 0),
        "maxChanges": MAX_CHANGES,
        "candidates": state.get("candidates", []),
        "events": state.get("events", []),
        "conclusion": row.answer_json,
        "updatedAt": row.updated_at,
    }


def get(run_id: str) -> dict | None:
    with database.session_scope() as db:
        row = db.get(AgentRun, run_id)
        return _public(row) if row else None


def latest_for_thread(thread_id: str) -> dict | None:
    with database.session_scope() as db:
        row = (
            db.query(AgentRun)
            .filter(AgentRun.thread_id == thread_id)
            .order_by(AgentRun.created_at.desc())
            .first()
        )
        return _public(row) if row else None


def _candidate(strategy: dict, jobs: list[dict]) -> dict:
    lineage = strategy.get("lineage") or {}
    return {
        "strategyId": strategy["id"],
        "name": strategy.get("name"),
        "trialIndex": int(lineage.get("trialIndex") or 0),
        "changedVariable": lineage.get("changedVariable"),
        "rationale": lineage.get("rationale"),
        "jobIds": [job["id"] for job in jobs],
        "jobs": [{"id": job["id"], "windowKind": job.get("windowKind"), "status": job.get("status")} for job in jobs],
        "status": "running",
        "verdict": None,
        "metrics": None,
    }


def start(thread_id: str, strategy: dict, jobs: list[dict]) -> dict:
    now = utc_now()
    run_id = new_id()
    state = {
        "rootStrategyId": strategy["id"],
        "currentStrategyId": strategy["id"],
        "chartJobId": jobs[0]["id"],
        "changeCount": 0,
        "consecutiveNonImprovements": 0,
        "bestStrategyId": None,
        "bestScore": -1.0,
        "candidates": [_candidate(strategy, jobs)],
        "events": [
            _event("queued", "Nautilus validation queued", f"{len(jobs)} evidence windows for {strategy.get('name')}."),
        ],
    }
    with database.session_scope() as db:
        db.add(AgentRun(id=run_id, thread_id=thread_id, kind="research", status="running",
                        input_json={"strategyId": strategy["id"]}, state_json=state,
                        created_at=now, updated_at=now))
        for job in jobs:
            row = db.get(Backtest, job["id"])
            if row:
                row.agent_run_id = run_id
    strategy_store.set_status(strategy["id"], "testing")
    return get(run_id)


def _core(spec: dict) -> dict:
    ignored = {"id", "name", "description", "origin", "lineage", "status", "createdAt", "updatedAt"}
    return {k: copy.deepcopy(v) for k, v in spec.items() if k not in ignored}


def _diff_paths(left: Any, right: Any, prefix: str = "") -> list[str]:
    if isinstance(left, dict) and isinstance(right, dict):
        paths = []
        for key in sorted(set(left) | set(right)):
            path = f"{prefix}.{key}" if prefix else key
            if key not in left or key not in right:
                paths.append(path)
            else:
                paths.extend(_diff_paths(left[key], right[key], path))
        return paths
    if left != right:
        return [prefix]
    return []


def validate_revision(run_id: str, incoming: dict) -> None:
    run = get(run_id)
    if not run or run["status"] != "analyzing":
        raise ValueError("wait for the current Nautilus validation before creating another strategy")
    if should_stop(run):
        raise ValueError("the five-change research budget is exhausted; conclude the research")
    current = strategy_store.get_strategy(run["currentStrategyId"])
    lineage = incoming.get("lineage") or {}
    if lineage.get("parentId") != current["id"]:
        raise ValueError("a revision must name the current strategy as lineage.parentId")
    if int(lineage.get("trialIndex") or 0) != int((current.get("lineage") or {}).get("trialIndex") or 0) + 1:
        raise ValueError("lineage.trialIndex must increment by exactly one")
    changed = _diff_paths(_core(current), _core(incoming))
    declared = lineage.get("changedVariable")
    if len(changed) != 1 or declared != changed[0]:
        raise ValueError(f"change exactly one variable and set lineage.changedVariable to its path; changed={changed}")
    if not (lineage.get("rationale") or "").strip():
        raise ValueError("a revision requires a lineage rationale based on the completed result")


def queue_revision(run_id: str, strategy: dict, jobs: list[dict]) -> dict:
    with database.session_scope() as db:
        row = db.get(AgentRun, run_id)
        if row is None or row.status != "analyzing":
            raise ValueError("the workflow is not ready for another candidate")
        state = dict(row.state_json or {})
        candidates = list(state.get("candidates", []))
        candidates.append(_candidate(strategy, jobs))
        state.update({"currentStrategyId": strategy["id"], "chartJobId": jobs[0]["id"],
                      "changeCount": int(state.get("changeCount", 0)) + 1, "candidates": candidates})
        events = list(state.get("events", []))
        lin = strategy.get("lineage") or {}
        events.append(_event("revision", f"Changed {lin.get('changedVariable')}", lin.get("rationale") or ""))
        events.append(_event("queued", "Nautilus validation queued", f"{len(jobs)} evidence windows for {strategy.get('name')}."))
        state["events"] = events
        row.state_json = state
        row.status = "running"
        row.updated_at = utc_now()
        for job in jobs:
            backtest = db.get(Backtest, job["id"])
            if backtest:
                backtest.agent_run_id = run_id
    strategy_store.set_status(strategy["id"], "testing")
    return get(run_id)


def _report_summary(strategy_id: str) -> tuple[dict, dict]:
    strategy = strategy_store.get_strategy(strategy_id)
    with database.session_scope() as db:
        latest_is = (db.query(Backtest).filter(Backtest.strategy_id == strategy_id, Backtest.window_kind == "is")
                     .order_by(Backtest.created_at.desc()).first())
        mode = latest_is.mode if latest_is else spec_mod.required_mode(strategy)
        attempts = db.query(ResearchProposal).filter(ResearchProposal.strategy_id.is_not(None)).count()
    report = validation.report(
        strategy_id, mode=mode, risk=strategy.get("risk"),
        trial_index=max(1, attempts),
        include_oos=False,
    )
    is_ = report.get("inSample") or {}
    metrics = {k: is_.get(k) for k in ("trades", "netPnl", "profitFactor", "expectancyR", "maxDrawdownPct")}
    metrics["walkForwardPositive"] = sum(1 for item in report.get("walkForward", []) if (item.get("netPnl") or 0) > 0)
    metrics["walkForwardTotal"] = len(report.get("walkForward", []))
    return report, metrics


def complete_batch(run_id: str) -> dict | None:
    """Atomically record a finished candidate and reserve its analysis turn."""
    with database.session_scope() as db:
        from research_agent.memory import _write_reservation
        _write_reservation(db)
        row = db.get(AgentRun, run_id)
        if row is None or row.status != "running":
            return None
        related = db.query(Backtest).filter(Backtest.agent_run_id == run_id).all()
        state = dict(row.state_json or {})
        current = next((c for c in reversed(state.get("candidates", [])) if c["strategyId"] == state.get("currentStrategyId")), None)
        current_ids = set((current or {}).get("jobIds", []))
        batch = [job for job in related if job.id in current_ids]
        if not batch or any(job.status not in TERMINAL for job in batch):
            return None
        batch_errors = [job.message or f"{job.window_kind} failed" for job in batch if job.status == "error"]
        row.status = "analyzing"
        row.updated_at = utc_now()

    run = get(run_id)
    strategy_id = run["currentStrategyId"]
    try:
        if batch_errors:
            report, metrics = {}, {}
            verdict = {"status": "technical_error", "passes": False, "untestable": True,
                       "score": None, "failures": batch_errors}
        else:
            report, metrics = _report_summary(strategy_id)
            verdict = report.get("verdict")
            if not verdict:
                verdict = {"status": "technical_error", "untestable": True, "score": None,
                           "failures": ["validation report unavailable"]}
    except Exception as exc:
        report, metrics, verdict = {}, {}, {"status": "technical_error", "untestable": True, "score": None, "failures": [str(exc)]}

    with database.session_scope() as db:
        row = db.get(AgentRun, run_id)
        state = dict(row.state_json or {})
        candidates = list(state.get("candidates", []))
        candidate = next(c for c in reversed(candidates) if c["strategyId"] == strategy_id)
        technical_failure = (verdict or {}).get("untestable") or (verdict or {}).get("status") == "technical_error"
        candidate["status"] = "blocked" if technical_failure else "complete"
        if technical_failure:
            row.status = "blocked"
        candidate["verdict"] = verdict
        candidate["metrics"] = metrics
        jobs_by_id = {job.id: job for job in db.query(Backtest).filter(Backtest.agent_run_id == run_id).all()}
        candidate["jobs"] = [{**j, "status": jobs_by_id[j["id"]].status,
                              "message": jobs_by_id[j["id"]].message} for j in candidate["jobs"]]
        score = float((verdict or {}).get("score") or 0)
        improved = score > float(state.get("bestScore", -1))
        if improved and not technical_failure:
            state["bestScore"] = score
            state["bestStrategyId"] = strategy_id
            state["consecutiveNonImprovements"] = 0
        elif not technical_failure:
            state["consecutiveNonImprovements"] = int(state.get("consecutiveNonImprovements", 0)) + 1
        state["candidates"] = candidates
        events = list(state.get("events", []))
        status = (verdict or {}).get("status", "error")
        events.append(_event("result", f"Nautilus result: {status}",
                             f"PF {metrics.get('profitFactor')} · expectancy {metrics.get('expectancyR')}R · "
                             f"walk-forward {metrics.get('walkForwardPositive')}/{metrics.get('walkForwardTotal')}."))
        state["events"] = events
        row.state_json = state
        row.updated_at = utc_now()
    return get(run_id)


def conclude(run_id: str, outcome: str, summary: str, strategy_id: str | None = None) -> dict:
    if outcome not in {"champion", "no_edge", "blocked", "stopped"}:
        raise ValueError("invalid research outcome")
    with database.session_scope() as db:
        row = db.get(AgentRun, run_id)
        if row is None:
            raise ValueError("research workflow not found")
        if row.status not in {"analyzing", "awaiting_approval", "blocked"}:
            raise ValueError("wait for Nautilus to finish before concluding the research")
        state = dict(row.state_json or {})
        if outcome == "no_edge" and any(c.get("status") == "blocked" or (c.get("verdict") or {}).get("untestable") or not c.get("verdict") for c in state.get("candidates", [])):
            raise ValueError("unavailable/technical evidence cannot support a no-edge conclusion")
        winner = strategy_id or state.get("bestStrategyId")
        if outcome == "champion":
            allowed = {c["strategyId"] for c in state.get("candidates", []) if (c.get("verdict") or {}).get("status") == "pass"}
            if winner not in allowed:
                raise ValueError("only a strategy that passed the validation verdict can be the champion")
        answer = {"outcome": outcome, "strategyId": winner if outcome == "champion" else None,
                  "summary": summary, "at": utc_now()}
        row.answer_json = answer
        row.status = {"champion": "done", "no_edge": "budget_exhausted", "blocked": "blocked", "stopped": "stopped"}[outcome]
        events = list(state.get("events", []))
        events.append(_event("conclusion", "Validation candidate retained" if outcome == "champion" else outcome.replace("_", " "), summary))
        state["events"] = events
        row.state_json = state
        row.updated_at = utc_now()
    if outcome == "champion":
        strategy_store.set_status(winner, "candidate")
    elif outcome == "no_edge":
        for candidate in get(run_id)["candidates"]:
            strategy_store.set_status(candidate["strategyId"], "rejected")
    return get(run_id)


def should_stop(run: dict) -> bool:
    # The public shape omits this counter, so derive non-improvement stopping from the persisted row.
    with database.session_scope() as db:
        row = db.get(AgentRun, run["id"])
        state = row.state_json or {}
        return int(state.get("changeCount", 0)) >= MAX_CHANGES or int(state.get("consecutiveNonImprovements", 0)) >= MAX_NON_IMPROVEMENTS


def _record_window_result(run_id: str, job_id: str) -> None:
    with database.session_scope() as db:
        row = db.get(AgentRun, run_id)
        job = db.get(Backtest, job_id)
        if row is None or job is None or row.status != "running":
            return
        state = dict(row.state_json or {})
        candidates = list(state.get("candidates", []))
        current = next((c for c in reversed(candidates) if c["strategyId"] == state.get("currentStrategyId")), None)
        if current is None:
            return
        for item in current.get("jobs", []):
            if item["id"] == job_id:
                item["status"] = job.status
                item["message"] = job.message
        events = list(state.get("events", []))
        if not any(event.get("jobId") == job_id for event in events):
            label = job.window_kind.upper()
            event = _event("window" if job.status == "done" else "error",
                           f"{label} window {'complete' if job.status == 'done' else 'failed'}",
                           job.message or "Nautilus results are ready.")
            event["jobId"] = job_id
            events.append(event)
        state["events"] = events
        state["candidates"] = candidates
        row.state_json = state
        row.updated_at = utc_now()


def job_finished(run_id: str | None, job_id: str) -> None:
    if not run_id:
        return
    _record_window_result(run_id, job_id)
    run = complete_batch(run_id)
    if not run or run["status"] != "analyzing":
        return

    _start_resume(run_id)


def _start_resume(run_id: str) -> bool:
    """Start one analysis continuation per run in this API process."""
    with _resume_lock:
        if run_id in _resuming:
            return False
        _resuming.add(run_id)

    def resume() -> None:
        from research_agent import service
        try:
            service.continue_workflow(run_id)
        except Exception as exc:
            with database.session_scope() as db:
                row = db.get(AgentRun, run_id)
                if row and row.status == "analyzing":
                    state = dict(row.state_json or {})
                    events = list(state.get("events", []))
                    events.append(_event("error", "Stratos could not analyze the result", str(exc)))
                    state["events"] = events
                    row.state_json = state
                    row.status = "error"
                    row.updated_at = utc_now()
        finally:
            with _resume_lock:
                _resuming.discard(run_id)

    threading.Thread(target=resume, daemon=True, name=f"stratos-{run_id}").start()
    return True


def recover_pending_runs() -> int:
    """Resume durable workflow analysis interrupted by an API restart."""
    with database.session_scope() as db:
        rows = (db.query(AgentRun).filter(AgentRun.status.in_(("running", "analyzing")))
                .order_by(AgentRun.created_at).all())
        pending = [(row.id, row.status) for row in rows]

    resumed = 0
    for run_id, status in pending:
        if status == "running":
            run = complete_batch(run_id)
            if not run or run["status"] != "analyzing":
                continue
        if _start_resume(run_id):
            resumed += 1
    return resumed
