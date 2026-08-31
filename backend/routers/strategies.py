"""Strategy routes.

Phase 0: strategies are still JSON files under backend/strategies/ (the v1
format). Phase 3 moves them into SQLite through the v1→v2 converter.
"""

import json
import uuid
from pathlib import Path

from fastapi import APIRouter, Body, HTTPException

import agent_llm
import strategy_spec

STRATEGIES_DIR = Path(__file__).resolve().parent.parent / "strategies"

router = APIRouter(prefix="/api/strategies", tags=["strategies"])


def _strategy_file(strategy_id: str) -> Path:
    return STRATEGIES_DIR / f"{strategy_id}.json"


def load_strategy(strategy_id: str) -> dict:
    f = _strategy_file(strategy_id)
    if not f.exists():
        raise HTTPException(404, f"strategy '{strategy_id}' not found")
    return json.loads(f.read_text(encoding="utf-8"))


@router.get("")
def list_strategies():
    if not STRATEGIES_DIR.exists():
        return []
    strategies = [
        json.loads(f.read_text(encoding="utf-8")) for f in sorted(STRATEGIES_DIR.glob("*.json"))
    ]
    return sorted(strategies, key=lambda s: s.get("name", ""))


@router.post("")
def save_strategy(strategy: dict = Body(...)):
    errors = strategy_spec.validate_strategy(strategy)
    if errors:
        raise HTTPException(400, "; ".join(errors))
    if not strategy.get("id"):
        strategy["id"] = uuid.uuid4().hex[:12]
    STRATEGIES_DIR.mkdir(exist_ok=True)
    _strategy_file(strategy["id"]).write_text(json.dumps(strategy, indent=2), encoding="utf-8")
    return strategy


@router.post("/generate")
def generate_strategy(body: dict = Body(...)):
    """Idea -> deterministic strategy, via Claude + agent_tools.create_strategy.
    400 if ANTHROPIC_API_KEY isn't set (mirrors /api/chat/status's
    "not configured" pattern) rather than 500ing. Becomes a background
    AgentRun in Phase 4."""
    for field in ("name", "symbol", "direction", "prompt"):
        if not body.get(field):
            raise HTTPException(400, f"'{field}' is required")
    try:
        return agent_llm.generate_strategy(
            name=body["name"], symbol=body["symbol"], direction=body["direction"],
            prompt=body["prompt"], interval=body.get("interval") or None,
            risk=body.get("risk"),
        )
    except agent_llm.LLMNotConfigured as e:
        raise HTTPException(400, f"AI strategy generation not configured: {e}")
    except Exception as e:
        raise HTTPException(502, f"strategy generation failed: {e}")


@router.delete("/{strategy_id}")
def delete_strategy(strategy_id: str):
    f = _strategy_file(strategy_id)
    if not f.exists():
        raise HTTPException(404, f"strategy '{strategy_id}' not found")
    f.unlink()
    return {"deleted": strategy_id}
