import pytest
from sqlalchemy.orm import sessionmaker

import database
from knowledge import embedder, graph, local_store


@pytest.fixture()
def db(tmp_path, monkeypatch):
    eng = database.make_engine(f"sqlite+pysqlite:///{tmp_path / 'p.db'}")
    database.init_db(eng)
    monkeypatch.setattr(database, "engine", eng)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=eng, autoflush=False, future=True))
    graph.reset_backend()
    yield
    eng.dispose()


def test_hash_embedder_is_deterministic_and_normalised():
    a, b = embedder.hash_embed("opening range breakout"), embedder.hash_embed("opening range breakout")
    assert a == b and abs(sum(x * x for x in a) - 1) < 1e-9
    assert embedder.cosine(a, embedder.hash_embed("opening range retest")) > embedder.cosine(a, embedder.hash_embed("kelly fraction sizing"))


def test_store_search_credibility_and_format(db):
    assert graph.backend() == "local"
    f1 = graph.record_fact("Position sizing of 0.5% risk per trade keeps risk of ruin low for retail futures accounts.",
                           source={"id": None, "title": "Tharp", "url": "http://x"}, credibility=0.8, tags=["sizing"], evidence_type="theory")
    graph.record_fact("Prop-firm marketing claims 20% monthly returns are typical.", source={"title": "promo"}, credibility=0.2)
    graph.record_note("On ES in April–June a rel_volume filter raised PF from 1.1 to 1.4", tags=["ORB"], ref_id="abc")
    hits = graph.search("risk per trade position sizing", k=5)
    assert hits and hits[0]["id"] == f1["id"] and hits[0]["credibility"] == 0.8
    assert not any("Prop-firm" in h["text"] for h in hits)            # below 0.4 -> hypothesis, filtered
    hyp = local_store.search("prop firm monthly returns", k=5, min_credibility=0.0)
    assert hyp and hyp[0]["kind"] == "hypothesis"
    block = local_store.format_facts(hits)
    assert "credibility 0.80" in block and "Tharp" in block
    assert local_store.invalidate(f1["id"]) and not any(h["id"] == f1["id"] for h in graph.search("position sizing", k=5))
    assert graph.status()["backend"] == "local" and graph.status()["facts"] == 2   # live facts only — the invalidated one no longer counts


def test_experiment_and_finding_records(db):
    e = graph.record_experiment("s1", "s0", "exit.target.value", "3R instead of 2R", {"trades": 50, "profitFactor": 1.2, "expectancyR": 0.1, "maxDrawdownPct": 4})
    assert e["kind"] == "experiment" and "changed exit.target.value" in e["text"]
    f = graph.record_finding("s1", "b1", "order-flow", "winners have positive bar delta", 0.7)
    assert f["credibility"] == 0.7
    assert any(h["kind"] == "experiment" for h in graph.search("target 3R experiment", k=5, min_credibility=0.0))
