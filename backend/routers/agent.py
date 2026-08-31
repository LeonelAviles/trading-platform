"""Agent routes: the tool bridge (Hermes), agent runs and their WebSocket feed (PLATFORM-SPEC.md §6)."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Body, HTTPException, WebSocket, WebSocketDisconnect

import agent_tools
from agent import runs
from agent import tools_v2  # noqa: F401 — installs the Phase 4 tools into the manifest

router = APIRouter(tags=["agent"])


@router.get("/api/agent/tools")
def list_agent_tools():
    """OpenAI-format function-calling manifest (Hermes consumes this shape)."""
    return agent_tools.TOOLS


@router.post("/api/agent/tools/{name}")
def call_agent_tool(name: str, arguments: dict = Body(default={})):
    """Dispatch one tool call; failures come back as {"error": ...} with a 200."""
    return agent_tools.call_tool(name, arguments)


# --------------------------------------------------------------------------
# Runs
# --------------------------------------------------------------------------

@router.get("/api/agent/runs")
def list_runs(limit: int = 50):
    return runs.list_runs(limit)


@router.post("/api/agent/runs")
def create_run(body: dict = Body(...)):
    """{kind: "generate", prompt, symbol?, direction?, name?, interval?, risk?}"""
    kind = body.get("kind") or "generate"
    if kind not in ("generate", "chat_action", "teaching_compile"):
        raise HTTPException(400, "kind must be generate | chat_action | teaching_compile")
    if kind in ("generate", "chat_action") and not (body.get("prompt") or "").strip():
        raise HTTPException(400, "'prompt' is required")
    input_ = {k: body.get(k) for k in ("prompt", "symbol", "direction", "name", "interval", "risk", "sessionId") if body.get(k) is not None}
    return runs.start_run(kind, input_)


@router.get("/api/agent/runs/{run_id}")
def get_run(run_id: str, events: bool = True):
    r = runs.get(run_id, with_state=events)
    if r is None:
        raise HTTPException(404, f"run '{run_id}' not found")
    return r


@router.post("/api/agent/runs/{run_id}/answer")
def answer_run(run_id: str, body: dict = Body(...)):
    text = (body.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "'text' is required")
    try:
        return runs.answer(run_id, text)
    except KeyError:
        raise HTTPException(404, f"run '{run_id}' not found")
    except ValueError as e:
        raise HTTPException(409, str(e))


@router.post("/api/agent/runs/{run_id}/cancel")
def cancel_run(run_id: str):
    r = runs.cancel(run_id)
    if r is None:
        raise HTTPException(404, f"run '{run_id}' not found")
    return r


@router.websocket("/ws/agent/{run_id}")
async def run_events(ws: WebSocket, run_id: str):
    """Streams the run's event log (progress, tool events, questions, done)."""
    await ws.accept()
    r = runs.get(run_id)
    if r is None:
        await ws.send_json({"type": "error", "message": "run not found"})
        await ws.close()
        return
    seq = 0
    try:
        while True:
            for ev in runs.events(run_id, seq):
                seq = ev["seq"]
                await ws.send_json(ev)
            cur = runs.get(run_id)
            if cur is None:
                break
            await ws.send_json({"type": "status", "status": cur["status"], "progress": cur["progress"], "costUsd": cur["costUsd"]})
            if cur["status"] in ("done", "error", "cancelled", "budget_exhausted"):
                break
            await asyncio.sleep(0.8)
    except WebSocketDisconnect:
        return
    except Exception:
        return
    try:
        await ws.close()
    except Exception:
        pass
