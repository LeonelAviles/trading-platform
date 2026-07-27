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
    return data_store.bars_to_records(data_store.get_bars(symbol, interval, start, end))


@app.get("/api/range")
def get_range(symbol: str = Query(...)):
    """First/last available bar time — lets the UI bound replay selection."""
    bars = data_store.load_ohlcv_bars(symbol)
    return {
        "start": int(bars.index[0].timestamp()),
        "end": int(bars.index[-1].timestamp()),
        "bars1min": len(bars),
    }


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
# Assistant chat (LLM to be plugged in — see below)
# --------------------------------------------------------------------------

@app.get("/api/chat/status")
def chat_status():
    # Flip to True once an LLM is wired into /api/chat, so the UI can show it.
    return {"connected": False, "model": None}


@app.post("/api/chat")
def chat(body: dict = Body(...)):
    """Assistant reply endpoint.

    `body` = { messages: [{role, content}, ...], context: {...} }
    `context` is whatever the UI wants the model to know (current symbol,
    interval, etc.); ignored for now.

    ======================  LLM INTEGRATION POINT  ======================
    Plug your model in here. Call your provider with `messages` (and any
    context), and return the assistant's reply. Keep the call server-side so
    API keys never reach the browser. For example:

        client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        resp = client.messages.create(model="claude-...", messages=messages,
                                       system=build_system_prompt(context))
        return {"role": "assistant", "content": resp.content[0].text}

    Until then, return a clearly-labeled stub so the panel works end to end.
    =====================================================================
    """
    messages = body.get("messages", [])
    last_user = next((m.get("content", "") for m in reversed(messages) if m.get("role") == "user"), "")
    return {
        "role": "assistant",
        "content": (
            "The chat panel is connected to the backend, but no LLM is wired in yet. "
            "Add your model in the /api/chat endpoint (backend/main.py).\n\n"
            f"Echo — you said: {last_user}"
        ),
    }
