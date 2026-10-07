# Lab notebook — Carmine Rosato order-flow masterclass, replicated on the platform (2026-09-07)

Source: YouTube `j9ZuUlVGDr8`, "This Is My EXACT Profitable Trading Strategy (FULL Course)", Carmine Rosato, uploaded
2026-08-07, 71 min (a recorded live masterclass from 2026-06-30). Transcript pulled with yt-dlp (auto captions, ~13.5k words);
the video itself was not watched, so chart details are inferred from what he says. Specs, run summaries and the scripts
that produced them are in [`2026-09-07-rosato-orderflow-masterclass/`](2026-09-07-rosato-orderflow-masterclass/).

**Verdict: rejected.** Every mechanical rendering of his setups loses on the Apr–Jul in-sample window and again on the
Dec–Mar holdout. The parts of his method that carry the edge (hand-drawn higher-timeframe zones, intrabar reads of a
20-tick footprint and a heat map, a stop 3–5 points behind the level) are exactly the parts the platform cannot express at
1-minute bar resolution. Details and engine gaps below.

## 1. What he actually trades (distilled from the transcript)

Instrument ES. Tools: a **delta footprint on a 20-tick range** (not time-based), a **Bookmap heat map** (resting liquidity +
executed bubbles), and a candlestick chart only for context and levels. "Time is irrelevant" — he enters the moment the
print he wants appears, not on a candle close.

**The CLC rule** (his three pillars; no trade without all three):

| Pillar | What he means | Examples he gives |
|---|---|---|
| **Context** | Balanced vs trending, volume, pace of tape, who is trapped | "balance range structure → not a morning to expect a breakout, but a reversal" |
| **Location** | A pre-planned level of interest | HTF supply/demand zones (weekly, daily, 4h, 3h, 2h, 90m, 1h, 30m, 15m), pre-market high/low, prior-session levels, **balance-range high/low**, and the level where aggressive buyers/sellers appeared on a breakout |
| **Confirmation** | Who is winning, passive or aggressive | a passive order at the level **gets filled** (price transacts there) and price holds; heavy aggressive delta **with no follow-through** (absorption); a **delta flip** (largest buying of the session at the high immediately followed by the largest selling) |

**Setup A — balanced market / fade the extremes.** Identify a multi-day balance range (he draws it on a 30-minute chart,
e.g. a three-day range). Never trade the middle. At the range high he is "first and only" a seller, at the low a buyer —
but not on the first blind test: he waits for a rejection plus confirmation (passive seller filled, absorption of the
buying, delta flip). Targets: the opposite side / the day's low / a "zero print"; stop just above the rejection extreme.
His examples: short 7590 stop 7593.5 target 7575 (5.5R realised, $14k); short 7470 after an offer filled at 7490 with
112 aggressive buyers (3.6R, $7.6k).

**Setup B — initiative / breakout-pullback.** He does not buy breakouts ("95 % of the time"). When the range breaks with
strong aggressive buying (delta outliers, low-volume node, zero prints), he marks where those buyers appeared and longs
the **pullback into that level** on confirmation: "my entry is a reversal signal, but I'm going for a continuation
setup". Example: failed breakdown of 7423, break above 7454 with buyers at 7455, long the pullback at 7462, 5-point stop,
10+ point target (3.5R, ~$10k, 25 minutes).

**Setup C — sweep of a low at a demand zone.** Sell-off into a pre-planned demand zone (7360–7380 from a week earlier); a
passive buyer on the heat map gets filled while aggressive sellers hit it and price does not follow through → long right
there (7367, stop 7364), targets the pre-market high (7416) and prior resistance (7454). 7R, $20k.

Risk: stop = "where my analysis is incorrect", typically 3–5 ES points; June 2026 realised 4.17 average win/loss, losses
≈ $2.5k, wins ≈ $10k+. He skips days with no setup and treats a missed rejection as context for the next trade.

## 2. Mapping to Strategy Spec v2

| His concept | Platform expression | Notes |
|---|---|---|
| Balance range high/low | `highest` / `lowest` `(n=2, exclude_current=false, tf=1D)` | UTC-day bars aggregated in-engine, so the level is stable through the RTH session; `n=3` also tried |
| "At the level" | `touched(level, 4, 0)` and `close` back across the level | ±1 pt tolerance, same bar |
| Absorption (aggression, no follow-through) | `bar_delta ≤ −300` on the touch bar that closes back above (p75 of RTH 1-min \|delta\| = 287) | per-bar, not per-price-level |
| Delta flip | sequence: `bar_delta ≤ −500` at the level (p90), then within 5 bars `bar_delta ≥ +300` with close above the level | |
| Delta toward the bounce | `bar_delta ≥ +300` on the touch bar | the confirmation the key-levels study found works at session extremes |
| Passive buyer filled (heat map) | `large_resting_size_near(bid, min_size=100, within_ticks=8) > 0` — ticks mode, 1-second MBO liquidity view | p90–p95 resting size near price is ≈ 100 contracts ([[es-1min-orderflow-scale]]) |
| Balanced context | `adx(14, tf=1h) < 20` or `within_ticks(highest(3,1D), lowest(3,1D), 320)` (3-day range ≤ 80 pts ≈ p20) | |
| Breakout-pullback | `retest(level, 4, 30)` (+ `bar_delta > 0` on the pullback bar; optional sequence: breakout close with `bar_delta ≥ 500`) | **long-only** — see engine gap 1 |
| Stop behind the extreme | `structure: session_low` + 8 ticks (mirrored `session_high` for shorts); also 24 t / 40 t / `bar_low` + 4 t | no way to anchor a stop to the level that defined the trade — gap 2 |
| Targets | `rr 3` (his realised R), `rr 2`, `level: session_high` | |
| Sizing / costs | 1 contract, $2.25/side + 1-tick slippage each way (≈ $29.50 round trip) | |

Direction `both`: the long tree above is mirrored for shorts (highest↔lowest, deltas negated, bid↔ask). Entry window
09:31–15:00, flatten 15:58, max 2 trades/day, 10-bar cooldown. Bars mode unless a book primitive is used.

Not reproducible: his drawn HTF supply/demand zones, pre-market high/low (no primitive), zero prints / low-volume nodes
at a price, and the intrabar entry on a 20-tick range footprint.

## 3. Results — in-sample, 2026-04-01 → 2026-07-31 (105 sessions, bars mode)

Setup A (fade the 2-day balance extremes, `direction: both`):

| variant | n | net $ | PF | win % | exp R | max DD $ | med stop t | long / short $ | Apr / May / Jun / Jul |
|---|---|---|---|---|---|---|---|---|---|
| A0 touch & reclaim, no flow (control) | 91 | −2,060 | 0.94 | 30.8 | −0.07 | 11,113 | 34 | −3.8k / +1.8k | +0.3k / −6.8k / +4.0k / +0.4k |
| **A1 absorption (sell delta ≤ −300, close back above)** | 50 | −10,475 | 0.47 | 20.0 | −0.41 | 12,655 | 37 | −3.7k / −6.8k | +1.0k / −5.8k / −4.3k / −1.4k |
| A1 sell delta ≤ −500 | 30 | −2,722 | 0.70 | 20.0 | −0.37 | 6,374 | 30 | −0.7k / −2.0k | +1.8k / −1.8k / −1.2k / −1.5k |
| A1 stop 24 t | 53 | −6,251 | 0.54 | 18.9 | −0.38 | 7,725 | 24 | 0.0k / −6.3k | −3.6k / −2.3k / +0.5k / −0.8k |
| A1 stop 40 t | 49 | −9,133 | 0.55 | 20.4 | −0.36 | 12,528 | 40 | −1.3k / −7.9k | −3.7k / −5.2k / −0.6k / +0.4k |
| A1 stop under signal bar + 4 t | 56 | −3,740 | 0.58 | 14.3 | −0.56 | 6,304 | 10 | +0.3k / −4.0k | −0.8k / −2.6k / 0.0k / −0.4k |
| A1 target 2R | 52 | −8,472 | 0.56 | 28.8 | −0.22 | 12,190 | 33 | −2.9k / −5.6k | −0.2k / −6.0k / −3.9k / +1.6k |
| A1 target session high/low | 52 | −8,072 | 0.50 | 26.9 | −0.20 | 10,462 | 37 | −1.8k / −6.3k | −2.8k / −5.3k / −1.9k / +1.9k |
| A1 2-day range ≤ 100 pts | 32 | −2,282 | 0.77 | 25.0 | −0.31 | 6,509 | 30 | −1.2k / −1.1k | +3.0k / −3.6k / −0.5k / −1.3k |
| A1 3-day range | 37 | −11,729 | 0.25 | 13.5 | −0.58 | 12,566 | 37 | −6.6k / −5.1k | −1.3k / −5.1k / −2.1k / −3.3k |
| A2 delta flip (sequence) | 28 | −276 | 0.98 | 25.0 | −0.14 | 5,571 | 54 | +2.4k / −2.7k | +3.5k / −0.5k / −2.7k / −0.5k |
| A3 buy delta ≥ +300 on the touch | 67 | +98 | 1.00 | 31.3 | −0.07 | 7,574 | 59 | +4.1k / −4.0k | +0.3k / −3.4k / +4.1k / −0.9k |
| A0 + 1h ADX < 20 | 45 | +648 | 1.04 | 31.1 | 0.03 | 7,348 | 33 | +0.7k / −0.1k | +1.0k / −2.6k / +5.1k / −2.8k |
| A3 + 1h ADX < 20 | 32 | −1,832 | 0.89 | 31.2 | −0.03 | 6,032 | 56 | −0.9k / −0.9k | −1.1k / −3.9k / +4.8k / −1.7k |
| A0 + 3-day range ≤ 80 pts | 13 | +2,179 | 2.10 | 46.2 | 0.21 | 846 | 23 | +1.1k / +1.1k | −0.8k / +1.2k / +1.0k / +0.7k |
| A3 + 3-day range ≤ 80 pts | 7 | +2,818 | 4.69 | 57.1 | 0.47 | 396 | 46 | +1.6k / +1.2k | +0.6k / +1.1k / +0.8k / +0.4k |
| A3 longs only (trend diagnostic) | 27 | +4,791 | 1.36 | 44.4 | 0.23 | 3,495 | 65 | +4.8k / — | +4.7k / +0.6k / +1.3k / −1.9k |
| A4 resting bid/offer wall ≥ 100 (ticks) | see §5 | | | | | | | | |

Reading: the control **without** order flow (PF 0.94) beats the absorption-confirmed version (PF 0.47); the "sellers
absorbed at the low" bar is more often the start of a breakdown than its end at 1-minute resolution (stopped trades die in
a median 9.5 bars with 8 ticks of MFE). Shorts carry most of the loss — ES rallied ≈ 900 points over the window, and
Apr 2026 2-day ranges were 70–280 points wide, i.e. the tape was almost never "balanced". The only positive cells are
the two balance-context filters (3-day range ≤ 80 pts), with 13 and 7 trades — a hint about the Context pillar, not evidence.

Setup B (breakout-pullback, long-only because of engine gap 1):

| variant | n | net $ | PF | win % | exp R | max DD $ | Apr / May / Jun / Jul |
|---|---|---|---|---|---|---|---|
| B0 retest of 2-day high, no flow (control), stop 20 t, 2R | 49 | +1,404 | 1.18 | 38.8 | 0.13 | 2,290 | +1.1k / +2.4k / −1.1k / −0.9k |
| B1 + buyers on the pullback bar | 39 | +262 | 1.04 | 35.9 | 0.05 | 2,252 | −0.7k / +2.9k / −1.1k / −0.9k |
| B1 stop 40 t | 37 | −766 | 0.94 | 35.1 | −0.03 | 3,876 | −1.3k / +2.4k / −0.2k / −1.6k |
| B1 stop under pullback swing low, 3R | 35 | −1,245 | 0.90 | 40.0 | 0.11 | 4,252 | −0.1k / +1.5k / −0.4k / −2.2k |
| **B2 strong-delta breakout (≥ +500) then pullback with buyers** | 27 | +1,178 | 1.28 | 40.7 | 0.19 | 1,946 | −1.1k / +3.9k / −0.8k / −0.8k |
| B2 target 3R | 27 | +1,478 | 1.31 | 33.3 | 0.24 | 2,458 | −1.1k / +4.7k / −0.6k / −1.6k |
| B3 retest of the prior-day high with buyers | 48 | +1,672 | 1.22 | 39.6 | 0.16 | 2,557 | −0.9k / +4.7k / −1.4k / −0.7k |

Every B cell is positive only because of May 2026; three of four months lose.

## 4. Holdout — 2025-12-01 → 2026-03-31 (one shot, variants chosen before looking)

| variant | n | net $ | PF | win % | exp R | max DD $ | Dec / Jan / Feb / Mar |
|---|---|---|---|---|---|---|---|
| A0 touch & reclaim (control) | 86 | −19,487 | 0.49 | 24.4 | −0.34 | 22,086 | −0.5k / −9.8k / −4.3k / −4.9k |
| A3 buy delta on the touch | 69 | −16,898 | 0.53 | 26.1 | −0.34 | 19,494 | −0.5k / −11.0k / −3.6k / −1.8k |
| A0 + 3-day range ≤ 80 pts | 19 | −923 | 0.81 | 26.3 | −0.29 | 3,479 | +0.5k / −1.5k / — / — |
| A3 + 3-day range ≤ 80 pts | 13 | −1,034 | 0.76 | 30.8 | −0.21 | 2,790 | +1.1k / −2.1k / — / — |
| B0 retest control | 43 | −556 | 0.93 | 32.6 | −0.03 | 3,070 | −1.4k / −0.4k / −0.4k / +1.7k |
| B2 strong-delta breakout pullback | 24 | −1,833 | 0.62 | 25.0 | −0.29 | 2,442 | −1.1k / −1.1k / −0.1k / +0.5k |

Dec–Mar is the more rotational regime (trend-day share 13.5 % vs 17.1 % in Apr–Jul), i.e. the tape his balance setup wants,
and the fades still lose ≈ $17–19k per contract. The balance-filtered cells reverse sign. The pullback family's May edge
does not reappear.

## 5. Heat-map confirmation (A4, ticks mode + 1-second MBO liquidity view)

Touch-and-reclaim of the 2-day range low/high plus a resting bid/offer of ≥ 100 contracts within 8 ticks of price on
the 1-second MBO liquidity view (his "passive buyer at the level filling"). Ticks mode, all 105 sessions covered by
liquidity data, 468 s.

| variant | n | net $ | PF | win % | exp R | max DD $ | med stop t | long / short $ | Apr / May / Jun / Jul |
|---|---|---|---|---|---|---|---|---|---|
| A4 resting wall ≥ 100 within 8 t | 37 | +684 | 1.07 | 29.7 | 0.01 | 2,206 | 31 | +0.4k / +0.3k | +0.9k / −0.5k / +1.1k / −0.8k |

Flat: the whole net is one trade (2026-04-21 short, +$2,258); without it the run is −$1,574. Stopped trades die in a
median 11 bars with 8 ticks of favourable excursion, the same signature as the bar-delta confirmations. Average slippage
2.0 ticks in ticks mode. The wall filter does select fewer, slightly better touches than the control (PF 1.07 vs 0.94),
but not enough to matter, and it was not run on the holdout because there is nothing to protect.

## 6. Why it fails here, and what the platform would need

1. **Resolution.** He reacts to a 20-tick-range footprint and heat-map prints inside the minute; the engine acts on the
   1-minute close ± 1 tick. The key-levels study already measured this: by the time a 1-minute confirmation closes, the
   fill is a median 12 ticks off the level, and stopped trades here show a median 8 ticks of favourable excursion. A 3–5 pt
   stop placed 1 minute late is inside the noise ([[es-1min-orderflow-scale]]).
2. **Location is discretionary.** His levels are hand-drawn HTF supply/demand zones plus pre-market highs/lows; the
   platform has neither. A mechanical 2-/3-day high/low is a poor stand-in, and in Apr–Jul it was rarely a "balance" at all.
3. **Absorption is per price, not per bar.** "1,000 contracts hit a 100-lot offer and price does not move" is a
   footprint-cell statement; `bar_delta` and `absorption(side, min_volume, max_range_ticks)` summarise the whole bar.
4. **Selection and survivorship.** He presents a $68k June of hand-picked trades at 4:1; the mechanical population of
   "touch the range extreme" events is ~1 per session and its base rate is what the control rows show.

Engine gaps surfaced by this study (not fixed here):

- **`retest` does not mirror.** `expr.mirror()` swaps the level (`highest`→`lowest`) but `Op._retest_step` keeps the
  long-side comparisons (`close > lvl`), so a `direction: both` spec using `retest` looks for shorts that break *above*
  the range low. Setup B was run long-only because of this.
- **No level-anchored stop.** A stop "just beyond the level that defined the trade" cannot be expressed; `structure:
  session_low` produced 75-point stops when the level was reached from below (e.g. 2026-04-02).
- **Missing primitives** for this family: overnight/pre-market high & low, HTF zone detection, per-price absorption /
  zero prints / low-volume nodes, and range (tick) bars as a primary timeframe.

## 7. Platform state after this study

Saved to the strategy store with status `rejected` and the results in their descriptions (names start "Rosato"):
A1 absorption (the faithful rendering), A0 control, A0 + 3-day balance filter, A2 delta flip, B2 breakout-pullback, and
A4 passive-wall (ticks). 25 spec files, every run summary and the generator/runner scripts are in the study folder.
| id | strategy |
|---|---|
| `006a699dc2d5` | Rosato A1 — balance-low absorption (the faithful rendering) |
| `8c0ebd4162a0` | Rosato A0 — touch & reclaim, no flow (control) |
| `6764acb215a1` | Rosato A0 + 3-day range ≤ 80 pts (child of A0) |
| `61799c37d4db` | Rosato A2 — delta flip (child of A1) |
| `3fe8c6e4ce47` | Rosato A4 — resting bid wall (ticks) |
| `ac1103002a53` | Rosato B2 — strong-delta breakout, then pullback (long-only) |

In-sample IS/OOS numbers are appended to each description. The A1 row also holds the video reference in `meta.source`.
Re-running any of them: `python -m engine.backtest_worker <spec> 2026-04-01 2026-07-31 bars out.json` from `backend/`
(the study ran the worker directly; the API server was not running).
