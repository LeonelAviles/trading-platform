"""Source scoring rubric + ingestion with the LLM mocked (PLATFORM-SPEC.md §4.8)."""

import json

import pytest
from sqlalchemy.orm import sessionmaker

import database
from agent import client as C
from agent import research
from knowledge import graph, local_store


@pytest.fixture()
def db(tmp_path, monkeypatch):
    eng = database.make_engine(f"sqlite+pysqlite:///{tmp_path / 'p.db'}")
    database.init_db(eng)
    monkeypatch.setattr(database, "engine", eng)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=eng, autoflush=False, future=True))
    graph.reset_backend()
    from market import paths as pm
    pm.configure(data_dir=tmp_path / "data", market_data_dir=tmp_path / "md")
    yield
    pm.configure(data_dir=pm.REPO_ROOT / "data", market_data_dir=pm.REPO_ROOT / "market-data")
    eng.dispose()


def test_credibility_rubric():
    assert research.credibility_from({"tier": 1}) == 1.0
    assert research.credibility_from({"tier": 2, "hasData": True, "hasCitations": True}) == 0.85
    assert research.credibility_from({"tier": 3, "conflictOfInterest": True}) == 0.25
    assert research.credibility_from({"tier": 4, "hasData": True}) == 0.0
    assert research.credibility_from({"tier": 1, "isMicrostructureClaim": True, "yearPublished": 2005}) == 0.9


def _scored(tier, **kw):
    return json.dumps({"tier": tier, "hasData": True, "hasCitations": False, "conflictOfInterest": False, "yearPublished": 2022,
                       "isMicrostructureClaim": False, "summary": "s", "reason": "r", **kw})


def _summary(n=2):
    return json.dumps({"claims": [{"text": f"Claim number {i}: absorption at the value area low precedes reversals.", "evidenceType": "backtest",
                                   "tags": ["absorption"], "instruments": ["ES"], "regimes": []} for i in range(n)],
                       "definitions": [{"term": "Initial balance", "definition": "the first hour's range of the RTH session"}],
                       "parameters": [], "caveats": ["small sample"]})


def test_ingest_scores_and_records_facts(db):
    fake = C.FakeAnthropic(script=[[("text", _scored(2))], [("text", _summary())]])
    llm = C.LLM(fake)
    r = research.ingest_document("https://quantblog.example/absorption", "Absorption study", "x" * 800, "footprint absorption", llm)
    assert r["tier"] == 2 and r["credibility"] == 0.8 and r["facts"] == 3
    hits = graph.search("absorption value area low", k=5)
    assert hits and hits[0]["credibility"] == 0.8 and hits[0]["source"] == "Absorption study"
    src = research.sources()[0]
    assert src["tier"] == 2 and src["scored"]["hasData"] is True
    # Same source + topic is not ingested twice.
    assert research.ingest_document("https://quantblog.example/absorption", "Absorption study", "x" * 800, "footprint absorption", llm).get("skipped")


def test_tier4_blocked_and_corroboration(db):
    fake = C.FakeAnthropic(script=[[("text", _scored(4))]])
    r = research.ingest_document("https://promo.example/signals", "Best signals", "buy now " * 200, "topic", C.LLM(fake))
    assert r["blocked"] and local_store.count() == 0
    # A tier-1 source saying the same thing as an existing tier-2 claim raises credibility by 0.1.
    local_store.add("Absorption at the value area low precedes reversals in ES.", kind="claim", credibility=0.75, source_url="http://other")
    assert research.corroborate("Absorption at the value area low precedes reversals in ES.", 0.8, "me") == pytest.approx(0.9)
    assert research.corroborate("Completely unrelated statement about lunar cycles.", 0.8, "me") == 0.8


def test_run_topic_with_fake_search_and_fetch(db):
    script = [[("text", _scored(1))], [("text", _summary(1))], [("text", _scored(3))], [("text", _summary(1))]]
    llm = C.LLM(C.FakeAnthropic(script=script))
    research.seed_queue()
    q = research.queue()
    assert len(q) >= 10 and q[0]["requestedBy"] == "seed"
    tid = q[0]["id"]
    pages = {"https://a.example/p": ("Paper", "p " * 500), "https://b.example/blog": ("Blog", "b " * 500), "https://c.example/empty": ("", "")}
    out = research.run_topic(tid, llm, fetch=lambda u: pages[u], search=lambda topic, llm_: [{"url": u, "title": t[0]} for u, t in pages.items()])
    assert out["status"] == "done" and len(out["sources"]) == 2 and any(e["error"] == "no text" for e in out["errors"])
    assert [s["credibility"] for s in out["sources"]] == [1.0, 0.5]
    assert research.queue()[-1]["status"] == "done" or any(x["status"] == "done" for x in research.queue())


def test_budget_stops_topic_and_requeues(db, monkeypatch):
    monkeypatch.setenv("LLM_DAILY_RESEARCH_BUDGET_USD", "0.0001")
    llm = C.LLM(C.FakeAnthropic(script=[[("text", _scored(1))]], tokens_in=50_000))
    t = research.enqueue("something")
    out = research.run_topic(t["id"], llm, fetch=lambda u: ("T", "x" * 800), search=lambda topic, llm_: [{"url": "https://x/1", "title": "T"}])
    assert out["status"] == "queued" and any("budget" in e.get("error", "") for e in out["errors"])
