"""Reusable source-verified rows, not substituted OHLCV or fitted outcomes."""
import json
from types import SimpleNamespace

import databento_dbn as dbn
import pandas as pd
import pytest
import zstandard

from engine.session import et_to_ns
from market import paths
from research_ml import core, feature_store, mbo_source, service
from research_ml.schema import MBOFeatureRequest
from tests.test_mbo_research import DAYS, events, plan, raw_context, write_dbn  # noqa: F401


def request(p=None):
    p = p or plan()
    return MBOFeatureRequest.model_validate({k: getattr(p, k) for k in MBOFeatureRequest.model_fields})


@pytest.mark.parametrize("kind", ["ridge", "kmeans"])
def test_reuse_without_raw_preserves_features_model_and_approval(raw_context, monkeypatch, kind):
    tid, p = raw_context
    raw, _ = mbo_source.load(plan(kind), [str(d) for d in DAYS], .25)
    prepared = feature_store.prepare(request(), .25)["partitions"]
    assert len(prepared) == 3 and not any(x["reused"] for x in prepared)
    for f in p.raw_dir.rglob("*.zst"):
        f.unlink()
    monkeypatch.setattr(mbo_source, "extract_day", lambda *a: pytest.fail("must reuse saved features"))
    again = feature_store.prepare(request(), .25)["partitions"]
    assert all(x["reused"] for x in again)
    cached_plan = plan(kind, dataSource="prepared_MBO_features")
    cached, provenance = feature_store.load(cached_plan, [str(d) for d in DAYS], .25)
    pd.testing.assert_frame_equal(core.validate_bars(raw), cached)
    assert core.discover(raw, plan(kind), .25) == core.discover(cached, cached_plan, .25)
    assert provenance["sources"] == [x["document"]["rawSource"] for x in prepared]
    proposal = service.propose(tid, cached_plan)
    assert proposal["status"] == "proposed"
    assert len(proposal["document"]["dataset"]["featurePartitions"]) == 3
    from research_agent.memory import ResearchConflict
    with pytest.raises(ResearchConflict, match="approval"):
        service.run(tid, proposal["id"])
    service.decide(tid, proposal["id"], proposal["digest"], "approve", "Synthetic cached dataset check")
    # The experiment snapshot survives deletion of the reusable store as well.
    import shutil
    shutil.rmtree(p.mbo_features_dir)
    result = service.run(tid, proposal["id"])
    assert result["status"] == "candidate", result


@pytest.mark.parametrize("change", [
    {"depthLevels": 1}, {"maxBookAgeMs": 60000}, {"maxEventLagMs": 60000},
    {"maxUnknownTradeFraction": .5}, {"contract": "ESZ6"},
])
def test_extraction_settings_never_silently_reuse_other_partitions(raw_context, change):
    feature_store.prepare(request(), .25)
    with pytest.raises(ValueError, match="prepare these settings first"):
        feature_store.load(plan(dataSource="prepared_MBO_features", **change), [str(d) for d in DAYS], .25)


def test_tick_source_and_extractor_versions_are_part_of_identity(raw_context, monkeypatch):
    feature_store.prepare(request(), .25)
    for p, tick in [(plan(), .5), (plan(sources=[dict(s.model_dump(), instrumentId=20) for s in plan().sources]), .25)]:
        with pytest.raises(ValueError, match="prepare these settings first"):
            feature_store.load(p, [str(d) for d in DAYS], tick)
    monkeypatch.setattr(feature_store, "FEATURE_VERSION", "new_feature_semantics")
    with pytest.raises(ValueError, match="prepare these settings first"):
        feature_store.load(plan(), [str(d) for d in DAYS], .25)


@pytest.mark.parametrize("file", ["rows.parquet", "metadata.json"])
def test_corrupt_partition_blocks_proposal_without_raw_fallback(raw_context, monkeypatch, file):
    tid, _ = raw_context
    feature_store.prepare(request(), .25)
    folder = feature_store._path(feature_store._identity(request(), request().sources[0], .25))
    (folder / file).write_bytes(b"corrupt")
    monkeypatch.setattr(mbo_source, "extract_day", lambda *a: pytest.fail("no raw fallback"))
    with pytest.raises(ValueError, match="checksum|metadata"):
        service.propose(tid, plan(dataSource="prepared_MBO_features"))
    assert service.history(tid)["experiments"] == []


def test_raw_drift_never_overwrites_saved_partition(raw_context):
    _, p = raw_context
    first = feature_store.prepare(request(), .25)["partitions"]
    (p.raw_dir / "ES" / f"{DAYS[0]}.mbo.dbn.zst").write_bytes(b"changed source")
    with pytest.raises(ValueError, match="raw source differs"):
        feature_store.prepare(request(), .25)
    # Explicit prepared reads retain the original source identity, independent
    # of later changes to raw files. Each proposal pins those checked rows.
    _, source = feature_store.load(plan(), [str(d) for d in DAYS], .25)
    assert source["featurePartitions"][0]["digest"] == first[0]["digest"]


def test_failed_preparation_publishes_no_partial_partition(raw_context, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(pd.DataFrame, "to_parquet", fail)
    with pytest.raises(OSError, match="disk full"):
        feature_store.prepare(request(), .25)
    assert feature_store.inventory()["partitions"] == []
    assert not list(paths.get_paths().mbo_features_dir.rglob(".preparing-*"))


def test_day_subset_reuses_same_partition_and_inventory_filters(raw_context):
    feature_store.prepare(request(), .25)
    subset = request().model_copy(update={"sources": [request().sources[1]]})
    assert feature_store.prepare(subset, .25)["partitions"][0]["reused"]
    inventory = feature_store.inventory("ESU6", DAYS[1], DAYS[1])
    assert len(inventory["partitions"]) == 1
    assert inventory["partitions"][0]["document"]["identity"]["source"]["day"] == str(DAYS[1])
    assert not feature_store.inventory("ESZ6")["partitions"]


def test_inventory_ignores_uncommitted_staging_metadata(raw_context):
    feature_store.prepare(request(), .25)
    folder = feature_store._path(feature_store._identity(request(), request().sources[0], .25))
    staging = folder.parent / ".preparing-interrupted"
    staging.mkdir()
    (staging / "metadata.json").write_text("incomplete")
    assert len(feature_store.inventory()["partitions"]) == 3


def test_concurrent_preparation_publishes_one_verified_partition(raw_context):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    from unittest.mock import patch
    barrier = threading.Barrier(2)
    extract = mbo_source.extract_day
    def synchronized(*args):
        result = extract(*args)
        barrier.wait(timeout=10)
        return result
    req = request().model_copy(update={"sources": [request().sources[0]]})
    with patch.object(mbo_source, "extract_day", synchronized), ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: feature_store.prepare(req, .25), range(2)))
    a, b = [r["partitions"][0] for r in results]
    assert a["digest"] == b["digest"] and a["reused"] != b["reused"]
    assert len(feature_store.inventory()["partitions"]) == 1


def test_contracts_in_same_raw_file_stay_separate(raw_context):
    _, p = raw_context
    day = DAYS[0]
    rows = events(day)
    other = [{**r, "symbol": "ESZ6", "instrument_id": 20,
              "price": r["price"] + (100_000_000_000 if r["action"] != "R" else 0)} for r in rows]
    mappings = [SimpleNamespace(raw_symbol=symbol, intervals=[SimpleNamespace(
        start_date=day, end_date=day + pd.Timedelta(days=1), symbol=str(iid))])
        for symbol, iid in [("ESU6", 10), ("ESZ6", 20)]]
    metadata = dbn.Metadata("GLBX.MDP3", et_to_ns(day, "00:00"), dbn.SType.RAW_SYMBOL,
        dbn.SType.INSTRUMENT_ID, dbn.Schema.MBO, symbols=["ESU6", "ESZ6"], mappings=mappings)
    encoded = [dbn.MBOMsg(r["publisher_id"], r["instrument_id"], r["ts_event"], r["order_id"],
        r["price"], r["size"], dbn.Action(r["action"]), dbn.Side(r["side"]), r["ts_recv"],
        flags=r["flags"], channel_id=r["channel_id"], sequence=r["sequence"])
        for r in sorted(rows + other, key=lambda r: r["ts_recv"])]
    raw = p.raw_dir / "ES" / f"{day}.mbo.dbn.zst"
    raw.write_bytes(zstandard.compress(metadata.encode() + b"".join(bytes(r) for r in encoded)))
    first = request().model_copy(update={"sources": [request().sources[0]]})
    second = first.model_copy(update={"contract": "ESZ6",
        "sources": [first.sources[0].model_copy(update={"instrumentId": 20})]})
    for req in (first, second):
        feature_store.prepare(req, .25)
    a, _ = feature_store._read(feature_store._identity(first, first.sources[0], .25))
    b, _ = feature_store._read(feature_store._identity(second, second.sources[0], .25))
    assert (b.close - a.close == 100).all()
    assert len(feature_store.inventory()["partitions"]) == 2


def test_feature_api_and_prepared_experiment_api(client, monkeypatch, tmp_path):
    p = paths.Paths(tmp_path / "data", tmp_path / "market")
    monkeypatch.setattr(paths, "_paths", p)
    monkeypatch.setattr(service.validation, "_splits", lambda _: {
        "inSample": [str(d) for d in DAYS], "outOfSample": []})
    for day in DAYS:
        write_dbn(p.raw_dir / "ES" / f"{day}.mbo.dbn.zst", day, events(day))
    response = client.post("/api/ml/features/prepare", json=request().model_dump(mode="json"))
    assert response.status_code == 200, response.text
    assert len(response.json()["partitions"]) == 3
    assert len(client.get(f"/api/ml/features?contract=ESU6&start={DAYS[1]}").json()["partitions"]) == 2
    assert client.get("/api/ml/features?contract=../").status_code == 422
    tid = client.post("/api/agent/threads", json={"title": "Saved feature API test"}).json()["id"]
    for f in p.raw_dir.rglob("*.zst"):
        f.unlink()
    response = client.post(f"/api/agent/threads/{tid}/ml-experiments",
        json=plan("kmeans", dataSource="prepared_MBO_features").model_dump(mode="json"))
    assert response.status_code == 200, response.text
    assert response.json()["document"]["dataset"]["source"] == "prepared_MBO_features"


def test_cli_reuses_partitions_without_s3(raw_context, tmp_path, monkeypatch, capsys):
    _, p = raw_context
    feature_store.prepare(request(), .25)
    for f in p.raw_dir.rglob("*.zst"):
        f.unlink()
    from scripts import prepare_mbo_features, restore
    monkeypatch.setattr(restore, "main", lambda *a: pytest.fail("cached days need no S3"))
    file = tmp_path / "request.json"
    file.write_text(json.dumps([request().model_dump(mode="json")]))
    assert prepare_mbo_features.main(["--request", str(file), "--restore"]) == 0
    assert capsys.readouterr().out.count('"reused": true') == 3
