import builtins
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import sessionmaker

import database
from research_agent import knowledge, service, workflow
from research_agent import tools as agent_tools
import strategy_store
from models import AgentRun, Backtest
from tests.test_spec_validation import ORB


@pytest.fixture()
def agent_db(tmp_path, monkeypatch):
    eng = database.make_engine(f"sqlite+pysqlite:///{tmp_path / 'agent.db'}")
    database.init_db(eng)
    monkeypatch.setattr(database, "engine", eng)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=eng, autoflush=False, future=True))
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text(
        "# Reliable Research\n\nUse walk-forward validation to preserve time order and estimate stability.\n\n"
        "## Costs\n\nInclude commission and slippage before accepting a trading strategy.\n",
        encoding="utf-8",
    )
    (repo / "example.py").write_text("# this is a Python comment\ndef score():\n    return 'deflated sharpe'\n", encoding="utf-8")
    knowledge.ingest_repository(root=repo, name="test-research", url="https://example.test/repo", revision="abc123", license_name="MIT")
    yield repo
    eng.dispose()


def test_ingest_search_and_graph(agent_db):
    sources = knowledge.list_sources()
    assert sources[0]["documents"] == 2
    assert sources[0]["chunks"] == 3  # two Markdown sections + one Python chunk
    result = knowledge.search("walk forward validation", 3)
    assert result and "preserve time order" in result[0]["content"]
    assert result[0]["url"].startswith("https://example.test/repo/blob/abc123/")
    assert knowledge.graph_summary()["nodes"]["repository"] == 1


def test_notebook_outputs_are_not_ingested(tmp_path):
    notebook = tmp_path / "test.ipynb"
    notebook.write_text(
        '{"cells":[{"cell_type":"markdown","source":["# Evidence\\n","Walk forward."],"outputs":[]},'
        '{"cell_type":"code","source":["print(1)"],"outputs":[{"text":["SECRET OUTPUT"]}]}]}',
        encoding="utf-8",
    )
    text = knowledge.read_document(notebook)
    assert "Walk forward" in text and "print(1)" in text and "SECRET OUTPUT" not in text


class FakeResponses:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            call = SimpleNamespace(type="function_call", call_id="call_1", name="search_knowledge", arguments='{"query":"walk forward validation","limit":3}')
            return SimpleNamespace(id="resp_1", output=[call], output_text="")
        return SimpleNamespace(id="resp_2", output=[], output_text="Use time-ordered folds. [test-research:README.md — Reliable Research]")


class FakeStreamingResponses:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        assert kwargs["stream"] is True
        if len(self.calls) == 1:
            call = SimpleNamespace(
                type="function_call", call_id="call_1", name="search_knowledge",
                arguments='{"query":"walk forward validation","limit":3}',
            )
            response = SimpleNamespace(id="resp_1", output=[call], output_text="")
            return iter([
                SimpleNamespace(type="response.created", response=SimpleNamespace(id="resp_1")),
                SimpleNamespace(type="response.completed", response=response),
            ])
        response = SimpleNamespace(id="resp_2", output=[], output_text="Use time-ordered folds.")
        return iter([
            SimpleNamespace(type="response.output_text.delta", delta="Use time-ordered "),
            SimpleNamespace(type="response.output_text.delta", delta="folds."),
            SimpleNamespace(type="response.completed", response=response),
        ])


def test_agent_tool_loop_and_citations(agent_db):
    thread = service.create_thread()
    fake = SimpleNamespace(responses=FakeResponses())
    answer = service.chat(thread["id"], "How do I validate?", client=fake)
    assert "time-ordered" in answer["message"]["content"]
    assert answer["message"]["citations"][0]["path"] == "README.md"
    saved = service.get_thread(thread["id"])
    assert [m["role"] for m in saved["messages"]] == ["user", "assistant"]
    assert fake.responses.calls[1]["previous_response_id"] == "resp_1"


def test_agent_streams_progress_text_and_persists_answer(agent_db):
    thread = service.create_thread()
    fake = SimpleNamespace(responses=FakeStreamingResponses())
    events = list(service.chat_events(thread["id"], "How do I validate?", client=fake))
    assert events[0]["stage"] == "connecting"
    assert "search_knowledge" in [event.get("tool") for event in events]
    assert "".join(event.get("delta", "") for event in events) == "Use time-ordered folds."
    assert events[-1]["type"] == "done"
    assert events[-1]["message"]["citations"][0]["path"] == "README.md"
    assert [m["role"] for m in service.get_thread(thread["id"])["messages"]] == ["user", "assistant"]
    assert fake.responses.calls[1]["previous_response_id"] == "resp_1"


def test_client_wraps_sdk_load_timeout(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    real_import = builtins.__import__

    def broken_import(name, *args, **kwargs):
        if name == "openai":
            raise TimeoutError("filesystem stalled")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", broken_import)
    with pytest.raises(service.AgentConfigurationError, match="TimeoutError"):
        service._client()


def test_empty_message_rejected(agent_db):
    thread = service.create_thread()
    with pytest.raises(ValueError, match="empty"):
        service.chat(thread["id"], "  ", client=SimpleNamespace())


def test_workflow_enforces_one_variable_revision(agent_db):
    thread = service.create_thread()
    root = strategy_store.save_strategy(ORB)
    jobs = []
    with database.session_scope() as db:
        for kind in ("is", "wf1", "wf2", "wf3"):
            row = Backtest(strategy_id=root["id"], mode="ticks", window_kind=kind, status="queued", metrics_json={})
            db.add(row); db.flush()
            jobs.append({"id": row.id, "windowKind": kind, "status": "queued"})
    run = workflow.start(thread["id"], root, jobs)
    with database.session_scope() as db:
        db.get(AgentRun, run["id"]).status = "analyzing"

    child = {**root, "id": "newcandidate1", "name": "ORB target 2.5",
             "exit": {**root["exit"], "target": {**root["exit"]["target"], "value": 2.5}},
             "lineage": {"parentId": root["id"], "changedVariable": "exit.target.value",
                         "rationale": "Improve reward relative to risk.", "trialIndex": 1}}
    workflow.validate_revision(run["id"], child)
    invalid = {**child, "constraints": {**root["constraints"], "maxTradesPerDay": 2}}
    with pytest.raises(ValueError, match="exactly one variable"):
        workflow.validate_revision(run["id"], invalid)


def test_workflow_only_accepts_passing_champion(agent_db):
    thread = service.create_thread()
    root = strategy_store.save_strategy(ORB)
    with database.session_scope() as db:
        job = Backtest(strategy_id=root["id"], mode="ticks", window_kind="is", status="done", metrics_json={})
        db.add(job); db.flush()
        jobs = [{"id": job.id, "windowKind": "is", "status": "done"}]
    run = workflow.start(thread["id"], root, jobs)
    with database.session_scope() as db:
        row = db.get(AgentRun, run["id"])
        state = dict(row.state_json)
        state["candidates"][0]["verdict"] = {"status": "fail", "score": 0.5}
        row.state_json = state
        row.status = "analyzing"
    with pytest.raises(ValueError, match="passed"):
        workflow.conclude(run["id"], "champion", "Not enough evidence", root["id"])
    done = workflow.conclude(run["id"], "no_edge", "I couldn't find an edge.")
    assert done["status"] == "budget_exhausted"
    assert done["conclusion"]["outcome"] == "no_edge"


def _synthetic_sessions():
    """Two RTH sessions of 1-minute bars: day 1 sets prior levels (low 100),
    day 2 breaks that low on its 3rd bar with heavy selling and closes below."""
    import pandas as pd

    def bar(ts, o, h, lo, c, vol=100, buy=50, sell=50):
        return {"ts": ts, "open": o, "high": h, "low": lo, "close": c,
                "volume": float(vol), "delta": float(buy - sell), "buy_vol": float(buy), "sell_vol": float(sell)}

    day1 = [bar(f"2026-04-01 09:{30 + i}", 101, 103, 100, 102) for i in range(5)]
    day2_px = [(102, 102.5, 101, 101.5, 50, 25, 25),   # inside
               (101.5, 102, 100.5, 101, 50, 25, 25),   # inside
               (101, 101.2, 99, 99.5, 200, 40, 160),   # FIRST breach of 100, 80% sell share
               (99.5, 100.4, 98, 98.5, 100, 50, 50),   # continuation
               (98.5, 99, 97, 97.5, 100, 50, 50)]      # closes below the level
    day2 = [bar(f"2026-04-02 09:{30 + i}", *p) for i, p in enumerate(day2_px)]
    rows = day1 + day2
    idx = pd.DatetimeIndex([pd.Timestamp(r.pop("ts"), tz="America/New_York").tz_convert("UTC") for r in rows])
    return pd.DataFrame(rows, index=idx.rename("ts_event"))


def test_level_event_stats_first_breach_and_flow_filter(monkeypatch):
    from research_agent import analysis

    monkeypatch.setattr(analysis.data_store, "get_bars", lambda symbol, interval: _synthetic_sessions())
    monkeypatch.setattr(analysis, "load_instruments", lambda: SimpleNamespace(
        root_for_symbol=lambda symbol: SimpleNamespace(root="ES", tick_size=0.25),
        session=SimpleNamespace(rth_start="09:30", rth_end="16:00"),
    ))
    result = analysis.level_event_stats("ES1!", "prior_day_low", min_aggressor_share=0.55)
    assert result["sessions"] == 1 and result["breakSessions"] == 1  # day 1 only defines the level
    assert result["breakRatePct"] == 100.0
    event = result["recentEvents"][0]
    assert event["date"] == "2026-04-02" and event["minuteAfterOpen"] == 2
    assert event["aggressorShare"] == 0.8 and event["closedBeyond"] is True
    assert event["mfePoints"] == 3.0    # level 100 down to the 97 low
    assert event["maePoints"] == 0.4    # the 100.4 poke back above
    assert result["all"]["closedBeyondLevelPct"] == 100.0
    assert result["filtered"]["events"] == 1  # 0.8 sell share passes the 0.55 filter
    strict = analysis.level_event_stats("ES1!", "prior_day_low", min_aggressor_share=0.9)
    assert strict["filtered"]["events"] == 0


def test_level_event_stats_reversal_boundary(monkeypatch):
    from research_agent import analysis

    monkeypatch.setattr(analysis.data_store, "get_bars", lambda symbol, interval: _synthetic_sessions())
    monkeypatch.setattr(analysis, "load_instruments", lambda: SimpleNamespace(
        root_for_symbol=lambda symbol: SimpleNamespace(root="ES", tick_size=0.25),
        session=SimpleNamespace(rth_start="09:30", rth_end="16:00"),
    ))
    # Back-to-level: the bar after the break pokes 100.4 >= 100, ending the
    # window — follow-through is the event bar's 1.0 point, one minute in.
    back = analysis.level_event_stats("ES1!", "prior_day_low", measure_until="level")
    event = back["recentEvents"][0]
    assert event["reversed"] is True and event["minutesToReversal"] == 1
    assert event["mfePoints"] == 1.0 and event["maePoints"] == 0.0
    assert back["all"]["reversedToBoundaryPct"] == 100.0
    assert back["all"]["followThroughBeforeReversalPoints"]["median"] == 1.0
    # Prior-day range midpoint (101.5) is never revisited after the break, so
    # the full-session follow-through survives.
    mid = analysis.level_event_stats("ES1!", "prior_day_low", measure_until="range_mid")
    event = mid["recentEvents"][0]
    assert event["reversed"] is False and event["minutesToReversal"] is None
    assert event["mfePoints"] == 3.0
    assert mid["all"]["reversedToBoundaryPct"] == 0.0
    with pytest.raises(ValueError, match="range level"):
        analysis.level_event_stats("ES1!", "vwap", direction="above", measure_until="range_mid")


def test_level_event_tool_is_clamped_and_never_queues(agent_db, monkeypatch):
    seen = {}
    monkeypatch.setattr(agent_tools, "load_instruments", lambda: SimpleNamespace(
        root_for_symbol=lambda symbol: SimpleNamespace(root="ES") if symbol == "ES1!" else None,
    ))
    monkeypatch.setattr(agent_tools.validation, "window_for",
                        lambda root, kind: (date(2026, 4, 1), date(2026, 7, 31)))

    def fake_stats(symbol, level, date_from=None, date_to=None, **kw):
        seen.update(symbol=symbol, level=level, start=date_from, end=date_to, **kw)
        return {"symbol": symbol, "breakSessions": 3}

    monkeypatch.setattr(agent_tools.analysis, "level_event_stats", fake_stats)
    result, _ = agent_tools.execute("get_level_event_stats", {
        "symbol": "ES1!", "level": "opening_range_high", "direction": None, "or_minutes": None,
        "date_from": "2025-01-01", "date_to": "2027-01-01",
        "min_aggressor_share": 0.55, "min_volume": None, "max_range_ticks": None,
    })
    assert seen["start"].isoformat() == "2026-04-01" and seen["end"].isoformat() == "2026-07-31"
    assert seen["or_minutes"] == 15 and seen["min_aggressor_share"] == 0.55
    assert result["scope"] == "in_sample_only" and result["oosHidden"] is True
    with database.session_scope() as db:
        assert db.query(Backtest).count() == 0


def test_list_backtest_jobs_reports_status_and_hides_oos(agent_db):
    sid = strategy_store.save_strategy(ORB)["id"]
    with database.session_scope() as db:
        db.add(Backtest(strategy_id=sid, mode="ticks", window_kind="is", status="done",
                        metrics_json={"strategyName": "A", "trades": 15, "netPnl": 1895.0, "profitFactor": 1.47}))
        db.add(Backtest(strategy_id=sid, mode="ticks", window_kind="oos", status="done",
                        metrics_json={"strategyName": "A", "trades": 9, "netPnl": -500.0}))
        db.add(Backtest(strategy_id=sid, mode="ticks", window_kind="wf1", status="queued", metrics_json={}))
    result, _ = agent_tools.execute("list_backtest_jobs", {"strategy_id": sid, "limit": None})
    by_kind = {row["windowKind"]: row for row in result["jobs"]}
    assert by_kind["is"]["metrics"]["netPnl"] == 1895.0
    assert by_kind["oos"]["metrics"] is None and by_kind["oos"]["oosHidden"] is True
    assert by_kind["oos"]["status"] == "done"  # existence is visible, results are not
    assert by_kind["wf1"]["status"] == "queued"


def test_strategy_language_reports_real_operators():
    language, _ = agent_tools.execute("get_strategy_language", {})
    assert "cross_above" in language["operators"] and "first_above" in language["operators"]
    assert "crosses_above" not in language["operators"] and "all" not in language["operators"]


def test_daily_direction_tool_is_clamped_to_in_sample(agent_db, monkeypatch):
    seen = {}
    monkeypatch.setattr(agent_tools, "load_instruments", lambda: SimpleNamespace(
        root_for_symbol=lambda symbol: SimpleNamespace(root="ES") if symbol == "ES1!" else None,
    ))
    monkeypatch.setattr(agent_tools.validation, "window_for",
                        lambda root, kind: (date(2026, 4, 1), date(2026, 7, 31)))

    def fake_stats(symbol, start, end):
        seen.update(symbol=symbol, start=start, end=end)
        return {"symbol": symbol, "sessions": 80}

    monkeypatch.setattr(agent_tools.data_store, "get_daily_direction_stats", fake_stats)
    result, _ = agent_tools.execute("get_daily_direction_stats", {
        "symbol": "ES1!", "date_from": "2025-01-01", "date_to": "2027-01-01",
    })
    assert seen["start"].isoformat() == "2026-04-01" and seen["end"].isoformat() == "2026-07-31"
    assert result["scope"] == "in_sample_only" and result["oosHidden"] is True
    with database.session_scope() as db:
        assert db.query(Backtest).count() == 0  # observational analysis never queues Nautilus
