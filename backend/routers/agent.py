"""HTTP boundary for the new Stratos Research agent."""

import json

from pydantic import BaseModel, Field
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from research_agent import service

router = APIRouter(prefix="/api/agent", tags=["agent"])


class NewThread(BaseModel):
    title: str = "New research"


class ChatRequest(BaseModel):
    threadId: str
    message: str = Field(min_length=1, max_length=20_000)


@router.get("/status")
def get_status():
    return service.status()


@router.get("/threads")
def list_threads():
    return service.list_threads()


@router.post("/threads")
def create_thread(body: NewThread):
    return service.create_thread(body.title)


@router.get("/threads/{thread_id}")
def get_thread(thread_id: str):
    thread = service.get_thread(thread_id)
    if thread is None:
        raise HTTPException(404, f"thread '{thread_id}' not found")
    return thread


@router.get("/threads/{thread_id}/workflow")
def get_thread_workflow(thread_id: str):
    if service.get_thread(thread_id) is None:
        raise HTTPException(404, f"thread '{thread_id}' not found")
    return service.workflow.latest_for_thread(thread_id) or {}


@router.post("/chat")
def chat(body: ChatRequest):
    try:
        return service.chat(body.threadId, body.message)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except service.AgentConfigurationError as exc:
        raise HTTPException(503, str(exc))
    except service.AgentRuntimeError as exc:
        raise HTTPException(502, str(exc))


@router.post("/chat/stream")
def chat_stream(body: ChatRequest):
    try:
        events = service.chat_events(body.threadId, body.message)
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    def ndjson():
        for event in events:
            yield json.dumps(event, separators=(",", ":"), default=str) + "\n"

    return StreamingResponse(
        ndjson(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# Explicit user-control endpoints: intentionally absent from model tools.
from typing import Literal
from pydantic import ConfigDict
from research_agent import evidence, knowledge, memory


class ProposalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hypothesis: str = Field(min_length=1, max_length=20_000)
    strategy: dict
    testPlan: dict
    concepts: list[str] = Field(max_length=100)
    passageIds: list[str] = Field(max_length=100)
    parentId: str | None = None


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    digest: str = Field(min_length=64, max_length=64)
    action: Literal["approve", "reject_stop", "reject_revise"]
    reason: str = Field(min_length=1, max_length=20_000)


class SourceTextRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=220)
    content: str = Field(min_length=1, max_length=2_000_000)
    kind: Literal["user_note", "selected_source"]
    sourceUrl: str | None = None
    author: str | None = None
    license: str | None = None


def _research_call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except memory.ResearchConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/research/context")
def research_context():
    return memory.dataset_context()


@router.get("/threads/{thread_id}/proposals")
def proposals(thread_id: str):
    return memory.history(thread_id)


@router.post("/threads/{thread_id}/proposals")
def propose(thread_id: str, body: ProposalRequest):
    return _research_call(memory.propose, thread_id, body.hypothesis, body.strategy, body.testPlan,
                          body.concepts, body.passageIds, body.parentId)


@router.get("/threads/{thread_id}/proposals/{proposal_id}")
def proposal(thread_id: str, proposal_id: str):
    return _research_call(memory.get, proposal_id, thread_id)


@router.post("/threads/{thread_id}/proposals/{proposal_id}/decision")
def decide_proposal(thread_id: str, proposal_id: str, body: DecisionRequest):
    return _research_call(memory.decide, proposal_id, thread_id, body.digest, body.action, body.reason)


@router.post("/threads/{thread_id}/proposals/{proposal_id}/queue")
def queue_proposal(thread_id: str, proposal_id: str):
    return _research_call(memory.enqueue, proposal_id, thread_id)


@router.post("/knowledge/text")
def ingest_source_text(body: SourceTextRequest):
    return _research_call(knowledge.ingest_text, title=body.title, content=body.content, kind=body.kind,
                          source_url=body.sourceUrl, author=body.author, license_name=body.license)


@router.get("/knowledge/passages/{passage_id}")
def source_passage(passage_id: str):
    return _research_call(knowledge.passage, passage_id)


@router.get("/evidence/{job_id}/trades")
def research_trade_evidence(job_id: str, offset: int = 0, limit: int = 50):
    return _research_call(evidence.trade_evidence, job_id, offset, limit)
