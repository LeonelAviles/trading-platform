"""Research, knowledge and usage routes (PLATFORM-SPEC.md §6)."""

from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException, Request

import database
from agent import client as llm_client
from agent import research
from knowledge import graph as kg
from models import PrimitiveRequest

router = APIRouter(prefix="/api", tags=["research"])


@router.get("/research/queue")
def get_queue():
    research.seed_queue()
    return research.queue()


@router.post("/research/queue")
def add_topic(body: dict = Body(...)):
    topic = (body.get("topic") or "").strip()
    if not topic:
        raise HTTPException(400, "'topic' is required")
    return research.enqueue(topic, requested_by=body.get("requestedBy") or "user")


@router.post("/research/run")
def run_research(body: dict = Body(default={})):
    """Run up to `maxTopics` queued topics in a background thread (stops at the daily budget)."""
    n = int(body.get("maxTopics") or 1)
    return research.start_worker(n, requested_by="user")


@router.get("/research/status")
def research_status():
    return {"workerRunning": research.worker_running(), "knowledge": kg.status(), "usage": llm_client.usage_summary(),
            "autorun": research.autorun_status()}


@router.get("/research/sources")
def get_sources():
    return {"sources": research.sources(), "jobs": research.source_jobs()}


@router.post("/research/sources")
def add_source(body: dict = Body(...)):
    """Hand the agent a source: {url, topic?} or {text, title?, topic?}. Fetched,
    scored and summarised in the background; watch `jobs` in GET /research/sources."""
    try:
        return research.add_source(url=body.get("url"), text=body.get("text"), title=body.get("title"), topic=body.get("topic"))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/research/sources/upload")
async def upload_source(request: Request, title: str | None = None, topic: str | None = None, filename: str | None = None):
    """Raw PDF or text file as the request body (Content-Type application/pdf or text/*)."""
    data = await request.body()
    if not data:
        raise HTTPException(400, "empty body")
    try:
        t, text = research.text_from_upload(data, request.headers.get("content-type"), filename)
        return research.add_source(text=text, title=title or t or filename, topic=topic)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:  # noqa: BLE001 — unreadable PDF etc.
        raise HTTPException(400, f"could not read the file: {type(e).__name__}: {e}")


@router.get("/research/settings")
def get_research_settings():
    """Self-study schedule and trusted domains (stored under settings key research.settings)."""
    return research.settings()


@router.put("/research/settings")
def put_research_settings(body: dict = Body(...)):
    return research.update_settings(body)


@router.post("/research/prune")
def prune_low_tier(body: dict = Body(default={})):
    """Invalidate facts from sources below the accepted tier (Settings → Trusted domains)."""
    return research.prune_below_tier(body.get("minTier"))


@router.get("/research/autorun")
def get_autorun():
    return research.autorun_status()


@router.post("/research/autorun/tick")
def autorun_now():
    """Run the scheduler step now (what the background loop does every minute)."""
    return research.autorun_tick()


@router.get("/research/primitive-requests")
def primitive_requests():
    with database.session_scope() as db:
        rows = db.query(PrimitiveRequest).order_by(PrimitiveRequest.created_at.desc()).all()
        return [{"id": r.id, "name": r.name, "description": r.description, "params": r.params_json, "pseudocode": r.pseudocode,
                 "sources": r.sources_json, "status": r.status, "createdAt": r.created_at} for r in rows]


@router.post("/research/primitive-requests/{req_id}/status")
def set_primitive_request_status(req_id: str, body: dict = Body(...)):
    status = body.get("status")
    if status not in ("open", "implemented", "rejected"):
        raise HTTPException(400, "status must be open | implemented | rejected")
    with database.session_scope() as db:
        row = db.get(PrimitiveRequest, req_id)
        if row is None:
            raise HTTPException(404, "not found")
        row.status = status
        return {"id": row.id, "status": status}


@router.get("/knowledge/graph")
def knowledge_graph(min_credibility: float = 0.0, kinds: str | None = None, tiers: str | None = None, sources: bool = True, max_nodes: int = 600):
    """Nodes / edges / clusters / central concepts / gaps for the /knowledge page.
    `kinds` and `tiers` are comma-separated filters."""
    from knowledge import graph_view

    k = tuple(x for x in (kinds or "").split(",") if x) or None
    t = tuple(int(x) for x in (tiers or "").split(",") if x.strip().isdigit()) or None
    return graph_view.build(min_credibility=min_credibility, kinds=k, tiers=t, include_sources=sources, max_nodes=max(50, min(2000, max_nodes)))


@router.get("/knowledge/graph/node/{node_id:path}")
def knowledge_graph_node(node_id: str):
    from knowledge import graph_view

    d = graph_view.node_detail(node_id)
    if d is None:
        raise HTTPException(404, "node not found")
    return d


@router.get("/knowledge/facts")
def knowledge_facts(ids: str):
    from knowledge import graph_view

    return graph_view.facts_by_id([x for x in ids.split(",") if x][:200])


@router.get("/knowledge/search")
def knowledge_search(q: str, k: int = 12, min_credibility: float = 0.4):
    return kg.search(q, k=k, min_credibility=min_credibility)


@router.get("/usage")
def usage():
    """llm_usage aggregates for the month, budget, price table (estimates)."""
    return llm_client.usage_summary()
