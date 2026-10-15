"""Synthetic proof of workflow correctness, never evidence of a trading edge."""
import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

import database
from engine import expr
from market import paths
from models import Backtest, MLExperiment, ResearchProposal
from research_agent import memory, service as agent
from research_ml import accounts, core, service
from research_ml.schema import AccountPolicy, Plan
from tests.helpers_bars import bar, make_ctx


@pytest.fixture
def plan():
    return Plan(hypothesis="Synthetic threshold software test", method="volume_threshold_v1", symbol="ES1!",
                interval="1min", direction="long", start="2026-07-13", trainEnd="2026-07-13",
                validationEnd="2026-07-14", testEnd="2026-07-15", target="next_open_to_horizon_close_ticks",
                horizonBars=1, roundTripCostTicks=0.5, quantiles=[0.5], maxCandidates=2,
                minTrainSignals=2, minEvaluationSignals=2, minMeanNetTicks=1,
                minImprovementOverBaselineTicks=0, multipleTestingPolicy="descriptive_only_no_significance_claim")


@pytest.fixture
def bars():
    # Future close is tied to previous volume purely to make an oracle fixture.
    frames = []
    for day in (13, 14, 15):
        index = pd.date_range(f"2026-07-{day} 09:30", periods=20, freq="min", tz="America/New_York")
        volume = np.tile([10.0, 100.0], 10)
        close = 5000 + np.where(np.roll(volume, 1) > 50, 2.0, -2.0)
        frames.append(pd.DataFrame({"open": 5000.0, "high": 5002.0, "low": 4998.0,
                                    "close": close, "volume": volume}, index=index))
    return pd.concat(frames)


@pytest.fixture
def context(agent_db, monkeypatch, tmp_path, bars):
    monkeypatch.setattr(paths, "_paths", paths.Paths(tmp_path / "data", tmp_path / "raw"))
    split = {"inSample": ["2026-07-13", "2026-07-14", "2026-07-15"], "outOfSample": ["2026-07-16"]}
    monkeypatch.setattr(service.validation, "_splits", lambda _: copy.deepcopy(split))
    monkeypatch.setattr(service.data_store, "get_bars", lambda *args: bars.copy())
    return agent.create_thread()["id"], split


def approved(context, plan):
    tid, _ = context
    p = service.propose(tid, plan)
    service.decide(tid, p["id"], p["digest"], "approve", "Reviewed synthetic fixture")
    return p


def test_split_and_horizon_purge(bars, plan):
    plan = plan.model_copy(update={"horizonBars": 3})
    parts = core.samples(bars, plan, 0.25)
    assert [len(p) for p in parts.values()] == [17, 17, 17]
    assert parts["train"].labelEnd.max() < parts["validation"].index.min()
    assert parts["validation"].labelEnd.max() < parts["test"].index.min()
    gap = bars.drop(bars.index[8])
    assert len(core.samples(gap, plan, 0.25)["train"]) == 13
    # First label uses next bar OPEN, not the current known close.
    first = parts["train"].iloc[0]
    assert first.netTicks == (bars.iloc[3].close - bars.iloc[1].open) / .25 - .5


def test_future_changes_cannot_change_fit_or_quantiles(bars, plan):
    baseline = core.discover(bars, plan, .25)
    changed = bars.copy()
    changed.loc[changed.index.date > plan.trainEnd, "volume"] *= 10_000
    changed.loc[changed.index.date > plan.trainEnd, "close"] = 4998
    later = core.discover(changed, plan, .25)
    assert later["model"] == baseline["model"]
    assert later["trials"] == baseline["trials"]
    assert later["outcome"] == "no_edge"
    assert "test" not in later["metrics"]
    assert baseline == core.discover(bars, plan, .25)


@pytest.mark.parametrize("direction", ["long", "short"])
def test_export_matches_engine_expression_at_threshold(bars, plan, direction):
    plan = plan.model_copy(update={"direction": direction})
    result = core.discover(bars, plan, .25)
    assert result["outcome"] == "candidate"
    model = core.Threshold(result["model"]["threshold"], result["model"]["op"])
    ctx = make_ctx()
    # Evaluate the production expression compiler, including equality edge.
    node = expr.Evaluator(model.expression(), ctx, direction)
    values = [10, model.threshold, 100]
    for i, volume in enumerate(values):
        ctx.on_bar(bar(i, 5000, v=volume))
        assert node.eval() == bool(model.predict([volume])[0])


def test_no_edge_and_insufficient_training_stop(bars, plan):
    untrainable = plan.model_copy(update={"minTrainSignals": 1000})
    result = core.discover(bars, untrainable, .25)
    assert result["outcome"] == "no_edge" and result["metrics"] == {}
    # Validation success cannot hide a failed test.
    bars.loc[bars.index.date == plan.testEnd, "close"] = 4998
    result = core.discover(bars, plan, .25)
    assert result["outcome"] == "no_edge" and "test" in result["metrics"]


@pytest.mark.parametrize("change", [{"symbol": "NQ1!"}, {"horizonBars": 0}, {"quantiles": [float("nan")]},
                                     {"quantiles": [.5, .5]}, {"maxCandidates": 1}, {"trainEnd": "2026-07-16"},
                                     {"roundTripCostTicks": float("inf")}, {"minTrainSignals": True}])
def test_invalid_choices_fail_closed(plan, change):
    with pytest.raises(ValidationError):
        Plan.model_validate({**plan.model_dump(mode="json"), **change})


def test_missing_policy_no_defaults(plan):
    raw = plan.model_dump(mode="json")
    del raw["roundTripCostTicks"]
    with pytest.raises(ValidationError):
        Plan.model_validate(raw)


@pytest.mark.parametrize("kind", ["duplicate", "unsorted", "nan", "ohlc", "naive"])
def test_invalid_market_inputs(bars, kind):
    if kind == "duplicate":
        bars = pd.concat([bars, bars.tail(1)])
    elif kind == "unsorted":
        bars = bars.iloc[::-1]
    elif kind == "nan":
        bars.iloc[0, 0] = np.nan
    elif kind == "ohlc":
        bars.iloc[0, 1] = 1
    else:
        bars.index = bars.index.tz_localize(None)
    with pytest.raises(ValueError):
        core.validate_bars(bars)


def test_no_fit_without_exact_approval_and_no_cross_thread(context, plan, monkeypatch):
    tid, _ = context
    p = service.propose(tid, plan)
    with pytest.raises(memory.ResearchConflict, match="approval"):
        service.run(tid, p["id"])
    with pytest.raises(memory.ResearchConflict, match="digest"):
        service.decide(tid, p["id"], "0" * 64, "approve", "wrong digest")
    with pytest.raises(memory.ResearchConflict, match="thread"):
        service.decide(agent.create_thread()["id"], p["id"], p["digest"], "approve", "wrong thread")
    service.decide(tid, p["id"], p["digest"], "approve", "reviewed")
    result = service.run(tid, p["id"])
    assert result["status"] == "candidate"
    monkeypatch.setattr(core, "discover", lambda *a: pytest.fail("refitted on retry"))
    assert service.run(tid, p["id"]) == result
    with database.session_scope() as db:
        assert db.query(Backtest).count() == db.query(ResearchProposal).count() == 0


def test_concurrent_run_consumes_approval_once(context, plan, monkeypatch):
    p = approved(context, plan)
    original, calls = core.discover, []
    def spy(*args):
        calls.append(1)
        return original(*args)
    monkeypatch.setattr(core, "discover", spy)
    def run():
        try:
            return service.run(context[0], p["id"])["status"]
        except memory.ResearchConflict:
            return "busy"
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(lambda _: run(), range(2)))
    assert "candidate" in statuses and len(calls) == 1


@pytest.mark.parametrize("drift", ["snapshot", "code", "scope", "document"])
def test_drift_blocks_without_fitting(context, plan, monkeypatch, drift):
    p = approved(context, plan)
    monkeypatch.setattr(core, "discover", lambda *a: pytest.fail("must not fit"))
    if drift == "snapshot":
        service._snapshot_path(p["id"]).write_text("tampered")
    elif drift == "code":
        monkeypatch.setattr(service, "provenance", lambda: {})
    elif drift == "scope":
        context[1]["outOfSample"].append("2026-07-14")
    else:
        with database.session_scope() as db:
            row = db.get(MLExperiment, p["id"])
            row.document_json = {**row.document_json, "scope": "changed"}
        with pytest.raises(memory.ResearchConflict, match="changed"):
            service.run(context[0], p["id"])
        return
    result = service.run(context[0], p["id"])
    assert result["status"] == "blocked" and result["result"]["outcome"] != "no_edge"


def test_snapshot_is_reproducible_after_source_changes(context, plan, monkeypatch):
    p = approved(context, plan)
    monkeypatch.setattr(service.data_store, "get_bars", lambda *a: pytest.fail("must use approved snapshot"))
    assert service.run(context[0], p["id"])["status"] == "candidate"


def test_final_holdout_and_stopping(context, plan):
    tid, split = context
    with pytest.raises(ValueError, match="holdout"):
        service.propose(tid, plan.model_copy(update={"testEnd": date(2026, 7, 16)}))
    p = service.propose(tid, plan)
    service.decide(tid, p["id"], p["digest"], "reject_stop", "stop synthetic test")
    with pytest.raises(memory.ResearchConflict):
        service.run(tid, p["id"])
    with pytest.raises(memory.ResearchConflict):
        service.propose(tid, plan)
    with database.session_scope() as db:
        assert memory.thread_stopped(db, tid)


def test_separate_account_policies_and_missing_evidence():
    policy = AccountPolicy(name="Synthetic personal criteria", startingEquity=1000, minNetPnlUsd=0,
                           maxClosedTradeDrawdownPct=5, maxDailyNetLossUsd=40, minTrades=2)
    trades = [{"pnlUsd": 100, "commissionUsd": 2, "exitTime": 1, "sessionDate": "2026-07-13"},
              {"pnlUsd": -50, "commissionUsd": 2, "exitTime": 2, "sessionDate": "2026-07-14"}]
    assert accounts.assess(trades, None)["status"] == "unassessed"
    personal = accounts.assess(trades, policy)
    assert personal["status"] == "criteria_failed"
    assert personal["observed"]["closedTradeDrawdownPct"] == pytest.approx(50 / 1100 * 100)
    prop = accounts.assess(trades, policy.model_copy(update={"maxDailyNetLossUsd": 60}))
    assert prop["status"] == "criteria_met"
    assert accounts.assess([{}], policy)["status"] == "unassessed"


def test_candidate_handoff_requires_new_approval(context, plan, monkeypatch, tmp_path):
    from engine import spec
    from tests.test_spec_validation import ORB
    p = approved(context, plan)
    service.run(context[0], p["id"])
    # Full explicit execution choices are fixture values, not product defaults.
    strategy = spec.normalize(ORB)
    strategy.update(direction="long", filters=[])
    strategy["timeframes"] = {"primary": "1min", "context": []}
    strategy["entry"]["sequence"] = []
    strategy["execution"]["mode"] = "bars"
    strategy["exit"]["stop"].update(type="ticks", value=20, structure=None)
    strategy["risk"].update(weeklyLossLimitPct=0, maxTradesPerDay=1, stopAfterConsecutiveLosses=1)
    strategy["risk"]["passCriteria"]["minWalkForwardWindowsPositive"] = 0
    dataset = {"windows": {"is": ["2026-07-13", "2026-07-15"]},
               "inSampleSessions": context[1]["inSample"], "splitsSha256": "synthetic", "frontMonthSha256": "synthetic",
               "inventory": {"bars": [{"path": "synthetic"}], "ticks": []},
               "identityPolicy": "metadata_inventory_not_immutable_snapshot"}
    monkeypatch.setattr(memory, "dataset_context", lambda: copy.deepcopy(dataset))
    validation_plan = {"windows": ["is"], "objective": "Synthetic engine validation", "comparison": "descriptive",
                       "uncertaintyPolicy": "descriptive only", "multipleTestingPolicy": "unknown external trials",
                       "dataPolicy": dataset["identityPolicy"]}
    exported = service.candidate_proposal(context[0], p["id"], strategy, validation_plan)
    assert exported["status"] == "proposed", exported["blockers"]
    assert exported["decision"] is None
    assert exported["document"]["strategy"]["entry"]["trigger"]["args"][0] == {"field": "volume"}
    assert exported["document"]["strategy"]["meta"]["mlDiscovery"]["experimentDigest"] == p["digest"]
    assert spec.validate_spec(exported["document"]["strategy"]) == []
    with pytest.raises(memory.ResearchConflict, match="approval"):
        memory.enqueue(exported["id"], context[0])
    with database.session_scope() as db:
        assert db.query(Backtest).count() == 0
    # The same exported candidate can proceed through the existing controller
    # after a second explicit synthetic approval. Dispatch is intercepted.
    import json
    from engine import jobs
    monkeypatch.setattr(jobs, "JOBS_DIR", tmp_path / "jobs")
    monkeypatch.setattr(jobs, "start", lambda _: None)
    memory.decide(exported["id"], context[0], exported["digest"], "approve", "Approve synthetic Nautilus fixture")
    queued = memory.enqueue(exported["id"], context[0])
    job_id = queued["evidence"][0]["jobId"]
    with database.session_scope() as db:
        job = db.get(Backtest, job_id)
        job.status = "done"
    ts = pd.Timestamp("2026-07-13 10:00", tz="America/New_York").timestamp()
    (jobs._job_dir(job_id) / "trades.json").write_text(json.dumps({"trades": [
        {"pnlUsd": 100, "commissionUsd": 2, "exitTime": ts, "sessionDate": "2026-07-13"}]}))
    policy = AccountPolicy(name="Synthetic policy", startingEquity=strategy["risk"]["accountSize"],
                           minNetPnlUsd=0, maxClosedTradeDrawdownPct=5, maxDailyNetLossUsd=40, minTrades=1)
    report = accounts.report(context[0], p["id"], job_id, None, policy)
    assert report["personal"]["status"] == "criteria_met" and report["propFirm"]["status"] == "unassessed"
    assert len(report["artifactSha256"]) == 64
    with pytest.raises(ValueError, match="equity must match"):
        accounts.report(context[0], p["id"], job_id, policy.model_copy(update={"startingEquity": 50}), None)
    # A changed result cannot be used as if it were the reviewed evidence.
    with database.session_scope() as db:
        row = db.get(MLExperiment, p["id"])
        row.result_json = {**row.result_json, "reason": "tampered"}
    with pytest.raises(memory.ResearchConflict, match="artifact changed"):
        service.candidate_proposal(context[0], p["id"], strategy, validation_plan)


def test_routes_require_explicit_plan_and_decision(client, monkeypatch, tmp_path, bars, plan):
    monkeypatch.setattr(paths, "_paths", paths.Paths(tmp_path / "data", tmp_path / "raw"))
    monkeypatch.setattr(service.validation, "_splits", lambda _: {
        "inSample": ["2026-07-13", "2026-07-14", "2026-07-15"], "outOfSample": ["2026-07-16"]})
    monkeypatch.setattr(service.data_store, "get_bars", lambda *a: bars.copy())
    tid = client.post("/api/agent/threads", json={}).json()["id"]
    base = f"/api/agent/threads/{tid}/ml-experiments"
    assert client.post(base, json={}).status_code == 422
    response = client.post(base, json=plan.model_dump(mode="json"))
    assert response.status_code == 200, response.text
    p = response.json()
    assert client.post(f"{base}/{p['id']}/run").status_code == 409
    assert client.post(f"{base}/{p['id']}/decision", json={"digest": p["digest"], "action": "approve", "reason": "reviewed"}).status_code == 200
    response = client.post(f"{base}/{p['id']}/run")
    assert response.status_code == 200 and response.json()["status"] == "candidate", response.text
    history = client.get(base).json()
    assert history["recordedAttempts"] == 1 and len(history["experiments"]) == 1
    assert client.post(f"{base}/{p['id']}/accounts", json={"jobId": "unrelated", "propFirm": None, "personal": None}).status_code == 400


def test_missing_sessions_fail_before_approval(context, plan, bars, monkeypatch):
    monkeypatch.setattr(service.data_store, "get_bars", lambda *a: bars[bars.index.date != plan.validationEnd])
    with pytest.raises(ValueError, match="missing frozen sessions"):
        service.propose(context[0], plan)
    assert service.history(context[0])["experiments"] == []


def test_agent_cannot_approve_or_run_ml():
    from research_agent import tools
    # New human endpoints must never accidentally enter the model's tool list.
    schemas = str(tools.TOOL_DEFINITIONS)
    assert "ml-experiments" not in schemas and "volume_threshold_v1" not in schemas
