"""Strategies in SQLite: save/validate/update/risk/status/lineage."""

import pytest
from sqlalchemy.orm import sessionmaker

import database
import strategy_store as st
from tests.test_spec_validation import ORB


@pytest.fixture()
def store(tmp_path, monkeypatch):
    eng = database.make_engine(f"sqlite+pysqlite:///{tmp_path / 'p.db'}")
    database.init_db(eng)
    monkeypatch.setattr(database, "engine", eng)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=eng, autoflush=False, future=True))
    yield
    eng.dispose()


def test_save_get_update_roundtrip(store):
    s = st.save_strategy(ORB)
    assert len(s["id"]) == 12 and s["status"] == "draft" and s["risk"]["accountSize"] == 100000
    assert s["createdAt"] and s["updatedAt"]
    same = st.get_strategy(s["id"])
    assert same["entry"]["trigger"] == ORB["entry"]["trigger"]
    # Updates through the stored document (which carries timestamps) must not trip validation.
    up = st.update_strategy(s["id"], {"name": "ORB v2"})
    assert up["name"] == "ORB v2" and up["id"] == s["id"]
    assert [x["id"] for x in st.list_strategies()] == [s["id"]]
    with pytest.raises(st.StrategyError):
        st.save_strategy({**ORB, "direction": "sideways"})
    assert st.validate({**ORB, "bogus": 1})


def test_v1_documents_are_converted(store):
    v1 = {"name": "legacy", "symbol": "ES1!", "direction": "short", "conditions": [{"type": "rsi_above", "period": 14, "value": 70}],
          "stop": {"type": "fixed_points", "value": 4}, "target": {"type": "rr", "value": 2}, "session": {"start": "13:30", "end": "19:55"}}
    s = st.save_strategy(v1)
    assert s["schemaVersion"] == 2 and s["direction"] == "short" and s["meta"]["convertedFrom"] == "v1"


def test_risk_and_status(store):
    s = st.save_strategy(ORB)
    r = st.patch_risk(s["id"], {"riskPerTradePct": 1.0, "passCriteria": {"minTradesInSample": 80}})
    assert r["risk"]["proposedBy"] == "user" and r["risk"]["riskPerTradePct"] == 1.0
    assert r["risk"]["passCriteria"]["minTradesInSample"] == 80 and r["risk"]["passCriteria"]["maxDrawdownPct"] == 10
    assert st.set_status(s["id"], "candidate")["status"] == "candidate"
    with pytest.raises(st.StrategyError):
        st.set_status(s["id"], "bogus")


def test_lineage_tree_and_delete(store):
    root = st.save_strategy(ORB)
    child = st.save_strategy({**ORB, "name": "child", "lineage": {"parentId": root["id"], "changedVariable": "exit.target.value", "trialIndex": 1}})
    grand = st.save_strategy({**ORB, "name": "grand", "lineage": {"parentId": child["id"], "trialIndex": 2}})
    orphan = st.save_strategy({**ORB, "name": "orphan", "lineage": {"parentId": "nope00000000"}})
    assert orphan["lineage"]["parentId"] is None
    tree = st.lineage(grand["id"])
    assert tree["rootId"] == root["id"]
    assert tree["tree"]["children"][0]["name"] == "child" and tree["tree"]["children"][0]["children"][0]["name"] == "grand"
    assert tree["tree"]["children"][0]["changedVariable"] == "exit.target.value"
    assert st.delete_strategy(grand["id"]) and st.get_strategy(grand["id"]) is None
    assert not st.delete_strategy(grand["id"])
