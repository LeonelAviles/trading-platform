"""Desk summary (Phase 7): empty store, then a candidate with validation rows."""

import json

import pytest
from sqlalchemy.orm import sessionmaker

import database
import desk
import strategy_store as st
from models import Backtest
from tests.test_spec_validation import ORB


@pytest.fixture()
def store(tmp_path, monkeypatch):
    eng = database.make_engine(f"sqlite+pysqlite:///{tmp_path / 'p.db'}")
    database.init_db(eng)
    monkeypatch.setattr(database, "engine", eng)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=eng, autoflush=False, future=True))
    monkeypatch.setattr("engine.jobs.JOBS_DIR", tmp_path / "backtests", raising=False)
    yield tmp_path
    eng.dispose()


def _finished(db, sid, kind, metrics, trades=None, jobs_dir=None):
    b = Backtest(id=f"{kind}{sid}"[:12], strategy_id=sid, mode="bars", window_kind=kind, status="done",
                 date_from="2026-04-01", date_to="2026-05-15", metrics_json=metrics)
    db.add(b)
    db.flush()
    if trades is not None and jobs_dir is not None:
        d = jobs_dir / b.id
        d.mkdir(parents=True, exist_ok=True)
        (d / "trades.json").write_text(json.dumps({"trades": trades, "dailyReturns": [{"date": "2026-04-01", "returnPct": 0.1}] * 5}))
    return b.id


def test_empty_desk(store):
    d = desk.summary()
    assert d["candidates"] == [] and d["lineage"] == []
    assert d["strategies"] == {"total": 0, "byStatus": {}}
    assert d["testing"]["backtests"] == [] and d["testing"]["recentBacktests"] == []
    assert "budget" not in d and "research" not in d
    assert "roots" in d["coverage"] and "replayCache" in d["coverage"]


def test_candidate_card_and_lineage(store, monkeypatch):
    from engine import jobs

    monkeypatch.setattr(jobs, "JOBS_DIR", store / "backtests")
    monkeypatch.setattr(jobs, "_job_dir", lambda job_id: store / "backtests" / job_id)
    root = st.save_strategy({**ORB, "name": "ORB root"})
    child = st.save_strategy({**ORB, "name": "ORB 2.5R", "lineage": {"parentId": root["id"], "changedVariable": "exit.target.value", "trialIndex": 2}})
    st.set_status(child["id"], "candidate")
    trades = [{"pnlUsd": p, "r": p / 500, "pnl": p, "exitReason": "target" if p > 0 else "stop"} for p in ([600.0] * 8 + [-500.0] * 4)]
    with database.session_scope() as db:
        _finished(db, root["id"], "is", {"trades": 12, "profitFactor": 1.4, "expectancyR": 0.2, "verdict": {"status": "untestable", "failures": []}})
        _finished(db, child["id"], "is", {"trades": 12, "profitFactor": 2.4, "expectancyR": 0.6, "maxDrawdownPct": 2.0, "accountSize": 100000,
                                          "verdict": {"status": "pass", "failures": []},
                                          "analytics": {"byRegime": {"trend": {"expectancyR": 0.9, "profitFactor": 3.0}, "range": {"expectancyR": -0.2, "profitFactor": 0.8}}}},
                  trades=trades, jobs_dir=store / "backtests")
        _finished(db, child["id"], "wf1", {"trades": 4, "netPnl": 900.0})
        _finished(db, child["id"], "oos", {"trades": 5, "profitFactor": 1.3})
    d = desk.summary()
    assert d["strategies"]["byStatus"] == {"draft": 1, "candidate": 1}
    [c] = d["candidates"]
    assert c["id"] == child["id"] and c["status"] == "candidate" and c["parentId"] == root["id"]
    assert c["oosProfitFactor"] == 1.3 and c["oosTrades"] == 5
    assert c["monteCarloDd95Pct"] is not None and c["monteCarloDd95Pct"] >= 0
    assert c["walkForwardPositive"] == 1 and c["walkForwardWindows"] == 1
    assert c["regimeNotes"][0].startswith("best in trend") and "worst in range" in c["regimeNotes"][1]
    assert c["verdict"]["status"] in ("pass", "fail", "untestable")
    [lin] = d["lineage"]
    assert lin["rootId"] == root["id"] and lin["nodes"] == 2 and lin["champion"] == child["id"]
    assert lin["tree"]["children"][0]["id"] == child["id"]


def test_desk_route(client):
    r = client.get("/api/desk")
    assert r.status_code == 200
    body = r.json()
    assert set(body) >= {"candidates", "testing", "coverage", "lineage", "strategies"}
    assert "teaching" not in body
