# Last-30-minute intraday momentum on ES: not present (2026-09-07)

**Verdict: negative.** The best-evidenced finding from the SSRN scan
([`2026-09-07-ssrn-futures-literature.md`](2026-09-07-ssrn-futures-literature.md), A1) — the move from the prior
close to 15:30 ET predicts the 15:30→16:00 move (Gao, Han, Li, Zhou 2018; Baltussen, Da, Lammers, Martens 2021 on 60+
futures including ES) — does not show up in our data at any usable size. The unconditional strategy loses after
costs, every pre-listed conditioning stays inside noise, and the short side is actively harmful because the last
half hour carries a small unconditional long drift. No platform validation run is warranted.

## Data and protocol

- ES front month, 2025-12-01 → 2026-07-31, 166 full RTH sessions (same universe as the key-levels study), joined to
  `front_month.parquet`. Day-pairs need the same contract on both days and no half day in between: 156 pairs
  (3 roll pairs, 7 without a usable prior session; 6 half days excluded: 12-24, 01-19, 02-16, 05-25, 06-19, 07-03).
- Hypothesis and variants V0–V8 were listed before running. Everything under "exploratory" is post-hoc.
- Prices: prior close = 15:59 bar close; 15:30 level = 15:29 bar close; strategy entry = 15:30 bar open; exit =
  15:58 bar open (the platform's default `flattenAt`). Costs per round trip from `instruments.yaml`: 1 tick slippage
  per side + $4.50 commission = 0.59 pt ($29.50). One contract.
- Move-size normaliser: trailing 20-session mean RTH range (no lookahead). Volume filter V4 uses full-day volume,
  which is not observable at 15:30; it is there only to bound what a causal volume filter could achieve.
- Script and outputs: [`2026-09-07-intraday-momentum/`](2026-09-07-intraday-momentum/) (`study.py`,
  `results.json`, `sessions.csv`).

## Predictability of the 15:30→16:00 move (points on points, n = 156)

| predictor | slope | t | R² | sign agreement | p (sign) |
|---|---|---|---|---|---|
| prior close → 15:30 (Baltussen et al.) | −0.002 | −0.09 | 0.000 | 47.7 % | 0.57 |
| prior close → 10:00 (Gao et al., first half hour) | +0.017 | +0.72 | 0.003 | 51.0 % | 0.81 |
| 15:00 → 15:30 (Gao et al., 12th half hour) | +0.157 | +2.20 | 0.030 | 49.7 % | 0.94 |
| control: prior close → 15:00 predicting 15:00 → 15:30 | +0.020 | +1.02 | 0.007 | 57.9 % | 0.05 |

The 12th-half-hour slope is driven by a few large days; sign agreement is a coin flip and the matching strategy
(X4 below) is flat. Baseline drift of the legs: 15:30→16:00 mean +0.48 pt (t 0.5, positive 49 %); 15:30 open →
15:58 open mean +1.31 pt (t 1.45, positive 56 %) — a small unconditional long lean that explains the long/short
asymmetry below.

Signed last-30 move by |early move| tercile (points earned by following the early sign): small +1.12, mid −4.96,
large +1.73 — non-monotonic, i.e. noise. Up days −0.51, down days −1.47.

## Strategy: enter 15:30 open in the early-move direction, flat 15:58

| variant | n | gross pt/trade | net pt/trade | net PF | win | t (net) | dev Dec–Apr PF | diag May–Jul PF |
|---|---|---|---|---|---|---|---|---|
| V0 unconditional | 156 | −0.72 | −1.31 | 0.72 | 45.5 % | −1.44 | 0.83 | 0.57 |
| V1 \|move\| ≥ 0.25 × range | 108 | −0.98 | −1.57 | 0.69 | 45.4 % | −1.37 | 0.76 | 0.61 |
| V2 \|move\| ≥ 0.50 × range | 83 | −0.67 | −1.26 | 0.75 | 45.8 % | −0.92 | 0.74 | 0.78 |
| V3 \|move\| ≥ 0.75 × range | 53 | +0.82 | +0.23 | 1.05 | 49.1 % | +0.13 | 0.82 | 1.52 |
| V4 volume > trailing median (non-causal) | 75 | 0.00 | −0.59 | 0.89 | 45.3 % | −0.37 | 0.92 | 0.85 |
| V5 V2 and V4 | 46 | +0.29 | −0.30 | 0.95 | 45.7 % | −0.14 | 0.70 | 1.46 |
| V6 up days only (long) | 78 | +0.59 | 0.00 | 1.00 | 51.3 % | 0.00 | 1.30 | 0.65 |
| V7 down days only (short) | 78 | −2.03 | −2.62 | 0.59 | 39.7 % | −1.69 | 0.63 | 0.53 |
| V8 15:00→15:30 leg agrees with early sign | 98 | −0.57 | −1.16 | 0.75 | 48.0 % | −1.02 | 1.02 | 0.49 |

Permutation control for V0 (early sign shuffled across days, 20,000 draws): one-sided p = 0.78. Net monthly P&L
for V0 is negative in 6 of 8 months; January alone is −$5.4k. The only variant above water, V3, has 53 trades,
t = 0.13, and a losing development phase.

## Half-hour scan (diagnostic, 11 tests, not a strategy)

At each half-hour boundary T from 10:00 to 15:30, regress the next 30 minutes on (P_T − prior close):

| T | 10:00 | 10:30 | 11:00 | 11:30 | 12:00 | 12:30 | 13:00 | 13:30 | 14:00 | 14:30 | 15:00 | 15:30 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| t | −0.5 | −1.4 | +0.2 | −1.8 | −0.1 | +2.1 | −1.3 | −0.1 | −0.4 | +0.1 | +1.0 | −0.1 |

One boundary out of twelve reaches |t| > 2 (12:30), which is what chance produces. There is no half hour in the
day where the running move from the prior close predicts the next half hour.

## Exploratory, post-hoc (same data, no holdout — recorded so they are not re-discovered)

| variant | n | gross | net | net PF | note |
|---|---|---|---|---|---|
| X1 15:00 open → 15:30 open, sign(prior close → 15:00) | 156 | +0.01 | −0.58 | 0.87 | permutation p = 0.47 |
| X2 15:00 open → 15:58 open, same sign | 156 | −1.38 | −1.97 | 0.72 | |
| X3 X1 with \|move\| ≥ 0.5 × range | 81 | +1.27 | +0.68 | 1.17 | t 0.5; dev phase PF 0.81 |
| X4 15:30 → 15:58, sign(15:00 → 15:30) (Gao's 2nd predictor) | 152 | +0.10 | −0.49 | 0.89 | |

## Reading

- The papers' effect is a few basis points per day and, per Baltussen et al., concentrated when option dealers
  are short gamma and on large-move days. Eight months of one regime is a small sample for a few-bps effect, so
  this is "absent at any size we could trade", not "disproved". It is also consistent with the effect having
  decayed after publication.
- What the data do say: the last 30 minutes of ES in this window have a slight long lean (+1.3 pt from 15:30 to
  15:58, t 1.45) that is not conditional on the day's direction. Shorting into the close on down days lost
  −2.6 pt per trade net.
- Process note: the first run paired only 129 days because Sunday-evening bars reset the prior session (every
  Monday lost its pair) and its control regression overlapped with its target (prior close→15:30 contains
  15:00→15:30, so the t = 4 it showed was mechanical). Both are fixed in `study.py`; the numbers above are from the
  corrected run.

## Next candidates from the same scan, in order

1. **E3 — Howard's time-based exit stack on the IB60 champion** (`17d18cc2cd1b`): wide stop + trailing + breakeven
   + no-progress `timeStop`, plus a 09:30–10:00 no-trade window. Pure exit change, expressible today, one lineage
   child per variable.
2. **E2 — failed auctions at prior-day VAH/VAL conditioned on test count** (Perry 2026). Needs `prior_day_vah/val`
   and a `level_test_count` primitive; the key-levels notebook already computes the prior-day value area, so the
   offline version is a small extension of that machinery.
