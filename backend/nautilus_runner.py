"""Runs strategies through NautilusTrader and normalizes results into simple
trade records the frontend can draw.

Each real backtest runs the nautilus_backtest.py worker in a subprocess
(isolated from the API process), which builds a BacktestEngine in-process and
writes trades JSON. No account, login, or Docker is required.

Job lifecycle: POST /api/backtests creates a job folder under backtests/ and
a background thread runs the worker, persisting job.json at each step so jobs
survive backend restarts.

A built-in "demo" mode runs a fixed breakout rule directly against the loaded
bars (clearly labeled, not the engine) as a fast pipeline check.

Trade record schema (shared by the worker and demo):
  { id, direction, qty, entryTime, entryPrice, exitTime, exitPrice,
    stopPrice, targetPrice, pnl, reason }
"""

import json
import shutil
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

import data_store

BACKEND_DIR = Path(__file__).resolve().parent
REPO_ROOT = BACKEND_DIR.parent
JOBS_DIR = REPO_ROOT / "backtests"
WORKER = BACKEND_DIR / "nautilus_backtest.py"

_jobs_lock = threading.Lock()
_live_threads: dict[str, threading.Thread] = {}


def engine_status() -> dict:
    try:
        import nautilus_trader
        return {"engine": "nautilustrader", "installed": True, "version": nautilus_trader.__version__}
    except Exception:
        return {"engine": "nautilustrader", "installed": False, "version": None}


def _job_path(job_id: str) -> Path:
    return JOBS_DIR / job_id


def _write_job(job: dict):
    path = _job_path(job["id"])
    path.mkdir(parents=True, exist_ok=True)
    (path / "job.json").write_text(json.dumps(job, indent=2))


def _read_job(job_id: str) -> dict | None:
    f = _job_path(job_id) / "job.json"
    if not f.exists():
        return None
    return json.loads(f.read_text())


def _is_thread_alive(job_id: str) -> bool:
    t = _live_threads.get(job_id)
    return t is not None and t.is_alive()


def list_jobs() -> list[dict]:
    if not JOBS_DIR.exists():
        return []
    jobs = []
    for d in JOBS_DIR.iterdir():
        job = _read_job(d.name)
        if not job:
            continue
        if job["status"] in ("preparing", "running") and not _is_thread_alive(job["id"]):
            job["status"] = "error"
            job["message"] = "interrupted by backend restart"
            _write_job(job)
        jobs.append(job)
    return sorted(jobs, key=lambda j: j["createdAt"], reverse=True)


def get_job(job_id: str) -> dict | None:
    return _read_job(job_id)


def delete_job(job_id: str) -> bool:
    path = _job_path(job_id)
    if not path.exists():
        return False
    shutil.rmtree(path, ignore_errors=True)
    _live_threads.pop(job_id, None)
    return True


def _summarize(trades: list[dict]) -> dict:
    wins = [t for t in trades if t["pnl"] > 0]
    return {
        "trades": len(trades),
        "wins": len(wins),
        "winRate": round(len(wins) / len(trades) * 100, 1) if trades else 0,
        "totalPnl": round(sum(t["pnl"] for t in trades), 2),
    }


# --------------------------------------------------------------------------
# Demo backtest: a fixed 5-bar-low breakout short, evaluated directly on the
# loaded bars. NOT the engine — a fast way to exercise the UI pipeline.
# --------------------------------------------------------------------------

def run_demo(symbol: str, interval: str = "1min") -> list[dict]:
    bars = data_store.bars_to_records(data_store.get_bars(symbol, interval))
    trades = []
    position = None
    lookback = 5
    for i, bar in enumerate(bars):
        if position is not None:
            if bar["high"] >= position["stopPrice"]:
                exit_price, reason = position["stopPrice"], "stop"
            elif bar["low"] <= position["targetPrice"]:
                exit_price, reason = position["targetPrice"], "target"
            elif i == len(bars) - 1:
                exit_price, reason = bar["close"], "end_of_data"
            else:
                continue
            position.update({
                "exitTime": bar["time"], "exitPrice": round(exit_price, 4), "reason": reason,
                "pnl": round(-(exit_price - position["entryPrice"]) * position["qty"], 2),
            })
            trades.append(position)
            position = None
            continue
        if i < lookback:
            continue
        prior_low = min(b["low"] for b in bars[i - lookback:i])
        if bar["close"] < prior_low:
            entry = bar["close"]
            stop = entry * 1.003
            target = entry - (stop - entry) * 2
            position = {
                "id": str(uuid.uuid4()), "direction": "short", "qty": 100,
                "entryTime": bar["time"], "entryPrice": round(entry, 4),
                "stopPrice": round(stop, 4), "targetPrice": round(target, 4),
            }
    return trades


# --------------------------------------------------------------------------
# Real NautilusTrader backtest (subprocess worker)
# --------------------------------------------------------------------------

def _nautilus_thread(job: dict, strategy: dict):
    job_dir = _job_path(job["id"])
    try:
        job.update(status="running", message="running NautilusTrader backtest")
        _write_job(job)

        strat_path = job_dir / "strategy.json"
        strat_path.write_text(json.dumps(strategy, indent=2), encoding="utf-8")
        trades_path = job_dir / "trades.json"
        log_path = job_dir / "worker.log"

        with open(log_path, "w", encoding="utf-8") as lf:
            proc = subprocess.run(
                [sys.executable, str(WORKER), str(strat_path), str(trades_path)],
                cwd=str(BACKEND_DIR), stdout=lf, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, timeout=600, text=True,
            )
        if proc.returncode != 0 or not trades_path.exists():
            tail = log_path.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-4:]
            raise RuntimeError("backtest failed: " + " ".join(tail) if tail else "backtest failed — see worker.log")

        result = json.loads(trades_path.read_text(encoding="utf-8"))
        job.update(
            status="done", message=None,
            trades=result["trades"], summary=result["summary"], source="nautilus",
        )
        _write_job(job)
    except Exception as e:
        job.update(status="error", message=str(e))
        _write_job(job)


def start_backtest(strategy: dict | None, demo: bool, symbol: str | None = None) -> dict:
    job = {
        "id": uuid.uuid4().hex[:12],
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "strategyId": strategy.get("id") if strategy else None,
        "strategyName": strategy.get("name") if strategy else "Demo (built-in sample logic)",
        "symbol": (strategy or {}).get("symbol") or symbol,
        "status": "preparing",
        "message": None,
        "source": "demo" if demo else "nautilus",
    }
    with _jobs_lock:
        _write_job(job)

    if demo:
        try:
            trades = run_demo(job["symbol"])
            job.update(status="done", trades=trades, summary=_summarize(trades))
        except Exception as e:
            job.update(status="error", message=str(e))
        _write_job(job)
        return job

    t = threading.Thread(target=_nautilus_thread, args=(job, strategy), daemon=True)
    _live_threads[job["id"]] = t
    t.start()
    return job
