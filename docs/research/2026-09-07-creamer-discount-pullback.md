# Lab notebook — Chris Creamer's "discount pullback" (Robbins World Cup), replicated on the platform (2026-09-07)

Source: YouTube `PL7LKUsCgIQ`, "Trading WORLD CHAMPION Reveals the Orderflow Strategy That Won the Robbins Cup",
IQ Capital interview with Chris Creamer, uploaded 2026-08-11, 58 min. Transcript from auto-captions (~10.9k words);
the whiteboard was not seen, so chart details are inferred from speech. He trades **MNQ**; the platform has ES only,
so everything below is on ES front month. Specs, run summaries, scripts and the offline diagnostic are in
[`2026-09-07-creamer-discount-pullback/`](2026-09-07-creamer-discount-pullback/). Second study of the day after
[Rosato](2026-09-07-rosato-orderflow-masterclass.md).

**Verdict: inconclusive, leaning rejected.** A faithful mechanical rendering finds 0 setups in four months; the
loosened three-step rendering finds 10 (flat) and its best-looking sibling (15 trades, PF 1.98 in-sample) loses 19 of 19
on the holdout (PF 0.16). His own numbers (≈ 1 trade/day, 60–65 % wins, PF ≈ 1.8) describe a population an order of
magnitude larger than anything a fixed 0.705–0.886 zone below yesterday's value produces on ES, so the discretionary
parts of "location" carry the method. Details in §3–§5.

## 1. What he actually trades (distilled)

Intraday, **first 90 minutes of New York** only (hard shutoff), 5-minute footprint candles (bid×ask volume and delta
profiles), hourly and 15-minute for structure. Zero to two trades a day; stop after two losses in a row.
He frames it as context → location → confirmation:

| Step | What he does | Notes |
|---|---|---|
| **Environment** | Hourly / 4-hour structure: value-up, value-down or sideways (higher highs and lows, daily or cash-session value areas migrating). Gamma regime (naive GEX for NQ): positive = dampened, negative = amplified volatility. | Trade **with** the value structure. GEX only sets expectations; no GEX data on the platform. |
| **Location** | "Discount" in a value-up structure = the **0.705 / 0.786 / 0.886 Fibonacci retracement** of the last swing-low-to-swing-high leg (a golden-pocket zone). It must sit **outside the value area** (below VAL); a fib inside value is ignored. Wants a swing point ("internal structure", possibly a sweep) before price enters the zone. **Below 0.886 without the flip = no trade.** | Hourly / 15-minute swings; the value area he draws spans the overnight (Asia/London) profile in his sketch. |
| **Confirmation** | On the 5-minute footprint in the zone: aggressive sellers (negative delta, volume concentrated at the candle's low) that get **no result** — the candle closes bullish. Absorption alone is not enough ("it happens constantly"); he needs the **shift of dominance**: the next candle opens, pulls back, sellers try again and **fail higher**, the candle flips bullish again (ask-side imbalances ≥ 400 % lighting up). Entry on that second failure. | Two failures, the second at a higher low. |
| **Stop** | Under the second seller failure ("where they weren't able to push past the first time"). | 5-minute bar low. |
| **Target / management** | Swing points (prior swing high). If buyers cannot reclaim the value area, break-even or cut. Trails behind buyer aggression near POC / call wall. Realised **1.5–2 R**, **60–65 % wins, profit factor ≈ 1.8**. | |
| **Participation filter** | ≥ 20,000 MNQ contracts per 5-minute candle; below that (lunch) he does not trade. | ES equivalent ≈ 10,000 (see §2). |

## 2. Mapping to Strategy Spec v2

Two things the DSL could not express were added as primitives (DECISIONS.md, "Creamer pullback study"):
`leg_retracement(n, price)` (the expression tree has no arithmetic, so a fib zone could not be built from
`swing_high` / `swing_low`) and `prior_day_poc/vah/val`. `swing_high` / `swing_low` became valid target levels.

| His concept | Platform expression |
|---|---|
| Value-up structure | `close(1h) > ema(20, 1h)` (mirrored for value-down) |
| Discount zone | `between(leg_retracement(n=3, price=extreme, tf=15min), 0.705, 0.886)` — the bar's low probes the zone; `price=close` in round 1 |
| Outside value | `low < prior_day_val` (previous RTH session's 70 % value area) |
| Absorption bar | `bar_delta < 0` and `close > open` on a 5-minute bar |
| Second failure higher | `low > lowest(n=1)` (higher low than the previous bar) and `close > open` |
| Entry | market at that bar's close; stop `structure: bar_low` + 4 ticks; target `rr 2` (also 1.5R, `level: swing_high`, break-even at 1R, trail) |
| Participation | `volume ≥ 10000` per 5-minute bar (ES 09:30–11:00 p10 ≈ 11.3k, midday p50 ≈ 11.5k; his 20k MNQ floor plays the same role) |
| Session / limits | entries 09:30–11:00, flatten 15:58, max 2 trades/day, stop after 2 consecutive losses, 1 contract, $2.25/side + 1 tick slippage |

Round 1 put zone, absorption and "below VAL" on the **same** bar (his drawing reads that way); round 2 made them a
three-step sequence: zone touched (wick) → within 6 bars an absorption bar → within 3 bars the second failure.

Not reproducible: GEX regime, the exact overnight value area he draws, per-price delta concentration in the wick,
the 400 % bid/ask imbalance highlight (per-price; bars mode only has bar delta), MNQ itself.

## 3. Why round 1 fired nothing — condition frequencies (offline, 5-minute bars, 09:30–11:00, Apr–Jul, 1,566 bars / 87 sessions)

| condition | share of bars |
|---|---|
| hourly close > EMA20 (value-up) | 64.8 % |
| close inside 0.705–0.886 of the 15-min leg | 4.2 % |
| **low** inside the zone (wick) | 6.9 % |
| close below prior-day VAL | 24.6 % |
| bar delta < 0 | 45.8 % |
| bullish close | 51.8 % |
| volume ≥ 10k | 93.4 % |

Joint counts (long side): zone (close) & trend 36 bars / 10 sessions; & below VAL 3 bars / 1 session; & absorption on
the same bar **0**. With the wick reading: zone & trend 93 bars / 21 sessions; & absorption on the same bar 8 / 6; &
below VAL 1 / 1. "Below yesterday's value" and "hourly uptrend" rarely coexist, and a bar that probes the zone and
closes bullish has usually closed back out of it — hence `price: extreme` and the three-step sequence.

## 4. Results — in-sample 2026-04-01 → 2026-07-31 (105 sessions, bars mode, 5-minute primary)

Round 1 (zone, absorption and VAL on one bar):

| variant | n | net $ | PF | win % | exp R | note |
|---|---|---|---|---|---|---|
| C1 faithful (zone close, below VAL, sellers, bullish → second failure) | 0 | | | | | no population |
| C1 without VAL | 3 | +199 | 1.28 | 33 | −0.05 | |
| C1 without hourly trend | 3 | +1,924 | — | 100 | 1.84 | 3 winners |
| C1 swing leg on 5-min (n=5) | 1 | −667 | 0 | 0 | −1.02 | |
| C0 no delta (control, same location) | 3 | −214 | 0.71 | 33 | −0.08 | |
| **C0b absorption + second failure anywhere (no location)** | 93 | +2,482 | 1.11 | 40.9 | 0.16 | Apr +2.3k / May +0.6k / Jun −0.9k / Jul +0.5k |
| C1 breakeven, buyers-on-entry, no-volume-floor, 1.5R, swing target, trail, swing stop, single flip | 0 | | | | | no population |

Round 2 (three-step sequence: wick in zone → within 6 bars absorption bar → within 3 bars second failure higher):

| variant | n | net $ | PF | win % | exp R | max DD $ | long / short $ | Apr / May / Jun / Jul |
|---|---|---|---|---|---|---|---|---|
| **C2 faithful** (15-min leg, no VAL, trend, volume floor) | 10 | −8 | 1.00 | 40.0 | 0.12 | 1,174 | +1.0k / −1.1k | +0.8k / −0.1k / 0.0k / −0.6k |
| C2 + zone below yesterday's VAL | 3 | −338 | 0.53 | 33 | −0.09 | 722 | −0.3k / — | |
| C2 no delta condition (control) | 26 | +308 | 1.04 | 42.3 | 0.20 | 2,574 | +1.8k / −1.5k | +1.4k / −0.4k / −0.2k / −0.5k |
| C2 golden pocket 0.618–0.886 | 15 | +132 | 1.04 | 40.0 | 0.13 | 1,819 | 0.0k / +0.1k | +1.2k / −0.1k / +0.6k / −1.5k |
| C2 swing leg on the 5-min chart | 15 | +2,608 | 1.98 | 53.3 | 0.52 | 1,718 | +1.5k / +1.1k | +1.7k / +0.1k / +1.9k / −1.0k |
| C2 no hourly trend filter | 15 | −130 | 0.96 | 40.0 | 0.12 | 1,753 | 0.0k / −0.1k | +1.2k / −0.7k / 0.0k / −0.6k |
| C2 target 1.5R | 10 | −570 | 0.74 | 40.0 | −0.07 | 1,436 | +0.5k / −1.1k | |
| C2 target swing high | 10 | −108 | 0.95 | 30.0 | 0.01 | 1,698 | +0.8k / −0.9k | |
| C2 break-even at 1R | 10 | −645 | 0.70 | 30.0 | — | 1,811 | +0.4k / −1.1k | exp R column is an engine artefact after a break-even move |
| C2 stop under the 5-min swing low | 11 | +963 | 1.23 | 36.4 | 0.05 | 3,290 | −0.1k / +1.0k | +1.9k / −0.5k / −2.1k / +1.6k |
| C2 entries until 15:00 | 25 | −388 | 0.92 | 36.0 | 0.00 | 3,114 | +1.1k / −1.4k | +2.7k / −0.5k / −0.5k / −2.1k |

Ten to twenty-six trades in four months against his one a day. Nothing here is distinguishable from zero; the 5-min-leg
cell is the kind of number a 15-trade grid produces by chance (see its holdout).

## 5. Holdout — 2025-12-01 → 2026-03-31 (one shot, chosen before looking)

| variant | n | net $ | PF | win % | exp R | Dec / Jan / Feb / Mar |
|---|---|---|---|---|---|---|
| C1 faithful | 1 | +383 | — | 100 | 1.82 | one trade |
| C0 no delta (same location) | 3 | −251 | 0.60 | 33 | −0.10 | |
| C0b no location | 95 | −5,740 | 0.80 | 30.5 | −0.15 | +0.1k / −0.2k / +0.9k / −6.6k |

| **C2 faithful** (pre-chosen) | 6 | +1,636 | 2.54 | 50.0 | 0.41 | — / −0.8k / +1.6k / +0.8k |
| C2 no delta (pre-chosen) | 14 | +2,737 | 1.69 | 28.6 | −0.18 | −1.8k / +0.5k / +2.8k / +1.3k |
| C2 swing leg on 5-min (**post-hoc**, picked after seeing IS) | 19 | −7,460 | 0.16 | 10.5 | −0.73 | −1.0k / +0.7k / −2.1k / −5.0k |

Six and fourteen trades cannot confirm anything; the post-hoc pick is the usual lesson.

## 6. What would change the answer

- **The value-area reference.** His sketch draws value over the overnight (Asia/London) profile; the platform only has
  the prior RTH session's value area, and "below yesterday's value" with an hourly uptrend almost never happens.
  An overnight/ETH value-area primitive is the first thing to add before revisiting.
- **The zone.** A fixed 0.705–0.886 band of the last confirmed 15-minute swing leg is narrower than a hand-drawn
  fib on whatever leg he chooses; the band is hit 7 % of the time, and the swing confirmation lags 45 minutes.
- **Per-price flow.** His absorption is delta concentrated at the candle's low and 400 % bid/ask imbalances at
  specific prices; bars mode has only the bar's net delta. A ticks-mode `stacked_imbalances` / `exhaustion` version
  is possible but the population problem comes first.
- **Instrument.** He trades MNQ; there is no NQ data on the platform.

## 7. Platform state after this study

| id | strategy | status |
|---|---|---|
| `17d18c9b6ef0` | Creamer C1 — faithful round-1 rendering (0 trades) | rejected |
| `dbaa32a1ec7d` | Creamer C2 — zone touch, absorption, second failure (child of C1) | testing (population too small to judge) |
| `d579bce7840b` | Creamer C2 — no delta condition (control) | rejected |
| `2d83160b566c` | Creamer C2 — swing leg on the 5-min chart | rejected (holdout) |
| `7e1032488c9a` | Creamer C0b — absorption + second failure anywhere (no location) | rejected (holdout PF 0.80) |

Engine additions: `leg_retracement`, `prior_day_poc/vah/val`, `swing_high/low` targets (DECISIONS.md), schema
re-exported, tests in `test_primitives.py`. 25 spec files, 31 run summaries, generators, runner and `diag_creamer.py`
are in the study folder. Nothing committed.
