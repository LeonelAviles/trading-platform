"""Owner-provided sources, trusted-domain tiers and the self-study scheduler
(the three additions on top of the Phase 4 research worker)."""

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import sessionmaker

import database
from agent import client as C
from agent import research
from knowledge import graph, local_store
from tests.test_source_scoring import _scored, _summary


@pytest.fixture()
def db(tmp_path, monkeypatch):
    eng = database.make_engine(f"sqlite+pysqlite:///{tmp_path / 'p.db'}")
    database.init_db(eng)
    monkeypatch.setattr(database, "engine", eng)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=eng, autoflush=False, future=True))
    graph.reset_backend()
    from market import paths as pm

    pm.configure(data_dir=tmp_path / "data", market_data_dir=tmp_path / "md")
    research._source_jobs.clear()
    yield
    pm.configure(data_dir=pm.REPO_ROOT / "data", market_data_dir=pm.REPO_ROOT / "market-data")
    eng.dispose()


def test_domain_rules_override_the_model(db):
    assert research.domain_rule("https://arxiv.org/abs/2101.00001") == "tier1"
    assert research.domain_rule("https://papers.ssrn.com/sol3/papers.cfm?id=1") == "tier1"
    assert research.domain_rule("https://www.quantpedia.com/strategies/x") == "tier2"
    assert research.domain_rule("https://randomblog.example/post") is None
    # model says blog (3): a tier-1 domain wins; a tier-2 domain caps at 2; blocked forces 4
    assert research.apply_domain_rules("https://arxiv.org/abs/1", {"tier": 3})["tier"] == 1
    assert research.apply_domain_rules("https://quantpedia.com/x", {"tier": 3})["tier"] == 2
    assert research.apply_domain_rules("https://quantpedia.com/x", {"tier": 1})["tier"] == 1
    research.update_settings({"trustedDomains": {"blocked": "spamsignals.example, www.pump.example/"}})
    out = research.apply_domain_rules("https://www.spamsignals.example/buy", {"tier": 2, "hasData": True})
    assert out["tier"] == 4 and out["credibility"] == 0.0 and out["domainRule"] == "blocked"
    # settings round-trip and normalisation
    s = research.update_settings({"trustedDomains": {"tier1": ["https://www.myjournal.example/path", "arxiv.org"]}, "intervalHours": 3, "topicsPerRun": 99})
    assert s["trustedDomains"]["tier1"][:2] == ["myjournal.example", "arxiv.org"] and s["intervalHours"] == 3 and s["topicsPerRun"] == 10
    assert research.domain_rule("https://sub.myjournal.example/x") == "tier1"


def test_score_source_uses_domain_rules(db):
    fake = C.FakeAnthropic(script=[[("text", _scored(3))]])
    scored = research.score_source("https://arxiv.org/abs/2101.00001", "paper", "x" * 1000, C.LLM(fake))
    assert scored["tier"] == 1 and scored["domainRule"] == "tier1" and scored["credibility"] >= 1.0


def test_owner_pasted_text_becomes_owner_tagged_facts(db):
    fake = C.FakeAnthropic(script=[[("text", _scored(2))], [("text", _summary(2))]])
    job = research.add_source(text="Absorption at the value area low precedes reversals. " * 20, title="My notes", topic="footprint absorption",
                              llm=C.LLM(fake), background=False)
    assert job["status"] == "done", job
    assert job["url"].startswith("owner://") and job["result"]["facts"] == 3
    src = research.sources()[0]
    assert src["scored"]["providedBy"] == "user" and src["title"] == "My notes" and src["tier"] == 2
    hits = local_store.search("absorption value area low reversal", k=5, min_credibility=0.0)
    assert hits and all("owner" in (h.get("tags") or []) for h in hits)
    assert all("footprint absorption" in (h.get("tags") or []) for h in hits)


def test_owner_url_uses_fetch_and_rejects_bad_input(db):
    fake = C.FakeAnthropic(script=[[("text", _scored(3))], [("text", _summary(1))]])
    job = research.add_source(url="https://blog.example/orb", llm=C.LLM(fake), background=False,
                              fetch=lambda u: ("ORB post", "opening range breakout text " * 40))
    assert job["status"] == "done" and job["topic"] == research.OWNER_TOPIC and job["result"]["tier"] == 3
    with pytest.raises(ValueError):
        research.add_source(url="ftp://x")
    with pytest.raises(ValueError):
        research.add_source()
    short = research.add_source(url="https://blog.example/empty", llm=C.LLM(fake), background=False, fetch=lambda u: ("", "tiny"))
    assert short["status"] == "error" and "200 characters" in short["error"]
    assert [j["id"] for j in research.source_jobs()][:2] == [short["id"], job["id"]]


def test_upload_text_and_pdf_extraction(db):
    title, text = research.text_from_upload(b"plain notes about VWAP " * 20, "text/plain", "notes.txt")
    assert title == "notes.txt" and text.startswith("plain notes")
    from io import BytesIO

    from pypdf import PdfWriter

    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    buf = BytesIO()
    w.write(buf)
    title, text = research.text_from_upload(buf.getvalue(), "application/pdf", "paper.pdf")
    assert title == "paper.pdf" and text.strip() == ""


def test_autorun_tick_decisions(db, monkeypatch):
    calls = []

    def start(n, by):
        calls.append((n, by))
        return {"started": True}

    now = datetime.now(timezone.utc)   # _record_run stamps the wall clock, so offsets are relative to it
    assert research.autorun_tick(now, start)["reason"] == "disabled"
    research.update_settings({"autoRun": True, "intervalHours": 6, "topicsPerRun": 2})
    assert research.autorun_tick(now, start)["reason"] == "queue empty" and not calls
    research.seed_queue()
    assert research.autorun_tick(now, start)["ran"] and calls == [(2, "autorun")]
    # a finished run stamps lastRunAt; the next tick inside the interval waits, after it runs again
    research._record_run("autorun", [{"topic": "t", "status": "done", "sources": [{"facts": 3}], "errors": []}])
    st = research.autorun_status(now)
    assert st["lastResult"]["facts"] == 3 and st["nextRunAt"] and st["enabled"]
    assert research.autorun_tick(now + timedelta(hours=1), start)["reason"] == "not due"
    assert research.autorun_tick(now + timedelta(hours=7), start)["ran"] and len(calls) == 2
    # the daily research cap stops it and is recorded as the skip reason
    monkeypatch.setattr(C, "usage_summary", lambda: {"researchCapped": True})
    out = research.autorun_tick(now + timedelta(hours=14), start)
    assert out["reason"] == "daily research budget spent" and len(calls) == 2
    assert research.autorun_status()["skipped"]["reason"] == "daily research budget spent"


def test_routes(client):
    r = client.get("/api/research/settings")
    assert r.status_code == 200 and "arxiv.org" in r.json()["trustedDomains"]["tier1"] and r.json()["autoRun"] is False
    r = client.put("/api/research/settings", json={"autoRun": True, "intervalHours": 12, "trustedDomains": {"tier2": "goodblog.example"}})
    assert r.json()["autoRun"] is True and r.json()["intervalHours"] == 12 and r.json()["trustedDomains"]["tier2"] == ["goodblog.example"]
    a = client.get("/api/research/autorun").json()
    assert a["enabled"] and a["nextRunAt"] and a["running"] is False
    assert client.post("/api/research/sources", json={}).status_code == 400
    assert client.post("/api/research/sources", json={"url": "notaurl"}).status_code == 400
    assert client.post("/api/research/sources/upload", content=b"").status_code == 400
    s = client.get("/api/research/sources").json()
    assert set(s) == {"sources", "jobs"}
    st = client.get("/api/research/status").json()
    assert "autorun" in st and st["autorun"]["enabled"]


def test_min_tier_gate_blocks_tier3(db):
    # default: tiers 1-2 acceptable; a tier-3 blog is scored but yields no facts
    fake = C.FakeAnthropic(script=[[("text", _scored(3))]])
    out = research.ingest_document("https://blog.example/post", "Blog", "x" * 800, "topic", C.LLM(fake))
    assert out["blocked"] and out["tier"] == 3 and "below the accepted tier" in out["reason"]
    assert local_store.count() == 0
    src = research.sources()[0]
    assert src["tier"] == 3 and src["credibility"] is not None   # scored, kept for the sources table
    # tier 2 passes
    fake = C.FakeAnthropic(script=[[("text", _scored(2))], [("text", _summary(1))]])
    out = research.ingest_document("https://quantblog.example/x", "Quant", "x" * 800, "topic", C.LLM(fake))
    assert out.get("facts") == 2
    # tier 3 cannot be accepted: the setting is hard-capped at 2 (owner decision)
    assert research.update_settings({"minTier": 3})["minTier"] == 2
    fake = C.FakeAnthropic(script=[[("text", _scored(3))]])
    out = research.ingest_document("https://blog.example/post2", "Blog2", "x" * 800, "topic", C.LLM(fake))
    assert out["blocked"] and out["tier"] == 3
    assert research.update_settings({"minTier": 9})["minTier"] == 2
    assert research.update_settings({"minTier": 0})["minTier"] == 1


def test_prune_below_tier(db):
    fake = C.FakeAnthropic(script=[[("text", _scored(2))], [("text", _summary(2))], [("text", _scored(1))], [("text", _summary(1))]])
    llm = C.LLM(fake)
    research.ingest_document("https://quantblog.example/a", "Blog", "x" * 800, "t", llm)
    research.ingest_document("https://arxiv.org/abs/9", "Paper", "x" * 800, "t", llm)
    assert local_store.count() == 5
    out = research.prune_below_tier(1)
    assert out["invalidated"] == 3
    assert local_store.count() == 2   # only the tier-1 paper's facts remain live


def test_credit_failure_leaves_topic_queued(db):
    research.seed_queue()
    tid = research.queue()[0]["id"]

    def broken_search(topic, llm, max_uses=5):
        raise RuntimeError("Your credit balance is too low to access the Anthropic API")

    r = research.run_topic(tid, C.LLM(C.FakeAnthropic(script=[])), search=broken_search)
    assert r["status"] == "queued" and "credit balance" in r["errors"][0]["error"]
    assert next(t for t in research.queue() if t["id"] == tid)["status"] == "queued"

    def broken_other(topic, llm, max_uses=5):
        raise RuntimeError("boom")

    r = research.run_topic(tid, C.LLM(C.FakeAnthropic(script=[])), search=broken_other)
    assert r["status"] == "error"
