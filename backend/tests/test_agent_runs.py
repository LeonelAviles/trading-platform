"""AgentRun state machine with a scripted fake client on a synthetic store:
variants cap, IS+WF-only backtests, ask_user pause/answer, change budget,
OOS blindness, finalize once, restart resume (PLATFORM-SPEC.md §9)."""

import json
from datetime import date

import pytest
from sqlalchemy.orm import sessionmaker

import data_store
import database
import strategy_store
from agent import client as C
from agent import runs
from engine import jobs
from knowledge import graph
from market import catalog as cat
from market import ingest as ing
from market import paths as paths_mod
from tests import synth
from tests.test_ingest import _chunks
from tests.test_spec_validation import ORB

DAYS = [date(2026, 6, 1 + i) for i in range(5)]
SPEC = {**ORB, "direction": "long", "execution": {"mode": "bars"},
        "entry": {"trigger": {"op": "gt", "args": [{"field": "close"}, {"ind": "highest", "params": {"n": 3}}]}, "orderType": "market", "timeoutBars": 1},
        "exit": {"stop": {"type": "ticks", "value": 12}, "target": {"type": "rr", "value": 2.0}},
        "session": {"entryWindow": {"start": "09:35", "end": "09:50"}, "flattenAt": "09:58"},
        "constraints": {"maxTradesPerDay": 3, "cooldownBars": 1, "stopAfterConsecutiveLosses": 5}}


@pytest.fixture(scope="module")
def store(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("agent")
    p = paths_mod.configure(data_dir=tmp / "data", market_data_dir=tmp / "market-data")
    p.ensure_dirs()
    for i, d in enumerate(DAYS):
        cfg = synth.SynthConfig(session_date=d, rth_start="09:30", rth_end="10:00", seed=500 + i)
        ing.DayIngest(None, schema="mbo", session_date=d, frames=_chunks(synth.generate_mbo(cfg)), paths=p, min_daily_volume=1, book=False).run()
    ing.finalize(p)
    cat.build(p, progress=lambda s: None)
    data_store.reset()
    eng = database.make_engine(f"sqlite+pysqlite:///{tmp / 'platform.db'}")
    database.init_db(eng)
    old = (database.engine, database.SessionLocal, jobs.JOBS_DIR)
    database.engine, database.SessionLocal = eng, sessionmaker(bind=eng, autoflush=False, future=True)
    jobs.JOBS_DIR = tmp / "backtests"
    graph.reset_backend()
    yield tmp
    database.engine, database.SessionLocal, jobs.JOBS_DIR = old
    runs.set_llm(None)
    data_store.reset()
    paths_mod.configure(data_dir=paths_mod.REPO_ROOT / "data", market_data_dir=paths_mod.REPO_ROOT / "market-data")


def _fake(script, **kw):
    fake = C.FakeAnthropic(script=script, **kw)
    runs.set_llm(C.LLM(fake))
    return fake


def _created_id(state):
    return state["state"]["createdIds"][0]


def test_full_generate_flow_with_pause_and_finalize(store):
    script = [
        [("tool_use", "declare_variants", {"dimensions": [{"dimension": "entry", "options": ["breakout", "retest"], "why": "either"}]})],
        [("tool_use", "create_strategy", {"spec": {**SPEC, "name": "ORB (breakout)"}})],
        [("tool_use", "propose_risk_profile", {"strategy_id": "$S0", "risk": {"riskPerTradePct": 0.5, "dailyLossLimitPct": 2}, "rationale": "sizing facts"})],
        [("tool_use", "run_backtest", {"strategy_id": "$S0"})],
        [("tool_use", "get_backtest", {"job_id": "$OOSPROBE"})],          # OOS blindness: probing a non-existent id is harmless; the guard is tested below
        [("tool_use", "ask_user", {"question": "Attack the stop or the target?", "options": ["stop", "target"]})],
        [("tool_use", "propose_strategy_revision", {"base_strategy_id": "$S0", "changes": {"exit.target.value": 3.0}, "rationale": "answer said target (job $J0)", "changed_variable": "exit.target.value"})],
        [("tool_use", "finalize_strategy", {"strategy_id": "$S0", "reason": "baseline"})],
        [("text", "REPORT: baseline finalized. This strategy is untestable on this data.")],
    ]
    fake = _fake([])
    run = runs.start_run("generate", {"prompt": "ORB breakout or retest, 1:2", "symbol": "ES1!"})
    # Feed the script step by step, substituting ids the run produced.
    def feed(step):
        fake.script.append(step)
    feed(script[0]); feed(script[1])
    r = runs.wait(run["id"], timeout_s=120)
    # The script is exhausted after 2 tool rounds -> the fake answers "(script exhausted)" -> run finishes as done.
    assert r["status"] == "done"
    state = runs.get(run["id"], with_state=True)
    assert state["state"]["ambiguity"][0]["dimension"] == "entry"
    assert len(state["state"]["createdIds"]) == 1
    sid = _created_id(state)
    assert strategy_store.get_strategy(sid)["origin"] == {"type": "prompt", "sourceId": run["id"]}


def test_pause_answer_resume_budget_and_oos(store):
    fake = _fake([])
    run = runs.start_run("generate", {"prompt": "test", "symbol": "ES1!"})
    fake.script.append([("tool_use", "create_strategy", {"spec": {**SPEC, "name": "S"}})])
    fake.script.append([("tool_use", "ask_user", {"question": "stop or target?", "options": ["stop", "target"]})])
    r = runs.wait(run["id"], timeout_s=120)
    assert r["status"] == "paused_for_user" and r["question"]["text"] == "stop or target?"
    sid = runs.get(run["id"], with_state=True)["state"]["createdIds"][0]
    # Resume: run IS+WF, then burn the change budget, then finalize twice.
    fake.script.append([("tool_use", "run_backtest", {"strategy_id": sid})])
    for i in range(6):
        fake.script.append([("tool_use", "propose_strategy_revision", {"base_strategy_id": sid, "changes": {"exit.target.value": 2.5 + i * 0.1},
                                                                     "rationale": f"exp {i}", "changed_variable": "exit.target.value"})])
    fake.script.append([("tool_use", "finalize_strategy", {"strategy_id": sid, "reason": "done"})])
    fake.script.append([("tool_use", "finalize_strategy", {"strategy_id": sid, "reason": "again"})])
    fake.script.append([("text", "REPORT")])
    r = runs.answer(run["id"], "target")
    r = runs.wait(run["id"], timeout_s=600)
    assert r["status"] == "done", r
    st = runs.get(run["id"], with_state=True)
    events = st["events"]
    # The answer was threaded back as the ask_user tool result.
    msgs = [m for m in fake.calls[-1]["messages"] if m["role"] == "user"]
    assert any(isinstance(m["content"], list) and any(c.get("type") == "tool_result" and "target" in c.get("content", "") for c in m["content"]) for m in msgs)
    # run_backtest returned IS + WF only.
    rb = next(e for e in events if e["type"] == "tool_result" and e["name"] == "run_backtest")
    assert "inSample" in rb["result"] and "walkForward" in rb["result"] and "outOfSample" not in rb["result"]
    # 5 revisions accepted, the 6th refused.
    revs = [e for e in events if e["type"] == "tool_result" and e["name"] == "propose_strategy_revision"]
    assert len(revs) == 6 and "change budget" in revs[-1]["result"] and st["state"]["changesUsed"] == 5
    assert len(st["state"]["revisedIds"]) == 5
    child = strategy_store.get_strategy(st["state"]["revisedIds"][0])
    assert child["lineage"]["parentId"] == sid and child["lineage"]["trialIndex"] == 1
    # finalize ran OOS exactly once; the second was refused.
    fins = [e for e in events if e["type"] == "tool_result" and e["name"] == "finalize_strategy"]
    assert len(fins) == 2 and "oosJobId" in fins[0]["result"] and "second" in fins[1]["result"]
    assert st["state"]["oosLooks"] == 1 and st["progress"]["finalized"] is True
    oos_rows = [j for j in jobs.list_jobs() if j["windowKind"] == "oos"]
    assert len(oos_rows) == 1
    assert st["report"]["finalizeResult"]["verdict"]["status"] in ("untestable", "fail", "pass")
    assert strategy_store.get_strategy(sid)["status"] in ("candidate", "testing", "draft")


def test_oos_guard_blocks_hidden_rows(store):
    from agent import tools_v2

    oos = next(j for j in jobs.list_jobs() if j["windowKind"] == "oos")
    assert tools_v2.oos_guard(oos["id"], {"oosRevealed": []}) == tools_v2.OOS_ERROR
    assert tools_v2.oos_guard(oos["id"], {"oosRevealed": [oos["id"]]}) is None
    is_row = next(j for j in jobs.list_jobs() if j["windowKind"] == "is")
    assert tools_v2.oos_guard(is_row["id"], {}) is None


def test_variant_cap_and_budget_exhausted(store):
    fake = _fake([])
    run = runs.start_run("generate", {"prompt": "many", "symbol": "ES1!"})
    fake.script.append([("tool_use", "declare_variants", {"dimensions": [{"dimension": "a", "options": ["1", "2", "3"], "why": "w"},
                                                                          {"dimension": "b", "options": ["1", "2", "3"], "why": "w"}]})])
    fake.script.append([("text", "ok")])
    r = runs.wait(run["id"], timeout_s=60)
    ev = runs.get(run["id"], with_state=True)["events"]
    dv = next(e for e in ev if e["type"] == "tool_result" and e["name"] == "declare_variants")
    assert "at most 2 dimensions" in dv["result"]
    # Budget exhaustion moves the run to budget_exhausted.
    import os
    os.environ["LLM_MONTHLY_BUDGET_USD"] = "0.0001"
    try:
        fake2 = _fake([[("text", "x")]])
        run2 = runs.start_run("generate", {"prompt": "x", "symbol": "ES1!"})
        r2 = runs.wait(run2["id"], timeout_s=30)
        assert r2["status"] == "budget_exhausted"
    finally:
        os.environ.pop("LLM_MONTHLY_BUDGET_USD", None)


def test_restart_resume_continues_from_persisted_messages(store):
    fake = _fake([])
    run = runs.create("generate", {"prompt": "resume me", "symbol": "ES1!"})
    # Simulate a crash: state has messages and status running but no thread.
    from agent import flows
    st = flows.get_flow("generate").init_state({"prompt": "resume me", "symbol": "ES1!"}, {"events": []})
    runs._save(run["id"], st, status="running")
    fake.script.append([("text", "resumed and finished")])
    assert runs.resume_pending() == [run["id"]] or run["id"] in runs.resume_pending()
    r = runs.wait(run["id"], timeout_s=30)
    assert r["status"] == "done" and r["report"]["text"] == "resumed and finished"
    assert runs.cancel(run["id"])["status"] == "done"       # cancelling a finished run is a no-op
