"""Controller verification with synthetic metadata only; no market backtests."""
import copy
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

import database
import strategy_store
from engine import jobs, spec
from models import AgentRun, Backtest, ResearchDecision, ResearchEvidence, ResearchProposal, Strategy
from research_agent import evidence, knowledge, memory, service, tools, workflow
from research_agent.readiness import executable_errors
from tests.test_research_agent import agent_db  # noqa: F401 — fixture
from tests.test_spec_validation import ORB


@pytest.fixture
def setup(agent_db, monkeypatch, tmp_path):
    context = {"windows": {"is": ["2026-04-01", "2026-04-30"], "wf1": ["2026-04-15", "2026-04-30"]},
               "splitsSha256": "synthetic-splits", "manifestSha256": None, "frontMonthSha256": "synthetic-map",
               "engineConfig": "synthetic config", "inventory": {"bars": [{"path": "synthetic"}], "ticks": []},
               "identityPolicy": "metadata_inventory_not_immutable_snapshot", "entrySnapshots": "unavailable"}
    monkeypatch.setattr(memory, "dataset_context", lambda: copy.deepcopy(context))
    monkeypatch.setattr(jobs, "JOBS_DIR", tmp_path / "jobs")
    started = []
    def dispatch(jid):
        # Jobs/workflow/approval/evidence must all be durable before dispatch.
        with database.session_scope() as db:
            row = db.get(Backtest, jid)
            assert row.agent_run_id and db.get(AgentRun, row.agent_run_id)
            link = db.query(ResearchEvidence).filter_by(job_id=jid).one()
            assert db.query(ResearchDecision).filter_by(proposal_id=link.proposal_id, action="approve").one()
        started.append(jid)
    monkeypatch.setattr(jobs, "start", dispatch)
    strategy = spec.normalize(ORB)
    strategy["execution"]["mode"] = "bars"
    thread = service.create_thread()["id"]
    plan = {"windows": ["is", "wf1"], "objective": "Synthetic software fixture",
            "comparison": "descriptive", "uncertaintyPolicy": "unresolved; descriptive only",
            "multipleTestingPolicy": "no significance claim; external trials unknown",
            "dataPolicy": context["identityPolicy"]}
    return SimpleNamespace(thread=thread, strategy=strategy, plan=plan, context=context, started=started)


def proposal(setup, **kwargs):
    return memory.propose(setup.thread, "Synthetic fixture hypothesis", kwargs.get("strategy", setup.strategy),
                          kwargs.get("plan", setup.plan), ["opening range"], [], kwargs.get("parent_id"))


def approve(p):
    return memory.decide(p["id"], p["threadId"], p["digest"], "approve", "Reviewed this exact synthetic fixture")


def counts():
    with database.session_scope() as db:
        return tuple(db.query(model).count() for model in (Backtest, ResearchEvidence, AgentRun, Strategy))


def test_raw_draft_keeps_missing_choices_and_cannot_approve(setup):
    p = proposal(setup, strategy=ORB)
    assert p["status"] == "draft"
    assert "risk" not in p["document"]["strategy"]
    assert any("risk" in error for error in p["blockers"])
    with pytest.raises(memory.ResearchConflict, match="not executable"):
        approve(p)
    assert counts() == (0, 0, 0, 0)


def test_exact_approval_is_required_and_idempotent(setup):
    p = proposal(setup)
    assert p["blockers"] == []
    with pytest.raises(memory.ResearchConflict, match="approval"):
        memory.enqueue(p["id"], setup.thread)
    with pytest.raises(memory.ResearchConflict, match="digest mismatch"):
        memory.decide(p["id"], setup.thread, "0" * 64, "approve", "yes")
    other = service.create_thread()["id"]
    with pytest.raises(memory.ResearchConflict):
        memory.enqueue(p["id"], other)
    assert counts() == (0, 0, 0, 0)
    approve(p)
    approve(p)
    queued = memory.enqueue(p["id"], setup.thread)
    again = memory.enqueue(p["id"], setup.thread)
    assert queued == again and len(setup.started) == 2
    assert counts() == (2, 2, 1, 1)
    with pytest.raises(memory.ResearchConflict, match="immutable"):
        memory.decide(p["id"], setup.thread, p["digest"], "reject_stop", "Changed my mind")


def test_concurrent_queue_consumes_approval_once(setup):
    p = proposal(setup); approve(p)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: memory.enqueue(p["id"], setup.thread), range(2)))
    assert results[0]["strategyId"] == results[1]["strategyId"]
    assert counts() == (2, 2, 1, 1) and len(setup.started) == 2


def test_rejection_reason_preserved_and_revise_is_not_permission(setup):
    p = proposal(setup)
    memory.decide(p["id"], setup.thread, p["digest"], "reject_revise", "Target choice unsupported")
    with pytest.raises(memory.ResearchConflict, match="approval"):
        memory.enqueue(p["id"], setup.thread)
    child = proposal(setup, parent_id=p["id"])
    with pytest.raises(memory.ResearchConflict, match="approval"):
        memory.enqueue(child["id"], setup.thread)
    assert memory.get(p["id"], setup.thread)["decision"]["reason"] == "Target choice unsupported"
    assert counts() == (0, 0, 0, 0)


def test_stop_prevents_later_approved_proposal(setup):
    p = proposal(setup)
    memory.decide(p["id"], setup.thread, p["digest"], "reject_stop", "Stop this research")
    child = proposal(setup, parent_id=p["id"]); approve(child)
    with pytest.raises(memory.ResearchConflict, match="stopped"):
        memory.enqueue(child["id"], setup.thread)
    assert counts() == (0, 0, 0, 0)


def test_stale_dataset_or_plan_cannot_queue(setup):
    p = proposal(setup); approve(p)
    setup.context["engineConfig"] = "changed"
    with pytest.raises(memory.ResearchConflict, match="dataset/config changed"):
        memory.enqueue(p["id"], setup.thread)
    assert counts() == (0, 0, 0, 0)
    with database.session_scope() as db:
        row = db.get(ResearchProposal, p["id"])
        doc = copy.deepcopy(row.document_json)
        doc["testPlan"]["objective"] = "mutated"
        row.document_json = doc
    with pytest.raises(memory.ResearchConflict, match="content changed"):
        memory.enqueue(p["id"], setup.thread)


def test_partial_job_creation_rolls_back_all_rows_and_files(setup, monkeypatch):
    p = proposal(setup); approve(p)
    write = Path.write_text
    calls = []
    def broken(path, *args, **kwargs):
        if path.name == "strategy.json":
            calls.append(path)
            if len(calls) == 2:
                raise OSError("synthetic disk failure")
        return write(path, *args, **kwargs)
    monkeypatch.setattr(Path, "write_text", broken)
    with pytest.raises(OSError, match="disk failure"):
        memory.enqueue(p["id"], setup.thread)
    assert counts() == (0, 0, 0, 0) and not setup.started
    assert not list(jobs.JOBS_DIR.iterdir())
    assert memory.get(p["id"], setup.thread)["status"] == "approved"


def test_child_is_separate_approval_and_workflow_checked_before_jobs(setup):
    p = proposal(setup); approve(p)
    q = memory.enqueue(p["id"], setup.thread)
    raw = copy.deepcopy(setup.strategy)
    raw["exit"]["target"]["value"] = 2.5
    raw["lineage"] = {"parentId": q["strategyId"], "trialIndex": 1,
                      "changedVariable": "exit.target.value", "rationale": "Synthetic changed variable"}
    child = proposal(setup, strategy=raw, parent_id=p["id"])
    with pytest.raises(memory.ResearchConflict, match="approval"):
        memory.enqueue(child["id"], setup.thread)
    approve(child)
    with pytest.raises(memory.ResearchConflict, match="wait for"):
        memory.enqueue(child["id"], setup.thread)
    assert counts() == (2, 2, 1, 1)
    with database.session_scope() as db:
        db.get(AgentRun, q["runId"]).status = "awaiting_approval"
    memory.enqueue(child["id"], setup.thread)
    assert counts() == (4, 4, 1, 2)
    assert memory.history(setup.thread)["recordedAttempts"] == 2


def test_backend_stop_budget_blocks_even_approved_child(setup):
    p = proposal(setup); approve(p); q = memory.enqueue(p["id"], setup.thread)
    child = proposal(setup, parent_id=p["id"]); approve(child)
    with database.session_scope() as db:
        run = db.get(AgentRun, q["runId"])
        run.status = "awaiting_approval"
        run.state_json = {**run.state_json, "consecutiveNonImprovements": 3}
    with pytest.raises(memory.ResearchConflict, match="budget"):
        memory.enqueue(child["id"], setup.thread)
    assert counts() == (2, 2, 1, 1)


def test_agent_versions_immutable_and_legacy_queue_cannot_bypass(setup):
    p = proposal(setup); approve(p); q = memory.enqueue(p["id"], setup.thread)
    strategy = strategy_store.get_strategy(q["strategyId"])
    with pytest.raises(strategy_store.StrategyError, match="immutable"):
        strategy_store.update_strategy(strategy["id"], {"direction": "short"})
    with pytest.raises(ValueError, match="approved"):
        jobs.run_validation(strategy)
    with pytest.raises(ValueError, match="approved proposal"):
        jobs.create_job({**strategy, "origin": {"type": "manual"}})
    strategy_store.set_status(strategy["id"], "candidate")
    assert counts() == (2, 2, 1, 1)


def test_worker_rechecks_approved_artifact_and_data(setup):
    p = proposal(setup); approve(p); q = memory.enqueue(p["id"], setup.thread)
    jid = q["evidence"][0]["jobId"]
    raw = json.loads((jobs._job_dir(jid) / "strategy.json").read_text())
    memory.verify_job_inputs(jid, raw)
    with pytest.raises(memory.ResearchConflict, match="artifact"):
        memory.verify_job_inputs(jid, {**raw, "direction": "short"})
    setup.context["splitsSha256"] = "changed"
    with pytest.raises(memory.ResearchConflict, match="dataset"):
        memory.verify_job_inputs(jid, raw)


def test_missing_data_and_unsupported_modes_stay_drafts(setup):
    setup.context["inventory"]["bars"] = []
    assert any("dataset unavailable" in e for e in proposal(setup)["blockers"])
    raw = copy.deepcopy(setup.strategy)
    raw["instrument"] = {"root": "NQ", "symbol": "NQ1!"}
    assert any("ES1!" in e for e in executable_errors(raw))
    raw = copy.deepcopy(setup.strategy)
    raw["session"]["entryWindow"] = {"start": "18:00", "end": "23:00"}
    assert any("RTH" in e for e in executable_errors(raw))
    raw = copy.deepcopy(setup.strategy)
    del raw["exit"]["stop"]
    assert any("exit.stop" in e for e in executable_errors(raw))


def test_technical_failure_is_blocked_not_no_edge(setup, monkeypatch):
    p = proposal(setup); approve(p); q = memory.enqueue(p["id"], setup.thread)
    with database.session_scope() as db:
        for row in db.query(Backtest).all():
            row.status, row.message = "error", "synthetic missing data"
    monkeypatch.setattr(workflow, "_report_summary", lambda _: pytest.fail("must not infer results on technical failure"))
    run = workflow.complete_batch(q["runId"])
    assert run["status"] == "blocked" and run["candidates"][0]["metrics"] == {}
    with pytest.raises(ValueError, match="no-edge"):
        workflow.conclude(run["id"], "no_edge", "invalid conclusion")
    with database.session_scope() as db:
        assert db.get(AgentRun, run["id"]).state_json["consecutiveNonImprovements"] == 0


def test_automatic_resume_is_read_only_and_does_not_force_conclusion(setup):
    p = proposal(setup); approve(p); q = memory.enqueue(p["id"], setup.thread)
    with database.session_scope() as db:
        db.get(AgentRun, q["runId"]).status = "analyzing"
    context = {"thread_id": setup.thread, "run_id": q["runId"], "automatic_analysis": True}
    for name in ("run_validation", "save_strategy", "propose_experiment", "conclude_research"):
        with pytest.raises(ValueError, match="read-only"):
            tools.execute(name, {}, context)
    client = SimpleNamespace(responses=SimpleNamespace(create=lambda **kw: SimpleNamespace(id="fake", output=[], output_text="Review the evidence before another proposal.")))
    result = service.continue_workflow(q["runId"], client=client)
    assert result["workflow"]["status"] == "awaiting_approval" and result["workflow"]["conclusion"] is None
    assert counts() == (2, 2, 1, 1)


def test_note_ingestion_short_passages_and_immutable_repository_versions(agent_db):
    note = knowledge.ingest_text(title="Note", content="Avoid hindsight regimes.", kind="user_note", source_url=None, author="user", license_name=None)
    found = knowledge.search("hindsight")[0]
    assert found["url"].startswith("/api/agent/knowledge/passages/")
    read = knowledge.passage(found["id"])
    assert read["content"] == "Avoid hindsight regimes." and read["revision"] == note["revision"]
    old = knowledge.search("preserve time order")[0]
    knowledge.ingest_repository(root=agent_db, name="test-research", url="https://example.test/repo", revision="new-revision", license_name="MIT")
    assert knowledge.passage(old["id"])["revision"] == "abc123"
    with pytest.raises(ValueError, match="http"):
        knowledge.ingest_text(title="Bad", content="secret", kind="selected_source", source_url="file:///etc/passwd", author=None, license_name=None)


def artifact(setup, trades, kind="is"):
    with database.session_scope() as db:
        row = Backtest(mode="bars", window_kind=kind, status="done", metrics_json={"symbol": "ES1!"})
        db.add(row); db.flush()
        jid = row.id
    path = jobs._job_dir(jid)
    path.mkdir(parents=True)
    (path / "trades.json").write_text(json.dumps({"trades": trades}))
    return jid


def test_full_trade_paging_missing_snapshots_and_oos_block(setup):
    trades = [{"id": str(i), "pnlUsd": i, "sessionDate": "2026-04-01"} for i in range(60)]
    jid = artifact(setup, trades)
    result = evidence.trade_evidence(jid, 25, 30)
    assert len(result["trades"]) == 30 and result["trades"][0]["id"] == "25"
    assert result["trades"][0]["mae"] is None
    assert result["trades"][0]["entrySnapshot"]["status"] == "unavailable"
    with pytest.raises(ValueError, match="OOS"):
        evidence.trade_evidence(artifact(setup, trades, "oos"), 0, 20)
    (jobs._job_dir(jid) / "trades.json").unlink()
    with pytest.raises(ValueError, match="unavailable"):
        evidence.trade_evidence(jid, 0, 20)


def test_group_comparison_clusters_overlap_and_insufficient_evidence(setup):
    trades = [{"pnlUsd": day + i, "sessionDate": f"2026-04-{day:02}", "direction": side}
              for day in range(1, 11) for i, side in enumerate(("long", "short"))]
    jid = artifact(setup, trades)
    policy = {"method": "session_cluster_bootstrap", "confidence": 0.9, "resamples": 100, "seed": 7, "minSessions": 5}
    result = evidence.compare_groups(jid, {"direction": "long"}, {"direction": "short"}, policy)
    assert result["uncertainty"]["meanPnlDifferenceIntervalUsd"] == [-1.0, -1.0]
    assert result["overlapTrades"] == 0 and result["repeatedTesting"]["significance"].startswith("not established")
    overlap = evidence.compare_groups(jid, {}, {}, policy)
    assert overlap["overlapTrades"] == 20
    assert overlap["uncertainty"]["meanPnlDifferenceIntervalUsd"] == [0.0, 0.0]
    insufficient = evidence.compare_groups(jid, {}, {}, {**policy, "minSessions": 11})
    assert insufficient["uncertainty"]["status"] == "unavailable"
    assert evidence.compare_groups(jid, {}, {}, None)["uncertainty"]["status"] == "unresolved"
    with pytest.raises(ValueError, match="unsupported selector"):
        evidence.compare_groups(jid, {"arbitrary": 1}, {}, policy)


def test_stability_cost_scenarios_are_descriptive_and_no_queue(setup):
    jid = artifact(setup, [{"pnlUsd": 10, "r": 1, "sessionDate": "2026-04-01", "regimeTags": ["trend"]},
                           {"pnlUsd": -5, "r": -0.5, "sessionDate": "2026-04-02"}])
    result = evidence.stability([jid], [0, 3])
    assert result["results"][0]["costSensitivity"][1]["netPnlUsd"] == -1
    assert "trend" in result["results"][0]["byRegime"]
    assert not setup.started


def test_http_contract_rejects_forged_decisions_and_ingests_user_text(client, monkeypatch):
    monkeypatch.setattr(memory, "dataset_context", lambda: {"windows": {}, "identityPolicy": "metadata_inventory_not_immutable_snapshot",
                        "splitsSha256": None, "frontMonthSha256": None, "inventory": {"bars": [], "ticks": []}})
    thread = client.post("/api/agent/threads", json={}).json()["id"]
    p = client.post(f"/api/agent/threads/{thread}/proposals", json={"hypothesis": "draft", "strategy": ORB,
                    "testPlan": {}, "concepts": [], "passageIds": []}).json()
    assert p["status"] == "draft"
    decision = client.post(f"/api/agent/threads/{thread}/proposals/{p['id']}/decision",
                           json={"digest": p["digest"], "action": "approve", "reason": "reviewed"})
    assert decision.status_code == 409
    assert client.post(f"/api/agent/threads/{thread}/proposals/{p['id']}/queue").status_code == 409
    note = client.post("/api/agent/knowledge/text", json={"title": "Note", "content": "Short note", "kind": "user_note"})
    assert note.status_code == 200
    assert not any(t["name"] in {"approve", "decide_proposal", "ingest_source_text"} for t in tools.TOOL_DEFINITIONS)
