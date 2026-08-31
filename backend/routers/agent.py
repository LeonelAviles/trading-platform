"""Agent tool bridge.

One generic endpoint over agent_tools.call_tool() rather than a REST route
per tool. The tools are already a closed, self-describing set (TOOLS is the
manifest, call_tool the dispatcher), so hand-writing 15 routes would just be
a second copy of that mapping to keep in sync.

This exists so an out-of-process agent runtime — the Hermes plugin in
hermes_plugin/ — can drive the same tools agent_llm uses in-process, without
importing nautilus_trader/duckdb/polars into its own interpreter.

Phase 4 adds /api/agent/runs (AgentRun state machine) and /ws/agent/:runId.
"""

from fastapi import APIRouter, Body

import agent_tools

router = APIRouter(prefix="/api/agent", tags=["agent"])


@router.get("/tools")
def list_agent_tools():
    """OpenAI-format function-calling manifest. Hermes consumes this shape
    directly (ctx.register_tool(schema=...)); the plugin ships a generated
    copy so it can register while the backend is down, and uses this route
    to check for drift."""
    return agent_tools.TOOLS


@router.post("/tools/{name}")
def call_agent_tool(name: str, arguments: dict = Body(default={})):
    """Dispatch one tool call. call_tool() never raises — failures come back
    as {"error": ...} with a 200, because the caller is a model that should
    read the error and adjust, not an HTTP client that should retry."""
    return agent_tools.call_tool(name, arguments)
