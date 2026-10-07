# ES closing-momentum baseline — 2026-09-07

The simplified closing-momentum strategy did not show a profitable edge in the
available sample. It lost money before costs, after costs, and in both existing
data windows. This does not support deploying this baseline.

Research motivation: [Baltussen et al., Hedging Demand and Market Intraday
Momentum](https://ssrn.com/abstract=3760365). This is a sign-based trading
adaptation, not a reproduction of the paper's regressions or gamma-conditioned
tests.

## Fixed rules and execution

- One ES contract. At 15:30 America/New_York, compare the completed 15:29
  minute's close with the preceding observed RTH session's 15:59 minute close.
- Long above the previous close; short below; flat on equality.
- Entry uses the 15:30 minute's open, after the signal; exit uses the 16:00
  minute's open. No stops, targets, optimization, or overnight position.
- Choose the contract using the previous observed RTH day's front-month
  mapping. Use that same outright for the previous close, signal, and fills.
  This avoids using today's final volume ranking or comparing prices across
  two different contract legs.
- Require complete 390-minute RTH sessions for both current and prior days
  and all execution boundaries. Exclude short sessions and their next session.
- Costs from `backend/config/instruments.yaml`: $2.25 commission per side,
  $50 per point, and 0.25-point ticks. Base slippage: one tick per side
  ($29.50 round trip including commissions). Stress: two ticks per side
  ($54.50 round trip).

## Results

Data span: December 2025–July 2026. Of 172 observed RTH dates, the first is
warmup and 12 fail current/prior completeness requirements: 159 eligible trades.

| Strategy | Slippage per side | Gross P&L | Net P&L | Profit factor | Max drawdown |
|---|---:|---:|---:|---:|---:|
| Closing momentum | 1 tick | -$7,587.50 | -$12,278.00 | 0.699 | $14,702.00 |
| Closing momentum | 2 ticks | -$7,587.50 | -$16,253.00 | 0.623 | $18,602.00 |
| Always long, same window | 1 tick | $3,687.50 | -$1,003.00 | 0.971 | $5,831.00 |

Base momentum win rate: 44.65%; mean net P&L: -$77.22/trade. The approximate
95% five-session block-bootstrap interval for mean net P&L is [-$163.23, $6.04].
The interval includes zero; the short sample cannot establish that the effect
is permanently absent or justify reversing the signal without a new test.

Existing in-sample window (April–July 2026): 81 trades, -$4,639.50 net.
Existing holdout (December 2025–March 2026): 78 trades, -$7,638.50 net.
The holdout predates the in-sample window, so these are retrospective checks,
not forward validation. Both windows are now exposed for this hypothesis;
future tuning requires fresh validation data.

Execution uses minute-bar opens plus assumed slippage, not bid/ask or queue
simulation. No gamma filter was tested. The script is independent of the
platform strategy engine, so it supports a pure time exit without changing
global session settings, existing strategies, or database records.

## Reproduce

From the repository root:

```sh
.venv/bin/python backend/scripts/test_es_closing_momentum.py
cd backend
../.venv/bin/python -m pytest -q tests/test_closing_momentum_research.py
```

[Detailed results and exclusions](2026-09-07-es-closing-momentum/results.json)
and [per-session trade scenarios](2026-09-07-es-closing-momentum/trades.csv).
Each CSV session has four rows: two strategies times two cost assumptions.
