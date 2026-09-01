"""The router split must keep every route the frontend relies on reachable."""

LEGACY_PATHS = {
    ("GET", "/api/symbols"), ("GET", "/api/ohlcv"), ("GET", "/api/range"), ("GET", "/api/cvd"),
    ("GET", "/api/dom"), ("GET", "/api/dom-heatmap"),
    ("GET", "/api/strategies"), ("POST", "/api/strategies"),
    ("DELETE", "/api/strategies/{strategy_id}"),
    ("GET", "/api/engine/status"), ("GET", "/api/backtests"), ("POST", "/api/backtests"),
    ("GET", "/api/backtests/{job_id}"), ("DELETE", "/api/backtests/{job_id}"),
    ("GET", "/api/backtests/{job_id}/analytics"),
    ("GET", "/api/desk"), ("GET", "/api/settings"),
}

REMOVED_PREFIXES = ("/api/agent", "/api/chat", "/api/research", "/api/knowledge", "/api/usage", "/ws/agent", "/api/teaching")


def test_routes_registered(client):
    # Read the OpenAPI schema rather than app.routes: newer FastAPI keeps
    # included routers nested, and the schema is what clients see anyway.
    paths = client.get("/openapi.json").json()["paths"]
    routes = {(m.upper(), path) for path, ops in paths.items() for m in ops}
    missing = LEGACY_PATHS - routes
    assert not missing, f"routes lost in the split: {sorted(missing)}"
    leftovers = [p for p in paths if p.startswith(REMOVED_PREFIXES) or p.endswith(("/generate", "/insights", "/compile", "/answer"))]
    assert not leftovers, f"removed routes still registered: {leftovers}"


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["database"].startswith("sqlite")


def test_engine_status(client):
    r = client.get("/api/engine/status")
    assert r.status_code == 200
    assert r.json()["engine"] == "nautilustrader"


def test_strategies_list(client):
    r = client.get("/api/strategies")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_backtest_404(client):
    assert client.get("/api/backtests/does-not-exist").status_code == 404


def test_settings_roundtrip(client):
    assert client.get("/api/settings").json() == {}
    r = client.put("/api/settings", json={"replay.defaultSpeed": 2, "ui.theme": "dark"})
    assert r.status_code == 200
    assert client.get("/api/settings").json() == {"replay.defaultSpeed": 2, "ui.theme": "dark"}
    client.put("/api/settings", json={"replay.defaultSpeed": 5})
    assert client.get("/api/settings").json()["replay.defaultSpeed"] == 5


def test_main_shim_reexports_app(client):
    import app as app_module
    import main

    assert main.app is app_module.app
