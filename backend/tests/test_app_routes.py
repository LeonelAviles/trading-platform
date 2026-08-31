"""The router split must keep every pre-Phase-0 route reachable."""

LEGACY_PATHS = {
    ("GET", "/api/symbols"), ("GET", "/api/ohlcv"), ("GET", "/api/range"), ("GET", "/api/cvd"),
    ("GET", "/api/dom"), ("GET", "/api/dom-heatmap"),
    ("GET", "/api/strategies"), ("POST", "/api/strategies"), ("POST", "/api/strategies/generate"),
    ("DELETE", "/api/strategies/{strategy_id}"),
    ("GET", "/api/engine/status"), ("GET", "/api/backtests"), ("POST", "/api/backtests"),
    ("GET", "/api/backtests/{job_id}"), ("DELETE", "/api/backtests/{job_id}"),
    ("GET", "/api/backtests/{job_id}/analytics"), ("POST", "/api/backtests/{job_id}/insights"),
    ("GET", "/api/chat/status"), ("POST", "/api/chat"), ("POST", "/api/chat/stream"),
    ("GET", "/api/agent/tools"), ("POST", "/api/agent/tools/{name}"),
}


def test_legacy_routes_registered(client):
    # Read the OpenAPI schema rather than app.routes: newer FastAPI keeps
    # included routers nested, and the schema is what clients see anyway.
    paths = client.get("/openapi.json").json()["paths"]
    routes = {(m.upper(), path) for path, ops in paths.items() for m in ops}
    missing = LEGACY_PATHS - routes
    assert not missing, f"routes lost in the split: {sorted(missing)}"


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["database"].startswith("sqlite")


def test_engine_status(client):
    r = client.get("/api/engine/status")
    assert r.status_code == 200
    assert r.json()["engine"] == "nautilustrader"


def test_chat_status_offline_safe(client):
    r = client.get("/api/chat/status")
    assert r.status_code == 200
    assert "connected" in r.json()


def test_agent_tools_manifest(client):
    r = client.get("/api/agent/tools")
    assert r.status_code == 200
    names = {t["function"]["name"] for t in r.json()}
    assert "create_strategy" in names and "run_backtest" in names


def test_strategies_list(client):
    r = client.get("/api/strategies")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_backtest_404(client):
    assert client.get("/api/backtests/does-not-exist").status_code == 404


def test_settings_roundtrip(client):
    assert client.get("/api/settings").json() == {}
    r = client.put("/api/settings", json={"replay.defaultSpeed": 2, "llm.budgetUsd": 100})
    assert r.status_code == 200
    assert client.get("/api/settings").json() == {"llm.budgetUsd": 100, "replay.defaultSpeed": 2}
    client.put("/api/settings", json={"replay.defaultSpeed": 5})
    assert client.get("/api/settings").json()["replay.defaultSpeed"] == 5


def test_main_shim_reexports_app(client):
    import app as app_module
    import main

    assert main.app is app_module.app
