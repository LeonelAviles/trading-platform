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


pytestmark = pytest.mark.usefixtures("feature_database")


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
    with feature_store.connect() as conn:
        conn.execute("TRUNCATE prepared_mbo_rows, prepared_mbo_v2_rows, prepared_mbo_batches")
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
    with pytest.raises(ValueError, match="prepare these settings first"):
        feature_store.load(plan(featureVersion="mbo_receive_minute_v2"), [str(d) for d in DAYS], .25)


def test_corrupt_partition_blocks_proposal_without_raw_fallback(raw_context, monkeypatch):
    tid, _ = raw_context
    feature_store.prepare(request(), .25)
    with feature_store.connect() as conn:
        conn.execute("UPDATE prepared_mbo_rows SET close=close+1")
    monkeypatch.setattr(mbo_source, "extract_day", lambda *a: pytest.fail("no raw fallback"))
    with pytest.raises(ValueError, match="checksum"):
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


def legacy_partition(raw_context):
    """Write a synthetic PR4 fixture, never copy production prepared data."""
    import hashlib
    req = request().model_copy(update={"sources": [request().sources[0]]})
    source = req.sources[0]
    identity = feature_store._identity(req, source, .25)
    frame, raw = mbo_source.extract_day(req, source, .25)
    folder = feature_store.legacy._path(identity)
    folder.mkdir(parents=True)
    rows = frame.reset_index(drop=True)
    rows.insert(0, "timestamp_ns", frame.index.as_unit("ns").asi8)
    rows.to_parquet(folder / "rows.parquet", index=False)
    document = {"identity": identity, "rawSource": raw, "rows": len(frame),
                "parquetSha256": hashlib.sha256((folder / "rows.parquet").read_bytes()).hexdigest()}
    meta = {"partitionId": feature_store._digest(identity), "document": document,
            "digest": feature_store._digest(document)}
    (folder / "metadata.json").write_text(json.dumps(meta))
    return folder, frame, meta


def test_import_preserves_original_identity_bytes_and_order(raw_context):
    folder, frame, meta = legacy_partition(raw_context)
    before = {f.name: f.read_bytes() for f in folder.iterdir()}
    first = feature_store.import_partition(folder)
    again = feature_store.import_partition(folder)
    assert not first["reused"] and again["reused"]
    assert {k: v for k, v in first.items() if k != "reused"} == meta
    actual, actual_meta = feature_store._read(meta["document"]["identity"])
    pd.testing.assert_frame_equal(core.validate_bars(frame), actual)
    assert actual_meta == meta
    assert before == {f.name: f.read_bytes() for f in folder.iterdir()}
    assert feature_store.exists(meta["document"]["identity"])


def test_partial_and_duplicate_rows_rejected(raw_context):
    import psycopg
    folder, _, meta = legacy_partition(raw_context)
    feature_store.import_partition(folder)
    with pytest.raises(psycopg.errors.UniqueViolation):
        with feature_store.connect() as conn:
            conn.execute("INSERT INTO prepared_mbo_rows SELECT * FROM prepared_mbo_rows LIMIT 1")
    with feature_store.connect() as conn:
        conn.execute("DELETE FROM prepared_mbo_rows WHERE timestamp_ns=(SELECT min(timestamp_ns) FROM prepared_mbo_rows)")
    with pytest.raises(ValueError, match="row count|checksum"):
        feature_store._read(meta["document"]["identity"])
    with pytest.raises(ValueError, match="row count|checksum"):
        feature_store.import_partition(folder)


def test_import_failure_rolls_back_entire_day(raw_context, monkeypatch):
    folder, _, meta = legacy_partition(raw_context)
    read = feature_store._read_conn
    monkeypatch.setattr(feature_store, "_read_conn", lambda *a: (_ for _ in ()).throw(ValueError("parity failure")))
    with pytest.raises(ValueError, match="parity failure"):
        feature_store.import_partition(folder)
    assert not feature_store.exists(meta["document"]["identity"])
    with feature_store.connect() as conn:
        assert conn.execute("SELECT count(*) FROM prepared_mbo_rows").fetchone()[0] == 0
    monkeypatch.setattr(feature_store, "_read_conn", read)
    assert not feature_store.import_partition(folder)["reused"]


def test_import_rejects_corrupt_original(raw_context):
    folder, _, meta = legacy_partition(raw_context)
    (folder / "rows.parquet").write_bytes(b"broken")
    with pytest.raises(ValueError, match="checksum"):
        feature_store.import_partition(folder)
    assert not feature_store.exists(meta["document"]["identity"])


def test_capacity_guard_rolls_back_and_can_resume(raw_context, monkeypatch):
    folder, _, meta = legacy_partition(raw_context)
    monkeypatch.setenv("FEATURE_STORE_MAX_BYTES", "1")
    with pytest.raises(ValueError, match="capacity guard"):
        feature_store.import_partition(folder)
    assert not feature_store.exists(meta["document"]["identity"])
    monkeypatch.setenv("FEATURE_STORE_MAX_BYTES", str(2**30))
    assert not feature_store.import_partition(folder)["reused"]


def test_import_cli_dry_run_then_idempotent_resume(raw_context, capsys):
    from scripts.import_mbo_features import main
    folder, _, meta = legacy_partition(raw_context)
    assert main(["--min-free-gib", "1"]) == 0
    assert not feature_store.exists(meta["document"]["identity"])
    assert main(["--apply", "--min-free-gib", "1"]) == 0
    assert main(["--apply", "--min-free-gib", "1"]) == 0
    output = capsys.readouterr().out
    assert '"validated"' in output and '"committed"' in output and '"reused"' in output


def test_local_free_disk_guard(raw_context, monkeypatch):
    from scripts import import_mbo_features
    legacy_partition(raw_context)
    monkeypatch.setattr(import_mbo_features.shutil, "disk_usage", lambda _: SimpleNamespace(free=0))
    with pytest.raises(ValueError, match="free-disk"):
        import_mbo_features.main(["--apply"])
    assert not feature_store.inventory()["partitions"]


def test_incomplete_legacy_batch_fails_closed(raw_context):
    from scripts.import_mbo_features import main
    folder, _, _ = legacy_partition(raw_context)
    (folder / "metadata.json").unlink()
    with pytest.raises(ValueError, match="metadata"):
        main(["--apply"])
    assert not feature_store.inventory()["partitions"]


def test_legacy_frozen_snapshot_independent_of_database(raw_context, monkeypatch, tmp_path):
    import hashlib
    folder, frame, meta = legacy_partition(raw_context)
    content = service.snapshot_bytes(frame)
    frozen = tmp_path / "approved.json"
    frozen.write_bytes(content)
    # Historical dataset documents contain Parquet identities; snapshot format
    # and checksum remain byte compatible without reading either feature store.
    dataset = {"sha256": hashlib.sha256(content).hexdigest(),
               "featurePartitions": [{"id": meta["partitionId"], "digest": meta["digest"],
                                      "parquetSha256": meta["document"]["parquetSha256"]}]}
    monkeypatch.delenv("FEATURE_DATABASE_URL")
    actual = service.read_snapshot(frozen, dataset)
    pd.testing.assert_frame_equal(core.validate_bars(frame), actual)


def test_bounded_day_rejects_oversized_input(raw_context):
    folder, frame, meta = legacy_partition(raw_context)
    oversized = pd.concat([frame.iloc[:1]] * 391)
    with pytest.raises(ValueError):
        feature_store._publish(oversized, meta)
    assert not feature_store.exists(meta["document"]["identity"])


def test_reads_are_ordered_independent_of_physical_insertion(raw_context):
    folder, frame, meta = legacy_partition(raw_context)
    feature_store.import_partition(folder)
    with feature_store.connect() as conn:
        rows = conn.execute("SELECT * FROM prepared_mbo_rows ORDER BY timestamp_ns DESC").fetchall()
        conn.execute("DELETE FROM prepared_mbo_rows")
        with conn.cursor() as cur:
            cur.executemany("INSERT INTO prepared_mbo_rows VALUES (" + ",".join(["%s"] * 13) + ")", rows)
    actual, _ = feature_store._read(meta["document"]["identity"])
    pd.testing.assert_frame_equal(core.validate_bars(frame), actual)


def test_import_rejects_oversized_footer_before_allocating_rows(raw_context, monkeypatch):
    import pyarrow.parquet as pq
    folder, _, _ = legacy_partition(raw_context)
    original = pq.ParquetFile
    def bounded_reader(*args, **kwargs):
        reader = original(*args, **kwargs)
        return SimpleNamespace(metadata=SimpleNamespace(num_rows=10**9),
                               schema_arrow=reader.schema_arrow,
                               read=lambda: pytest.fail("oversized data must not be decoded"))
    monkeypatch.setattr(pq, "ParquetFile", bounded_reader)
    with pytest.raises(ValueError, match="row bound"):
        feature_store.import_partition(folder)
    assert not feature_store.inventory()["partitions"]


@pytest.mark.parametrize("kind", ["rowgroup", "column", "footer"])
def test_import_rejects_declared_decompression_bound_before_read(raw_context, monkeypatch, kind):
    import pyarrow.parquet as pq
    folder, _, _ = legacy_partition(raw_context)
    original = pq.ParquetFile
    def bounded_reader(*args, **kwargs):
        reader = original(*args, **kwargs)
        meta = reader.metadata
        column_size = feature_store.legacy.MAX_UNCOMPRESSED_BYTES+1 if kind == "column" else 1
        group_size = feature_store.legacy.MAX_UNCOMPRESSED_BYTES+1 if kind == "rowgroup" else 1
        footer_size = feature_store.legacy.MAX_METADATA_BYTES+1 if kind == "footer" else meta.serialized_size
        fake_meta = SimpleNamespace(num_rows=meta.num_rows, num_columns=meta.num_columns,
            num_row_groups=1, serialized_size=footer_size,
            row_group=lambda _: SimpleNamespace(total_byte_size=group_size,
                column=lambda _: SimpleNamespace(total_uncompressed_size=column_size)))
        return SimpleNamespace(metadata=fake_meta, schema_arrow=reader.schema_arrow,
                               read=lambda: pytest.fail("oversized pages must not be decoded"))
    monkeypatch.setattr(pq, "ParquetFile", bounded_reader)
    with pytest.raises(ValueError, match="uncompressed size|footer bound"):
        feature_store.import_partition(folder)
    assert not feature_store.inventory()["partitions"]
