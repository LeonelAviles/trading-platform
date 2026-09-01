"""Strategy package export / import (Phase 7): zip contents, the Nautilus
stub, a clean re-import, the forward-test transition and the compare route."""

import io
import json
import zipfile

import pytest

import strategy_package as pk
import strategy_store as st
from tests.test_spec_validation import ORB


@pytest.fixture()
def api(client, tmp_path, monkeypatch):
    from engine import jobs

    monkeypatch.setattr(jobs, "JOBS_DIR", tmp_path / "backtests")
    monkeypatch.setattr(jobs, "_job_dir", lambda job_id: tmp_path / "backtests" / job_id)
    return client


def _mk(api, name="ORB pkg", **extra):
    r = api.post("/api/strategies", json={**ORB, "name": name, **extra})
    assert r.status_code == 200, r.text
    return r.json()


def _finish(api, sid, kind, metrics, trades=None):
    import database
    from engine import jobs
    from models import Backtest

    bid = f"{kind}{sid}"[:12]
    with database.session_scope() as db:
        db.add(Backtest(id=bid, strategy_id=sid, mode="bars", window_kind=kind, status="done", date_from="2026-04-01",
                        date_to="2026-05-15", metrics_json={"strategyName": "x", "symbol": "ES1!", "summary": {"trades": metrics.get("trades", 0)}, **metrics}))
    d = jobs._job_dir(bid)
    d.mkdir(parents=True, exist_ok=True)
    (d / "trades.json").write_text(json.dumps({"trades": trades or []}))
    return bid


def test_package_contents_and_reimport(api):
    root = _mk(api, "ORB root")
    s = _mk(api, "ORB child", lineage={"parentId": root["id"], "changedVariable": "exit.target.value", "trialIndex": 2},
            origin={"type": "manual"})
    sid = s["id"]
    _finish(api, sid, "is", {"trades": 3, "profitFactor": 1.5, "expectancyR": 0.3, "verdict": {"status": "untestable", "failures": ["x"]}},
            trades=[{"pnlUsd": 500.0, "r": 1.0}, {"pnlUsd": -250.0, "r": -0.5}, {"pnlUsd": 500.0, "r": 1.0}])
    r = api.get(f"/api/strategies/{sid}/package")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert sid in r.headers["content-disposition"]
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    names = set(zf.namelist())
    assert {"manifest.json", "spec.json", "risk.json", "validation_report.json", "lineage.json", "nautilus_config.json"} <= names
    assert not [n for n in names if n in ("evidence/findings.json", "evidence/knowledge.json", "evidence/agent_run.json")]
    manifest = json.loads(zf.read("manifest.json"))
    assert manifest["packageVersion"] == pk.PACKAGE_VERSION and manifest["strategyId"] == sid
    spec = json.loads(zf.read("spec.json"))
    assert spec["id"] == sid and spec["entry"]["trigger"] == ORB["entry"]["trigger"]
    assert json.loads(zf.read("risk.json"))["accountSize"] == 100000
    rep = json.loads(zf.read("validation_report.json"))
    assert rep["inSample"]["trades"] == 3 and rep["monteCarlo"]["bootstrap"]["runs"] == 1000 and rep["verdict"]
    lin = json.loads(zf.read("lineage.json"))
    assert lin["rootId"] == root["id"] and lin["champion"] == sid
    nc = json.loads(zf.read("nautilus_config.json"))
    assert nc["strategy_path"] == "engine.backtest_worker:ExecStrategy" and nc["config"]["spec_path"] == "spec.json"
    assert nc["config"]["instrument_id"].startswith("ES") and "1-MINUTE" in nc["config"]["bar_type"]
    assert nc["config"]["params"]["primary"] == "1min"

    # Same store: the id is taken, so the import mints a new one and keeps the parent link.
    r2 = api.post("/api/strategies/import", content=r.content, headers={"content-type": "application/zip"})
    assert r2.status_code == 200, r2.text
    imp = r2.json()
    assert imp["renamedId"] and imp["originalId"] == sid and imp["parentKept"]
    again = api.get(f"/api/strategies/{imp['id']}").json()
    original = api.get(f"/api/strategies/{sid}").json()
    for k in ("name", "direction", "instrument", "timeframes", "session", "entry", "exit", "filters", "risk", "execution", "origin"):
        assert again.get(k) == original.get(k), k
    assert again["lineage"]["parentId"] == root["id"]
    assert again["origin"] == {"type": "manual", "sourceId": None}
    assert imp["validationReport"]["inSample"]["trades"] == 3

    # Deleted locally: the import restores the original id, and the package validates as a strategy.
    api.delete(f"/api/strategies/{sid}")
    r3 = api.post("/api/strategies/import", content=r.content)
    assert r3.status_code == 200 and r3.json()["id"] == sid and not r3.json()["renamedId"]
    assert api.get(f"/api/strategies/{sid}").json()["name"] == "ORB child"


def test_import_rejects_garbage(api):
    assert api.post("/api/strategies/import", content=b"").status_code == 400
    assert api.post("/api/strategies/import", content=b"not a zip").status_code == 400
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("readme.txt", "no spec")
    assert api.post("/api/strategies/import", content=buf.getvalue()).status_code == 400
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("spec.json", json.dumps({"name": "broken", "schemaVersion": 2}))
    r = api.post("/api/strategies/import", content=buf.getvalue())
    assert r.status_code == 400 and "entry" in r.text.lower() or "direction" in r.text.lower()


def test_forward_test_transition(api):
    s = _mk(api)
    r = api.post(f"/api/strategies/{s['id']}/forward-test")
    assert r.status_code == 409
    st.set_status(s["id"], "candidate")
    r = api.post(f"/api/strategies/{s['id']}/forward-test")
    assert r.status_code == 200 and r.json()["status"] == "forward_test"
    assert api.post(f"/api/strategies/{s['id']}/forward-test").status_code == 409


def test_compare_two_nodes(api):
    a = _mk(api, "A")
    b = _mk(api, "B", lineage={"parentId": a["id"], "changedVariable": "exit.target.value"})
    r = api.get(f"/api/strategies/{a['id']}/compare/{b['id']}")
    assert r.status_code == 404 and "no finished" in r.text
    ta = [{"pnlUsd": 500.0, "pnl": 500.0, "r": 1.0, "exitReason": "target"}] * 5 + [{"pnlUsd": -500.0, "pnl": -500.0, "r": -1.0, "exitReason": "stop"}] * 5
    tb = [{"pnlUsd": 600.0, "pnl": 600.0, "r": 1.2, "exitReason": "target"}] * 7 + [{"pnlUsd": -500.0, "pnl": -500.0, "r": -1.0, "exitReason": "stop"}] * 3
    _finish(api, a["id"], "is", {"trades": 10}, trades=ta)
    _finish(api, b["id"], "is", {"trades": 10}, trades=tb)
    r = api.get(f"/api/strategies/{a['id']}/compare/{b['id']}")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["a"]["strategyId"] == a["id"] and out["b"]["strategyId"] == b["id"] and out["window"] == "is"
    cmp_ = out["comparison"]
    assert "sideBySide" in cmp_ or "side_by_side" in cmp_ or "metrics" in cmp_
    assert cmp_["verdict"]
