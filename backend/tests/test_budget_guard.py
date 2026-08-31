"""Budget guard, usage logging and the fake client (PLATFORM-SPEC.md §4.9)."""

import pytest
from sqlalchemy.orm import sessionmaker

import database
from agent import client as C


@pytest.fixture()
def db(tmp_path, monkeypatch):
    eng = database.make_engine(f"sqlite+pysqlite:///{tmp_path / 'p.db'}")
    database.init_db(eng)
    monkeypatch.setattr(database, "engine", eng)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=eng, autoflush=False, future=True))
    monkeypatch.setenv("LLM_MONTHLY_BUDGET_USD", "1.0")
    monkeypatch.setenv("LLM_DAILY_RESEARCH_BUDGET_USD", "0.05")
    yield
    eng.dispose()


def test_cost_from_price_table(db):
    table = C.prices()
    assert table["claude-sonnet-5"]["placeholder"] is True
    assert C.estimate_cost("claude-sonnet-5", 1_000_000, 0, table=table) == 3.0
    assert C.estimate_cost("claude-haiku-4-5-20251001", 0, 1_000_000, table=table) == 5.0
    assert C.estimate_cost("claude-sonnet-5", 0, 0, cache_read=1_000_000, table=table) == 0.3


def test_usage_logged_and_cached_system(db):
    fake = C.FakeAnthropic(script=[[("text", "hi")]], tokens_in=100_000, tokens_out=10_000)
    llm = C.LLM(fake)
    r = llm.create(purpose="agent.generate", system="SYS", messages=[{"role": "user", "content": "x"}], tools=[{"name": "t", "description": "d", "input_schema": {"type": "object"}}])
    assert r.content[0].text == "hi"
    kw = fake.calls[0]
    assert kw["system"][0]["cache_control"] == {"type": "ephemeral"} and kw["tools"][-1]["cache_control"] == {"type": "ephemeral"}
    u = C.usage_summary()
    assert u["monthSpendUsd"] == pytest.approx(0.3 + 0.15)   # 100k in @3 + 10k out @15
    assert u["byPurpose"]["agent.generate"]["calls"] == 1 and u["capped"] is False


def test_monthly_cap_raises(db):
    fake = C.FakeAnthropic(script=[[("text", "a")], [("text", "b")], [("text", "c")]], tokens_in=200_000, tokens_out=10_000)
    llm = C.LLM(fake)
    llm.create(purpose="agent.generate", system="S", messages=[{"role": "user", "content": "x"}])   # $0.75
    llm.create(purpose="agent.generate", system="S", messages=[{"role": "user", "content": "x"}])   # $1.50 > 95% of $1
    assert C.usage_summary()["capped"] is True
    with pytest.raises(C.BudgetExhausted):
        llm.create(purpose="agent.generate", system="S", messages=[{"role": "user", "content": "x"}])
    assert len(fake.calls) == 2


def test_daily_research_cap(db):
    fake = C.FakeAnthropic(script=[[("text", "a")], [("text", "b")]], tokens_in=20_000, tokens_out=1000)   # $0.065 on sonnet
    llm = C.LLM(fake)
    llm.create(purpose="research.score", system="S", messages=[{"role": "user", "content": "x"}], tier="reasoning")
    with pytest.raises(C.BudgetExhausted):
        llm.create(purpose="research.summarize", system="S", messages=[{"role": "user", "content": "x"}])
    # Non-research purposes are only bound by the monthly cap.
    llm.create(purpose="chat", system="S", messages=[{"role": "user", "content": "x"}])
