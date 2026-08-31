"""Knowledge graph view: nodes/edges from tags, clusters, central, gaps, detail, routes."""

import pytest
from sqlalchemy.orm import sessionmaker

import database
from knowledge import graph, graph_view, local_store
from models import ResearchQueueItem, ResearchSource, Strategy, new_id, utc_now


@pytest.fixture()
def db(tmp_path, monkeypatch):
    eng = database.make_engine(f"sqlite+pysqlite:///{tmp_path / 'p.db'}")
    database.init_db(eng)
    monkeypatch.setattr(database, "engine", eng)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=eng, autoflush=False, future=True))
    graph.reset_backend()
    yield
    eng.dispose()


def _seed():
    with database.session_scope() as s:
        s.add(ResearchSource(id="src1", url="https://arxiv.org/abs/1", domain="arxiv.org", title="Paper", tier=1, credibility=1.0, fetched_at=utc_now()))
        s.add(ResearchSource(id="src2", url="https://blog.example/x", domain="blog.example", title="Blog", tier=3, credibility=0.3, fetched_at=utc_now()))
        s.add(ResearchQueueItem(id="q1", topic="order flow basics", priority=1, status="done", requested_by="seed", created_at=utc_now()))
        s.add(ResearchQueueItem(id="q2", topic="walk-forward validation", priority=1, status="queued", requested_by="seed", created_at=utc_now()))
        s.add(Strategy(id="abc123abc123", name="ORB", status="draft", created_at=utc_now(), updated_at=utc_now(), spec_json={}))
    src1 = {"id": "src1", "title": "Paper", "url": "https://arxiv.org/abs/1"}
    src2 = {"id": "src2", "title": "Blog", "url": "https://blog.example/x"}
    for i in range(4):
        graph.record_fact(f"Absorption at the value area low precedes reversals ({i}).", source=src1, credibility=1.0,
                          tags=["absorption", "value area", "reversal", "ES", "order flow basics"], evidence_type="backtest")
    for i in range(3):
        graph.record_fact(f"Stacked imbalances mark aggressive order flow ({i}).", source=src2, credibility=0.3,
                          tags=["stacked imbalance", "order-flow", "ES", "order flow basics"], evidence_type="anecdote")
    graph.record_fact("Kelly fraction: the bet size maximising log growth.", source=src1, credibility=1.0, tags=["definition", "kelly fraction", "position sizing"], evidence_type="theory")
    graph.record_fact("Kelly fraction sizing halves drawdown at half-Kelly.", source=src1, credibility=1.0, tags=["kelly fraction", "position sizing", "drawdown"], evidence_type="backtest")
    graph.record_experiment("abc123abc123", None, "exit.target.value", "wider target", {"expectancyR": 0.1})


def test_build_nodes_edges_clusters(db):
    _seed()
    g = graph_view.build()
    ids = {n["id"] for n in g["nodes"]}
    assert {"c:absorption", "c:value area", "c:order flow", "i:es", "t:order flow basics", "s:src1", "s:src2", "g:abc123abc123"} <= ids
    assert "c:definition" not in ids and not any(n["label"] == "abc123abc123" for n in g["nodes"])
    absorption = next(n for n in g["nodes"] if n["id"] == "c:absorption")
    assert absorption["facts"] == 4 and absorption["credibility"] == 1.0 and absorption["type"] == "concept"
    kelly = next(n for n in g["nodes"] if n["id"] == "c:kelly fraction")
    assert kelly["definition"].startswith("the bet size")
    e = next(e for e in g["edges"] if {e["source"], e["target"]} == {"c:absorption", "c:value area"})
    assert e["weight"] == 4 and len(e["factIds"]) == 4 and e["type"] == "cooccur"
    se = next(e for e in g["edges"] if {e["source"], e["target"]} == {"s:src1", "c:absorption"})
    assert se["type"] == "mentions" and se["weight"] == 4
    # hyphen and space variants merge
    assert next(n for n in g["nodes"] if n["id"] == "c:order flow")["facts"] == 3
    # absorption / value area / reversal vs stacked imbalance / order flow vs kelly / sizing / drawdown -> separate communities
    cl = {n["id"]: n.get("cluster") for n in g["nodes"]}
    assert cl["c:absorption"] == cl["c:value area"] == cl["c:reversal"]
    assert cl["c:kelly fraction"] == cl["c:position sizing"] == cl["c:drawdown"] != cl["c:absorption"]
    assert cl["c:stacked imbalance"] == cl["c:order flow"]
    assert cl["s:src1"] == cl["c:absorption"]     # a source sits with the cluster it feeds most
    assert g["stats"]["facts"] == 10 and g["stats"]["clusters"] >= 3 and g["stats"]["byType"]["source"] == 2
    names = [c["name"] for c in g["clusters"]]
    assert names[0].startswith("absorption")
    assert g["central"][0]["label"] in ("absorption", "value area", "reversal")
    assert any(h["label"] == "es" for h in g["hubs"]) and not any(c["label"] == "es" for c in g["central"])


def test_gaps_and_filters(db):
    _seed()
    g = graph_view.build()
    kinds = {(x["kind"], x["label"]) for x in g["gaps"]}
    assert ("unread_topic", "walk-forward validation") in kinds
    assert ("low_credibility", "stacked imbalance") in kinds or ("low_credibility", "order flow") in kinds
    assert not any(x["label"] == "order flow basics" and x["kind"] == "unread_topic" for x in g["gaps"])
    strong = graph_view.build(min_credibility=0.9)
    assert "c:stacked imbalance" not in {n["id"] for n in strong["nodes"]} and "c:absorption" in {n["id"] for n in strong["nodes"]}
    t1 = graph_view.build(tiers=(1,))
    assert "s:src2" not in {n["id"] for n in t1["nodes"]}
    nosrc = graph_view.build(include_sources=False)
    assert not any(n["type"] == "source" for n in nosrc["nodes"])
    exp = graph_view.build(kinds=("experiment",))
    assert {n["type"] for n in exp["nodes"]} <= {"strategy", "concept"} and exp["stats"]["facts"] == 1


def test_node_detail_and_routes(db, client):
    _seed()
    d = graph_view.node_detail("c:absorption")
    assert len(d["facts"]) == 4 and d["facts"][0]["credibility"] == 1.0
    assert d["neighbours"][0]["label"] in ("value area", "reversal", "es", "order flow basics", "Paper")
    assert graph_view.node_detail("c:nope") is None
    r = client.get("/api/knowledge/graph")
    assert r.status_code == 200 and set(r.json()) == {"nodes", "edges", "clusters", "central", "hubs", "gaps", "stats"}
    r = client.get("/api/knowledge/graph?kinds=claim,fact&tiers=1&min_credibility=0.5&sources=false")
    assert r.status_code == 200
    assert client.get("/api/knowledge/graph/node/c:nothing").status_code == 404
    assert client.get("/api/knowledge/facts?ids=x,y").json() == []
