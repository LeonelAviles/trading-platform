"""Strategy routes — Spec v2 in SQLite (strategy_store), PLATFORM-SPEC.md §6."""

from fastapi import APIRouter, Body, HTTPException

import agent_llm
import strategy_store
from engine import spec as spec_mod

router = APIRouter(prefix="/api/strategies", tags=["strategies"])


def load_strategy(strategy_id: str) -> dict:
    s = strategy_store.get_strategy(strategy_id)
    if s is None:
        raise HTTPException(404, f"strategy '{strategy_id}' not found")
    return s


@router.get("")
def list_strategies():
    return strategy_store.list_strategies()


@router.post("")
def save_strategy(strategy: dict = Body(...)):
    """Create or update (when `id` is present). Accepts v2 specs and legacy v1 documents (converted)."""
    try:
        return strategy_store.save_strategy(strategy)
    except strategy_store.StrategyError as e:
        raise HTTPException(400, str(e))


@router.post("/validate")
def validate_strategy(strategy: dict = Body(...)):
    """spec -> {valid, errors[], requiredMode}"""
    errors = strategy_store.validate(strategy)
    spec = strategy_store.coerce(strategy)
    return {"valid": not errors, "errors": errors,
            "requiredMode": spec_mod.required_mode(spec) if not errors else None}


@router.post("/generate")
def generate_strategy(body: dict = Body(...)):
    """Idea -> strategies, as a resumable background AgentRun (Phase 4).
    Returns the run; watch it at /api/agent/runs/:id or /ws/agent/:id.
    Only `prompt` is required — name, symbol, direction and interval may be
    left to the agent (direction is often one of the ambiguities)."""
    from agent import runs

    if not (body.get("prompt") or "").strip():
        raise HTTPException(400, "'prompt' is required")
    input_ = {k: body.get(k) for k in ("prompt", "symbol", "direction", "name", "interval", "risk") if body.get(k) is not None}
    return runs.start_run("generate", input_)


@router.get("/{strategy_id}")
def get_strategy(strategy_id: str):
    return load_strategy(strategy_id)


@router.put("/{strategy_id}")
def put_strategy(strategy_id: str, strategy: dict = Body(...)):
    load_strategy(strategy_id)
    try:
        return strategy_store.save_strategy({**strategy, "id": strategy_id}, strategy_id=strategy_id)
    except strategy_store.StrategyError as e:
        raise HTTPException(400, str(e))


@router.delete("/{strategy_id}")
def delete_strategy(strategy_id: str):
    if not strategy_store.delete_strategy(strategy_id):
        raise HTTPException(404, f"strategy '{strategy_id}' not found")
    return {"deleted": strategy_id}


@router.get("/{strategy_id}/lineage")
def get_lineage(strategy_id: str):
    try:
        return strategy_store.lineage(strategy_id)
    except strategy_store.StrategyError as e:
        raise HTTPException(404, str(e))


@router.patch("/{strategy_id}/risk")
def patch_risk(strategy_id: str, risk: dict = Body(...)):
    """Strategy Settings modal: user overrides (proposedBy becomes 'user')."""
    load_strategy(strategy_id)
    try:
        return strategy_store.patch_risk(strategy_id, risk, proposed_by=risk.get("proposedBy", "user"))
    except strategy_store.StrategyError as e:
        raise HTTPException(400, str(e))


@router.post("/{strategy_id}/status")
def set_status(strategy_id: str, body: dict = Body(...)):
    load_strategy(strategy_id)
    try:
        return strategy_store.set_status(strategy_id, body.get("status"))
    except strategy_store.StrategyError as e:
        raise HTTPException(400, str(e))


@router.get("/schema/spec")
def get_spec_schema():
    """JSON Schema + primitive docs (same payload the agent's get_spec_schema tool returns)."""
    from engine import expr as X

    return {"schema": spec_mod.json_schema(), "primitives": spec_mod.primitive_docs(), "operators": sorted(X.OPS),
            "fields": list(X.FIELDS), "timeframes": list(spec_mod.TIMEFRAMES)}
