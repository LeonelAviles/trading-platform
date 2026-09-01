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
import threading
import urllib.robotparser
from datetime import datetime, timedelta, timezone
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
SETTINGS_KEY = "research.settings"
AUTORUN_STATE_KEY = "research.autorun.state"
# Domains whose tier is fixed by rule, not by the model's reading of the page.
# Editable on the Research page (stored under SETTINGS_KEY); suffix match.
DEFAULT_TRUSTED = {
    "tier1": ["arxiv.org", "ssrn.com", "cmegroup.com", "cftc.gov", "sec.gov", "nber.org", "jstor.org", "sciencedirect.com",
              "link.springer.com", "onlinelibrary.wiley.com", "tandfonline.com", "bis.org", "federalreserve.gov", "ecb.europa.eu"],
    "tier2": ["quantpedia.com", "quantocracy.com", "robotwealth.com", "quantstart.com", "tradingstats.net", "hudsonthames.org",
              "papers.nips.cc", "nautilustrader.io"],
    "blocked": [],
}
# minTier: only sources at this tier or better feed the knowledge base
# (1 = papers/exchanges/regulators only, 2 = + established practitioners).
# Hard-capped at 2 — tier 3 (blogs/forums) and tier 4 (marketing) never
# feed the graph. Owner decision 2026-08-31: tier 1-2 only.
DEFAULT_SETTINGS = {"autoRun": False, "intervalHours": 6, "topicsPerRun": 2, "minTier": 2, "trustedDomains": DEFAULT_TRUSTED}
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
# Settings (self-study schedule, trusted domains)
# ----------------------------------------------------------------------------

def _setting(key: str, default):
    from models import Setting

    with database.session_scope() as db:
        row = db.get(Setting, key)
        return dict(row.value_json) if row and isinstance(row.value_json, dict) else default


def _put_setting(key: str, value: dict) -> None:
    from models import Setting

    with database.session_scope() as db:
        row = db.get(Setting, key)
        if row is None:
            db.add(Setting(key=key, value_json=value))
        else:
            row.value_json = value


def settings() -> dict:
    stored = _setting(SETTINGS_KEY, {})
    out = {**DEFAULT_SETTINGS, **stored}
    td = {**DEFAULT_TRUSTED, **(stored.get("trustedDomains") or {})}
    out["trustedDomains"] = {k: _domains(td.get(k)) for k in ("tier1", "tier2", "blocked")}
    out["intervalHours"] = max(1, float(out.get("intervalHours") or 6))
    mt = out.get("minTier")
    out["minTier"] = min(2, max(1, 2 if mt in (None, "") else int(mt)))
    out["topicsPerRun"] = max(1, min(10, int(out.get("topicsPerRun") or 2)))
    out["autoRun"] = bool(out.get("autoRun"))
    return out


def update_settings(changes: dict) -> dict:
    cur = {**DEFAULT_SETTINGS, **_setting(SETTINGS_KEY, {})}
    for k in ("autoRun", "intervalHours", "topicsPerRun", "minTier"):
        if k in changes:
            cur[k] = changes[k]
    if "trustedDomains" in changes and isinstance(changes["trustedDomains"], dict):
        td = {**(cur.get("trustedDomains") or DEFAULT_TRUSTED)}
        for k in ("tier1", "tier2", "blocked"):
            if k in changes["trustedDomains"]:
                td[k] = _domains(changes["trustedDomains"][k])
        cur["trustedDomains"] = td
    _put_setting(SETTINGS_KEY, cur)
    return settings()


def _domains(value) -> list[str]:
    if isinstance(value, str):
        value = re.split(r"[\s,]+", value)
    out = []
    for v in value or []:
        d = str(v).strip().lower()
        d = re.sub(r"^https?://", "", d).split("/")[0]
        if d.startswith("www."):
            d = d[4:]
        if d and d not in out:
            out.append(d)
    return out


def domain_rule(url: str, trusted: dict | None = None) -> str | None:
    """'tier1' | 'tier2' | 'blocked' | None for a URL, by domain suffix."""
    host = (urlparse(url).netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return None
    td = trusted or settings()["trustedDomains"]
    for key in ("blocked", "tier1", "tier2"):
        for d in td.get(key) or []:
            if host == d or host.endswith("." + d):
                return key
    return None


def apply_domain_rules(url: str, scored: dict, trusted: dict | None = None) -> dict:
    rule = domain_rule(url, trusted)
    if rule == "blocked":
        scored["tier"] = 4
    elif rule == "tier1":
        scored["tier"] = 1
    elif rule == "tier2":
        scored["tier"] = min(int(scored.get("tier") or 3), 2)
    if rule:
        scored["domainRule"] = rule
    scored["credibility"] = credibility_from(scored)
    return scored


# ----------------------------------------------------------------------------
# Queue
# ----------------------------------------------------------------------------

def seed_queue() -> int:
    """Queue every seed topic not already in the queue (a top-up, not a
    once-only seed, so topics added to research_seed.yaml reach existing
    installations). Returns how many were added."""
    topics = yaml.safe_load(SEED.read_text()).get("topics", []) if SEED.exists() else []
    with database.session_scope() as db:
        existing = {t for (t,) in db.query(ResearchQueueItem.topic)}
        added = 0
        for i, t in enumerate(topics):
            if t in existing:
                continue
            db.add(ResearchQueueItem(id=new_id(), topic=t, priority=len(topics) - i, status="queued", requested_by="seed", created_at=utc_now()))
            added += 1
        return added


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
        system="You are a research assistant for a quantitative trading knowledge base. Search the web for authoritative sources on the topic and list the best 6 URLs with titles. Only peer-reviewed papers and preprints (arXiv q-fin, SSRN, journals), exchange and regulator documentation, textbooks, and established practitioner sources with a track record — blogs, forums, marketing and course-sales pages are rejected downstream, so do not list them.",
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
    return apply_domain_rules(url, scored)


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


def ingest_document(url: str, title: str, text: str, topic: str, llm: llm_client.LLM, provided_by: str = "worker") -> dict:
    domain = urlparse(url).netloc or "owner"
    extra_tags = ["owner"] if provided_by == "user" else []
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
    scored["providedBy"] = provided_by if provided_by == "user" else scored.get("providedBy", provided_by)
    with database.session_scope() as db:
        src = db.get(ResearchSource, source_id)
        src.tier = int(scored.get("tier") or 3)
        src.credibility = scored["credibility"]
        src.scored_json = scored
        src.title = title or src.title
    min_tier = settings()["minTier"]
    if scored["tier"] == 4 or scored["tier"] > min_tier:
        with database.session_scope() as db:
            db.add(ResearchDoc(id=new_id(), source_id=source_id, topic=topic, summary=scored.get("summary"), chunk_count=0, ingested_to_graph=0, created_at=utc_now()))
        reason = "marketing/promotion (tier 4)" if scored["tier"] == 4 else f"tier {scored['tier']} — below the accepted tier ({min_tier})"
        return {"sourceId": source_id, "tier": scored["tier"], "blocked": True, "reason": reason}
    summary = summarize(url, title, text, llm)
    claims = summary.get("claims") or []
    n = 0
    for c in claims:
        ctext = (c.get("text") or "").strip()
        if not ctext:
            continue
        cred = corroborate(ctext, scored["credibility"], source_id)
        kg.record_fact(ctext, source={"id": source_id, "title": title or domain, "url": url}, credibility=cred,
                       tags=list(dict.fromkeys((c.get("tags") or []) + (c.get("instruments") or []) + (c.get("regimes") or []) + [topic] + extra_tags)),
                       evidence_type=c.get("evidenceType"))
        n += 1
    for d in summary.get("definitions") or []:
        if d.get("term") and d.get("definition"):
            kg.record_fact(f"{d['term']}: {d['definition']}", source={"id": source_id, "title": title or domain, "url": url},
                           credibility=scored["credibility"], tags=["definition", topic] + extra_tags, evidence_type="theory")
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
        msg = str(e)
        if any(t in msg.lower() for t in ("credit balance", "authentication", "api key", "invalid x-api-key")):
            status = "queued"     # account problem, not a topic problem — retry when credits exist
        else:
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


# ----------------------------------------------------------------------------
# Owner-provided sources ("hand it a book")
# ----------------------------------------------------------------------------

OWNER_TOPIC = "owner-provided"
_source_jobs: dict[str, dict] = {}
_source_lock = threading.Lock()


def text_from_upload(data: bytes, content_type: str | None, filename: str | None = None) -> tuple[str, str]:
    """(title, text) for an uploaded PDF or text file."""
    ctype = (content_type or "").lower()
    name = filename or ""
    if "pdf" in ctype or name.lower().endswith(".pdf"):
        from io import BytesIO

        from pypdf import PdfReader

        reader = PdfReader(BytesIO(data))
        text = "\n".join((pg.extract_text() or "") for pg in reader.pages[:60])
        title = ((reader.metadata or {}).get("/Title") if reader.metadata else "") or name
        return str(title or ""), text[:MAX_TEXT_CHARS]
    return name, data.decode("utf-8", errors="replace")[:MAX_TEXT_CHARS]


def add_source(*, url: str | None = None, text: str | None = None, title: str | None = None, topic: str | None = None,
               llm: llm_client.LLM | None = None, fetch=fetch_text, background: bool = True) -> dict:
    """Ingest one source the owner chose: a URL (fetched like the worker does)
    or pasted / uploaded text. Facts it yields carry the `owner` tag and the
    source row says `providedBy: user`. Runs in a thread unless told otherwise."""
    topic = (topic or "").strip() or OWNER_TOPIC
    if not url and not (text or "").strip():
        raise ValueError("give a url or some text")
    if url:
        url = url.strip()
        if not re.match(r"^https?://", url):
            raise ValueError("url must start with http:// or https://")
    else:
        digest = hashlib.sha256((text or "").encode()).hexdigest()[:16]
        url = f"owner://{digest}"
    job = {"id": new_id(), "url": url, "title": title, "topic": topic, "status": "queued", "startedAt": utc_now(), "result": None}
    with _source_lock:
        _source_jobs[job["id"]] = job
        for old in list(_source_jobs)[:-20]:
            _source_jobs.pop(old, None)

    def work():
        job["status"] = "running"
        try:
            if text is None or not text.strip():
                t, body = fetch(url)
            else:
                t, body = title or "", text
            if len(body.strip()) < 200:
                raise ValueError("less than 200 characters of text — nothing to learn from")
            job["result"] = ingest_document(url, title or t or url, body, topic, llm or llm_client.LLM(), provided_by="user")
            job["status"] = "done"
        except llm_client.BudgetExhausted as e:
            job.update(status="budget", error=str(e))
        except Exception as e:  # noqa: BLE001
            job.update(status="error", error=f"{type(e).__name__}: {e}")
        job["finishedAt"] = utc_now()

    if background:
        threading.Thread(target=work, daemon=True, name=f"research-source-{job['id']}").start()
    else:
        work()
    return job


def source_jobs() -> list[dict]:
    with _source_lock:
        return [dict(j) for j in list(_source_jobs.values())[::-1]]


# ----------------------------------------------------------------------------
# Worker thread + self-study schedule
# ----------------------------------------------------------------------------

_worker_lock = threading.Lock()
_worker: threading.Thread | None = None


def worker_running() -> bool:
    return _worker is not None and _worker.is_alive()


def start_worker(max_topics: int = 1, requested_by: str = "user") -> dict:
    """Run up to `max_topics` queued topics in a background thread (stops at the daily budget)."""
    global _worker
    with _worker_lock:
        if worker_running():
            return {"started": False, "reason": "research worker already running"}

        def work():
            results = run_once(max_topics)
            _record_run(requested_by, results)

        _worker = threading.Thread(target=work, daemon=True, name="research-worker")
        _worker.start()
    return {"started": True, "maxTopics": max_topics, "requestedBy": requested_by}


def _record_run(requested_by: str, results: list[dict]) -> None:
    facts = sum(s.get("facts") or 0 for r in results for s in r.get("sources") or [])
    state = _setting(AUTORUN_STATE_KEY, {})
    state.update({
        "lastRunAt": utc_now(), "lastRunBy": requested_by,
        "lastResult": {"topics": [r.get("topic") for r in results], "statuses": [r.get("status") for r in results],
                       "sources": sum(len(r.get("sources") or []) for r in results), "facts": facts,
                       "errors": [e.get("error") for r in results for e in r.get("errors") or [] if e.get("error")][:5]},
    })
    _put_setting(AUTORUN_STATE_KEY, state)


def autorun_status(now: datetime | None = None) -> dict:
    cfg = settings()
    state = _setting(AUTORUN_STATE_KEY, {})
    now = now or datetime.now(timezone.utc)
    last = state.get("lastRunAt")
    next_at = None
    if cfg["autoRun"]:
        if last:
            next_at = (datetime.fromisoformat(last) + timedelta(hours=cfg["intervalHours"])).isoformat(timespec="seconds")
        else:
            next_at = now.isoformat(timespec="seconds")
    with database.session_scope() as db:
        queued = db.query(ResearchQueueItem).filter(ResearchQueueItem.status == "queued").count()
    usage = llm_client.usage_summary()
    return {
        "enabled": cfg["autoRun"], "intervalHours": cfg["intervalHours"], "topicsPerRun": cfg["topicsPerRun"],
        "lastRunAt": last, "lastRunBy": state.get("lastRunBy"), "lastResult": state.get("lastResult"), "nextRunAt": next_at,
        "running": worker_running(), "queued": queued, "researchCapped": usage.get("researchCapped"),
        "skipped": state.get("skipped"), "sourceJobs": source_jobs()[:5],
    }


def autorun_tick(now: datetime | None = None, start=None) -> dict:
    """One scheduler step: start a worker run when self-study is on, the
    interval has elapsed, there is something queued and the daily research
    budget is not spent. Returns what it decided (testable with `start`)."""
    cfg = settings()
    if not cfg["autoRun"]:
        return {"ran": False, "reason": "disabled"}
    now = now or datetime.now(timezone.utc)
    state = _setting(AUTORUN_STATE_KEY, {})
    last = state.get("lastRunAt")
    if last and now < datetime.fromisoformat(last) + timedelta(hours=cfg["intervalHours"]):
        return {"ran": False, "reason": "not due"}
    if worker_running():
        return {"ran": False, "reason": "worker busy"}
    with database.session_scope() as db:
        queued = db.query(ResearchQueueItem).filter(ResearchQueueItem.status == "queued").count()
    if queued == 0:
        _note_skip(state, "queue empty")
        return {"ran": False, "reason": "queue empty"}
    if llm_client.usage_summary().get("researchCapped"):
        _note_skip(state, "daily research budget spent")
        return {"ran": False, "reason": "daily research budget spent"}
    res = (start or start_worker)(cfg["topicsPerRun"], "autorun")
    return {"ran": bool(res.get("started")), "reason": res.get("reason"), "topics": cfg["topicsPerRun"]}


def _note_skip(state: dict, reason: str) -> None:
    state["skipped"] = {"at": utc_now(), "reason": reason}
    _put_setting(AUTORUN_STATE_KEY, state)


_scheduler_stop = threading.Event()
_scheduler: threading.Thread | None = None


def start_scheduler(poll_seconds: float = 60.0) -> None:
    """Daemon loop behind the self-study switch; safe to call once at startup."""
    global _scheduler
    if _scheduler is not None and _scheduler.is_alive():
        return
    _scheduler_stop.clear()

    def loop():
        while not _scheduler_stop.wait(poll_seconds):
            try:
                autorun_tick()
            except Exception as e:  # noqa: BLE001
                print(f"research scheduler: {type(e).__name__}: {e}", flush=True)

    _scheduler = threading.Thread(target=loop, daemon=True, name="research-scheduler")
    _scheduler.start()


def stop_scheduler() -> None:
    _scheduler_stop.set()


def prune_below_tier(min_tier: int | None = None) -> dict:
    """Invalidate stored facts whose source is worse than `min_tier` (default:
    the configured one). Reversible — rows are stamped invalid, not deleted."""
    from models import KnowledgeFact

    min_tier = int(min_tier or settings()["minTier"])
    with database.session_scope() as db:
        rows = (db.query(KnowledgeFact).join(ResearchSource, KnowledgeFact.source_id == ResearchSource.id)
                .filter(KnowledgeFact.invalid_at.is_(None), ResearchSource.tier > min_tier).all())
        for r in rows:
            r.invalid_at = utc_now()
        n = len(rows)
    return {"minTier": min_tier, "invalidated": n}
