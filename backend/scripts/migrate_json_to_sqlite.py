"""Import the legacy JSON-file records into SQLite (PLATFORM-SPEC.md §5 Phase 0).

    python scripts/migrate_json_to_sqlite.py [--dry-run]

- backtests/<id>/job.json  -> backtests rows (mode=bars, window_kind=full,
  trades_path pointing at the existing trades.json, metrics_json = summary).
- backend/strategies/*.json -> strategies rows, through the v1->v2 converter
  (`engine.v1_to_v2`, Phase 3). Until that module exists the strategies step
  is skipped with a notice, so this script is safe to run at any point and
  idempotent: rows whose id already exists are left untouched.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

import database  # noqa: E402
from models import Backtest, Strategy, utc_now  # noqa: E402

REPO_ROOT = BACKEND_DIR.parent
JOBS_DIR = REPO_ROOT / "backtests"
STRATEGIES_DIR = BACKEND_DIR / "strategies"


def _load_converter():
    try:
        from engine.v1_to_v2 import convert_v1_to_v2  # type: ignore
    except ImportError:
        return None
    return convert_v1_to_v2


def migrate_strategies(db, dry_run: bool) -> tuple[int, int]:
    convert = _load_converter()
    if convert is None:
        print("strategies: skipped — engine.v1_to_v2 converter arrives in Phase 3")
        return 0, 0
    imported = skipped = 0
    parents: dict[str, str | None] = {}
    for f in sorted(STRATEGIES_DIR.glob("*.json")) if STRATEGIES_DIR.exists() else []:
        v1 = json.loads(f.read_text(encoding="utf-8"))
        sid = v1.get("id") or f.stem
        if db.get(Strategy, sid) is not None:
            skipped += 1
            continue
        spec = convert(v1)
        parents[sid] = (spec.get("lineage") or {}).get("parentId")
        row = Strategy(
            id=sid,
            name=spec.get("name") or v1.get("name") or sid,
            status=spec.get("status", "draft"),
            origin_type=(spec.get("origin") or {}).get("type", "manual"),
            origin_id=(spec.get("origin") or {}).get("sourceId"),
            parent_id=None,                      # set in a second pass (FK order)
            spec_json=spec,
            risk_json=spec.get("risk"),
        )
        if v1.get("createdAt"):
            row.created_at = v1["createdAt"]
        print(f"strategies: import {sid} ({row.name})")
        if not dry_run:
            db.add(row)
        imported += 1
    if not dry_run:
        db.flush()
        for sid, pid in parents.items():
            if pid and db.get(Strategy, pid) is not None:
                db.get(Strategy, sid).parent_id = pid
    return imported, skipped


def migrate_backtests(db, dry_run: bool) -> tuple[int, int]:
    imported = skipped = 0
    if not JOBS_DIR.exists():
        return 0, 0
    for d in sorted(JOBS_DIR.iterdir()):
        job_file = d / "job.json"
        if not job_file.exists():
            continue
        job = json.loads(job_file.read_text(encoding="utf-8"))
        jid = job.get("id") or d.name
        if db.get(Backtest, jid) is not None:
            skipped += 1
            continue
        strategy_id = job.get("strategyId")
        if strategy_id and db.get(Strategy, strategy_id) is None:
            # Keep the reference readable in metrics_json; FK stays NULL so
            # the row inserts cleanly before Phase 3 migrates strategies.
            strategy_fk = None
        else:
            strategy_fk = strategy_id
        trades_path = d / "trades.json"
        metrics = dict(job.get("summary") or {})
        metrics["legacyStrategyId"] = strategy_id
        metrics["strategyName"] = job.get("strategyName")
        metrics["symbol"] = job.get("symbol")
        metrics["interval"] = job.get("interval")
        status = job.get("status") or "error"
        if status in ("preparing", "running"):
            status, message = "error", "interrupted before migration"
        else:
            message = job.get("message")
        row = Backtest(
            id=jid,
            strategy_id=strategy_fk,
            mode="bars",
            window_kind="full",
            status=status,
            message=message,
            trades_path=str(trades_path.relative_to(REPO_ROOT)) if trades_path.exists() else None,
            metrics_json=metrics,
            created_at=job.get("createdAt") or utc_now(),
        )
        print(f"backtests: import {jid} ({status}, strategy {strategy_id})")
        if not dry_run:
            db.add(row)
        imported += 1
    return imported, skipped


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    database.init_db()
    with database.session_scope() as db:
        s_new, s_skip = migrate_strategies(db, args.dry_run)
        b_new, b_skip = migrate_backtests(db, args.dry_run)
        if args.dry_run:
            db.rollback()
    print(
        f"done: strategies +{s_new} (skipped {s_skip}), backtests +{b_new} (skipped {b_skip})"
        + (" [dry run]" if args.dry_run else "")
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
