"""Synthetic event/DBN proofs. No licensed data or trading edge is claimed."""
import copy
from datetime import date, timedelta
from types import SimpleNamespace

import databento_dbn as dbn
import numpy as np
import pandas as pd
import pytest
import zstandard

from engine.mbo_features import FEATURES, NS, build_minutes
from engine.ml_signal import FrozenModel
from engine.session import et_to_ns
from engine import expr, spec
from market import paths
from research_agent import memory, service as agent
from research_ml import core, service, frozen, mbo_source
from research_ml.schema import MBORidgePlan, MBOClusterPlan
from tests.helpers_bars import make_ctx, bar
from tests.test_spec_validation import ORB

DAYS = [date(2026, 7, d) for d in (13, 14, 15)]


def plan(kind="ridge", **changes):
    common = dict(hypothesis="Synthetic joint MBO input software test", symbol="ES1!", interval="1min",
        direction="long", start=DAYS[0], trainEnd=DAYS[0], validationEnd=DAYS[1], testEnd=DAYS[2],
        target="next_open_to_horizon_close_ticks", horizonBars=1, roundTripCostTicks=0.0,
        minEvaluationSignals=2, minMeanNetTicks=0.0, minImprovementOverBaselineTicks=0.0,
        multipleTestingPolicy="descriptive_only_no_significance_claim", contract="ESU6",
        contractSelection="fixed_contract_before_experiment", featureWindowSeconds=60, depthLevels=2,
        maxBookAgeMs=120000, maxEventLagMs=120000, maxUnknownTradeFraction=0.0,
        sources=[dict(day=d, instrumentId=10, publisherId=1, channelId=0) for d in DAYS])
    if kind == "ridge":
        return MBORidgePlan(**{**common, "method": "mbo_ridge_v1", "ridgeAlpha": 1.0,
            "predictionThresholdTicks": 0.0, "minTrainSignals": 2, **changes})
    return MBOClusterPlan(**{**common, "method": "mbo_kmeans_v1", "clusterCount": 2,
        "maxIterations": 30, "minClusterSamples": 2, "activeClusters": [1],
        "interpretation": "Synthetic lexicographic regime; no economic interpretation", **changes})


def events(day=DAYS[0], minutes=30):
    start = et_to_ns(day, "09:30")
    seq = 0
    def event(t, a, s="N", p=5000, z=1, oid=0, flags=128):
        nonlocal seq
        seq += 1
        return dict(ts_recv=t, ts_event=t, publisher_id=1, instrument_id=10, channel_id=0,
            symbol="ESU6", action=a, side=s, price=int(p*NS), size=z, order_id=oid, sequence=seq, flags=flags)
    rows = [event(start-NS, "R", flags=40), event(start-NS, "A", "B", 4999, 10, 1, 40),
            event(start-NS, "A", "A", 5001, 10, 2, 168)]
    for i in range(minutes):
        t = start+i*60*NS+NS
        high = i % 2
        rows += [event(t, "M", "B", 4999, 10+90*high, 1),
                 event(t, "M", "A", 5000.25+high*.75, 100-90*high, 2)]
        # Outcome of following bar tracks the previous feature vector.
        close = 4998 if high else 5002
        rows += [event(t+NS, "T", "B" if high else "A", 5000, 1+9*high),
                 event(t+2*NS, "A", "B", 4998.75, 4, 100+i),
                 event(t+3*NS, "C", "B", 4998.75, 2, 100+i),
                 event(t+4*NS, "F", "B", 4998.75, 2, 100+i),
                 event(t+5*NS, "C", "B", 4998.75, 2, 100+i),
                 event(t+6*NS, "T", "B" if high else "A", close, 1)]
    return rows


def extract(rows, p=None, day=DAYS[0], minutes=30):
    p = p or plan()
    return build_minutes(iter(rows), start_ns=et_to_ns(day, "09:30"),
        end_ns=et_to_ns(day, "09:30")+minutes*60*NS, contract=p.contract, source=p.sources[DAYS.index(day)],
        depth=p.depthLevels, max_age_ms=p.maxBookAgeMs, max_lag_ms=p.maxEventLagMs,
        max_unknown_fraction=p.maxUnknownTradeFraction, tick_size=.25)


def frame():
    return pd.concat([extract(events(d), day=d)[0] for d in DAYS])


def write_dbn(path, day, rows):
    mapping = SimpleNamespace(raw_symbol="ESU6", intervals=[SimpleNamespace(
        start_date=day, end_date=day+timedelta(days=1), symbol="10")])
    meta = dbn.Metadata("GLBX.MDP3", et_to_ns(day, "00:00"), dbn.SType.RAW_SYMBOL,
        dbn.SType.INSTRUMENT_ID, dbn.Schema.MBO, symbols=["ESU6"], mappings=[mapping])
    records = [dbn.MBOMsg(r["publisher_id"], r["instrument_id"], r["ts_event"], r["order_id"],
        r["price"], r["size"], dbn.Action(r["action"]), dbn.Side(r["side"]), r["ts_recv"],
        flags=r["flags"], channel_id=r["channel_id"], sequence=r["sequence"]) for r in rows]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(zstandard.compress(meta.encode()+b"".join(bytes(r) for r in records)))


def test_event_feature_values_snapshot_activity_and_fill_semantics():
    f, audit = extract(events(), minutes=30)
    assert list(f.columns) == core.COLUMNS + FEATURES
    assert f.iloc[0].spread_ticks == 5
    assert f.iloc[0].depth_imbalance == pytest.approx(-90/110)
    assert f.iloc[0].aggressive_buy_rate == 0
    assert f.iloc[0].aggressive_sell_rate == pytest.approx(2/60)
    assert f.iloc[1].aggressive_buy_rate == pytest.approx(11/60)
    assert (f.add_rate == 1/60).all()  # two initial snapshot adds excluded
    assert (f.cancel_reduction_rate == 4/60).all()  # F did not remove size twice
    assert audit["snapshots"] == 1 and audit["unknownTradeVolume"] == 0


def test_raw_dbn_decoder_preserves_source_identifiers(tmp_path):
    path = tmp_path / "test.dbn.zst"
    write_dbn(path, DAYS[0], events())
    decoded = list(mbo_source.raw_records(path))
    assert decoded[0]["publisher_id"] == 1 and decoded[0]["instrument_id"] == 10
    assert decoded[0]["channel_id"] == 0 and decoded[0]["symbol"] == "ESU6"
    pd.testing.assert_frame_equal(extract(decoded)[0], extract(events())[0])


@pytest.mark.parametrize("change, message", [
    ({"flags": 132}, "flags"), ({"flags": 136}, "flags"), ({"flags": 192}, "flags"),
    ({"flags": 144}, "flags"), ({"sequence": 0}, "sequence"),
    ({"symbol": "ESZ6"}, "symbol"), ({"action": "R"}, "reset"),
    ({"action": "C", "order_id": 9999}, "unknown"), ({"size": -1}, "price/size"),
    ({"ts_event": 0}, "clock"), ({"side": "N"}, "resting side"),
])
def test_bad_events_fail_closed(change, message):
    rows = events()
    rows[4].update(change)
    with pytest.raises(ValueError, match=message):
        extract(rows)


def test_unknown_trades_never_become_sells_and_quality_is_explicit():
    rows = events()
    rows[5]["side"] = "N"
    with pytest.raises(ValueError, match="unknown aggressor"):
        extract(rows)
    f, audit = extract(rows, plan(maxUnknownTradeFraction=1.0))
    assert f.iloc[0].aggressive_sell_rate == 1/60 and f.iloc[0].aggressive_buy_rate == 0
    assert f.iloc[0].volume == 2 and audit["unknownTradeVolume"] == 1


def test_receive_time_late_and_future_exclusion():
    rows = events()
    original, _ = extract(rows)
    # A trade timestamped in minute zero arrives in minute one. Its price and
    # flow only affect minute one, never backfilled into the earlier feature.
    late = {**rows[5], "ts_recv": rows[13]["ts_recv"], "ts_event": rows[5]["ts_event"],
            "sequence": rows[13]["sequence"], "size": 99, "side": "B"}
    changed = rows[:13] + [late] + rows[13:]
    received, _ = extract(changed)
    pd.testing.assert_series_equal(original.iloc[0], received.iloc[0])
    assert received.iloc[1].aggressive_buy_rate == original.iloc[1].aggressive_buy_rate + 99/60
    # Future malformed records are never consumed past a requested cutoff.
    future = {**rows[-1], "ts_recv": et_to_ns(DAYS[0], "10:00"), "flags": 132}
    pd.testing.assert_frame_equal(extract(rows+[future])[0], original)


def test_completion_boundary_and_missing_or_stale_book():
    rows = events()
    # Defer a whole event containing an early trade until its closing record.
    trade = {**rows[5], "ts_recv": et_to_ns(DAYS[0], "09:30")+59*NS, "flags": 0,
             "sequence": 50}
    complete = {**trade, "action": "N", "ts_recv": et_to_ns(DAYS[0], "09:31"), "flags": 128}
    minimal = rows[:3] + [trade, complete]
    result, _ = extract(minimal, plan(maxBookAgeMs=300000), minutes=2)
    assert result.index[0].tz_convert("America/New_York").strftime("%H:%M") == "09:31"
    assert len(result) == 1
    with pytest.raises(ValueError, match="incomplete"):
        extract(rows + [{**rows[-1], "flags": 0}])
    with pytest.raises(ValueError, match="snapshot"):
        extract(rows[3:])
    stale, audit = extract(rows, plan(maxBookAgeMs=1))
    assert stale.empty and audit["omittedMinutes"] == 30


@pytest.mark.parametrize("kind", ["ridge", "kmeans"])
def test_joint_fits_train_only_and_expression_parity(kind):
    p, bars = plan(kind), frame()
    first = core.discover(bars, p, .25)
    assert first["outcome"] == "candidate", first
    model = FrozenModel.model_validate(first["model"]["frozen"])
    assert len(model.mean) == len(FEATURES) and first["trials"][0]["jointFeatureCount"] == 6
    if kind == "ridge":
        assert np.count_nonzero(np.asarray(model.coefficients)) >= 4
    else:
        assert len(model.centers[0]) == 6
    future = bars.index.date > p.trainEnd
    changed = bars.copy()
    changed.loc[future, FEATURES] *= 100
    other = core.discover(changed, p, .25)
    assert first["model"] == other["model"] and first["trials"] == other["trials"]
    assert first == core.discover(bars, p, .25)
    ctx = make_ctx()
    node = expr.Evaluator(model.expression(), ctx, "long")
    decisions = []
    for i, (_, r) in enumerate(bars.iterrows()):
        b = bar(i, 5000)
        ctx.on_bar(b)
        ctx.ml_features = (ctx.now_ns, r[FEATURES].tolist())
        node.on_bar()
        decisions.append(node.eval())
    assert decisions == model.predict(bars[FEATURES]).tolist()
    ctx.ml_features = (ctx.now_ns-1, bars.iloc[0][FEATURES].tolist())
    with pytest.raises(ValueError, match="unavailable"):
        node.eval()
    # Multiple non-volume inputs can affect inference in the frozen model.
    base = np.asarray(model.mean)[None, :]
    responsive = 0
    for j in range(4):
        lo, hi = base.copy(), base.copy()
        lo[0, j] -= model.scale[j]*100
        hi[0, j] += model.scale[j]*100
        responsive += int(model.predict(lo)[0] != model.predict(hi)[0])
    assert responsive >= 2


def test_multivariate_fit_rejects_targets_in_unsupervised_matrix_and_missing_inputs():
    from research_ml.multivariate import MBOKMeans
    f = frame()
    train = core.samples(f, plan("kmeans"), .25)["train"]
    with pytest.raises(ValueError, match="exactly"):
        MBOKMeans().fit(train, plan("kmeans"))
    with pytest.raises(ValueError, match="all ordered"):
        core.discover(f.drop(columns=FEATURES[0]), plan(), .25)
    with pytest.raises(ValueError, match="OHLCV cannot"):
        core.discover(f[core.COLUMNS], plan(), .25)


@pytest.fixture
def raw_context(agent_db, monkeypatch, tmp_path):
    p = paths.Paths(tmp_path/"data", tmp_path/"market")
    monkeypatch.setattr(paths, "_paths", p)
    monkeypatch.setattr(service.validation, "_splits", lambda _: {
        "inSample": [str(d) for d in DAYS], "outOfSample": ["2026-07-16"]})
    for d in DAYS:
        write_dbn(p.raw_dir / "ES" / f"{d}.mbo.dbn.zst", d, events(d))
    monkeypatch.setattr(service.data_store, "get_bars", lambda *a: pytest.fail("MBO must not read continuous OHLCV"))
    return agent.create_thread()["id"], p


def export(tid, eid):
    strategy = spec.normalize(ORB)
    strategy.update(direction="long", filters=[])
    strategy["timeframes"] = {"primary": "1min", "context": []}
    strategy["entry"]["sequence"] = []
    strategy["execution"]["mode"] = "bars"
    strategy["exit"]["stop"].update(type="ticks", value=20, structure=None)
    strategy["risk"].update(weeklyLossLimitPct=0, maxTradesPerDay=5, stopAfterConsecutiveLosses=5)
    strategy["constraints"].update(maxTradesPerDay=5, stopAfterConsecutiveLosses=5)
    strategy["risk"]["passCriteria"]["minWalkForwardWindowsPositive"] = 1
    strategy["session"].update(entryWindow={"start": "09:32", "end": "09:55"}, flattenAt="09:58")
    test_plan = dict(windows=["is", "wf1", "wf2"], objective="Synthetic execution parity",
        comparison="descriptive", uncertaintyPolicy="no inference", multipleTestingPolicy="unknown external trials",
        dataPolicy="immutable_MBO_receive_time_snapshot")
    return service.candidate_proposal(tid, eid, strategy, test_plan)


@pytest.mark.parametrize("kind", ["ridge", "kmeans"])
@pytest.mark.parametrize("data_source", ["raw_DBN_MBO", "prepared_MBO_features"])
def test_raw_to_approval_to_frozen_nautilus(raw_context, kind, monkeypatch, data_source, request):
    if data_source == "prepared_MBO_features":
        request.getfixturevalue("feature_database")
    tid, paths_ = raw_context
    chosen = plan(kind, dataSource=data_source)
    if data_source == "prepared_MBO_features":
        from research_ml import feature_store
        from research_ml.schema import MBOFeatureRequest
        request = MBOFeatureRequest.model_validate({k: getattr(chosen, k) for k in MBOFeatureRequest.model_fields})
        feature_store.prepare(request, .25)
        for path in paths_.raw_dir.rglob("*.zst"):
            path.unlink()
    p = service.propose(tid, chosen)
    assert p["document"]["dataset"]["source"] == data_source
    assert len(p["document"]["dataset"]["sources"]) == 3
    with pytest.raises(memory.ResearchConflict, match="approval"):
        service.run(tid, p["id"])
    service.decide(tid, p["id"], p["digest"], "approve", "Synthetic exact plan")
    result = service.run(tid, p["id"])
    assert result["status"] == "candidate", result["result"]
    proposed = export(tid, p["id"])
    assert not proposed["blockers"], proposed["blockers"]
    with pytest.raises(memory.ResearchConflict, match="approval"):
        memory.enqueue(proposed["id"], tid)
    strategy = proposed["document"]["strategy"]
    assert frozen.context(strategy)["frontMonthSha256"] is None
    assert frozen.context(strategy)["windows"]["wf2"] == [str(DAYS[2]), str(DAYS[2])]
    from engine import jobs
    import database
    from models import Backtest
    monkeypatch.setattr(jobs, "JOBS_DIR", paths_.data_dir / "jobs")
    started = []
    monkeypatch.setattr(jobs, "start", started.append)
    memory.decide(proposed["id"], tid, proposed["digest"], "approve", "Synthetic execution approval")
    queued = memory.enqueue(proposed["id"], tid)
    assert len(started) == 3
    with database.session_scope() as db:
        for evidence in queued["evidence"]:
            job = db.get(Backtest, evidence["jobId"])
            import json
            saved = json.loads((jobs._job_dir(job.id) / "strategy.json").read_text())
            memory.verify_job_inputs(job.id, saved)
    # Direct synthetic engine tests below exercise the approved spec without
    # starting subprocesses. Source files are not re-read after capture.
    for path in paths_.raw_dir.rglob("*.zst"):
        path.unlink()
    from engine import backtest_worker, instruments
    monkeypatch.setattr(instruments, "resolve_ranges", lambda *a: pytest.fail("no retrospective contract map"))
    from engine.primitives.ml import FrozenMBOSignal
    seen = []
    original = FrozenMBOSignal.value
    def capture(self, ctx):
        value = original(self, ctx)
        seen.append((ctx.now_ns, value))
        return value
    monkeypatch.setattr(FrozenMBOSignal, "value", capture)
    one = backtest_worker.run_backtest(strategy, DAYS[2], DAYS[2], "bars")
    first_seen = seen.copy()
    two = backtest_worker.run_backtest(strategy, DAYS[2], DAYS[2], "bars")
    assert one["trades"] and one["summary"] == two["summary"]
    economic = lambda r: [{k: v for k, v in t.items() if k != "id"} for t in r["trades"]]
    assert economic(one) == economic(two)
    assert one["meta"]["contracts"] == ["ESU6"]
    _, _, f = frozen.load(strategy)
    f = f[f.index.date == DAYS[2]]
    model = FrozenModel.model_validate(result["result"]["model"]["frozen"])
    expected = dict(zip((f.index.as_unit("ns").asi8+60*NS).tolist(), model.predict(f[FEATURES]).tolist()))
    assert first_seen and all(expected[t] == v for t, v in first_seen)
    for change in ("dates", "trigger", "snapshot"):
        altered = copy.deepcopy(strategy)
        if change == "trigger":
            altered["entry"]["trigger"]["args"][0]["params"]["model"] = "{}"
        elif change == "snapshot":
            service._snapshot_path(p["id"]).write_text("tamper")
        message = {"dates": "dates exceed", "trigger": "inference contract", "snapshot": "snapshot changed"}[change]
        with pytest.raises(ValueError, match=message):
            frozen.replay(altered, DAYS[0], DAYS[-1]+timedelta(days=1)) if change == "dates" else frozen.load(altered)


@pytest.mark.parametrize("kind", ["ridge", "kmeans"])
def test_mbo_horizon_gap_purge_and_no_edge(kind):
    p = plan(kind, horizonBars=3)
    f = frame()
    parts = core.samples(f, p, .25)
    assert [len(x) for x in parts.values()] == [27, 27, 27]
    assert parts["train"].labelEnd.max() < parts["validation"].index.min()
    gap = f.drop(f.index[8])
    assert len(core.samples(gap, p, .25)["train"]) == 23
    stopped = core.discover(f, plan(kind, minMeanNetTicks=10000.0), .25)
    assert stopped["outcome"] == "no_edge" and "test" not in stopped["metrics"]


@pytest.mark.parametrize("missing", ["sources", "depthLevels", "maxBookAgeMs", "maxEventLagMs",
                                      "maxUnknownTradeFraction", "horizonBars", "contractSelection"])
def test_mbo_schema_has_no_implicit_quality_or_trading_choices(missing):
    from research_ml.schema import parse_plan
    from pydantic import ValidationError
    raw = plan().model_dump(mode="json")
    del raw[missing]
    with pytest.raises(ValidationError):
        parse_plan(raw)


def test_invalid_cluster_is_blocked_not_no_edge(raw_context):
    tid, _ = raw_context
    p = service.propose(tid, plan("kmeans", maxIterations=1))
    service.decide(tid, p["id"], p["digest"], "approve", "Test stopping on bounded fit failure")
    result = service.run(tid, p["id"])
    assert result["status"] == "blocked" and "iteration budget" in result["result"]["reason"]
    assert service.run(tid, p["id"]) == result


def test_no_raw_data_fails_closed_before_proposal(raw_context):
    tid, p = raw_context
    (p.raw_dir / "ES" / f"{DAYS[0]}.mbo.dbn.zst").unlink()
    with pytest.raises(ValueError, match="OHLCV/warm caches cannot substitute"):
        service.propose(tid, plan())
    assert service.history(tid)["experiments"] == []


def test_source_mapping_must_cover_every_session(raw_context):
    tid, _ = raw_context
    p = plan()
    p.sources[0] = p.sources[0].model_copy(update={"instrumentId": 999})
    with pytest.raises(ValueError, match="snapshot"):
        service.propose(tid, p)


def test_short_export_does_not_mirror_frozen_signal():
    result = core.discover(frame(), plan(direction="short", predictionThresholdTicks=-1.0), .25)
    model = FrozenModel.model_validate(result["model"]["frozen"])
    ctx = make_ctx()
    ctx.on_bar(bar(0, 5000))
    ctx.ml_features = (ctx.now_ns, frame().iloc[0][FEATURES].tolist())
    long = expr.Evaluator(model.expression(), ctx, "long")
    short = expr.Evaluator(model.expression(), ctx, "short")
    long.on_bar()
    short.on_bar()
    assert long.eval() == short.eval() == bool(model.predict([ctx.ml_features[1]])[0])


def test_full_book_memory_budget_fails_closed(monkeypatch):
    from engine import mbo_features
    monkeypatch.setattr(mbo_features, "MAX_BOOK_ORDERS", 2)
    with pytest.raises(ValueError, match="memory budget"):
        extract(events())


def test_depth_uses_small_levels_and_explicit_n():
    rows = events()
    rows[2]["flags"] = 40
    deeper = [dict(rows[1], price=int(4998.5*NS), size=100, order_id=3),
              dict(rows[2], price=int(5002.5*NS), size=1, order_id=4, flags=168)]
    rows = rows[:3] + deeper + rows[3:]
    shallow, _ = extract(rows, plan(depthLevels=1))
    deep, _ = extract(rows, plan(depthLevels=2))
    assert shallow.iloc[0].depth_imbalance == pytest.approx(-90/110)
    assert deep.iloc[0].depth_imbalance == pytest.approx(9/211)


def test_ridge_jointly_learns_independent_activity_and_book_inputs():
    from research_ml.multivariate import MBORidge
    rng = np.random.default_rng(17)
    x = rng.normal(size=(300, len(FEATURES)))
    weights = np.array([1., -2., 3., -4., 5., -6.])
    train = pd.DataFrame(x, columns=FEATURES)
    train["netTicks"] = x @ weights
    model, _ = MBORidge().fit(train, plan(ridgeAlpha=.000001))
    np.testing.assert_allclose(np.asarray(model.coefficients)/model.scale, weights, atol=1e-6)
