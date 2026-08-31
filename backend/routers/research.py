"""Research, knowledge and usage routes (PLATFORM-SPEC.md §6)."""

from __future__ import annotations

import threading

from fastapi import APIRouter, Body, HTTPException

import database
from agent import client as llm_client
from agent import research
from knowledge import graph as kg
from models import PrimitiveRequest

router = APIRouter(prefix="/api", tags=["research"])
_worker_lock = threading.Lock()
_worker: threading.Thread | None = None


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
    global _worker
    n = int(body.get("maxTopics") or 1)
    with _worker_lock:
        if _worker is not None and _worker.is_alive():
            return {"started": False, "reason": "research worker already running"}
        _worker = threading.Thread(target=research.run_once, args=(n,), daemon=True, name="research-worker")
        _worker.start()
    return {"started": True, "maxTopics": n}


@router.get("/research/status")
def research_status():
    return {"workerRunning": _worker is not None and _worker.is_alive(), "knowledge": kg.status(), "usage": llm_client.usage_summary()}


@router.get("/research/sources")
def get_sources():
    return research.sources()


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


@router.get("/knowledge/search")
def knowledge_search(q: str, k: int = 12, min_credibility: float = 0.4):
    return kg.search(q, k=k, min_credibility=min_credibility)


@router.get("/usage")
def usage():
    """llm_usage aggregates for the month, budget, price table (estimates)."""
    return llm_client.usage_summary()
