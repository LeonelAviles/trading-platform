# Lab notebook — ES intraday strategy research (2026-08-31)

Data: ES front month, 1-min bars built from MBO (Databento), 2026-04-01 → 2026-07-31.
105 partitions, 87 RTH sessions (84 full 390-bar days + 3 half days). Regime tags: 78 rotational / 18 trend days.
Offline sim conventions (mirrors engine bars mode): entry at signal-bar close ± 1 tick, stop booked 1 tick worse,
$2.25/side commission ⇒ $29.50 round-trip cost per contract. 1 contract. Engine confirms in NautilusTrader.

Sources (tier 1): Gao, Han, Li & Zhou 2018 JFE "Market intraday momentum"; Baltussen, Da, Lammers & Martens 2021 JFE
"Hedging demand and market intraday momentum" (60+ futures incl. ES, 1974–2020); Cont, Kukanov & Stoikov 2014 J. Fin. Econometrics
(order-flow imbalance); Andersen, Bondarenko, Kyle & Obizhaeva (ES intraday invariance); Zarattini & Aziz 2023 / Zarattini, Barbon & Aziz 2024 SSRN (ORB).
Corroborating negative result: arXiv 2605.04004 (14 OHLCV signal families on MNQ, none clear 2-pt friction OOS).

## Hypotheses and verdicts (offline, before engine)
| # | Hypothesis | Best variant | Verdict |
|---|---|---|---|
| H1 | Last-30-min intraday momentum (sign of prev-close→15:30 return) | PF 0.70, t −1.1 (n 83) | rejected in this sample |
| H2 | ORB breakout 5/15/30 min, stop opp side/mid, rr 1–3 | all PF < 1.0 (5-min: PF 0.5) | rejected |
| H3 | ORB fade (breakout failure), targets mid/opp/VWAP | best PF 1.06, most 0.5–0.8 | rejected |
| H4 | VWAP stretch reversion (16–48 ticks, 1.5–3 ATR) | all PF 0.56–0.82, t −2…−4.5 | rejected |
| H5 | Initial-balance (60-min) breakout, first close outside | PF 1.72 (IB-mid stop), 1.55–1.64 (64–80-tick stop), n 69, t 1.8–2.0 | **survives** |
| H6 | Prior-day H/L fade / break / absorption-at-level | fade PF 0.5–0.75; break ≤1.06; absorption 0.35–0.5 | rejected |
| H7 | Bar-delta divergence, delta thrust, CVD-trend VWAP pullback | PF 0.4–0.85, t −2…−7 | rejected |
| H8 | Overnight gap fade / continuation | fade PF ≤1.11, cont ≤1.10, n small | rejected |
| H9 | Session LVN (thin volume_at_price) continuation outside value | PF 1.20 (n 138, t 1.06) one cell; control not clearly worse | not supported |

## H5 single-variable experiments vs B0 (IB60, stop 48t, rr 1, 10:30–13:00, 1 trade/day) — B0: n 69, PF 1.21, +$4.0k
rr 1.5 → PF 1.33 | rr 2 → 1.10 | entry ends 11:00 → 1.23 | delta agrees → 1.29 | rel_volume ≥1 → 1.34 |
gap-with → 1.02 (n 43) | gap-against → 1.75 (n 33) | re-entries → 1.00 (n 271) | time stop 60 → 1.46 | time stop 120 → 1.37 |
breakeven 0.5R → 0.65 | breakeven 0.75R → 0.88 | N=45 → 0.96 | N=75 → 1.41 | N=90 → 1.20 |
stop 56t → 1.44 | 64t → 1.55 | 80t → 1.64 | 4×ATR → 1.43. Month P&L (B0): Apr +1.1k, May −2.6k, Jun +4.8k, Jul +0.6k.

## Engine confirmation of H5 baseline (strategy 4d9e3c3e4443, bars mode, IS = all 105 partitions)
72 trades, +$4,401, PF 1.23, win 55.6 %, exp 0.11 R, max DD 4.1 %, Sharpe 1.34; exits target 39 / stop 31 / flatten 2.
Offline sim had n 69, PF 1.21, +$4.0k → harness is calibrated to the engine (the 3 extra trades are half-days).

| # | Hypothesis | Best variant | Verdict |
|---|---|---|---|
| H10 | Afternoon range-extension continuation (13:00–15:30 → 15:58) | PF 1.35 at one cell (14:30, 40t); month P&L alternates sign | rejected |
| H11 | HTF mean reversion: RSI(2/7/14) and Bollinger(20,2) on 5/15-min, target VWAP or rr | PF 0.5–1.1; RSI2 t −4…−5 | rejected |
| H12 | Zarattini/Aziz ORB in paper form (first 5/15/30-min candle direction, 0.05–0.2×ATR14d stop, EOD) | PF 0.22–0.58, t −2…−3.8 | rejected (stocks-in-play effect, not ES) |
| H13 | Pre-close reversal (fade prev-close→15:45 move into 15:58) | PF 1.41 only for ref = prev close at 15:40/15:45; other refs ≤1.09; any stop kills it | not robust, treated as noise |
| H14 | Failed-IB-breakout fade (close back inside within W bars, stop = session extreme) | PF ≤1.13 (rr 1), negative for mid/VWAP targets | rejected |
| H15 | Responsive fade at IB edges on balanced days (no extension yet by 11:30) | 1–6 trades: the IB is extended on nearly every session | no signal population |
| H16a | Overnight (Globex) range breakout 09:30–11:30 | PF 0.63–1.11, best cells need 64-tick stops with n 64 | rejected |
| H16b | Volatility breakout open ± k·ATR14d (k 0.2–0.5) | PF 0.26–1.06 | rejected |
Observation: every early-session breakout (5/15/30-min OR, ON range, open±k·ATR, first-candle) loses; the ≥60-min IB breakout wins →
the first hour's moves are transient, later extensions persist. Tested the mirror next (H17: OR fade with IB-scale risk).
| H17 | **5-min OR fade with IB-scale risk**: first close outside the 5-min OR (either side, first break only) → fade, target = opposite side of the OR, stop 80 ticks | n 84, PF 1.38, +$11.4k, win 65 %, t 1.38; months Apr +8.2k / May +2.4k / Jun −6.0k / Jul +6.7k; no-stop version PF 1.69 (unbounded risk); mid target PF 1.40 | **survives** (only for the 5-min OR: 15/30-min fades lose → fragility flagged) |

## H17 single-variable experiments vs F0 (OR5 fade, target opp side, stop 80t, first break only, 09:35–10:30) — F0: n 84, PF 1.38, +$11.4k
OR length: 2 → 0.96 | 3 → 1.28 | 4 → 1.31 | 5 → 1.38 | 6 → 1.20 | 7 → 1.08 | 8 → 1.02 | 10 → 1.05.
Target: opp 1.38 | mid 1.40 | 24t 1.42 | 32t 1.40 | 48t 1.04 | 64t 1.09 | 0.5R 1.07 | 0.75R 1.12 | 1R 1.10 | VWAP 1.44.
Stop: 48t 1.16 | 64t 1.20 | 80t 1.38 | 100t 1.38 | 120t 1.31 | 160t 1.28.
Filters: OR ≥ 40 ticks → 1.44 (n 66, DD $5.2k) | OR ≥ 60 → 1.44 (n 33) | longs only 1.91 (n 40) | shorts only 1.05 (n 44) | entries until 10:00 → 1.43.
Walk-forward quarters (21 trades each): +$8.2k, +$1.4k, −$6.0k, +$7.8k.
Engine change needed: `or_high/or_low` level targets now follow the spec's OR length (was hard-coded 15 min) — DECISIONS.md.

## Engine (bars mode, IS) — IB lineage children, one variable each vs B0 (n 72, PF 1.23, +$4.4k, 0.11R, DD 4.1%)
| child | change | n | net | PF | exp R | DD % | months Apr/May/Jun/Jul |
|---|---|---|---|---|---|---|---|
| B1 | stop 48 → 64 t | 72 | +12,126 | 1.58 | 0.22 | 3.6 | +2.2k / −1.6k / +8.6k / +2.9k |
| B2 | target 1 → 1.5 R | 72 | +7,551 | 1.34 | 0.18 | 4.8 | +1.9k / −2.3k / +7.0k / +0.9k |
| B3 | time stop 60 bars | 72 | +7,064 | 1.49 | 0.17 | 3.1 | +1.8k / −1.3k / +5.3k / +1.3k |
| B4 | rel_volume ≥ 1 filter | 72 | +6,326 | 1.35 | 0.15 | 3.1 | +2.8k / −1.6k / +4.2k / +0.9k |
| B5 | IB 75 min | 67 | +6,961 | 1.42 | 0.18 | 3.8 | −0.2k / −0.4k / +3.4k / +4.1k |
| B6 | bar delta agrees | 72 | +5,614 | 1.30 | 0.14 | 3.1 | +1.6k / −1.6k / +4.7k / +0.9k |
| B7 | day-direction filter (close vs prior close) | 69 | +3,827 | 1.21 | 0.10 | 4.1 | no improvement |
| B8 | 5-min primary bars | 0 | — | — | — | — | zero trades → engine issue, investigating |
May is negative for every variant (the one month with 15 rotational sessions in a row).

## Engine (bars mode, IS) — OR5 fade baseline F0: n 87, +$14,084, PF 1.47, 0.16R, win 66.7%, DD 8.0%; target 57 / stop 29 / flatten 1; months +8.7k / +3.1k / −5.2k / +7.5k

## Engine — ticks mode check of IB B0 (426d1a2f6671): n 72, +$3,588, PF 1.18, 0.09R, DD 4.2%, avg slippage 2.5 ticks (bars: 1.3) — same trades, ~$800 more friction. 436 s per full ticks run.

## Engine (bars mode, IS) — OR5 fade children vs F0 (n 87, PF 1.47, +$14.1k, 0.16R, DD 8.0%)
| child | change | n | net | PF | exp R | win % | DD % | months |
|---|---|---|---|---|---|---|---|---|
| F1 | target VWAP | 87 | +9,221 | 1.60 | 0.11 | 82.8 | 4.7 | +4.8k / +3.0k / −3.1k / +4.5k |
| F2 | target 32 ticks | 87 | +10,396 | 1.60 | 0.12 | 80.5 | 5.4 | +6.9k / +2.7k / −4.0k / +4.9k |
| F3 | stop 100 t | 87 | +15,146 | 1.47 | 0.14 | 70.1 | 7.7 | +7.9k / +3.6k / −5.7k / +9.4k |
| F4 | OR range ≥ 40 t | — | rejected by the checker (within_ticks bug) → fixed, rerun below | | | | | |
| F5 | entries until 10:00 | 83 | +16,764 | 1.63 | 0.20 | 68.7 | 5.9 | +8.7k / +3.1k / −3.5k / +8.5k |
| F6 | longs only (diagnostic) | 57 | +13,781 | 1.84 | 0.24 | 71.9 | 3.6 | +7.2k / +6.6k / −3.6k / +3.7k |
| F7 | OR 4 min | 87 | +11,958 | 1.40 | 0.14 | 66.7 | 6.8 | |
| F8 | OR 6 min | 87 | +9,784 | 1.28 | 0.12 | 60.9 | 8.2 | |
June is negative for every OR-fade variant (the month with the most trend days); May is negative for every IB variant — the two families lose in different months.

## Engine fixes found on the way (backend, tests added, DECISIONS.md)
1. `within_ticks(a, b, n)` checker rejected a value for `b` (evaluator uses |a−b|) → OR-width filters were unwritable.
2. Bars mode with a multi-minute primary matched incoming bars against the composite bar type → zero trades silently (B8). After the fix B8 = PF 1.26, 71 trades.
Operational note: any edit under `backend/` restarts `uvicorn --reload` and the queue marks in-flight jobs "interrupted"; wait ≥30 s after an edit before queuing.

## Stepwise combinations (bars, IS)
IB: B9 = B1+time stop 1.76 | B10 = B1+IB75 1.58 | B11 = B1+rel_volume **1.76 (+$14.7k, 0.26R, DD 3.6%)** | B12 = B1+1.5R 1.46 |
    B13 = B11+time stop **1.93 (+$12.8k, 0.23R, DD 2.8%)** ← champion | B14 = B11+1.5R 1.58 (+$14.2k) | B15 = B11+IB75 1.47.
OR fade: F9 = F5+VWAP **1.80 (DD 4.1%)** | F10 = F5+32t 1.76 | F11 = F5+stop100 1.66 (+$18.6k) | F12 = F9+stop100 1.79 |
    F13 = F9+OR≥40t **2.04 (n 64, +$10.7k, 0.17R, win 84%, DD 2.5%)** ← champion | F4 alone 1.50.
Trials per family in the engine: 16 (IB) and 14 (OR fade) plus the offline grids → treat in-sample PFs as optimistic; WF1–3 (May/Jun/Jul folds) are the honest read.

## Validation (bars mode; IS = Apr–Jul, WF1 = May, WF2 = June, WF3 = July; no OOS holdout exists)
| strategy | IS n / PF / net / DD | WF1 | WF2 | WF3 | MC DD95 | verdict |
|---|---|---|---|---|---|---|
| IB60 B0 baseline (4d9e3c3e4443) | 72 / 1.23 / +$4.4k / 4.1% | −$2.8k (0.55) | +$5.3k (2.70) | +$0.3k (1.07) | 7.9% | untestable (<100 trades) |
| **IB60 champion B13 (17d18cc2cd1b)** | 72 / 1.93 / +$12.8k / 2.8% | +$0.4k (1.11) | +$7.6k (4.76) | +$1.3k (1.31) | 5.1% | untestable — 3/3 folds positive |
| OR5 fade F0 baseline (0994070678ce) | 87 / 1.47 / +$14.1k / 8.0% | +$3.1k (1.50) | −$6.2k (0.60) | +$8.5k (2.66) | 10.5% | untestable |
| **OR5 fade champion F13 (701c504e7320)** | 64 / 2.04 / +$10.7k / 2.5% | +$1.9k (1.94) | −$0.6k (0.90) | +$5.2k (3.51) | 4.9% | untestable — 2/3 folds positive |
The deflated-Sharpe line in the UI uses trials = 1; the true count is ~16 engine variants per family plus the offline grids, so read the DSR as an upper bound.
Ticks-mode IS + WF1–3 for both champions: see the Runs tab of each strategy (queued at the end of the session; ticks fills add ~1 tick of slippage per side vs bars).

## Ticks-mode validation of the champions (NautilusTrader TradeTick fills, resting-limit targets, ~2.5 ticks avg slippage)
| strategy | IS n / PF / net / exp / DD | WF1 May | WF2 Jun | WF3 Jul | MC DD95 |
|---|---|---|---|---|---|
| IB60 champion (17d18cc2cd1b) — IS job 3fa20058734f | 72 / 1.87 / +$12,089 / 0.22R / 2.9% | +$241 (1.07) | +$7,390 (4.59) | +$1,107 (1.27) | 5.2% |
| OR5 fade champion (701c504e7320) — IS job 03cb4c61d0d6 | 64 / 1.98 / +$10,050 / 0.16R / 2.6% | +$1,787 (1.87) | −$752 (0.88) | +$4,948 (3.40) | 5.0% |
Ticks and bars agree within ~$800 on every window; the ranking of folds is identical. Both verdicts stay "untestable" (64–72 trades < 100).
Where to look in the UI: Desk → candidates; /strategies → the two lineages (16 and 15 nodes); /strategies/:id → Runs tab; /review/<job id> → chart with the trades.
