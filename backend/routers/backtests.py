"""Backtest routes (NautilusTrader jobs via nautilus_runner)."""

from fastapi import APIRouter, Body, HTTPException

import agent_llm
import nautilus_runner
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
    strategy_id = body.get("strategyId")
    if not strategy_id:
        raise HTTPException(400, "'strategyId' is required")
    strategy = load_strategy(strategy_id)
    return nautilus_runner.start_backtest(strategy)


@router.post("/backtests/{job_id}/insights")
def get_backtest_insights(job_id: str):
    """One-shot analysis of a finished backtest — the "AI Insights" button.

    Seeded as a normal chat turn naming the job id, so the model reaches the
    numbers through the same tools (get_backtest_analytics, get_win_rate,
    compare_winners_vs_losers) it would use if asked in the panel, rather
    than through a second, separately-maintained prompt path.
    """
    job = nautilus_runner.get_job(job_id)
    if job is None:
        raise HTTPException(404, f"backtest '{job_id}' not found")
    prompt = (
        f"Analyze backtest {job_id}. Pull its analytics and compare winners against "
        f"losers, then give me the two or three things that most stand out — what's "
        f"working, what's losing money, and the single change most worth testing next."
    )
    return agent_llm.chat([{"role": "user", "content": prompt}])
