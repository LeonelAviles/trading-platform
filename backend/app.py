"""FastAPI application factory for the trading platform.

`uvicorn app:app` (or the legacy `uvicorn main:app`, which re-exports this
object) serves market data, strategies, NautilusTrader backtests, tick replay,
settings and the desk. Routes live in `routers/` — one
module per area.
"""

import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

# Real values live in backend/.env (gitignored); backend/.env.example is the
# template. Anything already exported in the shell wins over the file.
BACKEND_DIR = Path(__file__).resolve().parent
load_dotenv(BACKEND_DIR / ".env")

from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

import database  # noqa: E402
from routers import (  # noqa: E402
    backtests,
    desk,
    market,
    replay,
    settings,
    strategies,
)


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    # SQLite schema (PLATFORM-SPEC.md §4.7). Tests that build their own
    # engine set PLATFORM_SKIP_DB_INIT=1.
    if os.environ.get("PLATFORM_SKIP_DB_INIT") != "1":
        database.init_db()
    yield


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
        replay.router,
        settings.router,
        desk.router,
    ):
        app.include_router(router)

    @app.get("/api/health")
    def health():
        return {"ok": True, "database": database.describe_url()}

    return app


app = create_app()
