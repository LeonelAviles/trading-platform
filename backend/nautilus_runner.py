"""Compatibility shim — the job model moved to `engine/jobs.py` (Phase 2).

Kept so `agent_tools`, `agent_llm` and the Hermes plugin keep importing the
same names: list_jobs, get_job, delete_job, strategy_analytics,
start_backtest, engine_status, JOBS_DIR, STARTING_EQUITY.
"""

from engine.jobs import (  # noqa: F401
    JOBS_DIR,
    STARTING_EQUITY,
    delete_job,
    engine_status,
    get_job,
    list_jobs,
    start_backtest,
    strategy_analytics,
)
