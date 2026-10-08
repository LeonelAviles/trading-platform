"""SQLite job model + subprocess worker + validation windows on a 5-day synthetic store."""

import json
from datetime import date

import pytest

import data_store
import database
from engine import jobs, validation
from market import catalog as cat
from market import ingest as ing
from market import paths as paths_mod
from tests import synth
from tests.test_ingest import _chunks

DAYS = [date(2026, 6, 15 + i) for i in range(5)]


@pytest.fixture(scope="module")
def store(tmp_path_factory, monkeypatch_module=None):
    tmp = tmp_path_factory.mktemp("jobs")
    p = paths_mod.configure(data_dir=tmp / "data", market_data_dir=tmp / "market-data")
    p.ensure_dirs()
    for i, d in enumerate(DAYS):
        # Use the September contract: June expires at 09:30 on the final
        # fixture day, before the synthetic strategy can enter.
        cfg = synth.SynthConfig(session_date=d, symbols=("ESU6",), rth_start="09:30", rth_end="10:00", seed=300 + i)
        ing.DayIngest(None, schema="mbo", session_date=d, frames=_chunks(synth.generate_mbo(cfg)), paths=p, min_daily_volume=1, book=False).run()
    ing.finalize(p)
    cat.build(p, progress=lambda s: None)
    data_store.reset()
    # Redirect the job model at a temp DB + temp jobs dir.
    eng = database.make_engine(f"sqlite+pysqlite:///{tmp / 'platform.db'}")
    database.init_db(eng)
    from sqlalchemy.orm import sessionmaker
    old = (database.engine, database.SessionLocal, jobs.JOBS_DIR)
    database.engine, database.SessionLocal = eng, sessionmaker(bind=eng, autoflush=False, future=True)
    jobs.JOBS_DIR = tmp / "backtests"
    yield {"paths": p, "tmp": tmp}
    database.engine, database.SessionLocal, jobs.JOBS_DIR = old
    data_store.reset()
    paths_mod.configure(data_dir=paths_mod.REPO_ROOT / "data", market_data_dir=paths_mod.REPO_ROOT / "market-data")


def test_windows_from_splits(store):
    w = validation.windows("ES")
    # IS_FRACTION is 1.0: all 5 sessions are in-sample, no OOS window, full == is.
    # IS cut into 4 blocks -> edges [0, 1, 2, 4, 5]: wf1 = day 2, wf2 = days 3-4, wf3 = day 5.
    assert w["is"] == ("2026-06-15", "2026-06-19")
    assert w["wf1"] == ("2026-06-16", "2026-06-16") and w["wf2"] == ("2026-06-17", "2026-06-18") and w["wf3"] == ("2026-06-19", "2026-06-19")
    assert "oos" not in w
    assert w["full"] == w["is"]
    assert validation.windows("NQ") == {}
    with pytest.raises(ValueError):
        validation.window_for("ES", "bogus")


def test_full_window_spans_earlier_oos_and_later_is(store):
    split_path = store["paths"].splits
    original = json.loads(split_path.read_text())
    changed = json.loads(split_path.read_text())
    changed["roots"]["ES"]["inSample"] = ["2026-06-17", "2026-06-18", "2026-06-19"]
    changed["roots"]["ES"]["outOfSample"] = ["2026-06-15", "2026-06-16"]
    split_path.write_text(json.dumps(changed))
    try:
        assert validation.windows("ES")["full"] == ("2026-06-15", "2026-06-19")
    finally:
        split_path.write_text(json.dumps(original))


STRATEGY = {
    "id": "abc123abc123", "name": "open-close test", "instrument": {"symbol": "ES1!"},
    "timeframes": {"primary": "1min"},
    "session": {"entryWindow": {"start": "09:31", "end": "09:50"}, "flattenAt": "09:55"},
    "rules": {"kind": "test_open_close"},
    "exit": {"stop": {"type": "ticks", "value": 2000}, "target": {"type": "ticks", "value": 2000}},
    "sizing": {"type": "fixed_contracts", "value": 1, "maxContracts": 5},
    "execution": {"mode": "bars"},
}


def test_job_lifecycle_and_analytics(store):
    job = jobs.run_sync(STRATEGY, window_kind="is", timeout_s=300)
    assert job["status"] == "done", job["message"]
    assert job["windowKind"] == "is" and job["mode"] == "bars" and job["dateFrom"] == "2026-06-15"
    assert job["summary"]["trades"] == 5 and job["strategyName"] == "open-close test"
    assert job["strategyId"] == "abc123abc123"       # legacy id kept even without a strategies row
    assert len(job["trades"]) == 5 and all("pnlUsd" in t for t in job["trades"])
    assert (jobs.JOBS_DIR / job["id"] / "trades.json").exists()
    assert (jobs.JOBS_DIR / job["id"] / "worker.log").exists()
    assert (jobs.JOBS_DIR / job["id"] / "progress.json").exists()
    assert job["progress"]["sessionsCompleted"] == job["progress"]["sessionsTotal"] == 5
    assert job["progress"]["percent"] == 100.0 and job["progress"]["tradeCount"] == 5
    stats = jobs.strategy_analytics(job)
    assert stats["trades"] == 5 and stats["sessions"] == 5 and stats["sessionsTraded"] == 5
    assert set(stats["byRegime"]) and stats["byHour"][0]["hourEt"] == 9
    assert job["metrics"]["trades"] == 5 and "sharpe" in job["metrics"]
    listed = jobs.list_jobs()
    assert listed[0]["id"] == job["id"] and "trades" not in listed[0]
    assert "liveTrades" not in listed[0]["progress"]
    assert jobs.get_job("nope") is None


def test_running_job_exposes_live_progress_and_trades(store):
    job = jobs.create_job(STRATEGY, window_kind="wf1")
    live_trade = {
        "entryTime": 1, "exitTime": 2, "entryPrice": 6000, "exitPrice": 6001,
        "direction": "long", "pnlUsd": 50, "pnl": 50,
    }
    (jobs.JOBS_DIR / job["id"] / "progress.json").write_text(json.dumps({
        "sessionsCompleted": 1, "sessionsTotal": 2, "percent": 50.0,
        "currentDate": "2026-06-16", "tradeCount": 1, "liveTrades": [live_trade],
    }))
    with database.session_scope() as db:
        from models import Backtest
        db.get(Backtest, job["id"]).status = "running"

    detail = jobs.get_job(job["id"])
    assert detail["progress"]["percent"] == 50.0
    assert len(detail["trades"]) == 1 and detail["trades"][0]["pnlUsd"] == 50
    listed = next(j for j in jobs.list_jobs() if j["id"] == job["id"])
    assert listed["progress"]["tradeCount"] == 1 and "liveTrades" not in listed["progress"]
    jobs.delete_job(job["id"])


def test_validation_runs_is_and_wf_only(store):
    queued = jobs.run_validation(STRATEGY)
    assert [q["windowKind"] for q in queued] == ["is", "wf1", "wf2", "wf3"]
    import time
    for _ in range(600):
        states = {jobs.get_job(q["id"])["status"] for q in queued}
        if states <= {"done", "error"}:
            break
        time.sleep(0.5)
    assert states == {"done"}
    rep = validation.report("abc123abc123", mode="bars")
    assert rep["inSample"]["trades"] == 5
    assert [w["window"] for w in rep["walkForward"]] == ["wf1", "wf2", "wf3"]
    assert rep["outOfSample"] is None and rep["oosHidden"] is True and rep["oosAvailable"] is False
    assert rep["monteCarlo"]["bootstrap"]["runs"] == 1000 and rep["deflatedSharpe"]["observations"] == 5
    assert rep["verdict"]["untestable"] is True       # 5 trades << 100
    assert rep["risk"]["passCriteria"]["minTradesInSample"] == 100
    # OOS appears only once a holdout exists *and* an oos row exists. There is
    # no holdout at IS_FRACTION 1.0, so carve one by hand: freeze the first
    # four sessions as IS and let recompute_splits put the fifth in OOS.
    with pytest.raises(ValueError):
        validation.window_for("ES", "oos")
    sp_path = store["paths"].splits
    sp = json.loads(sp_path.read_text())
    sp["roots"]["ES"]["inSample"] = sp["roots"]["ES"]["inSample"][:4]
    sp_path.write_text(json.dumps(sp))
    ing.recompute_splits(ing.recompute_front_month(store["paths"]), store["paths"])
    assert validation.windows("ES")["oos"] == ("2026-06-19", "2026-06-19")
    oos = jobs.run_sync(STRATEGY, window_kind="oos", timeout_s=300)
    assert oos["status"] == "done"
    rep2 = validation.report("abc123abc123", mode="bars")
    assert rep2["oosAvailable"] and rep2["outOfSample"]["trades"] == 1
    assert validation.report("abc123abc123", mode="bars", include_oos=False)["outOfSample"] is None


def test_delete_job(store):
    job = jobs.run_sync(STRATEGY, window_kind="wf1", timeout_s=300)
    assert jobs.delete_job(job["id"]) and not jobs.delete_job(job["id"])
    assert not (jobs.JOBS_DIR / job["id"]).exists()


def test_recovery_finalizes_artifact_and_resumes_queue(store, monkeypatch):
    completed = jobs.create_job(STRATEGY, window_kind="wf1")
    queued = jobs.create_job(STRATEGY, window_kind="wf2")
    completed_dir = jobs.JOBS_DIR / completed["id"]
    (completed_dir / "trades.json").write_text(json.dumps({
        "trades": [], "dailyReturns": [], "summary": {"trades": 0}, "meta": {},
    }))
    with database.session_scope() as db:
        from models import Backtest
        db.get(Backtest, completed["id"]).status = "running"

    started = []
    monkeypatch.setattr(jobs, "start", lambda job_id: started.append(job_id))
    recovered = jobs.recover_pending_jobs()

    assert jobs.get_job(completed["id"])["status"] == "done"
    assert started == [queued["id"]]
    assert recovered == {"recovered": 1, "resumed": 1, "adopted": 0}
