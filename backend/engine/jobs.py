"""Backtest jobs — SQLite rows + subprocess worker (PLATFORM-SPEC.md §4.7, §5 Phase 2).

Replaces the job.json folders of `nautilus_runner`. A job is a `backtests`
row; its trade list lives on disk at `backtests/<id>/trades.json`
(`trades_path`). One worker subprocess runs at a time (a queue), which is
what keeps NautilusTrader + DuckDB + the API inside the 8.6 GB budget.

`get_job()` returns the dict shape the review page has
always consumed: {id, createdAt, strategyId, strategyName, symbol, interval,
status, message, source, summary, trades, mode, windowKind, dateFrom, dateTo}.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
from datetime import date, datetime, timezone
from pathlib import Path

import database
from config.instruments import load_instruments
from engine import analytics
from engine import pnl as P
from engine.session import session_date, NS
from models import Backtest, new_id, utc_now

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent
JOBS_DIR = REPO_ROOT / "backtests"
STARTING_EQUITY = 100_000.0
WORKER_TIMEOUT_S = 3600

_queue: queue.Queue = queue.Queue()
_worker_thread: threading.Thread | None = None
_lock = threading.Lock()
_running: set[str] = set()


def engine_status() -> dict:
    try:
        import nautilus_trader
        return {"engine": "nautilustrader", "installed": True, "version": nautilus_trader.__version__}
    except Exception:
        return {"engine": "nautilustrader", "installed": False, "version": None}


# ----------------------------------------------------------------------------
# Row <-> job dict
# ----------------------------------------------------------------------------

def _job_dir(job_id: str) -> Path:
    return JOBS_DIR / job_id


def _row_to_job(row: Backtest, with_trades: bool = False) -> dict:
    m = dict(row.metrics_json or {})
    job = {
        "id": row.id, "createdAt": row.created_at, "finishedAt": row.finished_at,
        "strategyId": row.strategy_id or m.get("legacyStrategyId"),
        "strategyName": m.get("strategyName"), "symbol": m.get("symbol"), "interval": m.get("interval", "1min"),
        "status": row.status, "message": row.message, "source": "nautilus",
        "mode": row.mode, "windowKind": row.window_kind, "dateFrom": row.date_from, "dateTo": row.date_to,
        "summary": m.get("summary"),
        "metrics": {k: v for k, v in m.items() if k not in ("summary", "strategyName", "symbol", "interval", "legacyStrategyId")} or None,
    }
    if with_trades:
        job["trades"] = load_trades(row.id, row.trades_path)
    return job


def _read_trades_file(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def normalize_trade(t: dict) -> dict:
    """Legacy (pre-Phase-2) records get the v2 keys they lack."""
    if "pnlUsd" in t:
        return t
    t = dict(t)
    t["pnlUsd"] = t.get("pnl", 0.0)
    t["exitReason"] = t.get("reason", "unknown")
    t["contracts"] = t.get("qty", 1)
    t.setdefault("commissionUsd", 0.0)
    t.setdefault("slippageTicks", 0.0)
    t.setdefault("mae", 0.0)
    t.setdefault("mfe", 0.0)
    t.setdefault("barsHeld", 0)
    t.setdefault("regimeTags", [])
    t["r"] = t.get("r") if t.get("r") is not None else P.r_multiple(t["entryPrice"], t["exitPrice"], t.get("stopPrice"), t["direction"])
    t["sessionDate"] = t.get("sessionDate") or session_date(int(t["entryTime"]) * NS).isoformat()
    return t


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def load_trades(job_id: str, trades_path: str | None = None) -> list[dict]:
    path = (Path(trades_path) if Path(trades_path).is_absolute() else REPO_ROOT / trades_path) if trades_path else _job_dir(job_id) / "trades.json"
    data = _read_trades_file(path)
    return [normalize_trade(t) for t in data.get("trades", [])]


def load_daily_returns(job_id: str) -> list[dict]:
    return _read_trades_file(_job_dir(job_id) / "trades.json").get("dailyReturns", [])


def list_jobs() -> list[dict]:
    with database.session_scope() as db:
        rows = db.query(Backtest).order_by(Backtest.created_at.desc()).all()
        out = []
        for r in rows:
            if r.status in ("queued", "running") and r.id not in _running:
                r.status, r.message = "error", "interrupted by backend restart"
            out.append(_row_to_job(r))
        return out


def get_job(job_id: str) -> dict | None:
    with database.session_scope() as db:
        row = db.get(Backtest, job_id)
        if row is None:
            return None
        return _row_to_job(row, with_trades=True)


def delete_job(job_id: str) -> bool:
    import shutil

    with database.session_scope() as db:
        row = db.get(Backtest, job_id)
        if row is None:
            return False
        db.delete(row)
    shutil.rmtree(_job_dir(job_id), ignore_errors=True)
    return True


def strategy_analytics(job: dict) -> dict:
    """Analytics payload for one job (cached in metrics_json when the run finishes)."""
    m = job.get("metrics") or {}
    if m.get("analytics"):
        return m["analytics"]
    trades = [normalize_trade(t) for t in (job.get("trades") or [])]
    daily = load_daily_returns(job["id"])
    return analytics.compute(trades, daily, float(m.get("accountSize") or STARTING_EQUITY))


# ----------------------------------------------------------------------------
# Creating and running jobs
# ----------------------------------------------------------------------------

def default_mode(strategy: dict) -> str:
    return ((strategy.get("execution") or {}).get("mode")) or ("bars" if "conditions" in strategy else "ticks")


def _symbol(strategy: dict) -> str:
    return (strategy.get("instrument") or {}).get("symbol") or strategy.get("symbol")


def create_job(strategy: dict, *, mode: str | None = None, window_kind: str = "full",
               date_from: date | None = None, date_to: date | None = None) -> dict:
    from engine import validation

    mode = mode or default_mode(strategy)
    symbol = _symbol(strategy)
    if date_from is None or date_to is None:
        root = load_instruments().root_for_symbol(symbol)
        if root is None:
            raise ValueError(f"unknown symbol {symbol!r}")
        date_from, date_to = validation.window_for(root.root, window_kind)
    job_id = new_id()
    job_dir = _job_dir(job_id)
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "strategy.json").write_text(json.dumps(strategy, indent=2), encoding="utf-8")
    metrics = {"strategyName": strategy.get("name"), "symbol": symbol,
               "interval": (strategy.get("timeframes") or {}).get("primary") or strategy.get("interval", "1min"),
               "accountSize": float((strategy.get("risk") or {}).get("accountSize") or STARTING_EQUITY)}
    with database.session_scope() as db:
        row = Backtest(id=job_id, strategy_id=strategy.get("id") if _strategy_exists(db, strategy.get("id")) else None,
                       mode=mode, window_kind=window_kind, date_from=date_from.isoformat(), date_to=date_to.isoformat(),
                       status="queued", message="queued", trades_path=_rel(job_dir / "trades.json"),
                       metrics_json={**metrics, "legacyStrategyId": strategy.get("id")})
        db.add(row)
        db.flush()
        job = _row_to_job(row)
    return job


def _strategy_exists(db, strategy_id) -> bool:
    if not strategy_id:
        return False
    from models import Strategy

    return db.get(Strategy, strategy_id) is not None


def _set(job_id: str, **fields) -> None:
    with database.session_scope() as db:
        row = db.get(Backtest, job_id)
        if row is None:
            return
        for k, v in fields.items():
            setattr(row, k, v)


def _run_job(job_id: str) -> None:
    job_dir = _job_dir(job_id)
    with database.session_scope() as db:
        row = db.get(Backtest, job_id)
        if row is None:
            return
        mode, date_from, date_to = row.mode, row.date_from, row.date_to
        metrics = dict(row.metrics_json or {})
    _set(job_id, status="running", message=f"running NautilusTrader backtest ({mode}, {date_from}..{date_to})")
    out_path = job_dir / "trades.json"
    log_path = job_dir / "worker.log"
    try:
        with open(log_path, "w", encoding="utf-8") as lf:
            proc = subprocess.run(
                [sys.executable, "-m", "engine.backtest_worker", str(job_dir / "strategy.json"), date_from, date_to, mode, str(out_path)],
                cwd=str(BACKEND_DIR), stdout=lf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                timeout=WORKER_TIMEOUT_S, text=True, env={**os.environ, "PYTHONWARNINGS": "ignore"},
            )
        if proc.returncode != 0 or not out_path.exists():
            tail = log_path.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-4:]
            raise RuntimeError("backtest failed: " + " ".join(tail) if tail else "backtest failed — see worker.log")
        result = json.loads(out_path.read_text(encoding="utf-8"))
        trades = result["trades"]
        stats = analytics.compute(trades, result.get("dailyReturns"), float(metrics.get("accountSize") or STARTING_EQUITY))
        metrics.update({
            "summary": result["summary"], "meta": result.get("meta"), "analytics": stats,
            **{k: stats[k] for k in ("trades", "netPnl", "winRate", "profitFactor", "expectancyR", "maxDrawdownPct", "sharpe", "sortino", "commission")},
        })
        _set(job_id, status="done", message=None, metrics_json=metrics, finished_at=utc_now())
        _attach_verdict(job_id)
    except Exception as e:
        _set(job_id, status="error", message=str(e), finished_at=utc_now())
    finally:
        with _lock:
            _running.discard(job_id)


def _attach_verdict(job_id: str) -> None:
    """After an in-sample run, store the verdict on the row so lists can show
    a chip without recomputing Monte Carlo per request."""
    from engine import validation

    with database.session_scope() as db:
        row = db.get(Backtest, job_id)
        if row is None or row.window_kind != "is":
            return
        sid = row.strategy_id or (row.metrics_json or {}).get("legacyStrategyId")
        mode = row.mode
    if not sid:
        return
    try:
        rep = validation.report(sid, mode=mode)
    except Exception:
        return
    if rep.get("verdict"):
        with database.session_scope() as db:
            row = db.get(Backtest, job_id)
            if row is not None:
                row.metrics_json = {**(row.metrics_json or {}), "verdict": {k: rep["verdict"][k] for k in ("status", "passes", "untestable", "failures", "score")}}


def _worker_loop():
    while True:
        job_id = _queue.get()
        try:
            _run_job(job_id)
        finally:
            _queue.task_done()


def start(job_id: str) -> None:
    global _worker_thread
    with _lock:
        _running.add(job_id)
        if _worker_thread is None or not _worker_thread.is_alive():
            _worker_thread = threading.Thread(target=_worker_loop, daemon=True, name="backtest-worker")
            _worker_thread.start()
    _queue.put(job_id)


def start_backtest(strategy: dict, *, mode: str | None = None, window_kind: str = "full",
                   date_from: date | None = None, date_to: date | None = None) -> dict:
    job = create_job(strategy, mode=mode, window_kind=window_kind, date_from=date_from, date_to=date_to)
    start(job["id"])
    return job


def run_sync(strategy: dict, *, mode: str | None = None, window_kind: str = "full", timeout_s: float = WORKER_TIMEOUT_S) -> dict:
    """Blocking helper for scripts/tests."""
    import time

    job = start_backtest(strategy, mode=mode, window_kind=window_kind)
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        cur = get_job(job["id"])
        if cur["status"] in ("done", "error"):
            return cur
        time.sleep(0.5)
    raise TimeoutError(f"backtest {job['id']} still running after {timeout_s}s")


def run_validation(strategy: dict, mode: str | None = None) -> list[dict]:
    """IS + WF1–3 as separate queued jobs (never OOS — that is a deliberate, separate look)."""
    from engine import validation

    root = load_instruments().root_for_symbol(_symbol(strategy)).root
    kinds = [k for k in ("is", "wf1", "wf2", "wf3") if k in validation.windows(root)]
    return [start_backtest(strategy, mode=mode, window_kind=k) for k in kinds]


def run_oos(strategy: dict, mode: str | None = None) -> dict:
    return start_backtest(strategy, mode=mode, window_kind="oos")
