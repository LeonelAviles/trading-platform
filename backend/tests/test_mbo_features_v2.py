"""Causal observation/software tests; never economic validation."""
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from engine import mbo_features_v2
from engine.feature_schema import V2, RICH_FEATURES, features_for
from engine.mbo_features import FEATURES, NS
from engine.session import et_to_ns
from engine.ml_signal import FrozenModel, parse_frozen
from research_ml import core, mbo_source_v2, feature_store, frozen, multivariate
from research_ml.schema import MBOFeatureRequest
from tests.test_mbo_research import DAYS, events, plan, extract, raw_context, export  # noqa: F401
from tests.test_mbo_feature_store import legacy_partition


def rich_extract(rows=None, day=DAYS[0], minutes=30, **changes):
    p = plan(featureVersion=V2, **changes)
    return mbo_features_v2.build_minutes(iter(events(day) if rows is None else rows),
        start_ns=et_to_ns(day, "09:30"), end_ns=et_to_ns(day, "09:30")+minutes*60*NS,
        contract=p.contract, source=p.sources[DAYS.index(day)], depth=p.depthLevels,
        max_age_ms=p.maxBookAgeMs, max_lag_ms=p.maxEventLagMs,
        max_unknown_fraction=p.maxUnknownTradeFraction, tick_size=.25)


def test_vwap_flow_units_prefix_and_session_reset():
    rich, audit = rich_extract()
    old, _ = extract(events())
    pd.testing.assert_frame_equal(rich[core.COLUMNS+FEATURES], old)
    assert list(rich.columns) == core.COLUMNS + RICH_FEATURES
    assert rich.iloc[0].session_vwap == 5001
    assert rich.iloc[0].distance_to_session_vwap_ticks == 4
    assert rich.iloc[0].session_vwap_slope_ticks == 0
    assert rich.iloc[1].session_vwap == 5000
    assert rich.iloc[1].session_vwap_slope_ticks == -4
    assert rich.iloc[0].signed_aggressive_flow_rate == pytest.approx(-2/60)
    assert rich.iloc[0].aggressive_flow_imbalance == -1
    assert rich.iloc[0].sell_absorption_proxy == 2
    assert audit["replenishmentPolicy"].endswith("proxy")
    tomorrow, _ = rich_extract(day=DAYS[1])
    assert tomorrow.iloc[0].session_vwap == rich.iloc[0].session_vwap
    assert tomorrow.iloc[0].session_vwap_slope_ticks == 0


def test_future_and_cutoff_cannot_change_earlier_features():
    base, _ = rich_extract(minutes=1)
    rows = events()
    future = et_to_ns(DAYS[0], "09:31")
    for r in rows:
        if r["ts_recv"] >= future and r["action"] == "T":
            r["price"] += 100*NS
    changed, _ = rich_extract(rows)
    pd.testing.assert_frame_equal(base, changed.iloc[:1])
    bad = {**rows[-1], "ts_recv": future, "ts_event": future, "flags": 192}
    # The first record at the exclusive cutoff is ignored before flag checking.
    at_cutoff = [r for r in events() if r["ts_recv"] < future] + [bad]
    pd.testing.assert_frame_equal(base, rich_extract(at_cutoff, minutes=1)[0])


def replenishment_rows():
    rows = events(minutes=1)[:3]
    start = et_to_ns(DAYS[0], "09:30")
    template = events(minutes=1)[5]
    for n, (offset, action, side, size, oid) in enumerate([
        (1, "T", "B", 10, 0), (1.2, "A", "A", 4, 101),
        (1.4, "A", "A", 6, 102), (2, "F", "B", 10, 0),
        (3, "T", "B", 2, 0), (4.1, "A", "A", 10, 103)]):
        t = start+int(offset*NS)
        rows.append({**template, "ts_recv": t, "ts_event": t, "action": action,
                     "side": side, "price": 5001*NS, "size": size, "order_id": oid, "sequence": n+4})
    return rows


def test_replenishment_matching_caps_expiry_and_fill_not_double_counted():
    frame, _ = rich_extract(replenishment_rows(), minutes=1)
    r = frame.iloc[0]
    assert r.volume == 12  # F never contributes a second execution.
    assert r.ask_replenishment_rate_proxy == pytest.approx(10/60)
    assert r.bid_replenishment_rate_proxy == 0
    assert r.buy_absorption_proxy == 12
    assert r.buy_absorption_replenishment_proxy == 10
    assert r.iceberg_repeat_replenishment_proxy == .5  # second matched add: 6/12
    assert r.aggressive_flow_imbalance == 1


def test_same_complete_event_does_not_claim_replenishment():
    rows = replenishment_rows()[:5]
    rows[3]["flags"] = 0
    frame, _ = rich_extract(rows, minutes=1)
    assert frame.iloc[0].ask_replenishment_rate_proxy == 0
    assert frame.iloc[0].iceberg_repeat_replenishment_proxy == 0


def test_unknown_only_and_missing_minutes_have_explicit_behavior():
    rows = events(minutes=1)
    for r in rows:
        if r["action"] == "T": r["side"] = "N"
    frame, _ = rich_extract(rows, minutes=2, maxUnknownTradeFraction=1.0)
    assert len(frame) == 1  # no-trade second minute omitted, not zero-filled
    assert frame.iloc[0].session_vwap == 5001
    assert (frame[["aggressive_flow_imbalance", "signed_aggressive_flow_rate",
                  "iceberg_repeat_replenishment_proxy"]] == 0).all().all()
    assert np.isfinite(frame.to_numpy()).all()


def test_v2_model_dimensions_order_and_train_only_scaling():
    frame = pd.concat([rich_extract(day=d)[0] for d in DAYS])
    chosen = plan(featureVersion=V2)
    parts = core.samples(frame, chosen, .25)
    model, trials = multivariate.MBORidge().fit(parts["train"][RICH_FEATURES+["netTicks"]], chosen)
    assert model is not None and len(model.features) == 18
    assert model.version == "frozen_mbo_v2" and model.featureVersion == V2
    assert trials[0]["jointFeatureCount"] == 18
    x = parts["train"][RICH_FEATURES]
    assert model.mean == x.mean().tolist()
    assert parse_frozen(model.model_dump_json()) == model
    expected = model.predict(x)
    assert expected.tolist() == [bool(model.predict(row)[0]) for row in x.to_numpy()[:,None,:]]
    with pytest.raises(ValueError, match="order"):
        model.predict(x[list(reversed(RICH_FEATURES))])
    with pytest.raises(ValueError):
        FrozenModel.model_validate({**model.model_dump(), "features": FEATURES})
    with pytest.raises(ValueError, match="ordered"):
        core.validate_bars(frame.drop(columns=RICH_FEATURES[-1]))
    changed = frame.copy()
    changed.loc[changed.index.tz_convert("America/New_York").date > chosen.trainEnd, RICH_FEATURES] *= 2
    other, _ = multivariate.MBORidge().fit(core.samples(changed, chosen, .25)["train"][RICH_FEATURES+["netTicks"]], chosen)
    assert other == model
    _, rows, _ = frozen.nautilus_day(frame, "ESU6", DAYS[0])
    assert all(len(values) == 18 for values in rows.values())


@pytest.mark.usefixtures("feature_database")
def test_postgres_v1_v2_coexist_without_invented_backfill(raw_context):
    folder, old_frame, meta = legacy_partition(raw_context)
    feature_store.import_partition(folder)
    chosen = plan(featureVersion=V2, dataSource="prepared_MBO_features")
    req = MBOFeatureRequest.model_validate(chosen.model_dump(include=set(MBOFeatureRequest.model_fields)))
    prepared = feature_store.prepare(req, .25)
    assert not any(p["reused"] for p in prepared["partitions"])
    actual, source = feature_store.load(chosen, [str(d) for d in DAYS], .25)
    raw, _ = mbo_source_v2.load(chosen, [str(d) for d in DAYS], .25)
    pd.testing.assert_frame_equal(actual, core.validate_bars(raw))
    assert source["featureVersion"] == V2
    old, old_meta = feature_store._read(meta["document"]["identity"])
    pd.testing.assert_frame_equal(old, core.validate_bars(old_frame))
    assert old_meta == meta and list(old.columns) == core.COLUMNS+FEATURES
    assert len(feature_store.inventory()["partitions"]) == 4
    with feature_store.connect() as conn:
        conn.execute("UPDATE prepared_mbo_v2_rows SET session_vwap=session_vwap+1")
    with pytest.raises(ValueError, match="checksum"):
        feature_store.load(chosen, [str(d) for d in DAYS], .25)


def test_staged_v2_cluster_fit_and_summary(raw_context):
    from research_ml import discovery
    from tests.test_cluster_discovery import discovery_plan
    tid, _ = raw_context
    proposed = discovery.propose(tid, discovery_plan(dataSource="raw_DBN_MBO", featureVersion=V2))
    discovery.decide(tid, proposed["id"], proposed["digest"], "approve", "Synthetic versioned software test")
    row = discovery.fit(tid, proposed["id"])
    assert row["status"] == "discovered", row
    assert row["result"]["summary"]["features"] == RICH_FEATURES
    assert row["result"]["clusterModel"]["featureVersion"] == V2
    assert row["result"]["clusterModel"]["version"] == "frozen_mbo_v2"
    assert row["result"]["diagnostics"]["jointFeatureCount"] == 18


def test_omitted_minute_still_contributes_session_vwap():
    rows = events(minutes=2)
    rows[4]["price"] = 4998*NS  # crossed first minute, repaired next minute
    frame, audit = rich_extract(rows, minutes=2)
    assert audit["omittedMinutes"] == 1 and len(frame) == 1
    assert frame.iloc[0].session_vwap == 5000  # includes first minute's trades
    assert frame.iloc[0].session_vwap_slope_ticks == -4


def test_replenishment_cannot_exceed_prior_aggressive_volume():
    rows = replenishment_rows()[:6]
    rows[4]["size"] = rows[5]["size"] = 100
    frame, _ = rich_extract(rows, minutes=1)
    assert frame.iloc[0].ask_replenishment_rate_proxy == pytest.approx(10/60)
    assert frame.iloc[0].iceberg_repeat_replenishment_proxy == 0


def test_replenishment_price_memory_guard(raw_context, monkeypatch):
    monkeypatch.setattr(mbo_features_v2, "MAX_MATCH_PRICES", 1)
    with pytest.raises(ValueError, match="price budget"):
        rich_extract(events(minutes=1), minutes=1)


def test_v1_default_identity_and_frozen_model_do_not_gain_v2_inputs():
    old = plan()
    req = MBOFeatureRequest.model_validate(old.model_dump(include=set(MBOFeatureRequest.model_fields)))
    identity = feature_store._identity(req, req.sources[0], .25)
    assert "featureVersion" not in identity["settings"]
    assert "engine/mbo_features_v2.py" not in identity["extractor"]["code"]
    model = FrozenModel(kind="ridge", features=FEATURES, mean=[0]*6, scale=[1]*6, coefficients=[0]*6)
    assert model.featureVersion == "mbo_receive_minute_v1" and model.version == "frozen_mbo_v1"
    assert model.predict([[0]*6]).tolist() == [False]
    with pytest.raises(ValueError): model.predict([[0]*18])


@pytest.mark.usefixtures("feature_database")
def test_inventory_has_explicit_metadata_memory_bound(raw_context, monkeypatch):
    chosen = plan(featureVersion=V2)
    req = MBOFeatureRequest.model_validate(chosen.model_dump(include=set(MBOFeatureRequest.model_fields)))
    feature_store.prepare(req, .25)
    monkeypatch.setattr(feature_store, "MAX_INVENTORY_BYTES", 1)
    with pytest.raises(ValueError, match="memory bound"):
        feature_store.inventory()


@pytest.mark.usefixtures("feature_database")
@pytest.mark.parametrize("floor", ["nan", "inf", "0"])
def test_nonfinite_or_zero_disk_floor_rejected(raw_context, monkeypatch, floor):
    chosen = plan(featureVersion=V2)
    req = MBOFeatureRequest.model_validate(chosen.model_dump(include=set(MBOFeatureRequest.model_fields)))
    monkeypatch.setenv("FEATURE_MIN_FREE_GIB", floor)
    with pytest.raises(ValueError, match="free-disk"):
        feature_store.prepare(req, .25)


@pytest.mark.usefixtures("feature_database")
def test_postgres_v2_duplicate_and_partial_batches_fail_closed(raw_context):
    import psycopg
    chosen = plan(featureVersion=V2)
    req = MBOFeatureRequest.model_validate(chosen.model_dump(include=set(MBOFeatureRequest.model_fields)))
    first = req.model_copy(update={"sources": req.sources[:1]})
    feature_store.prepare(first, .25)
    with pytest.raises(psycopg.errors.UniqueViolation):
        with feature_store.connect() as conn:
            conn.execute("INSERT INTO prepared_mbo_v2_rows SELECT * FROM prepared_mbo_v2_rows LIMIT 1")
    with feature_store.connect() as conn:
        conn.execute("DELETE FROM prepared_mbo_v2_rows WHERE timestamp_ns=(SELECT min(timestamp_ns) FROM prepared_mbo_v2_rows)")
    with pytest.raises(ValueError, match="row count|checksum"):
        feature_store.prepare(first, .25)


def test_trade_credit_expires_even_when_same_price_episode_continues():
    rows = replenishment_rows()
    old_trade, fresh_trade, addition = (rows[i].copy() for i in (3, 3, 4))
    start = et_to_ns(DAYS[0], "09:30")
    fresh_trade.update(ts_recv=start+1_900_000_000, ts_event=start+1_900_000_000, size=1, sequence=5)
    old_trade.update(size=100, sequence=4)
    addition.update(ts_recv=start+2_500_000_000, ts_event=start+2_500_000_000, size=101, sequence=6)
    frame, _ = rich_extract(rows[:3]+[old_trade, fresh_trade, addition], minutes=1)
    assert frame.iloc[0].volume == 101
    assert frame.iloc[0].ask_replenishment_rate_proxy == pytest.approx(1/60)


@pytest.mark.parametrize("offset,matched", [(2*NS, 10), (2*NS+1, 0)])
def test_credit_window_includes_exactly_one_second(offset, matched):
    rows = replenishment_rows()[:5]
    start = et_to_ns(DAYS[0], "09:30")
    rows[-1].update(ts_recv=start+offset, ts_event=start+offset, size=20)
    frame, _ = rich_extract(rows, minutes=1)
    assert frame.iloc[0].ask_replenishment_rate_proxy == pytest.approx(matched/60)


def test_one_add_consuming_several_credit_lots_counts_once():
    rows = replenishment_rows()
    old_trade, fresh_trade, addition = (rows[i].copy() for i in (3, 3, 4))
    start = et_to_ns(DAYS[0], "09:30")
    fresh_trade.update(ts_recv=start+1_100_000_000, ts_event=start+1_100_000_000, size=5, sequence=5)
    addition.update(ts_recv=start+1_200_000_000, ts_event=start+1_200_000_000, size=15, sequence=6)
    frame, _ = rich_extract(rows[:3]+[old_trade, fresh_trade, addition], minutes=1)
    assert frame.iloc[0].ask_replenishment_rate_proxy == pytest.approx(15/60)
    assert frame.iloc[0].iceberg_repeat_replenishment_proxy == 0


def test_credit_lots_have_independent_memory_bound(monkeypatch):
    monkeypatch.setattr(mbo_features_v2, "MAX_MATCH_LOTS", 1)
    with pytest.raises(ValueError, match="credit-lot budget"):
        rows = replenishment_rows()
        second = {**rows[3], "ts_recv": rows[3]["ts_recv"]+100_000_000,
                  "ts_event": rows[3]["ts_event"]+100_000_000, "sequence": 5}
        rich_extract(rows[:4]+[second], minutes=1)


def test_pr4_provenance_blocks_replay_but_v1_snapshot_and_model_remain_readable(monkeypatch, tmp_path):
    from contextlib import contextmanager
    import hashlib
    import database
    from research_agent import memory
    from research_ml import service
    # Actual source-code provenance fingerprint of PR4 (9f6e727), computed
    # with that commit's service.provenance() file selection and digest policy.
    pr4_code = "75a7b8e0afc456fb6ad0b47b865dff00e145c3161ab60886014195ec817660d1"
    old_frame = extract(events())[0]
    content = service.snapshot_bytes(old_frame)
    path = tmp_path / "legacy-approved-snapshot.json"
    path.write_bytes(content)
    dataset = {"sha256": hashlib.sha256(content).hexdigest()}
    pd.testing.assert_frame_equal(service.read_snapshot(path, dataset), core.validate_bars(old_frame))
    old_model = FrozenModel(kind="ridge", features=FEATURES, mean=[0]*6,
                            scale=[1]*6, coefficients=[0]*6)
    assert parse_frozen(old_model.model_dump_json()).features == FEATURES
    assert old_model.predict(old_frame[FEATURES]).tolist() == [False]*len(old_frame)
    document = {"plan": plan().model_dump(mode="json", exclude={"featureVersion"}),
                "provenance": {**service.provenance(), "codeSha256": pr4_code}, "dataset": dataset}
    digest = memory.digest(document)
    result = {"experimentDigest": digest}
    result_digest = memory.digest(result)
    row = SimpleNamespace(status="candidate", decision_json={"action": "approve"},
                          document_json=document, digest=digest,
                          result_json={**result, "resultDigest": result_digest})
    @contextmanager
    def old_records():
        yield SimpleNamespace(get=lambda *args: row)
    monkeypatch.setattr(database, "session_scope", old_records)
    strategy = {"meta": {"mlDiscovery": {"experimentId": "legacy-v1",
                "experimentDigest": digest, "resultDigest": result_digest}}}
    assert pr4_code != service.provenance()["codeSha256"]
    with pytest.raises(ValueError, match="code/config/sessions changed"):
        frozen.load(strategy)
