"""Validation windows and report (PLATFORM-SPEC.md §4.5).

Windows come from `data/market/splits.json` (frozen at ingest):

  is    the in-sample sessions (all of them while IS_FRACTION is 1.0 — with
        ~4 months of data there is no holdout yet)
  wf1–3 anchored walk-forward folds inside IS: IS is cut into 4 equal blocks;
        fold k tests block k+1 (blocks ≤ k are its "training" history — the
        spec has no fitted parameters, so each fold is a consistency check)
  oos   the frozen holdout — empty today; the window only exists once the
        split is re-frozen with a fraction below 1.0
  full  everything (human review on the chart) — equal to `is` today

`report(strategy_id, mode)` assembles the latest IS / WF / OOS rows for a
strategy into the structure `engine.verdict.evaluate` consumes, adding Monte
Carlo and the deflated Sharpe ratio computed from the IS trades.
"""

from __future__ import annotations

import json
from datetime import date

from engine import deflated_sharpe as dsr_mod
from engine import monte_carlo as mc
from engine import verdict as verdict_mod
from market.paths import get_paths

WINDOW_KINDS = ("is", "wf1", "wf2", "wf3", "oos", "full")


def _splits(root: str) -> dict | None:
    p = get_paths().splits
    if not p.exists():
        return None
    return json.loads(p.read_text()).get("roots", {}).get(root)


def windows(root: str) -> dict[str, tuple[str, str]]:
    """{kind: (date_from, date_to)} for every window that has sessions."""
    sp = _splits(root)
    if not sp or not sp.get("inSample"):
        return {}
    is_dates, oos_dates = sp["inSample"], sp.get("outOfSample", [])
    out = {"is": (is_dates[0], is_dates[-1])}
    n = len(is_dates)
    if n >= 4:
        edges = [round(n * k / 4) for k in range(5)]
        for k in (1, 2, 3):
            block = is_dates[edges[k]:edges[k + 1]]
            if block:
                out[f"wf{k}"] = (block[0], block[-1])
    if oos_dates:
        out["oos"] = (oos_dates[0], oos_dates[-1])
    out["full"] = (is_dates[0], (oos_dates or is_dates)[-1])
    return out


def window_for(root: str, kind: str) -> tuple[date, date]:
    w = windows(root)
    if kind not in w:
        raise ValueError(f"window '{kind}' not available for {root} (have {sorted(w)}); run scripts/ingest.py")
    a, b = w[kind]
    return date.fromisoformat(a), date.fromisoformat(b)


def report(strategy_id: str, mode: str | None = None, risk: dict | None = None, trial_index: int = 1,
           include_oos: bool = True) -> dict:
    """Assemble the validation report from the strategy's backtest rows."""
    import database
    from engine import jobs
    from models import Backtest

    with database.session_scope() as db:
        from sqlalchemy import or_

        # Rows reference a strategies row when it exists, else the legacy id kept in metrics_json.
        q = db.query(Backtest).filter(Backtest.status == "done").filter(
            or_(Backtest.strategy_id == strategy_id, Backtest.metrics_json["legacyStrategyId"].as_string() == strategy_id)
        )
        if mode:
            q = q.filter(Backtest.mode == mode)
        rows = q.order_by(Backtest.created_at.desc()).all()
        latest: dict[str, Backtest] = {}
        for r in rows:
            latest.setdefault(r.window_kind, r)
        picked = {k: {"id": r.id, "metrics": dict(r.metrics_json or {}), "dateFrom": r.date_from, "dateTo": r.date_to,
                      "mode": r.mode, "createdAt": r.created_at} for k, r in latest.items()}

    def metrics(kind):
        m = picked.get(kind)
        return dict(m["metrics"]) if m else None

    is_m = metrics("is")
    wf = [dict(metrics(f"wf{k}") or {}, window=f"wf{k}") for k in (1, 2, 3) if picked.get(f"wf{k}")]
    oos_m = metrics("oos") if include_oos else None
    monte = deflated = None
    if picked.get("is"):
        trades = jobs.load_trades(picked["is"]["id"])
        pnls = [t["pnlUsd"] for t in trades]
        account = float((risk or {}).get("accountSize") or (is_m or {}).get("accountSize") or 100_000)
        monte = mc.run_all(pnls, account) if pnls else None
        daily = jobs.load_daily_returns(picked["is"]["id"])
        if daily:
            deflated = dsr_mod.deflated_sharpe([d["returnPct"] / 100 for d in daily], trials=max(1, trial_index))
    job = {"inSample": is_m, "walkForward": wf, "outOfSample": oos_m, "monteCarlo": monte, "deflatedSharpe": deflated}
    v = verdict_mod.evaluate(job, risk) if is_m is not None else None
    return {
        "strategyId": strategy_id, "mode": mode, "windows": {k: {"id": v_["id"], "dateFrom": v_["dateFrom"], "dateTo": v_["dateTo"]} for k, v_ in picked.items()},
        "inSample": is_m, "walkForward": wf,
        "outOfSample": oos_m, "oosHidden": not include_oos or "oos" not in picked,
        "oosAvailable": "oos" in picked,
        "monteCarlo": monte, "deflatedSharpe": deflated,
        "verdict": v.to_dict() if v else None,
        "risk": verdict_mod.with_defaults(risk),
    }
