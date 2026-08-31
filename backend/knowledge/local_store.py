"""Local knowledge store in SQLite (`knowledge_facts`): the platform's memory
when Neo4j is not running, and a mirror of what goes into the graph when it is.
Retrieval = cosine similarity on local embeddings + keyword overlap, filtered by
credibility, newest first on ties."""

from __future__ import annotations

import re
from datetime import datetime, timezone

import database
from knowledge import embedder
from models import KnowledgeFact, new_id


def _kw(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if len(t) > 2}


def add(text: str, *, kind: str = "fact", tags: list[str] | None = None, credibility: float = 0.5,
        source_id: str | None = None, source_title: str | None = None, source_url: str | None = None,
        evidence_type: str | None = None, ref_id: str | None = None) -> dict:
    if credibility < 0.4 and kind in ("fact", "claim"):
        kind = "hypothesis"
    vec = embedder.embed([text])[0]
    with database.session_scope() as db:
        row = KnowledgeFact(id=new_id(), kind=kind, text=text, tags_json=list(tags or []), credibility=float(credibility),
                            source_id=source_id, source_title=source_title, source_url=source_url,
                            evidence_type=evidence_type, embedding_json=vec, ref_id=ref_id)
        db.add(row)
        db.flush()
        return _to_dict(row, 1.0)


def _to_dict(r: KnowledgeFact, score: float) -> dict:
    return {"id": r.id, "kind": r.kind, "text": r.text, "tags": r.tags_json or [], "credibility": round(r.credibility, 3),
            "source": r.source_title or r.source_url, "sourceUrl": r.source_url, "sourceId": r.source_id,
            "evidenceType": r.evidence_type, "refId": r.ref_id, "createdAt": r.created_at, "score": round(score, 4)}


def search(query: str, k: int = 12, min_credibility: float = 0.4, kinds: tuple[str, ...] | None = None) -> list[dict]:
    qv = embedder.embed([query])[0]
    qk = _kw(query)
    with database.session_scope() as db:
        q = db.query(KnowledgeFact).filter(KnowledgeFact.invalid_at.is_(None))
        if kinds:
            q = q.filter(KnowledgeFact.kind.in_(kinds))
        rows = q.all()
        scored = []
        for r in rows:
            if r.credibility < min_credibility and r.kind not in ("note", "experiment", "finding", "teaching"):
                continue
            sim = embedder.cosine(qv, r.embedding_json or [])
            kw = len(qk & _kw(r.text)) / (len(qk) or 1)
            score = 0.7 * sim + 0.3 * kw
            if score <= 0.05:
                continue
            scored.append((score, r))
        scored.sort(key=lambda x: (-x[0], x[1].created_at))
        return [_to_dict(r, s) for s, r in scored[:k]]


def invalidate(fact_id: str) -> bool:
    with database.session_scope() as db:
        row = db.get(KnowledgeFact, fact_id)
        if row is None:
            return False
        row.invalid_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        return True


def count(kind: str | None = None) -> int:
    with database.session_scope() as db:
        q = db.query(KnowledgeFact)
        if kind:
            q = q.filter(KnowledgeFact.kind == kind)
        return q.count()


def format_facts(facts: list[dict]) -> str:
    """The 'Relevant knowledge' block injected into prompts."""
    if not facts:
        return "Relevant knowledge: (none retrieved yet — run the research worker)"
    lines = ["Relevant knowledge (cite the credibility and source when you use one):"]
    for f in facts:
        tag = "hypothesis — test it, do not treat as best practice" if f["kind"] == "hypothesis" else f["kind"]
        lines.append(f"- [credibility {f['credibility']:.2f}, source: {f.get('source') or 'platform'}, {tag}] {f['text']}")
    return "\n".join(lines)
