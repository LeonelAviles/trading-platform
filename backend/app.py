"""FastAPI application factory for the trading platform.

`uvicorn app:app` (or the legacy `uvicorn main:app`, which re-exports this
object) serves market data, strategies, NautilusTrader backtests, the agent
tool bridge and the chat analyst. Routes live in `routers/` — one module per
area — so each later phase (replay, teaching, research, settings) can grow
its own file without touching the others.
"""

import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

# Before importing anything that reads the environment — agent_llm resolves
# ANTHROPIC_MODEL at import time. Real values live in backend/.env (gitignored);
# backend/.env.example is the template. Anything already exported in the shell
# wins, so a one-off `ANTHROPIC_MODEL=... uvicorn ...` still overrides the file.
BACKEND_DIR = Path(__file__).resolve().parent
load_dotenv(BACKEND_DIR / ".env")

from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

import database  # noqa: E402
from routers import (  # noqa: E402
    agent,
    backtests,
    chat,
    desk,
    market,
    replay,
    research,
    settings,
    strategies,
    teaching,
)


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    # SQLite schema (PLATFORM-SPEC.md §4.7). Tests that build their own
    # engine set PLATFORM_SKIP_DB_INIT=1.
    if os.environ.get("PLATFORM_SKIP_DB_INIT") != "1":
        database.init_db()
        try:
            from agent import runs as agent_runs

            resumed = agent_runs.resume_pending()
            if resumed:
                print(f"resumed agent runs: {resumed}", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"agent run resume failed: {e}", flush=True)
        if os.environ.get("RESEARCH_SCHEDULER", "1") != "0":
            from agent import research

            research.start_scheduler()
    yield
    try:
        from agent import research

        research.stop_scheduler()
    except Exception:  # noqa: BLE001
        pass


def create_app() -> FastAPI:
    app = FastAPI(title="Trading Platform API", lifespan=_lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    for router in (
        market.router,
        strategies.router,
        backtests.router,
        chat.router,
        agent.router,
        replay.router,
        teaching.router,
        research.router,
        settings.router,
        desk.router,
    ):
        app.include_router(router)

    @app.get("/api/health")
    def health():
        return {"ok": True, "database": database.describe_url()}

    return app


app = create_app()
