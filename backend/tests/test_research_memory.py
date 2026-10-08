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
from tests.test_spec_validation import ORB


@pytest.fixture
def setup(agent_db, monkeypatch, tmp_path):
    context = {"windows": {"is": ["2026-04-01", "2026-04-30"], "wf1": ["2026-04-15", "2026-04-30"]},
               "inSampleSessions": [f"2026-04-{day:02}" for day in range(1, 31)],
               "splitsSha256": "synthetic-splits", "manifestSha256": None, "frontMonthSha256": "synthetic-map",
               "engineConfig": "synthetic config", "inventory": {"bars": [{"path": "synthetic"}], "ticks": []},
               "identityPolicy": "metadata_inventory_not_immutable_snapshot", "entrySnapshots": "unavailable"}
    monkeypatch.setattr(memory, "dataset_context", lambda: copy.deepcopy(context))
    monkeypatch.setattr(memory.validation, "windows", lambda _: context["windows"])
    monkeypatch.setattr(memory.validation, "_splits", lambda _: {"inSample": [f"2026-04-{day:02}" for day in range(1, 31)]})
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
    strategy["exit"]["stop"].update(type="ticks", value=20, structure=None)  # explicit supported synthetic fixture
    strategy["risk"].update(weeklyLossLimitPct=0, maxTradesPerDay=1, stopAfterConsecutiveLosses=1)
    strategy["risk"]["passCriteria"]["minWalkForwardWindowsPositive"] = 1  # explicit fixture policy for its one WF window
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
        run = db.get(AgentRun, q["runId"])
        run.status = "awaiting_approval"
        state = copy.deepcopy(run.state_json)
        state["candidates"][-1]["status"] = "complete"
        run.state_json = state
    memory.enqueue(child["id"], setup.thread)
    assert counts() == (4, 4, 1, 2)
    assert memory.history(setup.thread)["recordedAttempts"] == 2


def test_backend_stop_budget_blocks_even_approved_child(setup):
    p = proposal(setup); approve(p); q = memory.enqueue(p["id"], setup.thread)
    child = proposal(setup, parent_id=p["id"]); approve(child)
    with database.session_scope() as db:
        run = db.get(AgentRun, q["runId"])
        run.status = "awaiting_approval"
        state = copy.deepcopy(run.state_json)
        state["candidates"][-1]["status"] = "complete"
        run.state_json = {**state, "consecutiveNonImprovements": 3}
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
        run = db.get(AgentRun, q["runId"])
        run.status = "analyzing"
        state = copy.deepcopy(run.state_json)
        state["candidates"][-1]["status"] = "complete"
        run.state_json = state
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
        row = Backtest(mode="bars", window_kind=kind, date_from="2026-04-01", date_to="2026-04-30", status="done", metrics_json={"symbol": "ES1!"})
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


def test_legacy_agent_recovery_cannot_execute_without_approval(setup):
    saved = strategy_store.save_strategy({**setup.strategy, "origin": {"type": "agent"}})
    with database.session_scope() as db:
        job = Backtest(strategy_id=saved["id"], mode="bars", window_kind="is", status="queued", metrics_json={})
        db.add(job); db.flush()
        jid = job.id
    with pytest.raises(memory.ResearchConflict, match="legacy agent job"):
        memory.verify_job_inputs(jid, saved)


def test_analysis_reservation_is_not_completed_evidence(setup):
    p = proposal(setup); approve(p); q = memory.enqueue(p["id"], setup.thread)
    child = proposal(setup, parent_id=p["id"]); approve(child)
    with database.session_scope() as db:
        db.get(AgentRun, q["runId"]).status = "analyzing"
    with pytest.raises(memory.ResearchConflict, match="evidence to be recorded"):
        memory.enqueue(child["id"], setup.thread)
    assert counts() == (2, 2, 1, 1)


def test_evidence_retention_returns_explicit_errors(setup):
    p = proposal(setup); approve(p); q = memory.enqueue(p["id"], setup.thread)
    with pytest.raises(ValueError, match="retained"):
        jobs.delete_job(q["evidence"][0]["jobId"])
    with pytest.raises(strategy_store.StrategyError, match="retained"):
        strategy_store.delete_strategy(q["strategyId"])
    assert counts() == (2, 2, 1, 1)


def test_analysis_resource_limits_are_explicit(setup, monkeypatch):
    jid = artifact(setup, [{"pnlUsd": 1, "sessionDate": "2026-04-01"}, {"pnlUsd": -1, "sessionDate": "2026-04-02"}])
    policy = {"method": "session_cluster_bootstrap", "confidence": 0.9, "resamples": 100, "seed": 7, "minSessions": 2}
    monkeypatch.setattr(evidence, "MAX_BOOTSTRAP_DRAWS", 100)
    with pytest.raises(ValueError, match="session draws"):
        evidence.compare_groups(jid, {}, {}, policy)
    monkeypatch.setattr(evidence, "MAX_ARTIFACT_BYTES", 1)
    with pytest.raises(ValueError, match="analysis limit"):
        evidence.trade_evidence(jid, 0, 10)
    with pytest.raises(ValueError, match="256 KB"):
        memory.propose(setup.thread, "x" * 256001, setup.strategy, setup.plan, [], [])


@pytest.mark.parametrize("path,value,expected", [
    ("risk.weeklyLossLimitPct", 5, "weekly risk"),
    ("constraints.maxConcurrentPositions", 2, "one concurrent"),
    ("sizing.type", "vol_scaled", "not implemented"),
    ("risk.maxTradesPerDay", 10, "must agree"),
])
def test_unsupported_or_conflicting_worker_policies_block_execution(setup, path, value, expected):
    raw = copy.deepcopy(setup.strategy)
    section, key = path.split(".")
    raw[section][key] = value
    assert any(expected in err for err in executable_errors(raw))


def test_migration_round_trip_preserves_legacy_rows(tmp_path):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text

    eng = database.make_engine(f"sqlite+pysqlite:///{tmp_path / 'migration.db'}")
    cfg = Config(str(database.BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(database.BACKEND_DIR / "alembic"))
    cfg.attributes["connection_engine"] = eng
    command.upgrade(cfg, "d4e6f8a0b2c3")
    with eng.begin() as conn:
        conn.execute(text("INSERT INTO agent_threads(id,title,created_at,updated_at) VALUES ('legacy','Legacy','2026','2026')"))
    command.upgrade(cfg, "head")
    with eng.connect() as conn:
        assert conn.scalar(text("SELECT title FROM agent_threads WHERE id='legacy'")) == "Legacy"
        assert conn.scalar(text("SELECT count(*) FROM research_decisions")) == 0
    command.downgrade(cfg, "d4e6f8a0b2c3")
    command.upgrade(cfg, "head")
    with eng.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM agent_threads")) == 1
    eng.dispose()


def test_ignored_expression_fields_and_history_pagination(setup):
    raw = copy.deepcopy(setup.strategy)
    raw["entry"]["trigger"]["unimplementedFilter"] = True
    assert any("ignored/unsupported" in e for e in executable_errors(raw))
    proposal(setup)
    proposal(setup)
    result = memory.history(setup.thread, limit=1)
    assert result["total"] == 2 and len(result["proposals"]) == 1
    assert memory.history(setup.thread, limit=1, offset=1)["proposals"][0]["id"] != result["proposals"][0]["id"]


def test_dataset_context_labels_absence_and_tracks_engine_code(tmp_path, monkeypatch):
    from market.paths import Paths
    monkeypatch.setattr(memory, "get_paths", lambda: Paths(tmp_path / "data", tmp_path / "raw"))
    monkeypatch.setattr(memory.validation, "windows", lambda _: {})
    monkeypatch.setattr(jobs, "BACKEND_DIR", tmp_path / "backend")
    engine = jobs.BACKEND_DIR / "engine"
    engine.mkdir(parents=True)
    source = engine / "worker.py"
    source.write_text("# synthetic version one")
    first = memory.dataset_context()
    assert first["splitsSha256"] is None and first["inventory"]["bars"] == []
    assert first["entrySnapshots"] == "unavailable"
    source.write_text("# synthetic version two")
    assert memory.dataset_context()["engineCodeSha256"] != first["engineCodeSha256"]


def test_plan_cannot_omit_explicitly_required_walk_forward_evidence(setup):
    setup.strategy["risk"]["passCriteria"]["minWalkForwardWindowsPositive"] = 2
    p = proposal(setup, plan={**setup.plan, "windows": ["is"]})
    assert p["status"] == "draft"
    assert any("cannot satisfy minWalkForwardWindowsPositive=2" in blocker for blocker in p["blockers"])
    with pytest.raises(memory.ResearchConflict, match="not executable"):
        approve(p)
    assert counts() == (0, 0, 0, 0)
    # Even selecting the sole available WF window cannot meet the explicit two-window criterion.
    assert proposal(setup)["status"] == "draft"
    setup.context["windows"]["wf2"] = ["2026-04-20", "2026-04-30"]
    complete = proposal(setup, plan={**setup.plan, "windows": ["is", "wf1", "wf2"]})
    assert complete["blockers"] == []


def test_recovery_reconstructs_verdict_after_analysis_reservation_crash(setup, monkeypatch):
    p = proposal(setup); approve(p); q = memory.enqueue(p["id"], setup.thread)
    with database.session_scope() as db:
        for job in db.query(Backtest).all():
            job.status = "done"
        # Simulate process death immediately after the reservation transaction.
        db.get(AgentRun, q["runId"]).status = "analyzing"
    monkeypatch.setattr(workflow, "_report_summary", lambda _: ({"verdict": {"status": "fail", "score": 0.5}}, {}))
    resumed = []
    def resume(run_id):
        run = workflow.get(run_id)
        assert run["candidates"][-1]["status"] == "complete"
        assert run["candidates"][-1]["verdict"]["status"] == "fail"
        resumed.append(run_id)
        return True
    monkeypatch.setattr(workflow, "_start_resume", resume)
    assert workflow.recover_pending_runs() == 1 and resumed == [q["runId"]]
    before = workflow.get(q["runId"])
    assert workflow.complete_batch(q["runId"]) is None
    assert workflow.get(q["runId"]) == before  # counters/events are not applied twice
    raw = copy.deepcopy(setup.strategy)
    raw["exit"]["target"]["value"] = 2.5
    raw["lineage"] = {"parentId": q["strategyId"], "trialIndex": 1,
                      "changedVariable": "exit.target.value", "rationale": "Synthetic next reviewed proposal"}
    child = proposal(setup, strategy=raw, parent_id=p["id"]); approve(child)
    assert memory.enqueue(child["id"], setup.thread)["strategyId"] != q["strategyId"]


@pytest.mark.parametrize("effective,alias,value,declared", [
    ("risk.riskPerTradePct", "sizing.value", 0.75, "risk.riskPerTradePct"),
    ("sizing.maxContracts", "risk.maxContracts", 3, "sizing.maxContracts"),
    ("constraints.maxTradesPerDay", "risk.maxTradesPerDay", 2, "constraints.maxTradesPerDay"),
    ("constraints.stopAfterConsecutiveLosses", "risk.stopAfterConsecutiveLosses", 2, "constraints.stopAfterConsecutiveLosses"),
])
def test_one_logical_risk_change_keeps_exact_approved_aliases(setup, effective, alias, value, declared):
    p = proposal(setup); approve(p); q = memory.enqueue(p["id"], setup.thread)
    with database.session_scope() as db:
        run = db.get(AgentRun, q["runId"])
        run.status = "awaiting_approval"
        state = copy.deepcopy(run.state_json)
        state["candidates"][-1]["status"] = "complete"
        run.state_json = state
    raw = copy.deepcopy(setup.strategy)
    for path in (effective, alias):
        section, key = path.split(".")
        raw[section][key] = value
    raw["lineage"] = {"parentId": q["strategyId"], "trialIndex": 1,
                      "changedVariable": declared, "rationale": "One reviewed logical risk choice"}
    # A different executable choice in addition to the aliases remains forbidden.
    invalid = copy.deepcopy(raw)
    invalid["exit"]["target"]["value"] = 3.0
    bad = proposal(setup, strategy=invalid, parent_id=p["id"]); approve(bad)
    with pytest.raises(memory.ResearchConflict, match="exactly one changed variable"):
        memory.enqueue(bad["id"], setup.thread)
    child = proposal(setup, strategy=raw, parent_id=p["id"]); approve(child)
    queued = memory.enqueue(child["id"], setup.thread)
    saved = strategy_store.get_strategy(queued["strategyId"])
    for path in (effective, alias):
        section, key = path.split(".")
        assert saved[section][key] == value
    assert memory.get(child["id"], setup.thread)["document"]["strategy"] == child["document"]["strategy"]


@pytest.mark.parametrize("mutation", ["job_dates", "trade_date", "timestamp", "daily_returns", "missing_dates"])
def test_mislabeled_holdout_evidence_is_blocked_everywhere(setup, mutation):
    sid = strategy_store.save_strategy(setup.strategy)["id"]
    trade = {"pnlUsd": 42, "sessionDate": "2026-04-01"}
    jid = artifact(setup, [trade])
    path = jobs._job_dir(jid) / "trades.json"
    payload = json.loads(path.read_text())
    with database.session_scope() as db:
        row = db.get(Backtest, jid)
        row.strategy_id = sid
        row.metrics_json = {**row.metrics_json, "netPnl": 42, "trades": 1}
        if mutation == "job_dates":
            row.date_from, row.date_to = "2026-05-01", "2026-05-31"
        if mutation == "missing_dates":
            row.date_from = None
    if mutation == "trade_date":
        payload["trades"][0]["sessionDate"] = "2026-05-01"
    if mutation == "timestamp":
        from datetime import datetime
        from zoneinfo import ZoneInfo
        payload["trades"][0]["entryTime"] = datetime(2026, 5, 1, 10, tzinfo=ZoneInfo("America/New_York")).timestamp()
    if mutation == "daily_returns":
        payload["dailyReturns"] = [{"date": "2026-05-01", "returnPct": 42}]
    path.write_text(json.dumps(payload))
    for read in (lambda: evidence.trade_evidence(jid, 0, 10),
                 lambda: evidence.compare_groups(jid, {}, {}, None),
                 lambda: evidence.stability([jid], []),
                 lambda: tools.execute("get_validation", {"strategy_id": sid})):
        with pytest.raises(ValueError, match="scope|permitted"):
            read()
    result, _ = tools.execute("list_backtest_jobs", {"strategy_id": sid, "limit": 20})
    assert result["jobs"][0]["metrics"] is None and result["jobs"][0]["evidenceUnavailable"]


def test_approved_evidence_uses_pinned_sessions_after_split_changes(setup, monkeypatch):
    p = proposal(setup); approve(p); q = memory.enqueue(p["id"], setup.thread)
    jid = next(item["jobId"] for item in q["evidence"] if item["window"] == "is")
    with database.session_scope() as db:
        db.get(Backtest, jid).status = "done"
    (jobs._job_dir(jid) / "trades.json").write_text(json.dumps({"trades": [{"pnlUsd": 1, "sessionDate": "2026-04-01"}]}))
    monkeypatch.setattr(memory.validation, "_splits", lambda _: {"inSample": ["2026-06-01"]})
    assert evidence.trade_evidence(jid, 0, 10)["total"] == 1


@pytest.mark.parametrize("field,value", [("stop", {"type": "structure", "structure": "swing_low"}),
                                          ("target", {"type": "level", "level": "vwap"})])
def test_implicit_exit_fallbacks_are_not_approved(setup, field, value):
    raw = copy.deepcopy(setup.strategy)
    raw["exit"][field].update(value)
    assert any("structure/level exits are unsupported" in e for e in executable_errors(raw))


@pytest.mark.parametrize("name,params", [("opening_range_high", {"minutes": None}),
                                         ("sma", {"period": 0}), ("sma", {"period": -1}),
                                         ("sma", {"period": None}), ("sma", {"period": "20"}),
                                         ("ema", {"period": None})])
def test_primitive_parameters_must_be_executable_choices(setup, name, params):
    raw = copy.deepcopy(setup.strategy)
    raw["entry"]["trigger"] = {"op": "gt", "args": [{"field": "close"}, {"ind": name, "params": params}]}
    assert executable_errors(raw)


@pytest.mark.parametrize("mode,override,allowed", [("ticks", -1, False), ("ticks", 3, False),
                                                   ("ticks", 0, True), ("ticks", 1, True), ("bars", -1, False),
                                                   ("bars", 3, False), ("bars", 0, True), ("bars", 1, True)])
def test_exact_slippage_approvals_match_worker_support(setup, mode, override, allowed):
    raw = copy.deepcopy(setup.strategy)
    raw["execution"].update(mode=mode, slippageTicksOverride=override)
    assert (not executable_errors(raw)) == allowed


def test_null_slippage_and_source_assertions_are_in_exact_snapshot(setup):
    source = knowledge.ingest_text(title="Selected source", content="Attributed synthetic research passage.", kind="selected_source",
                                   source_url="https://example.test/source", author="User-asserted author", license_name="User-asserted license")
    chunk = knowledge.search("Attributed synthetic")[0]
    exact = knowledge.passage(chunk["id"])
    assert exact["provenance"]["author"] == "User-asserted author"
    assert exact["provenance"]["license"] == "User-asserted license"
    p = memory.propose(setup.thread, "Synthetic proposal", setup.strategy, setup.plan, [], [chunk["id"]])
    assertions = p["document"]["passages"][0]["provenance"]
    assert assertions["author"] == exact["provenance"]["author"] and assertions["license"] == exact["provenance"]["license"]
    assert "fullText" not in assertions
    effective = p["document"]["effectiveExecution"]
    assert effective["requestedSlippageTicksOverride"] is None and effective["slippageSource"] == "engineConfig"
    assert effective["slippageTicks"] == 1  # actual committed configuration, not a new policy
    assert source["revision"] == exact["revision"]


def test_list_diff_rejects_two_independent_filter_changes(setup):
    setup.strategy["filters"] = [{"op": "gt", "args": [{"field": "close"}, 100]},
                                 {"op": "gt", "args": [{"field": "volume"}, 10]}]
    p = proposal(setup); approve(p); q = memory.enqueue(p["id"], setup.thread)
    with database.session_scope() as db:
        run = db.get(AgentRun, q["runId"])
        run.status = "awaiting_approval"
        state = copy.deepcopy(run.state_json)
        state["candidates"][-1]["status"] = "complete"
        run.state_json = state
    raw = copy.deepcopy(setup.strategy)
    raw["filters"][0]["args"][1] = 101
    raw["filters"][1]["args"][1] = 20
    raw["lineage"] = {"parentId": q["strategyId"], "trialIndex": 1, "changedVariable": "filters", "rationale": "Synthetic edits"}
    child = proposal(setup, strategy=raw, parent_id=p["id"]); approve(child)
    with pytest.raises(memory.ResearchConflict, match="exactly one changed variable"):
        memory.enqueue(child["id"], setup.thread)
    raw["filters"][1]["args"][1] = 10
    raw["lineage"]["changedVariable"] = "filters[0].args[1]"
    child = proposal(setup, strategy=raw, parent_id=p["id"]); approve(child)
    assert memory.enqueue(child["id"], setup.thread)["strategyId"] != q["strategyId"]


def test_research_report_reuses_verified_artifacts_without_unchecked_reads(setup, monkeypatch):
    sid = strategy_store.save_strategy(setup.strategy)["id"]
    jid = artifact(setup, [{"pnlUsd": 42, "sessionDate": "2026-04-01"}])
    with database.session_scope() as db:
        row = db.get(Backtest, jid)
        row.strategy_id = sid
        row.metrics_json = {**row.metrics_json, "netPnl": 42, "trades": 1}
    monkeypatch.setattr(jobs, "load_trades", lambda *_: pytest.fail("unverified reread"))
    monkeypatch.setattr(jobs, "load_daily_returns", lambda *_: pytest.fail("unverified reread"))
    monkeypatch.setattr(memory.validation.mc, "run_all", lambda *_: {"bootstrap": {"maxDrawdownPct": {"p95": 0}}})
    report, _ = tools.execute("get_validation", {"strategy_id": sid})
    assert report["inSample"]["netPnl"] == 42 and report["oosHidden"] is True


@pytest.mark.parametrize("mode", ["bars", "ticks"])
def test_null_slippage_rejects_unsupported_effective_config(setup, monkeypatch, tmp_path, mode):
    from research_agent import readiness
    import yaml
    config = yaml.safe_load(readiness.CONFIG_PATH.read_text())
    config["costs"]["slippage_ticks_market"] = 3
    path = tmp_path / "instruments.yaml"
    path.write_text(yaml.safe_dump(config))
    monkeypatch.setattr(readiness, "CONFIG_PATH", path)
    raw = copy.deepcopy(setup.strategy)
    raw["execution"].update(mode=mode, slippageTicksOverride=None)
    assert readiness.effective_execution(raw)["slippageTicks"] == 3
    assert any("bars and ticks support only integer 0 or 1" in error for error in readiness.executable_errors(raw))
