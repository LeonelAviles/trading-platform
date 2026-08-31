# Phase 2 — Backtester v2 and validation

Status: **done** (2026-08-31). Spec: PLATFORM-SPEC.md §4.3, §4.5, §4.6, §5 Phase 2.
Replaces `SETUP-ENGINE.md` and the legacy `nautilus_backtest.py` / `nautilus_runner.py` job folders.

## What the engine now gets right (the §2 bug list)

| Bug | Fix |
|---|---|
| PnL in points | `engine/pnl.py`: $ = points × multiplier × contracts (ES $50/pt, NQ $20/pt), net of `commission_per_side` × contracts × 2, all from `config/instruments.yaml` |
| Equity-style sizing | `fixed_contracts`, `fixed_risk` (`floor(account × risk% / (stop ticks × tick value))`, min 1, max `maxContracts`), `vol_scaled` |
| No costs | `PerContractFeeModel` on the simulated venue, `FillModel(prob_slippage=1)` = deterministic 1-tick market slippage, limits fill one tick worse (≈ trade-through) |
| Session in UTC | every window (`entryWindow`, `noTradeWindows`, `flattenAt`) is New York wall clock via `engine/session.py`; legacy v1 UTC sessions are converted on the run's first date and clamped to RTH |
| Blocking, no holdout | SQLite job queue (`engine/jobs.py`), one subprocess at a time; windows `is | wf1–3 | oos | full` from the frozen `splits.json` |

## Architecture

```
routers/backtests.py  POST /api/backtests {strategyId, mode?, windowKind?, dateFrom?, dateTo?}
                      POST /api/backtests/validate {strategyId, mode?}      -> IS + WF1..3 jobs
                      GET  /api/backtests/:id/validation                    -> report (OOS hidden until an oos row exists)
        │
engine/jobs.py        backtests row (SQLite) + backtests/<id>/{strategy.json, trades.json, worker.log}; queue thread
        │  subprocess: python -m engine.backtest_worker spec.json from to mode out.json
engine/backtest_worker.py
        ├─ exec_params()      spec (v2 subset or legacy v1) -> execution parameters
        ├─ ExecStrategy       NautilusTrader Strategy: the execution layer
        │     rules: engine/rules.py  RuleSource (V1Rules, TestOpenCloseRules; Phase 3 adds SpecRules)
        │     ledger: engine/ledger.py  v2 trade records, MAE/MFE, slippage, session date, regime tags
        └─ run_backtest()     venue SIM / MARGIN / NETTING / USD, catalog streamed one session at a time
engine/analytics.py   $ metrics, daily-return Sharpe/Sortino/Calmar, per-regime/hour/exit tables
engine/validation.py  windows from splits.json, report() = IS/WF/OOS + Monte Carlo + DSR + verdict
engine/monte_carlo.py bootstrap (1000) + skip test (drop 10%, 200 runs)
engine/deflated_sharpe.py  Bailey & López de Prado DSR on daily returns, trials = lineage trialIndex
engine/verdict.py     evaluate(job, risk) -> pass | fail | untestable against the risk profile
```

### Execution modes

| Mode | Data | Fills | Notes |
|---|---|---|---|
| `bars` | catalog 1-minute `Bar` (EXTERNAL), higher timeframes aggregated in-engine (`@1-MINUTE-EXTERNAL`); per-bar delta/buy/sell from the `bars_1m` sidecar | market at the signal bar's close ± 1 tick; stop/target evaluated on later bars' high/low, **stop first when both are touched**, booked at the level (stop − slippage) | ~5 s for 105 sessions |
| `ticks` (default for v2 specs) | catalog `TradeTick`, bars aggregated in-engine from prints; delta from aggressor sides | market at the venue's last price ± 1 tick; stop-market fills on the first print at/through the trigger; target is a resting reduce-only limit; brackets are placed once the entry is fully filled (market orders fill across prints) | ~3 s per session |
| `l3` | falls back to `ticks` with `meta.note` until Phase 5 writes `OrderBookDelta` for replay-cached days | | |

Common to both: forced flatten at `flattenAt` (default RTH end − 2 min), time stop, daily loss limit
(`risk.dailyLossLimitPct`), cooldown bars, consecutive-loss halt, max trades per day, entry only inside
`entryWindow` and outside `noTradeWindows`. Stop/target distances are re-anchored to the actual entry
fill; rule-provided structure levels (Phase 3) stay absolute.

### Trade record

```
{id, direction, contracts, entryTime, entryPrice, exitTime, exitPrice, stopPrice, targetPrice, exitReason,
 pnlPoints, pnlTicks, grossPnlUsd, commissionUsd, pnlUsd, slippageTicks, r, mae, mfe (ticks), barsHeld,
 sessionDate, regimeTags[], entryContextId, pnl, reason, qty}      # last three = legacy aliases
```
plus `dailyReturns: [{date, pnlUsd, returnPct}]` for every session in the window (zeros included).

### Validation protocol

`splits.json` (frozen at ingest, 70/30) → `is`; IS cut into 4 equal blocks → `wf1..3` test blocks 2..4;
`oos` = last 30 %, run **only** by finalize (Phase 4); `full` for human review on the chart. The agent's
`run_backtest` tool now runs `is` only. `validation.report()` assembles the latest row per window,
Monte Carlo and DSR from the IS trades, and the verdict against the strategy's risk profile
(defaults from §4.6 when the strategy has none). A finished IS job stores its verdict on the row so the
picker can show a chip.

## Frontend

AnalysisPanel tabs **Validation** (IS / WF / OOS-hidden table, verdict banner, DSR), **Monte Carlo**
(bootstrap + skip-test percentiles) and **Regimes** (per-tag and per-hour tables); ReviewPicker chips for
window, mode and verdict. `createBacktest(strategyId, {mode, windowKind})`, `createValidation`,
`fetchBacktestValidation`, `fetchBacktestAnalytics` in `api.js`.

## Results on the legacy strategies (bars mode, full window, Apr–Jul ES)

| Strategy | Trades | Net $ | Commission | PF | Exp R | Max DD % | Exits |
|---|---|---|---|---|---|---|---|
| ORB (`7d2140d663ec`) | 124 | +5,892 | 558 | 1.08 | 0.06 | 29.2 | stop 64 / target 16 / flatten 44 |
| ORB tighter (`342f508cd007`) | 218 | −7,569 | 981 | 0.91 | −0.05 | 23.7 | stop 153 / target 54 / flatten 11 |

Both are legitimately mediocre once costs and sizing are real — the point of Phase 2.

## Tests

`test_pnl.py` (ES/NQ $, ticks, R, sizing, slippage), `test_session_tz.py`, `test_monte_carlo.py`,
`test_deflated_sharpe.py`, `test_verdict.py`, `test_backtest_worker.py` (hand-computed PnL incl.
commission for "long at open, flat at close" on a synthetic catalog; bars vs ticks agreement; stop/target
exits; fixed-risk sizing + daily-loss halt; legacy v1 rules), `test_jobs.py` (queue → row states →
analytics; IS + WF1–3 rows and no OOS row until requested; delete).

## Deferred

- `l3` execution (needs Phase 5 deltas). Trailing stop, breakeven, scale-out, limit/stop entries,
  `direction: both`, structure stops/targets: Phase 3 with the DSL.
- Regime tags on legacy (pre-Phase-2) job records are empty; re-run them to get tags.
