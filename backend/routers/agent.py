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
