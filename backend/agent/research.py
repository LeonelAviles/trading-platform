"""Research pipeline (PLATFORM-SPEC.md §4.8): topic queue → Anthropic web search →
fetch (httpx + trafilatura / pypdf, robots.txt respected, raw text cached under
data/research_cache/) → source scoring rubric (fast model) → structured summary
(fast model) → knowledge facts with credibility. Stops at the daily research
budget (`agent.client.check_budget`).

`score_source()` and `summarize()` take the LLM as a parameter so the rubric is
testable with a scripted fake.
"""

from __future__ import annotations

import hashlib
import json
import re
import urllib.robotparser
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import yaml

import database
from agent import client as llm_client
from knowledge import graph as kg
from knowledge import local_store
from market.paths import get_paths
from models import ResearchDoc, ResearchQueueItem, ResearchSource, new_id, utc_now

SEED = Path(__file__).resolve().parent.parent / "config" / "research_seed.yaml"
TIER_BASE = {1: 1.0, 2: 0.75, 3: 0.45, 4: 0.0}
MAX_URLS_PER_TOPIC = 6
MAX_TEXT_CHARS = 20_000
USER_AGENT = "trading-platform-research/1.0 (+local research bot; respects robots.txt)"

SCORING_PROMPT = """You are scoring a document for a quantitative trading knowledge base. Return ONLY JSON:
{"tier": 1|2|3|4, "hasData": bool, "hasCitations": bool, "conflictOfInterest": bool, "yearPublished": int|null,
 "isMicrostructureClaim": bool, "summary": "one sentence", "reason": "one sentence"}
Tiers: 1 = peer-reviewed/preprint (arXiv q-fin, SSRN, journals), exchange or regulator documents (CME Group, CFTC),
textbooks by known authors; 2 = established practitioner books/blogs with a track record, well-known quant blogs,
conference talks; 3 = forums, YouTube transcripts, general blogs; 4 = marketing, signal-selling, prop-firm promotion,
course sales pages (blocked from the graph)."""

SUMMARY_PROMPT = """Extract knowledge from this document for a futures-trading research graph. Return ONLY JSON:
{"claims": [{"text": "one self-contained factual claim or definition", "evidenceType": "theory|backtest|anecdote|regulation",
             "tags": ["concept", ...], "instruments": ["ES", ...], "regimes": ["trend", ...]}],
 "definitions": [{"term": "...", "definition": "..."}], "parameters": [{"name": "...", "typicalValue": "...", "context": "..."}],
 "caveats": ["..."]}
Keep at most 8 claims, each under 35 words, precise enough to act on. Definitions: at most 4."""


def _json_from(text: str) -> dict:
    """Parse the model's JSON, tolerating ``` fences and a truncated tail
    (recovers the complete claim objects that were emitted)."""
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    # Truncated output: salvage complete {"text": ..., "evidenceType": ..., "tags": [...]} objects.
    claims = []
    for obj in re.finditer(r"\{\s*\"text\"\s*:\s*\"((?:[^\"\\\\]|\\\\.)*)\"(.*?)\}", text, re.S):
        body = obj.group(0)
        try:
            claims.append(json.loads(body))
        except json.JSONDecodeError:
            claims.append({"text": obj.group(1), "evidenceType": "theory", "tags": []})
    return {"claims": claims, "definitions": [], "truncated": True} if claims else {}


# ----------------------------------------------------------------------------
# Queue
# ----------------------------------------------------------------------------

def seed_queue() -> int:
    with database.session_scope() as db:
        if db.query(ResearchQueueItem).count():
            return 0
        topics = yaml.safe_load(SEED.read_text()).get("topics", []) if SEED.exists() else []
        for i, t in enumerate(topics):
            db.add(ResearchQueueItem(id=new_id(), topic=t, priority=len(topics) - i, status="queued", requested_by="seed", created_at=utc_now()))
        return len(topics)


def enqueue(topic: str, requested_by: str = "user", priority: int = 10) -> dict:
    with database.session_scope() as db:
        row = ResearchQueueItem(id=new_id(), topic=topic, priority=priority, status="queued", requested_by=requested_by, created_at=utc_now())
        db.add(row)
        db.flush()
        return {"id": row.id, "topic": topic, "status": "queued", "requestedBy": requested_by}


def queue() -> list[dict]:
    with database.session_scope() as db:
        rows = db.query(ResearchQueueItem).order_by(ResearchQueueItem.status, ResearchQueueItem.priority.desc(), ResearchQueueItem.created_at).all()
        return [{"id": r.id, "topic": r.topic, "priority": r.priority, "status": r.status, "requestedBy": r.requested_by, "createdAt": r.created_at} for r in rows]


def sources(limit: int = 200) -> list[dict]:
    with database.session_scope() as db:
        rows = db.query(ResearchSource).order_by(ResearchSource.fetched_at.desc()).limit(limit).all()
        return [{"id": r.id, "url": r.url, "domain": r.domain, "title": r.title, "tier": r.tier, "credibility": r.credibility,
                 "scored": r.scored_json, "fetchedAt": r.fetched_at} for r in rows]


# ----------------------------------------------------------------------------
# Search + fetch
# ----------------------------------------------------------------------------

def web_search(topic: str, llm: llm_client.LLM, max_uses: int = 5) -> list[dict]:
    """Anthropic server-side web search; returns [{url, title}]."""
    response = llm.create(
        purpose="research.search", tier="fast", max_tokens=1024,
        system="You are a research assistant. Search the web for authoritative sources on the topic and list the best 6 URLs with titles. Prefer papers, exchange documentation, textbooks and established practitioner sources; avoid marketing pages.",
        messages=[{"role": "user", "content": f"Topic: {topic}"}],
        tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": max_uses}], cache=False,
    )
    found: dict[str, str] = {}
    for block in response.content:
        btype = getattr(block, "type", None)
        if btype == "web_search_tool_result":
            for item in getattr(block, "content", []) or []:
                url, title = getattr(item, "url", None), getattr(item, "title", None)
                if url and url not in found:
                    found[url] = title or url
        elif btype == "text":
            for url in re.findall(r"https?://[^\s\)\]>\"']+", getattr(block, "text", "") or ""):
                found.setdefault(url.rstrip(".,"), url)
    return [{"url": u, "title": t} for u, t in list(found.items())[:MAX_URLS_PER_TOPIC]]


def _robots_ok(url: str) -> bool:
    try:
        p = urlparse(url)
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(f"{p.scheme}://{p.netloc}/robots.txt")
        rp.read()
        return rp.can_fetch(USER_AGENT, url)
    except Exception:
        return True


def fetch_text(url: str) -> tuple[str, str]:
    """(title, text) with a disk cache under data/research_cache/."""
    cache_dir = get_paths().research_cache_dir
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(url.encode()).hexdigest()[:24]
    cached = cache_dir / f"{key}.json"
    if cached.exists():
        d = json.loads(cached.read_text())
        return d.get("title", ""), d.get("text", "")
    if not _robots_ok(url):
        return "", ""
    import httpx

    with httpx.Client(follow_redirects=True, timeout=20.0, headers={"User-Agent": USER_AGENT}) as c:
        r = c.get(url)
        r.raise_for_status()
        ctype = r.headers.get("content-type", "")
        if "pdf" in ctype or url.lower().endswith(".pdf"):
            from io import BytesIO

            from pypdf import PdfReader

            reader = PdfReader(BytesIO(r.content))
            text = "\n".join((pg.extract_text() or "") for pg in reader.pages[:40])
            title = (reader.metadata or {}).get("/Title") if reader.metadata else ""
        else:
            import trafilatura

            text = trafilatura.extract(r.text, include_comments=False, include_tables=False) or ""
            m = re.search(r"<title[^>]*>(.*?)</title>", r.text, re.I | re.S)
            title = re.sub(r"\s+", " ", m.group(1)).strip() if m else ""
    text = text[:MAX_TEXT_CHARS]
    cached.write_text(json.dumps({"url": url, "title": title or "", "text": text}))
    return title or "", text


# ----------------------------------------------------------------------------
# Scoring + summarising (fast model; testable with a fake LLM)
# ----------------------------------------------------------------------------

def credibility_from(scored: dict) -> float:
    tier = int(scored.get("tier") or 3)
    c = TIER_BASE.get(tier, 0.45)
    if tier == 4:
        return 0.0
    if scored.get("hasData"):
        c += 0.05
    if scored.get("hasCitations"):
        c += 0.05
    if scored.get("conflictOfInterest"):
        c -= 0.2
    year = scored.get("yearPublished")
    if scored.get("isMicrostructureClaim") and isinstance(year, int) and year and datetime.now(timezone.utc).year - year > 10:
        c -= 0.1
    return max(0.0, min(1.0, round(c, 3)))


def score_source(url: str, title: str, text: str, llm: llm_client.LLM) -> dict:
    response = llm.create(purpose="research.score", tier="fast", max_tokens=400, system=SCORING_PROMPT, cache=True,
                          messages=[{"role": "user", "content": f"URL: {url}\nTitle: {title}\n\n{text[:6000]}"}])
    scored = _json_from("".join(getattr(b, "text", "") for b in response.content))
    scored.setdefault("tier", 3)
    scored["credibility"] = credibility_from(scored)
    return scored


def summarize(url: str, title: str, text: str, llm: llm_client.LLM) -> dict:
    response = llm.create(purpose="research.summarize", tier="fast", max_tokens=4000, system=SUMMARY_PROMPT, cache=True,
                          messages=[{"role": "user", "content": f"Title: {title}\nURL: {url}\n\n{text[:12000]}"}])
    return _json_from("".join(getattr(b, "text", "") for b in response.content))


def corroborate(text: str, credibility: float, source_id: str | None) -> float:
    """A claim's credibility rises when an independent tier-1/2 source already
    says the same thing (cosine ≥ 0.85 against stored facts)."""
    similar = [f for f in local_store.search(text, k=5, min_credibility=0.0) if f["score"] >= 0.85 and f.get("sourceId") != source_id
               and f.get("credibility", 0) >= 0.7 and f["kind"] in ("fact", "claim")]
    if similar:
        return min(1.0, credibility + 0.1)
    return credibility


def ingest_document(url: str, title: str, text: str, topic: str, llm: llm_client.LLM) -> dict:
    domain = urlparse(url).netloc
    with database.session_scope() as db:
        src = db.query(ResearchSource).filter(ResearchSource.url == url).one_or_none()
        if src is None:
            src = ResearchSource(id=new_id(), url=url, domain=domain, title=title or url, fetched_at=utc_now())
            db.add(src)
            db.flush()
        source_id = src.id
        prev = db.query(ResearchDoc).filter(ResearchDoc.source_id == source_id, ResearchDoc.topic == topic).all()
        already = any(d.chunk_count > 0 or (src.tier == 4) for d in prev)
        prior_scored = dict(src.scored_json) if src.scored_json else None
        for d in prev:
            if d.chunk_count == 0 and src.tier != 4:
                db.delete(d)            # a summary that produced nothing is retried
    if already:
        return {"sourceId": source_id, "skipped": "already ingested for this topic"}
    scored = prior_scored if prior_scored and prior_scored.get("credibility") is not None else score_source(url, title, text, llm)
    with database.session_scope() as db:
        src = db.get(ResearchSource, source_id)
        src.tier = int(scored.get("tier") or 3)
        src.credibility = scored["credibility"]
        src.scored_json = scored
        src.title = title or src.title
    if scored["tier"] == 4:
        with database.session_scope() as db:
            db.add(ResearchDoc(id=new_id(), source_id=source_id, topic=topic, summary=scored.get("summary"), chunk_count=0, ingested_to_graph=0, created_at=utc_now()))
        return {"sourceId": source_id, "tier": 4, "blocked": True}
    summary = summarize(url, title, text, llm)
    claims = summary.get("claims") or []
    n = 0
    for c in claims:
        ctext = (c.get("text") or "").strip()
        if not ctext:
            continue
        cred = corroborate(ctext, scored["credibility"], source_id)
        kg.record_fact(ctext, source={"id": source_id, "title": title or domain, "url": url}, credibility=cred,
                       tags=list(dict.fromkeys((c.get("tags") or []) + (c.get("instruments") or []) + (c.get("regimes") or []) + [topic])),
                       evidence_type=c.get("evidenceType"))
        n += 1
    for d in summary.get("definitions") or []:
        if d.get("term") and d.get("definition"):
            kg.record_fact(f"{d['term']}: {d['definition']}", source={"id": source_id, "title": title or domain, "url": url},
                           credibility=scored["credibility"], tags=["definition", topic], evidence_type="theory")
            n += 1
    with database.session_scope() as db:
        db.add(ResearchDoc(id=new_id(), source_id=source_id, topic=topic, summary=json.dumps(summary)[:4000], chunk_count=n, ingested_to_graph=1, created_at=utc_now()))
    kg._episode(f"source:{source_id}", json.dumps({"title": title, "url": url, "tier": scored["tier"], "credibility": scored["credibility"], "summary": summary})[:8000],
                f"research source tier {scored['tier']}")
    return {"sourceId": source_id, "tier": scored["tier"], "credibility": scored["credibility"], "facts": n}


# ----------------------------------------------------------------------------
# Worker
# ----------------------------------------------------------------------------

def run_topic(topic_id: str, llm: llm_client.LLM | None = None, fetch=fetch_text, search=web_search) -> dict:
    llm = llm or llm_client.LLM()
    with database.session_scope() as db:
        row = db.get(ResearchQueueItem, topic_id)
        if row is None:
            return {"error": "topic not found"}
        row.status = "running"
        topic = row.topic
    results, errors = [], []
    try:
        urls = search(topic, llm)
        for u in urls:
            try:
                title, text = fetch(u["url"])
                if len(text) < 400:
                    errors.append({"url": u["url"], "error": "no text"})
                    continue
                results.append(ingest_document(u["url"], u.get("title") or title, text, topic, llm))
            except llm_client.BudgetExhausted:
                raise
            except Exception as e:  # noqa: BLE001
                errors.append({"url": u["url"], "error": f"{type(e).__name__}: {e}"})
        status = "done"
    except llm_client.BudgetExhausted as e:
        status = "queued"
        errors.append({"error": str(e)})
    except Exception as e:  # noqa: BLE001
        status = "error"
        errors.append({"error": f"{type(e).__name__}: {e}"})
    with database.session_scope() as db:
        row = db.get(ResearchQueueItem, topic_id)
        if row:
            row.status = status
    return {"topic": topic, "status": status, "sources": results, "errors": errors}


def run_once(max_topics: int = 1, llm: llm_client.LLM | None = None) -> list[dict]:
    seed_queue()
    out = []
    with database.session_scope() as db:
        ids = [r.id for r in db.query(ResearchQueueItem).filter(ResearchQueueItem.status == "queued")
               .order_by(ResearchQueueItem.priority.desc(), ResearchQueueItem.created_at).limit(max_topics).all()]
    for tid in ids:
        r = run_topic(tid, llm)
        out.append(r)
        if r.get("status") == "queued":   # budget hit
            break
    return out
