"""Backtest routes — SQLite job model + subprocess worker (engine/jobs.py),
validation windows and report (engine/validation.py)."""

from datetime import date

from fastapi import APIRouter, Body, HTTPException

from engine import jobs as nautilus_runner
from engine import validation
from routers.strategies import load_strategy

router = APIRouter(prefix="/api", tags=["backtests"])


@router.get("/engine/status")
def get_engine_status():
    return nautilus_runner.engine_status()


@router.get("/backtests")
def list_backtests():
    # Trades can be large; the list view only needs job metadata.
    return [{k: v for k, v in job.items() if k != "trades"} for job in nautilus_runner.list_jobs()]


@router.get("/backtests/{job_id}")
def get_backtest(job_id: str):
    job = nautilus_runner.get_job(job_id)
    if job is None:
        raise HTTPException(404, f"backtest '{job_id}' not found")
    return job


@router.delete("/backtests/{job_id}")
def delete_backtest(job_id: str):
    if not nautilus_runner.delete_job(job_id):
        raise HTTPException(404, f"backtest '{job_id}' not found")
    return {"deleted": job_id}


@router.get("/backtests/{job_id}/analytics")
def get_backtest_analytics(job_id: str):
    """Dashboard analytics (stat tiles, equity curve, distribution, monthly
    table, exit-reason mix) derived from one backtest's closed trades."""
    job = nautilus_runner.get_job(job_id)
    if job is None:
        raise HTTPException(404, f"backtest '{job_id}' not found")
    return nautilus_runner.strategy_analytics(job)


@router.post("/backtests")
def create_backtest(body: dict = Body(...)):
    """{strategyId, mode?: bars|ticks|l3, windowKind?: is|wf1|wf2|wf3|oos|full, dateFrom?, dateTo?}.
    Default window is `full` (review on the chart); validation uses `is`/`wf*`."""
    strategy_id = body.get("strategyId")
    if not strategy_id:
        raise HTTPException(400, "'strategyId' is required")
    strategy = load_strategy(strategy_id)
    kind = body.get("windowKind") or "full"
    if kind not in validation.WINDOW_KINDS:
        raise HTTPException(400, f"windowKind must be one of {validation.WINDOW_KINDS}")
    mode = body.get("mode")
    if mode is not None and mode not in ("bars", "ticks", "l3"):
        raise HTTPException(400, "mode must be bars|ticks|l3")
    try:
        d0 = date.fromisoformat(body["dateFrom"]) if body.get("dateFrom") else None
        d1 = date.fromisoformat(body["dateTo"]) if body.get("dateTo") else None
        return nautilus_runner.start_backtest(strategy, mode=mode, window_kind=kind, date_from=d0, date_to=d1)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/backtests/validate")
def create_validation(body: dict = Body(...)):
    """Queue IS + WF1–3 for a strategy (never OOS). Returns the jobs."""
    strategy_id = body.get("strategyId")
    if not strategy_id:
        raise HTTPException(400, "'strategyId' is required")
    strategy = load_strategy(strategy_id)
    try:
        return nautilus_runner.run_validation(strategy, mode=body.get("mode"))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/backtests/{job_id}/validation")
def get_validation(job_id: str):
    """IS / WF / OOS / Monte Carlo / DSR / regimes / verdict for the job's strategy.
    OOS numbers appear only once an `oos` run exists."""
    job = nautilus_runner.get_job(job_id)
    if job is None:
        raise HTTPException(404, f"backtest '{job_id}' not found")
    strategy_id = job.get("strategyId")
    risk = None
    try:
        risk = load_strategy(strategy_id).get("risk") if strategy_id else None
    except HTTPException:
        pass
    rep = validation.report(strategy_id, mode=job.get("mode"), risk=risk) if strategy_id else {}
    rep["job"] = {k: job.get(k) for k in ("id", "windowKind", "mode", "dateFrom", "dateTo", "status")}
    rep["windowsAvailable"] = validation.windows(_root_of(job)) if job.get("symbol") else {}
    return rep


def _root_of(job: dict) -> str:
    from config.instruments import load_instruments

    spec = load_instruments().root_for_symbol(job.get("symbol") or "")
    return spec.root if spec else ""


@router.get("/validation/windows")
def get_windows(root: str = "ES"):
    return validation.windows(root)
