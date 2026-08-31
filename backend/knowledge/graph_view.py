"""The knowledge graph as a picture: nodes, edges, clusters, central concepts
and gaps, built from the local fact store (which mirrors everything that goes
to Neo4j, so it is complete in both backends).

Nodes: concepts (the tags on facts), topics (queue topics the worker ran),
instruments, regimes, sources, strategies (facts with a strategy ref_id) and
teaching sessions. Edges: two concept-like nodes that appear on the same fact
(weight = number of facts), source → concept, strategy/session → concept.
Clusters come from weighted label propagation (deterministic); centrality is
weighted degree plus how many clusters a node bridges; gaps are thin clusters,
isolated concepts and queued topics with no facts yet."""

from __future__ import annotations

import re
from collections import Counter, defaultdict

import database
from models import KnowledgeFact, ResearchQueueItem, ResearchSource, Strategy, TeachingSession

META_TAGS = {"definition", "all", "owner", "experiment", "finding", "note", "teaching", "general", "misc", "other"}
INSTRUMENTS = {"es", "nq", "ym", "rty", "cl", "gc", "zn", "zb", "6e", "btc", "eth", "mes", "mnq", "crypto", "futures", "equities", "fx"}
REGIMES = {"trend", "trend day", "range", "rotational", "volatile", "vol high", "vol mid", "vol low", "high volatility", "low volatility",
           "balance", "imbalance", "trending", "choppy", "consolidation"}
MAX_FACTS_PER_EDGE = 12


def norm(tag: str) -> str:
    t = re.sub(r"[-_/]+", " ", str(tag).strip().lower())
    t = re.sub(r"\s+", " ", t).strip()
    return t


HEX_ID = re.compile(r"^[0-9a-f]{12}$")


def _node_type(tag: str, topics: set[str]) -> str | None:
    if not tag or tag in META_TAGS or len(tag) < 2 or HEX_ID.match(tag):
        return None
    if tag in topics:
        return "topic"
    if tag in INSTRUMENTS:
        return "instrument"
    if tag in REGIMES:
        return "regime"
    return "concept"


def _facts(min_credibility: float, kinds: tuple[str, ...] | None) -> list[dict]:
    with database.session_scope() as db:
        q = db.query(KnowledgeFact).filter(KnowledgeFact.invalid_at.is_(None))
        if kinds:
            q = q.filter(KnowledgeFact.kind.in_(kinds))
        rows = q.order_by(KnowledgeFact.created_at).all()
        return [{"id": r.id, "kind": r.kind, "text": r.text, "tags": list(r.tags_json or []), "credibility": round(r.credibility, 3),
                 "sourceId": r.source_id, "source": r.source_title or r.source_url, "sourceUrl": r.source_url,
                 "evidenceType": r.evidence_type, "refId": r.ref_id, "createdAt": r.created_at}
                for r in rows if r.credibility >= min_credibility or r.kind in ("note", "experiment", "finding", "teaching")]


def _context() -> tuple[set[str], dict[str, dict], dict[str, dict], dict[str, dict], list[dict]]:
    with database.session_scope() as db:
        queue = db.query(ResearchQueueItem).all()
        topics = {norm(r.topic) for r in queue}
        queued = [{"id": r.id, "topic": r.topic, "status": r.status} for r in queue]
        sources = {r.id: {"id": r.id, "title": r.title or r.url, "url": r.url, "domain": r.domain, "tier": r.tier, "credibility": r.credibility,
                          "providedBy": (r.scored_json or {}).get("providedBy")} for r in db.query(ResearchSource).all()}
        strategies = {r.id: {"id": r.id, "name": r.name, "status": r.status} for r in db.query(Strategy).all()}
        sessions = {r.id: {"id": r.id, "symbol": r.symbol, "status": r.status} for r in db.query(TeachingSession).all()}
    return topics, sources, strategies, sessions, queued


def build(*, min_credibility: float = 0.0, kinds: tuple[str, ...] | None = None, tiers: tuple[int, ...] | None = None,
          include_sources: bool = True, max_nodes: int = 600) -> dict:
    topics, sources, strategies, sessions, queued = _context()
    facts = _facts(min_credibility, kinds)
    if tiers:
        allowed = {sid for sid, s in sources.items() if s.get("tier") in tiers}
        facts = [f for f in facts if f["sourceId"] in allowed or (f["sourceId"] is None and f["kind"] in ("experiment", "finding", "note", "teaching"))]

    nodes: dict[str, dict] = {}
    edges: dict[tuple[str, str], dict] = {}
    node_facts: dict[str, list[str]] = defaultdict(list)

    def add_node(nid: str, ntype: str, label: str, **extra) -> dict:
        n = nodes.get(nid)
        if n is None:
            n = nodes[nid] = {"id": nid, "type": ntype, "label": label, "facts": 0, "credibility": 0.0, **extra}
        return n

    def add_edge(a: str, b: str, fact_id: str, etype: str = "cooccur") -> None:
        if a == b:
            return
        key = (a, b) if a < b else (b, a)
        e = edges.get(key)
        if e is None:
            e = edges[key] = {"source": key[0], "target": key[1], "weight": 0, "type": etype, "factIds": []}
        e["weight"] += 1
        if len(e["factIds"]) < MAX_FACTS_PER_EDGE:
            e["factIds"].append(fact_id)

    for f in facts:
        tags = []
        for t in f["tags"]:
            nt = norm(t)
            ty = _node_type(nt, topics)
            if ty and nt not in tags:
                tags.append(nt)
        if f["kind"] == "claim" and f["evidenceType"] == "theory" and ": " in f["text"] and "definition" in {norm(t) for t in f["tags"]}:
            term = norm(f["text"].split(": ", 1)[0])
            if _node_type(term, topics) == "concept" and len(term) <= 40:
                if term not in tags:
                    tags.insert(0, term)
                add_node(f"c:{term}", "concept", term)["definition"] = f["text"].split(": ", 1)[1][:200]
        ids = []
        for t in tags:
            ty = _node_type(t, topics)
            nid = f"{ty[0]}:{t}"
            n = add_node(nid, ty, t)
            n["facts"] += 1
            n["credibility"] += f["credibility"]
            node_facts[nid].append(f["id"])
            ids.append(nid)
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                add_edge(ids[i], ids[j], f["id"])
        anchors = []
        if include_sources and f["sourceId"] and f["sourceId"] in sources:
            s = sources[f["sourceId"]]
            sid = f"s:{s['id']}"
            n = add_node(sid, "source", (s["title"] or s["url"] or "")[:60], tier=s["tier"], url=s["url"], domain=s["domain"], providedBy=s["providedBy"])
            n["facts"] += 1
            n["credibility"] += f["credibility"]
            node_facts[sid].append(f["id"])
            anchors.append(sid)
        if f["refId"]:
            if f["refId"] in strategies:
                st = strategies[f["refId"]]
                nid = f"g:{st['id']}"
                n = add_node(nid, "strategy", st["name"], status=st["status"])
            elif f["refId"] in sessions:
                se = sessions[f["refId"]]
                nid = f"t:{se['id']}"
                n = add_node(nid, "teaching", f"teaching {se['symbol']} {se['id'][:6]}", status=se["status"])
            else:
                nid = None
            if nid:
                n["facts"] += 1
                n["credibility"] += f["credibility"]
                node_facts[nid].append(f["id"])
                anchors.append(nid)
        for a in anchors:
            for cid in ids:
                add_edge(a, cid, f["id"], "mentions")

    for nid, n in nodes.items():
        n["credibility"] = round(n["credibility"] / n["facts"], 3) if n["facts"] else 0.0
        n["factIds"] = node_facts.get(nid, [])[:200]

    # Keep the graph drawable: drop the least-mentioned concept nodes past max_nodes.
    if len(nodes) > max_nodes:
        keep = sorted(nodes.values(), key=lambda n: (n["type"] not in ("concept", "topic", "regime", "instrument"), -n["facts"]))[:max_nodes]
        keep_ids = {n["id"] for n in keep}
        nodes = {k: v for k, v in nodes.items() if k in keep_ids}
        edges = {k: e for k, e in edges.items() if e["source"] in keep_ids and e["target"] in keep_ids}

    concept_types = {"concept", "topic", "regime", "instrument"}
    cluster_types = {"concept", "regime"}
    clusters = _clusters({k: v for k, v in nodes.items() if v["type"] in cluster_types},
                         [e for e in edges.values() if nodes[e["source"]]["type"] in cluster_types and nodes[e["target"]]["type"] in cluster_types])
    for cid, members in clusters.items():
        for m in members:
            nodes[m]["cluster"] = cid
    for n in nodes.values():
        if n["type"] not in cluster_types:
            neigh = [nodes[e["source"] if e["target"] == n["id"] else e["target"]] for e in edges.values() if n["id"] in (e["source"], e["target"])]
            counts = Counter(x.get("cluster") for x in neigh if x.get("cluster") is not None)
            n["cluster"] = counts.most_common(1)[0][0] if counts else None

    central = _central(nodes, edges, cluster_types)
    hubs = sorted([n for n in nodes.values() if n["type"] in ("topic", "instrument")], key=lambda n: -n["facts"])
    hubs = [{"id": n["id"], "label": n["label"], "type": n["type"], "facts": n["facts"]} for n in hubs[:12]]
    cluster_list = _cluster_list(nodes, edges, clusters, central)
    gaps = _gaps(nodes, edges, cluster_list, queued, facts)
    stats = {
        "facts": len(facts), "nodes": len(nodes), "edges": len(edges), "clusters": len(clusters),
        "byType": dict(Counter(n["type"] for n in nodes.values())),
        "byKind": dict(Counter(f["kind"] for f in facts)),
        "sources": len({f["sourceId"] for f in facts if f["sourceId"]}),
        "avgCredibility": round(sum(f["credibility"] for f in facts) / len(facts), 3) if facts else None,
        "density": round(2 * len(edges) / (len(nodes) * (len(nodes) - 1)), 4) if len(nodes) > 1 else 0.0,
    }
    return {"nodes": list(nodes.values()), "edges": list(edges.values()), "clusters": cluster_list, "central": central, "hubs": hubs,
            "gaps": gaps, "stats": stats}


def _clusters(nodes: dict[str, dict], edges: list[dict], rounds: int = 30) -> dict[int, list[str]]:
    """Communities of the concept graph: Louvain (networkx, seeded) when it is
    importable, otherwise deterministic weighted label propagation."""
    if not nodes:
        return {}
    adj: dict[str, dict[str, int]] = defaultdict(dict)
    for e in edges:
        adj[e["source"]][e["target"]] = e["weight"]
        adj[e["target"]][e["source"]] = e["weight"]
    order = sorted(nodes)
    label = {nid: i for i, nid in enumerate(order)}
    try:
        import networkx as nx

        g = nx.Graph()
        g.add_nodes_from(order)
        g.add_weighted_edges_from((e["source"], e["target"], e["weight"]) for e in edges)
        for i, comm in enumerate(nx.community.louvain_communities(g, weight="weight", seed=7, resolution=1.0)):
            for nid in comm:
                label[nid] = i
        rounds = 0
    except ImportError:
        pass
    for _ in range(rounds):
        changed = False
        for nid in order:
            if not adj[nid]:
                continue
            score: dict[int, float] = defaultdict(float)
            for other, w in adj[nid].items():
                score[label[other]] += w
            best = min(score.items(), key=lambda kv: (-kv[1], kv[0]))[0]
            if best != label[nid]:
                label[nid] = best
                changed = True
        if not changed:
            break
    groups: dict[int, list[str]] = defaultdict(list)
    for nid in order:
        groups[label[nid]].append(nid)
    # Merge singletons that have any neighbour into that neighbour's cluster; renumber by size.
    for lab, members in list(groups.items()):
        if len(members) == 1 and adj[members[0]]:
            nid = members[0]
            target = max(adj[nid].items(), key=lambda kv: kv[1])[0]
            groups[label[target]].append(nid)
            label[nid] = label[target]
            del groups[lab]
    ordered = sorted(groups.values(), key=lambda m: (-sum(nodes[x]["facts"] for x in m), m[0]))
    return {i: sorted(m) for i, m in enumerate(ordered)}


def _central(nodes: dict[str, dict], edges: dict, concept_types: set[str], k: int = 12) -> list[dict]:
    deg: dict[str, float] = defaultdict(float)
    bridges: dict[str, set] = defaultdict(set)
    for e in edges.values():
        a, b = nodes[e["source"]], nodes[e["target"]]
        if a["type"] in concept_types and b["type"] in concept_types:
            deg[a["id"]] += e["weight"]
            deg[b["id"]] += e["weight"]
            if a.get("cluster") is not None:
                bridges[b["id"]].add(a["cluster"])
            if b.get("cluster") is not None:
                bridges[a["id"]].add(b["cluster"])
    out = []
    for nid, d in deg.items():
        n = nodes[nid]
        out.append({"id": nid, "label": n["label"], "type": n["type"], "weightedDegree": d, "facts": n["facts"],
                    "bridges": len(bridges[nid] - ({n.get("cluster")} if n.get("cluster") is not None else set())), "cluster": n.get("cluster")})
    out.sort(key=lambda x: (-x["weightedDegree"], -x["bridges"], x["label"]))
    for n in nodes.values():
        n["degree"] = deg.get(n["id"], 0.0)
    return out[:k]


def _cluster_list(nodes: dict[str, dict], edges: dict, clusters: dict[int, list[str]], central: list[dict]) -> list[dict]:
    out = []
    for cid, members in clusters.items():
        mset = set(members)
        internal = sum(e["weight"] for e in edges.values() if e["source"] in mset and e["target"] in mset)
        external = sum(e["weight"] for e in edges.values() if (e["source"] in mset) != (e["target"] in mset)
                       and nodes[e["source"]]["type"] in ("concept", "regime") and nodes[e["target"]]["type"] in ("concept", "regime"))
        top = sorted(members, key=lambda m: (-nodes[m]["facts"], -nodes[m].get("degree", 0), m))
        labels = [nodes[m]["label"] for m in top if nodes[m]["type"] == "concept"][:3] or [nodes[m]["label"] for m in top[:3]]
        facts = len({fid for m in members for fid in nodes[m]["factIds"]})
        out.append({"id": cid, "name": " · ".join(labels), "size": len(members), "facts": facts, "internalWeight": internal,
                    "externalWeight": external, "top": [nodes[m]["label"] for m in top[:6]],
                    "avgCredibility": round(sum(nodes[m]["credibility"] for m in members) / len(members), 3) if members else None})
    out.sort(key=lambda c: (-c["facts"], -c["size"]))
    return out


def _gaps(nodes: dict[str, dict], edges: dict, clusters: list[dict], queued: list[dict], facts: list[dict]) -> list[dict]:
    gaps = []
    topic_nodes = {n["label"]: n for n in nodes.values() if n["type"] == "topic"}
    for q in queued:
        nt = norm(q["topic"])
        if q["status"] != "done" and nt not in topic_nodes:
            gaps.append({"kind": "unread_topic", "label": q["topic"], "why": f"queued topic, no facts yet ({q['status']})", "suggest": q["topic"], "queueId": q["id"]})
    for c in clusters:
        if c["size"] >= 2 and c["facts"] <= 3:
            gaps.append({"kind": "thin_cluster", "label": c["name"], "why": f"{c['size']} concepts but only {c['facts']} fact(s)",
                         "suggest": f"{c['top'][0]} in index futures trading", "cluster": c["id"]})
        elif c["size"] >= 4 and c["externalWeight"] == 0:
            gaps.append({"kind": "island", "label": c["name"], "why": "not connected to any other cluster",
                         "suggest": f"how {c['top'][0]} relates to {clusters[0]['top'][0]}" if clusters and clusters[0] is not c else c["top"][0], "cluster": c["id"]})
    isolated = [n for n in nodes.values() if n["type"] == "concept" and n.get("degree", 0) <= 1 and n["facts"] >= 2]
    for n in sorted(isolated, key=lambda x: -x["facts"])[:8]:
        gaps.append({"kind": "isolated_concept", "label": n["label"], "why": f"{n['facts']} facts, almost no links to other concepts",
                     "suggest": f"{n['label']}: how it connects to order flow, regimes and validation", "node": n["id"]})
    low = [n for n in nodes.values() if n["type"] == "concept" and n["facts"] >= 3 and n["credibility"] < 0.4]
    for n in sorted(low, key=lambda x: -x["facts"])[:6]:
        gaps.append({"kind": "low_credibility", "label": n["label"], "why": f"{n['facts']} facts averaging credibility {n['credibility']:.2f} — only weak sources so far",
                     "suggest": f"{n['label']} — peer-reviewed or exchange sources", "node": n["id"]})
    return gaps[:30]


def node_detail(node_id: str) -> dict | None:
    g = build()
    node = next((n for n in g["nodes"] if n["id"] == node_id), None)
    if node is None:
        return None
    neighbours = []
    for e in g["edges"]:
        if node_id in (e["source"], e["target"]):
            other = e["target"] if e["source"] == node_id else e["source"]
            on = next(n for n in g["nodes"] if n["id"] == other)
            neighbours.append({"id": other, "label": on["label"], "type": on["type"], "weight": e["weight"], "cluster": on.get("cluster")})
    neighbours.sort(key=lambda x: -x["weight"])
    return {"node": node, "facts": facts_by_id(node["factIds"]), "neighbours": neighbours}


def facts_by_id(ids: list[str]) -> list[dict]:
    if not ids:
        return []
    with database.session_scope() as db:
        rows = db.query(KnowledgeFact).filter(KnowledgeFact.id.in_(ids)).all()
        by = {r.id: r for r in rows}
        return [{"id": r.id, "kind": r.kind, "text": r.text, "tags": r.tags_json or [], "credibility": round(r.credibility, 3),
                 "source": r.source_title or r.source_url, "sourceUrl": r.source_url, "sourceId": r.source_id,
                 "evidenceType": r.evidence_type, "refId": r.ref_id, "createdAt": r.created_at}
                for fid in ids if (r := by.get(fid))]
