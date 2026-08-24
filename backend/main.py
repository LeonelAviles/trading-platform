"""FastAPI backend for the trading platform.

Serves OHLCV bars aggregated from Databento CSVs (see data_store), stores
strategy definitions, and orchestrates NautilusTrader backtests (see
nautilus_runner).
"""

import json
import uuid
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

import agent_llm
import data_store
import nautilus_runner
import strategy_spec

STRATEGIES_DIR = Path(__file__).resolve().parent / "strategies"

app = FastAPI(title="Trading Platform API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------
# Market data
# --------------------------------------------------------------------------

@app.get("/api/symbols")
def list_symbols():
    return {"symbols": data_store.list_symbols()}


@app.get("/api/ohlcv")
def get_ohlcv(
    symbol: str = Query(..., description="Ticker symbol, e.g. MSFT"),
    interval: str = Query("1min", description="Pandas offset alias: 1min, 5min, 1h, 1D, ..."),
    start: int | None = Query(None, description="Clip to bars at/after this unix timestamp"),
    end: int | None = Query(None, description="Clip to bars at/before this unix timestamp"),
):
    # bars_to_records() already returns plain int/float/dict — skip FastAPI's
    # default jsonable_encoder() pass (it's a generic recursive encoder built
    # for arbitrary/pydantic types, and that per-item overhead dominates the
    # response time once a payload gets into the 100k-row range).
    records = data_store.bars_to_records(data_store.get_bars(symbol, interval, start, end))
    return JSONResponse(content=records)


@app.get("/api/range")
def get_range(symbol: str = Query(...)):
    """First/last available bar time — lets the chart size its initial
    request, and bounds replay selection.

    Answered from min/max timestamps, not by building the 1-minute series:
    this is on the critical path of the first paint, and aggregating every
    tick just to read its two endpoints cost ~16s. The old `bars1min` count
    is gone with it — nothing consumed it, and it was the one field that
    could not be produced without the full aggregate.
    """
    start, end = data_store.data_range(symbol)
    return {"start": start, "end": end}


@app.get("/api/cvd")
def get_cvd(
    symbol: str = Query(...),
    interval: str = Query("1min"),
    start: int | None = Query(None),
    end: int | None = Query(None),
):
    """Cumulative Volume Delta, bucketed like /api/ohlcv so it lines up with the chart."""
    series = data_store.get_cvd(symbol, interval, start, end)
    return [{"time": int(ts.timestamp()), "cvd": round(float(v), 2)} for ts, v in series.items()]


@app.get("/api/dom")
def get_dom(
    symbol: str = Query(...),
    as_of: int | None = Query(None, description="Unix seconds; defaults to the latest event"),
    depth: int = Query(12, ge=1, le=50),
):
    """Approximate order-book snapshot reconstructed from recent Add/Cancel/Fill events."""
    return data_store.order_book_snapshot(symbol, as_of, depth)


# --------------------------------------------------------------------------
# Strategies (stored as JSON files the backtest worker reads directly)
# --------------------------------------------------------------------------

def _strategy_file(strategy_id: str) -> Path:
    return STRATEGIES_DIR / f"{strategy_id}.json"


def _load_strategy(strategy_id: str) -> dict:
    f = _strategy_file(strategy_id)
    if not f.exists():
        raise HTTPException(404, f"strategy '{strategy_id}' not found")
    return json.loads(f.read_text(encoding="utf-8"))


@app.get("/api/strategies")
def list_strategies():
    if not STRATEGIES_DIR.exists():
        return []
    strategies = [
        json.loads(f.read_text(encoding="utf-8")) for f in sorted(STRATEGIES_DIR.glob("*.json"))
    ]
    return sorted(strategies, key=lambda s: s.get("name", ""))


@app.post("/api/strategies")
def save_strategy(strategy: dict = Body(...)):
    errors = strategy_spec.validate_strategy(strategy)
    if errors:
        raise HTTPException(400, "; ".join(errors))
    if not strategy.get("id"):
        strategy["id"] = uuid.uuid4().hex[:12]
    STRATEGIES_DIR.mkdir(exist_ok=True)
    _strategy_file(strategy["id"]).write_text(json.dumps(strategy, indent=2), encoding="utf-8")
    return strategy


@app.post("/api/strategies/generate")
def generate_strategy(body: dict = Body(...)):
    """Idea -> deterministic strategy, via Claude + agent_tools.create_strategy.
    400 if ANTHROPIC_API_KEY isn't set (mirrors /api/chat/status's
    "not configured" pattern) rather than 500ing."""
    for field in ("name", "symbol", "direction", "prompt"):
        if not body.get(field):
            raise HTTPException(400, f"'{field}' is required")
    try:
        return agent_llm.generate_strategy(
            name=body["name"], symbol=body["symbol"], direction=body["direction"],
            prompt=body["prompt"], interval=body.get("interval", "1min"),
        )
    except agent_llm.LLMNotConfigured as e:
        raise HTTPException(400, f"AI strategy generation not configured: {e}")
    except Exception as e:
        raise HTTPException(502, f"strategy generation failed: {e}")


@app.delete("/api/strategies/{strategy_id}")
def delete_strategy(strategy_id: str):
    f = _strategy_file(strategy_id)
    if not f.exists():
        raise HTTPException(404, f"strategy '{strategy_id}' not found")
    f.unlink()
    return {"deleted": strategy_id}


# --------------------------------------------------------------------------
# Backtests (NautilusTrader jobs + built-in demo)
# --------------------------------------------------------------------------

@app.get("/api/engine/status")
def get_engine_status():
    return nautilus_runner.engine_status()


@app.get("/api/backtests")
def list_backtests():
    # Trades can be large; the list view only needs job metadata.
    return [{k: v for k, v in job.items() if k != "trades"} for job in nautilus_runner.list_jobs()]


@app.get("/api/backtests/{job_id}")
def get_backtest(job_id: str):
    job = nautilus_runner.get_job(job_id)
    if job is None:
        raise HTTPException(404, f"backtest '{job_id}' not found")
    return job


@app.delete("/api/backtests/{job_id}")
def delete_backtest(job_id: str):
    if not nautilus_runner.delete_job(job_id):
        raise HTTPException(404, f"backtest '{job_id}' not found")
    return {"deleted": job_id}


@app.get("/api/backtests/{job_id}/analytics")
def get_backtest_analytics(job_id: str):
    """Dashboard analytics (stat tiles, equity curve, distribution, monthly
    table, exit-reason mix) derived from one backtest's closed trades."""
    job = nautilus_runner.get_job(job_id)
    if job is None:
        raise HTTPException(404, f"backtest '{job_id}' not found")
    return nautilus_runner.strategy_analytics(job)


@app.post("/api/backtests")
def create_backtest(body: dict = Body(...)):
    if body.get("demo"):
        symbol = body.get("symbol")
        if not symbol:
            raise HTTPException(400, "demo backtest requires 'symbol'")
        return nautilus_runner.start_backtest(None, demo=True, symbol=symbol)
    strategy_id = body.get("strategyId")
    if not strategy_id:
        raise HTTPException(400, "'strategyId' (or demo: true) is required")
    strategy = _load_strategy(strategy_id)
    return nautilus_runner.start_backtest(strategy, demo=False)


# --------------------------------------------------------------------------
# Assistant chat — implementation removed for now. Routes stay in place so
# the frontend (ChatPanel, AI Insights) degrades gracefully to "offline"
# instead of hitting 404s.
# --------------------------------------------------------------------------

@app.get("/api/chat/status")
def chat_status():
    return {"connected": False, "model": None, "reason": "assistant not configured"}


@app.post("/api/chat")
def chat(body: dict = Body(...)):
    return {"role": "assistant", "content": "The assistant is not configured.", "error": True}


@app.post("/api/backtests/{job_id}/insights")
def get_backtest_insights(job_id: str):
    job = nautilus_runner.get_job(job_id)
    if job is None:
        raise HTTPException(404, f"backtest '{job_id}' not found")
    return {"role": "assistant", "content": "The assistant is not configured.", "error": True}


@app.post("/api/chat/stream")
def chat_stream(body: dict = Body(...)):
    """SSE stream — immediately reports the assistant as unconfigured."""
    def gen():
        event = {"type": "delta", "text": "The assistant is not configured."}
        yield f"data: {json.dumps(event)}\n\n"
        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
