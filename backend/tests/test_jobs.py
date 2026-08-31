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
        cfg = synth.SynthConfig(session_date=d, rth_start="09:30", rth_end="10:00", seed=300 + i)
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
    # 5 sessions -> 4 IS (round(3.5)=4), 1 OOS; IS cut into 4 blocks of one day.
    assert w["is"] == ("2026-06-15", "2026-06-18")
    assert w["wf1"] == ("2026-06-16", "2026-06-16") and w["wf3"] == ("2026-06-18", "2026-06-18")
    assert w["oos"] == ("2026-06-19", "2026-06-19")
    assert w["full"] == ("2026-06-15", "2026-06-19")
    assert validation.windows("NQ") == {}
    with pytest.raises(ValueError):
        validation.window_for("ES", "teaching")


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
    assert job["summary"]["trades"] == 4 and job["strategyName"] == "open-close test"
    assert job["strategyId"] == "abc123abc123"       # legacy id kept even without a strategies row
    assert len(job["trades"]) == 4 and all("pnlUsd" in t for t in job["trades"])
    assert (jobs.JOBS_DIR / job["id"] / "trades.json").exists()
    assert (jobs.JOBS_DIR / job["id"] / "worker.log").exists()
    stats = jobs.strategy_analytics(job)
    assert stats["trades"] == 4 and stats["sessions"] == 4 and stats["sessionsTraded"] == 4
    assert set(stats["byRegime"]) and stats["byHour"][0]["hourEt"] == 9
    assert job["metrics"]["trades"] == 4 and "sharpe" in job["metrics"]
    listed = jobs.list_jobs()
    assert listed[0]["id"] == job["id"] and "trades" not in listed[0]
    assert jobs.get_job("nope") is None


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
    assert rep["inSample"]["trades"] == 4
    assert [w["window"] for w in rep["walkForward"]] == ["wf1", "wf2", "wf3"]
    assert rep["outOfSample"] is None and rep["oosHidden"] is True and rep["oosAvailable"] is False
    assert rep["monteCarlo"]["bootstrap"]["runs"] == 1000 and rep["deflatedSharpe"]["observations"] == 4
    assert rep["verdict"]["untestable"] is True       # 4 trades << 100
    assert rep["risk"]["passCriteria"]["minTradesInSample"] == 100
    # OOS appears only once an oos row exists.
    oos = jobs.run_sync(STRATEGY, window_kind="oos", timeout_s=300)
    assert oos["status"] == "done"
    rep2 = validation.report("abc123abc123", mode="bars")
    assert rep2["oosAvailable"] and rep2["outOfSample"]["trades"] == 1
    assert validation.report("abc123abc123", mode="bars", include_oos=False)["outOfSample"] is None


def test_delete_job(store):
    job = jobs.run_sync(STRATEGY, window_kind="wf1", timeout_s=300)
    assert jobs.delete_job(job["id"]) and not jobs.delete_job(job["id"])
    assert not (jobs.JOBS_DIR / job["id"]).exists()
