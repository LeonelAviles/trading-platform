"""Staged discovery: no labels to fit/reviewer; no test before exact approval."""
import copy
import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import threading

import pytest

import database
from models import MLDiscovery, MLHeldoutUse
from research_agent import memory, service as agent
from research_ml import core, discovery, service
from research_ml.schema import ClusterDiscoveryPlan, ClusterHypothesisRequest, ClusterReview
from tests.test_mbo_research import export, plan, raw_context  # noqa: F401


def discovery_plan(**changes):
    old = plan().model_dump(mode="json")
    values = {k: old[k] for k in ClusterDiscoveryPlan.model_fields if k in old}
    return ClusterDiscoveryPlan.model_validate({**values, "method": "mbo_cluster_discovery_v1",
        "researchQuestion": "Describe recurring order flow without looking at outcomes", "clusterCount": 2,
        "maxIterations": 30, "minClusterSamples": 2, **changes})


def suggestion():
    return {"hypothesis": "Synthetic cluster hypothesis", "activeClusters": [1], "direction": "long",
        "horizonBars": 1, "interpretation": "Feature group, not an observed edge",
        "rationale": "Synthetic software check", "falsification": "Fails later cost-adjusted criteria"}


class Reviewer:
    def __init__(self, payload=None):
        self.calls = []
        self.responses = self
        self.payload = payload or {"assessment": "Plausible enough to test; no returns observed",
            "clusters": [{"clusterId": i, "label": f"Group {i}", "description": "Synthetic feature group",
                          "concerns": ["No economic evidence yet"]} for i in range(2)],
            "recommendation": "propose", "suggestion": suggestion()}

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(id="synthetic-response", output_parsed=self.payload)


def fitted(tid):
    proposed = discovery.propose(tid, discovery_plan())
    discovery.decide(tid, proposed["id"], proposed["digest"], "approve", "Reviewed synthetic data-only fit")
    result = discovery.fit(tid, proposed["id"])
    assert result["status"] == "discovered", result
    return result


def reviewed(tid):
    fitted_ = fitted(tid)
    result = discovery.review(tid, fitted_["id"], fitted_["digest"], client=Reviewer())
    assert result["status"] == "reviewed", result
    return result


def hypothesis_request(row, **changes):
    return ClusterHypothesisRequest.model_validate({**suggestion(), "discoveryDigest": row["digest"],
        "reviewDigest": row["review"]["digest"], "reviewDisposition": "accept", "reviewReason": "Reviewed suggestion",
        "target": "next_open_to_horizon_close_ticks", "roundTripCostTicks": 0.0, "minEvaluationSignals": 2,
        "minMeanNetTicks": 0.0, "minImprovementOverBaselineTicks": 0.0,
        "multipleTestingPolicy": "descriptive_only_no_significance_claim", **changes})


def test_complete_stages_require_two_approvals_and_never_refit(raw_context, monkeypatch):
    tid, _ = raw_context
    row = discovery.propose(tid, discovery_plan())
    assert "direction" not in row["document"]["plan"] and "activeClusters" not in row["document"]["plan"]
    with pytest.raises(memory.ResearchConflict, match="approval"):
        discovery.fit(tid, row["id"])
    discovery.decide(tid, row["id"], row["digest"], "approve", "Fit approval")
    row = discovery.fit(tid, row["id"])
    assert row["result"]["summary"]["rows"] == 30
    assert "metrics" not in row["result"] and row["result"]["outcomesUsed"] is False
    client = Reviewer()
    row = discovery.review(tid, row["id"], row["digest"], client=client)
    call = client.calls[0]
    assert "tools" not in call and "previous_response_id" not in call and call["store"] is False
    payload = json.loads(call["input"][0]["content"])
    assert set(payload) == {"researchQuestion", "contract", "discoveryStart", "discoveryEnd", "summary", "scope"}
    assert "netTicks" not in json.dumps(payload) and "validation" not in json.dumps(payload)
    assert call["text_format"] is ClusterReview
    assert service.history(tid)["experiments"] == []  # AI cannot create/approve/run a test.
    experiment = discovery.propose_hypothesis(tid, row["id"], hypothesis_request(row))
    with pytest.raises(memory.ResearchConflict, match="approval"):
        service.run(tid, experiment["id"])
    with database.session_scope() as db:
        assert db.query(MLHeldoutUse).count() == 0
    service.decide(tid, experiment["id"], experiment["digest"], "approve", "Approve frozen hypothesis")
    monkeypatch.setattr(discovery, "fit_clusters", lambda *a: pytest.fail("evaluation must not refit"))
    monkeypatch.setattr(core.METHODS["mbo_kmeans_v1"], "fit", lambda *a: pytest.fail("no legacy refit"))
    result = service.run(tid, experiment["id"])
    assert result["status"] == "candidate", result
    assert result["result"]["modelRefitted"] is False
    assert set(result["result"]["metrics"]) == {"validation", "test"}
    with database.session_scope() as db:
        assert db.query(MLHeldoutUse).count() == 2
    assert service.run(tid, experiment["id"]) == result
    proposed = export(tid, experiment["id"])
    assert not proposed["blockers"], proposed
    from engine import backtest_worker
    from tests.test_mbo_research import DAYS
    executed = backtest_worker.run_backtest(proposed["document"]["strategy"], DAYS[2], DAYS[2], "bars")
    assert executed["trades"] and executed["meta"]["contracts"] == ["ESU6"]


def test_fitting_and_ai_context_are_invariant_to_later_data(raw_context):
    tid, _ = raw_context
    first = fitted(tid)
    next_tid = agent.create_thread()["id"]
    second = discovery.propose(next_tid, discovery_plan())
    # Change only future rows in this independent frozen proposal, then pin
    # its new digest before approving. Training features remain identical.
    path = discovery._path(second["id"])
    payload = json.loads(path.read_text())
    for i, timestamp in enumerate(payload["timestamps"]):
        if timestamp > max(json.loads(discovery._path(first["id"]).read_text())["timestamps"][:30]):
            payload["values"][i] = [v * 2 for v in payload["values"][i]]
    content = json.dumps(payload, separators=(",", ":")).encode()
    path.write_bytes(content)
    import hashlib
    with database.session_scope() as db:
        row = db.get(MLDiscovery, second["id"])
        document = copy.deepcopy(row.document_json)
        document["dataset"]["sha256"] = hashlib.sha256(content).hexdigest()
        row.document_json, row.digest = document, memory.digest(document)
        second["digest"] = row.digest
    discovery.decide(next_tid, second["id"], second["digest"], "approve", "Synthetic future perturbation")
    second = discovery.fit(next_tid, second["id"])
    assert second["status"] == "discovered", second
    assert first["result"]["clusterModel"] == second["result"]["clusterModel"]
    assert first["result"]["summary"] == second["result"]["summary"]
    a, b = Reviewer(), Reviewer()
    discovery.review(tid, first["id"], first["digest"], client=a)
    discovery.review(next_tid, second["id"], second["digest"], client=b)
    assert a.calls[0]["input"] == b.calls[0]["input"]


def test_ai_stop_is_advice_user_revision_is_explicit(raw_context):
    tid, _ = raw_context
    row = fitted(tid)
    reviewer = Reviewer()
    reviewer.payload.update(recommendation="stop", suggestion=None)
    row = discovery.review(tid, row["id"], row["digest"], client=reviewer)
    assert row["status"] == "reviewed"
    with pytest.raises(ValueError, match="use revise"):
        discovery.propose_hypothesis(tid, row["id"], hypothesis_request(row))
    experiment = discovery.propose_hypothesis(tid, row["id"], hypothesis_request(row,
        reviewDisposition="revise", reviewReason="Explicit user override for a synthetic check"))
    assert experiment["status"] == "proposed" and experiment["decision"] is None


def test_unknown_clusters_and_invalid_ai_response_cannot_create_hypothesis(raw_context):
    tid, _ = raw_context
    row = fitted(tid)
    reviewer = Reviewer()
    reviewer.payload["suggestion"]["activeClusters"] = [7]
    row = discovery.review(tid, row["id"], row["digest"], client=reviewer)
    assert row["status"] == "review_failed" and "digest" not in row["review"]
    assert service.history(tid)["experiments"] == []
    with pytest.raises(memory.ResearchConflict):
        discovery.review(tid, row["id"], row["digest"], client=Reviewer())


def test_review_configuration_failure_does_not_consume_attempt(raw_context, monkeypatch):
    tid, _ = raw_context
    row = fitted(tid)
    def missing():
        raise agent.AgentConfigurationError("test missing key")
    monkeypatch.setattr(agent, "_client", missing)
    with pytest.raises(agent.AgentConfigurationError):
        discovery.review(tid, row["id"], row["digest"])
    assert discovery.history(tid)["discoveries"][0]["status"] == "discovered"
    row = discovery.review(tid, row["id"], row["digest"], client=Reviewer())
    # A recorded result remains readable without making another API request.
    assert discovery.review(tid, row["id"], row["digest"]) == row


def test_later_sessions_cannot_be_reused_in_new_threads(raw_context):
    tid, _ = raw_context
    other_tid = agent.create_thread()["id"]
    experiments = []
    for thread in (tid, other_tid):
        row = reviewed(thread)
        experiment = discovery.propose_hypothesis(thread, row["id"], hypothesis_request(row))
        service.decide(thread, experiment["id"], experiment["digest"], "approve", "Synthetic evaluation")
        experiments.append(experiment)
    assert service.run(tid, experiments[0]["id"])["status"] == "candidate"
    with pytest.raises(memory.ResearchConflict, match="already reserved"):
        service.run(other_tid, experiments[1]["id"])
    with pytest.raises(memory.ResearchConflict, match="already reserved"):
        discovery.propose(agent.create_thread()["id"], discovery_plan())


def test_stop_and_digest_tampering_block_progress(raw_context):
    tid, _ = raw_context
    row = reviewed(tid)
    with pytest.raises(memory.ResearchConflict, match="digest"):
        discovery.propose_hypothesis(tid, row["id"], hypothesis_request(row, reviewDigest="0" * 64))
    discovery.stop(tid, row["id"], row["digest"], "User chose to stop")
    with pytest.raises(memory.ResearchConflict):
        discovery.propose_hypothesis(tid, row["id"], hypothesis_request(row))
    with database.session_scope() as db:
        assert memory.thread_stopped(db, tid)
        db.get(MLDiscovery, row["id"]).document_json = {"changed": True}
    with pytest.raises(memory.ResearchConflict, match="document changed"):
        discovery.fit(tid, row["id"])


def test_concurrent_fit_reserves_once(raw_context, monkeypatch):
    tid, _ = raw_context
    row = discovery.propose(tid, discovery_plan())
    discovery.decide(tid, row["id"], row["digest"], "approve", "Concurrent software check")
    entered, release = threading.Event(), threading.Event()
    original = discovery.fit_clusters
    def controlled(*args):
        entered.set()
        assert release.wait(10)
        return original(*args)
    monkeypatch.setattr(discovery, "fit_clusters", controlled)
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(discovery.fit, tid, row["id"])
        assert entered.wait(10)
        try:
            with pytest.raises(memory.ResearchConflict, match="cannot rerun"):
                discovery.fit(tid, row["id"])
        finally:
            release.set()
        assert first.result()["status"] == "discovered"


def test_concurrent_review_reserves_one_api_call(raw_context):
    tid, _ = raw_context
    row = fitted(tid)
    entered, release = threading.Event(), threading.Event()
    reviewer = Reviewer()
    original = reviewer.parse
    def controlled(**kwargs):
        entered.set()
        assert release.wait(10)
        return original(**kwargs)
    reviewer.parse = controlled
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(discovery.review, tid, row["id"], row["digest"], reviewer)
        assert entered.wait(10)
        try:
            with pytest.raises(memory.ResearchConflict):
                discovery.review(tid, row["id"], row["digest"], client=reviewer)
        finally:
            release.set()
        assert first.result()["status"] == "reviewed"
    assert len(reviewer.calls) == 1


def test_validation_failure_never_scores_internal_test(raw_context, monkeypatch):
    tid, _ = raw_context
    row = reviewed(tid)
    experiment = discovery.propose_hypothesis(tid, row["id"], hypothesis_request(row, minMeanNetTicks=10000.0))
    service.decide(tid, experiment["id"], experiment["digest"], "approve", "Explicit no-edge fixture")
    calls = []
    original = core.evaluate
    def record(model, data):
        calls.append(str(data.index[0].tz_convert("America/New_York").date()))
        return original(model, data)
    monkeypatch.setattr(core, "evaluate", record)
    result = service.run(tid, experiment["id"])
    assert result["status"] == "no_edge" and "test" not in result["result"]["metrics"]
    assert calls == ["2026-07-14"]


def test_stop_during_review_retains_evidence_but_never_advances(raw_context):
    tid, _ = raw_context
    row = fitted(tid)
    reviewer = Reviewer()
    original = reviewer.parse
    def cancel(**kwargs):
        discovery.stop(tid, row["id"], row["digest"], "User stopped the pending review")
        return original(**kwargs)
    reviewer.parse = cancel
    result = discovery.review(tid, row["id"], row["digest"], client=reviewer)
    assert result["status"] == "stopped" and result["review"]["digest"]
    with pytest.raises(memory.ResearchConflict):
        discovery.propose_hypothesis(tid, row["id"], hypothesis_request(result))


def test_http_discovery_review_hypothesis_and_evaluation(client, raw_context, monkeypatch):
    tid, _ = raw_context
    base = f"/api/agent/threads/{tid}/ml-discoveries"
    response = client.post(base, json=discovery_plan().model_dump(mode="json"))
    assert response.status_code == 200, response.text
    row = response.json()
    assert client.post(f"{base}/{row['id']}/fit").status_code == 409
    assert client.post(f"{base}/{row['id']}/decision", json={"digest": row["digest"], "action": "approve", "reason": "HTTP test"}).status_code == 200
    assert client.post(f"{base}/{row['id']}/fit").json()["status"] == "discovered"
    reviewer = Reviewer()
    reviewer.with_options = lambda **kw: reviewer
    monkeypatch.setattr(agent, "_client", lambda: reviewer)
    row = client.post(f"{base}/{row['id']}/review", json={"digest": row["digest"]}).json()
    response = client.post(f"{base}/{row['id']}/hypothesis", json=hypothesis_request(row).model_dump(mode="json"))
    assert response.status_code == 200, response.text
    experiment = response.json()
    run_base = f"/api/agent/threads/{tid}/ml-experiments/{experiment['id']}"
    assert client.post(run_base + "/run").status_code == 409
    client.post(run_base + "/decision", json={"digest": experiment["digest"], "action": "approve", "reason": "HTTP hypothesis test"})
    assert client.post(run_base + "/run").json()["status"] == "candidate"
