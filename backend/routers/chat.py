"""Assistant chat — conversational loop over the same agent_tools the Hermes
plugin drives, so the in-app panel and the external agent see identical
strategies, backtests and findings.

Every route here stays answerable with no ANTHROPIC_API_KEY set: the
frontend renders an "offline" badge off /api/chat/status and shows the
error text in the bubble, which is friendlier than a 500.
"""

import json

from fastapi import APIRouter, Body
from fastapi.responses import StreamingResponse

import agent_llm

router = APIRouter(prefix="/api/chat", tags=["chat"])


@router.get("/status")
def chat_status():
    return agent_llm.chat_status()


@router.post("")
def chat(body: dict = Body(...)):
    return agent_llm.chat(body.get("messages") or [], body.get("context"))


@router.post("/stream")
def chat_stream(body: dict = Body(...)):
    """SSE stream of one assistant turn.

    stream_chat() yields errors as events rather than raising, so this
    generator has no failure path of its own — it always terminates with a
    "done" event and the reader in api.js always completes.
    """
    messages, context = body.get("messages") or [], body.get("context")

    def gen():
        for event in agent_llm.stream_chat(messages, context):
            yield f"data: {json.dumps(event)}\n\n"
        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
