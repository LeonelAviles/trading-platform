"""Strategy routes — Spec v2 in SQLite (strategy_store), PLATFORM-SPEC.md §6."""

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import Response

import strategy_package
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


@router.post("/import")
async def import_strategy(request: Request):
    """Re-import a package produced by GET /{id}/package. Body: the zip bytes
    (`Content-Type: application/zip`); `?keepId=false` always mints a new id."""
    data = await request.body()
    if not data:
        raise HTTPException(400, "empty body — send the package zip as the request body")
    keep = request.query_params.get("keepId", "true").lower() != "false"
    try:
        return strategy_package.import_package(data, keep_id=keep)
    except strategy_package.PackageError as e:
        raise HTTPException(400, str(e))


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


@router.get("/{strategy_id}/package")
def get_package(strategy_id: str):
    """Zip: spec, risk, validation report, lineage, evidence/, nautilus_config.json (Phase 7)."""
    s = load_strategy(strategy_id)
    data = strategy_package.build(strategy_id)
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in (s.get("name") or "strategy"))[:40]
    return Response(content=data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{safe}-{strategy_id}.zip"'})


@router.post("/{strategy_id}/forward-test")
def forward_test(strategy_id: str):
    """The one manual transition the desk offers: candidate -> forward_test.
    Forward testing itself is out of scope (PLATFORM-SPEC.md Phase 7)."""
    s = load_strategy(strategy_id)
    if s.get("status") != "candidate":
        raise HTTPException(409, f"only candidates move to forward_test (status is '{s.get('status')}')")
    return strategy_store.set_status(strategy_id, "forward_test")


@router.get("/{strategy_id}/compare/{other_id}")
def compare(strategy_id: str, other_id: str, window: str = "is", mode: str | None = None):
    """Two lineage nodes side by side: the latest finished backtest of the
    given window kind for each (engine.compare)."""
    from engine import compare as cmp

    load_strategy(strategy_id)
    load_strategy(other_id)
    a, b = _latest_job(strategy_id, window, mode), _latest_job(other_id, window, mode)
    if not a or not b:
        missing = [sid for sid, j in ((strategy_id, a), (other_id, b)) if not j]
        raise HTTPException(404, f"no finished '{window}' backtest for {', '.join(missing)}")
    try:
        out = cmp.compare_backtests(a["id"], b["id"])
    except cmp.CompareError as e:
        raise HTTPException(400, str(e))
    return {"a": {"strategyId": strategy_id, "backtestId": a["id"], "name": a.get("strategyName")},
            "b": {"strategyId": other_id, "backtestId": b["id"], "name": b.get("strategyName")},
            "window": window, "comparison": out}


def _latest_job(strategy_id: str, window: str, mode: str | None) -> dict | None:
    from engine import jobs

    for j in jobs.list_jobs():   # newest first
        if j.get("strategyId") == strategy_id and j.get("status") == "done" and j.get("windowKind") == window and (mode is None or j.get("mode") == mode):
            return j
    return None


@router.get("/schema/spec")
def get_spec_schema():
    """JSON Schema + primitive docs for the spec editor."""
    from engine import expr as X

    return {"schema": spec_mod.json_schema(), "primitives": spec_mod.primitive_docs(), "operators": sorted(X.OPS),
            "fields": list(X.FIELDS), "timeframes": list(spec_mod.TIMEFRAMES)}
