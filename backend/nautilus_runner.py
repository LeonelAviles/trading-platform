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
import os
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
# The simulated account the worker funds every backtest with — kept here (not
# in nautilus_backtest, which can't be imported without nautilus_trader
# installed) so percentage returns can be computed from a job's trades alone.
# Must match nautilus_backtest.py's starting_balances.
STARTING_EQUITY = 100_000.0

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
    # Atomic write: a poller (agent_tools.run_backtest polls every 0.5s) can
    # otherwise read job.json mid-write and hit an empty/truncated file,
    # since write_text() truncates before writing — os.replace() is atomic
    # on the same filesystem, so readers only ever see a complete file.
    target = path / "job.json"
    tmp = path / "job.json.tmp"
    tmp.write_text(json.dumps(job, indent=2))
    os.replace(tmp, target)


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


def _r_multiple(t: dict) -> float | None:
    """Trade P&L expressed in units of initial risk (R). Risk per share is
    the entry-to-stop distance; None if a trade has no stop (can't size R)."""
    risk_per_unit = abs(t["entryPrice"] - t["stopPrice"])
    if risk_per_unit <= 0 or not t.get("qty"):
        return None
    return t["pnl"] / (risk_per_unit * t["qty"])


def strategy_analytics(job: dict) -> dict:
    """Dashboard analytics derived from one backtest job's closed trades:
    stat tiles, an equity (R) curve, an R-multiple distribution, a monthly
    win-rate table, and the exit-reason mix. Every number here is computed
    directly from the job's trades — nothing is simulated or estimated.
    """
    trades = [t for t in (job.get("trades") or []) if t.get("exitTime") is not None]
    trades.sort(key=lambda t: t["exitTime"])

    if not trades:
        return {
            "trades": 0, "netPnl": 0, "winRate": 0, "profitFactor": 0,
            "expectancyR": 0, "maxDrawdown": 0, "sharpe": 0, "sqn": 0,
            "equityCurve": [], "distribution": [], "monthly": [], "exitReasons": [],
            "recentTrades": [],
        }

    rs = [_r_multiple(t) for t in trades]
    valid_rs = [r for r in rs if r is not None]

    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] < 0]
    gross_win = sum(t["pnl"] for t in wins)
    gross_loss = sum(t["pnl"] for t in losses)
    net_pnl = sum(t["pnl"] for t in trades)

    # Equity curve in R (cumulative), one point per closed trade.
    equity_curve = []
    cum_r = 0.0
    for t, r in zip(trades, rs):
        cum_r += r or 0.0
        equity_curve.append({"time": t["exitTime"], "cumR": round(cum_r, 3), "pnl": t["pnl"]})

    # Max drawdown, computed on cumulative $ P&L (peak-to-trough).
    peak = -float("inf")
    max_dd = 0.0
    cum = 0.0
    for t in trades:
        cum += t["pnl"]
        peak = max(peak, cum)
        max_dd = min(max_dd, cum - peak)

    # Sharpe / SQN from the per-trade R distribution (Van Tharp's SQN =
    # mean(R)/stdev(R) * sqrt(N); "Sharpe" here is the same ratio without the
    # sqrt(N) term — a per-trade risk-adjusted return, not a daily-return Sharpe).
    sharpe = sqn = 0.0
    if len(valid_rs) >= 2:
        mean_r = sum(valid_rs) / len(valid_rs)
        var_r = sum((r - mean_r) ** 2 for r in valid_rs) / (len(valid_rs) - 1)
        std_r = var_r ** 0.5
        if std_r > 0:
            sharpe = round(mean_r / std_r, 2)
            sqn = round(mean_r / std_r * (len(valid_rs) ** 0.5), 2)

    # R-multiple distribution, bucketed into 0.5R-wide bins from -3R to +3R+.
    buckets: dict[float, int] = {}
    for r in valid_rs:
        clamped = max(-3.0, min(3.0, r))
        edge = int(clamped // 0.5) * 0.5
        buckets[edge] = buckets.get(edge, 0) + 1
    distribution = [{"bucket": edge, "count": buckets[edge]} for edge in sorted(buckets)]

    # Monthly win-rate table (year -> month -> {trades, winRate, avgR}).
    monthly_map: dict[str, dict] = {}
    for t, r in zip(trades, rs):
        ts = datetime.fromtimestamp(t["exitTime"], tz=timezone.utc)
        key = f"{ts.year}-{ts.month:02d}"
        m = monthly_map.setdefault(key, {"year": ts.year, "month": ts.month, "trades": [], "rs": []})
        m["trades"].append(t)
        if r is not None:
            m["rs"].append(r)
    monthly = []
    for key in sorted(monthly_map):
        m = monthly_map[key]
        mwins = [t for t in m["trades"] if t["pnl"] > 0]
        monthly.append({
            "year": m["year"], "month": m["month"], "trades": len(m["trades"]),
            "winRate": round(len(mwins) / len(m["trades"]) * 100, 1),
            "avgR": round(sum(m["rs"]) / len(m["rs"]), 2) if m["rs"] else None,
        })

    # Exit-reason mix (target / stop / end_of_data / ...) — real, derivable
    # substitute for anything requiring market-context data we don't have.
    reason_counts: dict[str, int] = {}
    for t in trades:
        reason_counts[t["reason"]] = reason_counts.get(t["reason"], 0) + 1
    exit_reasons = [{"reason": k, "count": v} for k, v in sorted(reason_counts.items(), key=lambda kv: -kv[1])]

    return {
        "trades": len(trades),
        "netPnl": round(net_pnl, 2),
        "winRate": round(len(wins) / len(trades) * 100, 1),
        "profitFactor": round(abs(gross_win / gross_loss), 2) if gross_loss != 0 else (None if gross_win > 0 else 0),
        "expectancyR": round(sum(valid_rs) / len(valid_rs), 3) if valid_rs else 0,
        "maxDrawdown": round(max_dd, 2),
        "sharpe": sharpe,
        "sqn": sqn,
        "avgWin": round(gross_win / len(wins), 2) if wins else 0,
        "avgLoss": round(gross_loss / len(losses), 2) if losses else 0,
        "largestWin": round(max((t["pnl"] for t in wins), default=0), 2),
        "largestLoss": round(min((t["pnl"] for t in losses), default=0), 2),
        "equityCurve": equity_curve,
        "distribution": distribution,
        "monthly": monthly,
        "exitReasons": exit_reasons,
        "recentTrades": [
            {**t, "r": round(r, 2) if r is not None else None}
            for t, r in list(zip(trades, rs))[-25:][::-1]
        ],
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
        # The bar size this ran on — the chart switches to it when the job is
        # selected, so trades are drawn on the timeframe that produced them.
        "interval": (strategy or {}).get("interval", "1min"),
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
