"""Reproducible, standalone ES closing-momentum research (no platform mutations).

Run from the repo root: .venv/bin/python backend/scripts/test_es_closing_momentum.py
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import duckdb
import numpy as np
import yaml

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs/research/2026-09-07-es-closing-momentum"


def pnl(direction, entry, exit_price, multiplier, tick_size, commission, slippage):
    gross = direction * (exit_price - entry) * multiplier
    cost = 2 * commission + 2 * slippage * tick_size * multiplier if direction else 0
    return gross, gross - cost


def signal(current, previous):
    return int(current > previous) - int(current < previous)


def summarize(rows):
    x = np.array([r["net_usd"] for r in rows], dtype=float)
    if not len(x):
        return {"sessions": 0}
    equity = np.r_[0, x.cumsum()]
    losses = -x[x < 0].sum()
    rng = np.random.default_rng(20260907)
    # Resample consecutive five-session blocks to retain some serial dependence.
    starts = rng.integers(0, len(x), size=(5000, (len(x) + 4) // 5))
    idx = ((starts[:, :, None] + np.arange(5)) % len(x)).reshape(5000, -1)[:, :len(x)]
    ci = np.percentile(x[idx].mean(axis=1), [2.5, 97.5])
    return {
        "sessions": len(x), "trades": sum(r["direction"] != 0 for r in rows),
        "net_usd": round(float(x.sum()), 2), "mean_net_usd": round(float(x.mean()), 2),
        "gross_usd": round(sum(r["gross_usd"] for r in rows), 2),
        "win_rate_pct": round(float((x > 0).mean() * 100), 2),
        "profit_factor": round(float(x[x > 0].sum() / losses), 3) if losses else None,
        "max_drawdown_usd": round(float((np.maximum.accumulate(equity) - equity).max()), 2),
        "annualized_daily_sharpe": round(float(x.mean() / x.std(ddof=1) * np.sqrt(252)), 3)
        if len(x) > 1 and x.std(ddof=1) else None,
        "mean_net_95pct_block_bootstrap_ci": [round(float(v), 2) for v in ci],
    }


def candidates(daily, front):
    """Select today's contract using the preceding observed RTH day's mapping.

    Both signal prices must belong to that same outright, never different
    continuous-contract legs. Missing/short prior sessions are excluded.
    """
    dates = sorted({d for d, _ in daily})
    eligible, skipped = [], []
    for prev, today in zip(dates, dates[1:]):
        sym = front.get(prev)
        a, b = daily.get((prev, sym)), daily.get((today, sym))
        reason = None
        if a is None or b is None:
            reason = "prior-selected contract unavailable"
        elif a["n"] != 390 or b["n"] != 390:
            reason = "short or incomplete RTH session (current or prior)"
        elif any(v is None for v in (a["close"], b["signal"], b["entry"], b["exit"])):
            reason = "missing signal/entry/exit boundary"
        if reason:
            skipped.append({"date": str(today), "symbol": sym, "reason": reason})
            continue
        eligible.append({"date": str(today), "previous_date": str(prev), "symbol": sym,
                         "previous_close": a["close"], "signal_price": b["signal"],
                         "entry_reference": b["entry"], "exit_reference": b["exit"],
                         "momentum_direction": signal(b["signal"], a["close"])})
    return eligible, skipped


def main():
    config = yaml.safe_load((ROOT / "backend/config/instruments.yaml").read_text())
    cost = config["roots"]["ES"]
    con = duckdb.connect()
    source = str(ROOT / "data/market/bars_1m/root=ES/date=*/part.parquet")
    # Bar timestamps mark their OPEN. 15:29 close is known at 15:30;
    # execution uses the following bar open, not the signal bar's fill.
    raw = con.execute("""
        WITH bars AS (
          SELECT *, timezone('America/New_York', to_timestamp(ts / 1000000000.0)) AS et
          FROM read_parquet(?)
        )
        SELECT et::DATE AS day, symbol,
          count(*) FILTER (WHERE et::TIME >= TIME '09:30' AND et::TIME < TIME '16:00') AS n,
          max(close) FILTER (WHERE et::TIME = TIME '15:59') AS close,
          max(close) FILTER (WHERE et::TIME = TIME '15:29') AS signal,
          max(open) FILTER (WHERE et::TIME = TIME '15:30') AS entry,
          max(open) FILTER (WHERE et::TIME = TIME '16:00') AS exit
        FROM bars GROUP BY 1,2 HAVING n > 0 ORDER BY 1,2
    """, [source]).fetchall()
    duplicates = con.execute("SELECT count(*) FROM (SELECT symbol, ts FROM read_parquet(?) GROUP BY 1,2 HAVING count(*) > 1)", [source]).fetchone()[0]
    if duplicates:
        raise ValueError(f"Duplicate symbol/timestamp bars: {duplicates}")
    daily = {(r[0], r[1]): dict(zip(("n", "close", "signal", "entry", "exit"), r[2:])) for r in raw}
    front = dict(con.execute("SELECT date, symbol FROM read_parquet(?) WHERE root='ES'",
                            [str(ROOT / "data/market/front_month.parquet")]).fetchall())
    con.close()
    eligible, skipped = candidates(daily, front)
    split = json.loads((ROOT / "data/market/splits.json").read_text())["roots"]["ES"]
    train = set(split["inSample"])
    holdout = set(split["outOfSample"])
    rows = []
    base_slippage = config["costs"]["slippage_ticks_market"]
    for d in eligible:
        for strategy, direction in (("momentum", d["momentum_direction"]), ("always_long", 1)):
            for slippage in (base_slippage, base_slippage + 1):
                gross, net = pnl(direction, d["entry_reference"], d["exit_reference"],
                                 cost["multiplier"], cost["tick_size"], cost["commission_per_side"], slippage)
                rows.append({**d, "strategy": strategy, "direction": direction,
                             "slippage_ticks_per_side": slippage,
                             "split": "existing_in_sample" if d["date"] in train else
                             "existing_holdout" if d["date"] in holdout else "unassigned",
                             "gross_usd": gross, "net_usd": net})
    summaries = {}
    for strategy in ("momentum", "always_long"):
        for slip in (base_slippage, base_slippage + 1):
            subset = [r for r in rows if r["strategy"] == strategy and r["slippage_ticks_per_side"] == slip]
            summaries[f"{strategy}_{slip}tick"] = {
                label: summarize([r for r in subset if label == "all" or r["split"] == label])
                for label in ("all", "existing_in_sample", "existing_holdout")}
    report = {
        "method": "One contract; signal previous RTH close to 15:30; entry 15:30 bar open; exit 16:00 bar open; no stops/targets; zero signal stays flat.",
        "source": "https://ssrn.com/abstract=3760365",
        "contract_selection": "Previous observed RTH day's front-month mapping, same outright for both signal prices and fills.",
        "cost_config": cost, "base_slippage_ticks_per_side": base_slippage,
        "eligible_sessions": len(eligible), "skipped": skipped,
        "first_observed_session": str(min(d for d, _ in daily)),
        "last_observed_session": str(max(d for d, _ in daily)),
        "limitations": [
            "First observed RTH day is warmup only. Short/incomplete current or previous RTH days excluded.",
            "Minute-bar execution proxy, not executable bid/ask quotes or queue simulation.",
            "Existing holdout predates existing in-sample period; this is a retrospective audit, not forward validation.",
            "Both existing windows are evaluated and are now exposed for this hypothesis; future tuning needs fresh data.",
            "Sharpe annualizes eligible-session PnL at 252 sessions/year; excluded days are not included.",
            "Five-session circular block bootstrap intervals are approximate given this short sample.",
        ],
        "summaries": summaries,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "results.json").write_text(json.dumps(report, indent=2) + "\n")
    if rows:
        with (OUT / "trades.csv").open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    print(json.dumps({"eligible_sessions": len(eligible), "skipped_sessions": len(skipped), "summaries": summaries}, indent=2))


if __name__ == "__main__":
    main()
