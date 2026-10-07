# Lab notebook — which intraday levels matter for ES (2026-09-07)

Question: of the classic reference levels (open, prior H/L/C, prior POC/VAH/VAL, prior VWAP, overnight H/L,
round numbers, intraday HVNs, developing session extremes), which ones does ES actually respect — and does a level
that accumulates volume today still matter tomorrow?

Data: ES front month, 2025-12-01 → 2026-07-31, 166 full RTH sessions (09:30–16:00 ET, ≥370 1-min bars), 162 valid
day-pairs (roll days 12-15/03-16/06-15 and half days excluded from pairing). 1-min bars + trade prints from the tiered
Parquet store, front-month filtered via front_month.parquet (back-month pitfall respected). Scripts in
`docs/research/2026-09-07-es-key-levels/`.

Method (offline, no engine): a **touch** of level L = bar range within 2 ticks of L after price was ≥10 ticks away
(the arming side = approach side). Outcome within 30 min, walked bar by bar: **respect** = bounce ≥12 ticks back toward
the approach side before penetrating >8 ticks; **break** = penetration first; ambiguous same-bar = break (biases
*against* respect). `open` is tested from 10:00 on. **Control**: every real level gets up to 4 placebo levels at
±23/±41 ticks (dropped within 8 ticks of any real level) run through identical machinery — pooled placebo first-touch
respect = 43.2 % [41.1–45.2] (n 2,263), so any price bounces ~43 % of the time; a level only "matters" above that.
**Volume attraction** = mean vol/tick within ±2 ticks of L ÷ mean vol/tick over the day's range.

## First-touch respect vs matched placebo (primary config: arm 10t, bounce 12t, pen 8t, 30 min)

| level | touched | n first | respect | placebo (n) | z | density vs plc | verdict |
|---|---|---|---|---|---|---|---|
| **today's RTH open** | 68 % | 112 | **58.9 %** [49.7–67.6] | 41.6 % (231) | **+3.02** | 0.80 / 0.79 | **survives** (only Bonferroni survivor, 12 tests) |
| **prior-day VWAP** | 53 % | 86 | **53.5 %** | 36.4 % (143) | +2.54 | 0.84 / 0.74 | real but sub-Bonferroni |
| prior VAL | 48 % | 78 | 44.9 % | 33.8 % (145) | +1.63 | 0.80 / 0.77 | weak support edge |
| prior secondary HVNs | 52 % | 157 | 43.3 % | 37.1 % (272) | +1.26 | 0.76 / 0.77 | not distinguishable |
| prior VAH | 54 % | 87 | 48.3 % | 41.7 % (151) | +0.98 | | placebo-like |
| prior close | 59 % | 95 | 46.3 % | 41.1 % (146) | +0.80 | | placebo-like |
| overnight high | 62 % | 103 | 49.5 % | 45.3 % (232) | +0.72 | | placebo-like |
| morning POC (PM only) | 65 % | 107 | 57.0 % | 55.5 % (274) | +0.27 | | the whole afternoon reverts; POC adds nothing |
| overnight low | 53 % | 88 | 40.9 % | 40.4 % (198) | +0.08 | | placebo-like |
| prior POC | 56 % | 91 | 42.9 % | 43.2 % (125) | −0.05 | 0.66 / 0.76 | placebo-like |
| prior low | 41 % | 66 | 36.4 % | 44.4 % (180) | −1.14 | | breaks *more* than chance |
| prior high | 49 % | 79 | 43.0 % | 51.2 % (166) | −1.19 | | breaks *more* than chance |
| round 25s | 60 % | 247 | 43.7 % | pooled 43.2 % | +0.17 | | dead |
| round 100s | 54 % | 68 | 36.8 % | pooled 43.2 % | −1.05 | | dead |
| session-low retest (developing) | — | 107 | **74.8 %** [65.8–82.0] | vs pooled | +6.42* | | holds 3:1 — *no matched placebo possible, survivorship caveat |
| session-high retest (developing) | — | 113 | **61.1 %** [51.8–69.5] | vs pooled | +3.74* | | holds, weaker than lows |

\* dynamic levels: the extreme by construction already rejected price once, so part of the lift is mechanical;
still real-time-computable and the low/high asymmetry (74.8 vs 61.1) is informative — same dip-buying skew as
F6 longs-only (PF 1.84) in the 2026-08-31 study.

## Robustness — z vs matched placebo across 5 outcome configs (bounce/pen/horizon/arm/tol varied)

| level | (8/8/30) | (12/8/30) | (16/12/45) | (12/8/60) | (12/8/30, arm16 tol1) |
|---|---|---|---|---|---|
| open | +2.9 | +2.9 | +2.4 | +2.9 | +1.9 |
| prior VWAP | +2.5 | +2.5 | +1.2 | +2.5 | +1.5 |
| prior VAL | +2.2 | +1.4 | +1.0 | +1.4 | +1.9 |
| prior close | +0.3 | +0.4 | +0.6 | +0.4 | +0.8 |
| prior high | −0.6 | −0.5 | −0.8 | −0.5 | −0.5 |
| prior low | −0.7 | −1.2 | −1.3 | −1.2 | −1.7 |

Monthly (respect real|placebo): open beats its placebo 6/8 months (loses Dec-25, May-26); prior VWAP 6/8 (loses
Mar-26, ties Jul). Both sides work at the open (57 % from above / 61 % from below); prior VWAP is better as support
(59 % from above vs 47 %). First open retest is typically 10:10–10:54 ET (median 10:22), ~5 touches on a touched day
(first touch is the best: all-touches respect decays to 51.4 % vs 47.7 %).

Magnitudes (median bounce/pen ticks, first touch): open **14.5/6** vs placebo 11/9; prior VWAP **14/6.5** vs 7/10;
prior low 8/13 vs 11/10 — prior-day extremes don't just fail to hold, the tape trades *through* them (stop-run
magnet), consistent with H6's fade family losing money in the 2026-08-31 study.

## Does today's high-volume level matter tomorrow? Essentially no.

Day-D HVNs (smoothed profile peaks, ≥16t apart, top 3 = POC + 2) tested on D+1 against the same ±23/±41t placebos:

| | touch | first-touch respect | vol density ratio |
|---|---|---|---|
| POC (rank 1) | 56 % | 47 % (n 91) | 0.72 |
| HVN rank 2 | 50 % | 45 % (n 80) | 0.64 |
| HVN rank 3 | 54 % | 42 % (n 77) | 0.81 |
| placebo | 52 % | 41 % (n 774) | 0.74 |

- D+1's volume-at-price profile is **uncorrelated with D's** on the overlapping range: Spearman median **−0.09**
  (IQR −0.37…+0.20, overlap median 51 % of range).
- POC migrates a median **146 ticks (36.5 pts)** day-over-day (D+1 range median 273t); D+1's POC re-forms within
  8t of D's on **2 %** of days, within 16t on 5 %.
- No level family shows a volume-density ratio distinguishable from placebo — **volume concentrates where the
  *new* day spends time, not at yesterday's landmarks**. In this sample "a lot of volume traded here yesterday"
  carries no next-day information beyond any random nearby price.

## Conclusions

1. **Today's RTH open is the most important static level in the sample** — first retest bounces ≥3 pts before giving
   2 pts 59 % of the time (placebo 42 %), robust to every parameterization, both approach sides, 6/8 months.
2. **Prior-day VWAP is the best prior-day level** (53.5 % vs 36 %, biggest bounce-vs-placebo gap), then VAL faintly.
   The value-area *edges* out-rank its center: prior POC is dead.
3. **Prior-day high/low are not support/resistance here — they lean break-prone** (respect below placebo in all
   5 configs, penetration median > bounce median). Overnight H/L, prior close, round numbers: placebo-like.
4. **Developing extremes hold on retest** (lows 75 %, highs 61 %) with a strong long-side skew — but no clean control
   exists for a dynamic level, so treat the absolute rates, not the z, as the finding.
5. **Intraday volume concentration does not persist**: next-day touch/respect/volume at yesterday's HVNs ≈ placebo,
   profiles day-over-day uncorrelated, POC migrates ~36 pts. Value *migration*, not value *memory*, in this regime.
6. Caveats: one instrument, 8 months, one (high-vol, upward-drifting) regime; conditional-on-touch stats; 1-min
   resolution; 12 families tested → only `open` clears Bonferroni; levels tested in isolation (confluence untested).

Strategy hypotheses this licenses (next session, engine): (a) open-retest bounce 10:00–12:00, stop beyond-level,
target VWAP-scale — the only Bonferroni-clean edge; (b) session-low retest long — needs a definition that survives
real-time (arming distance, first retest only); (c) *negative* filter: don't fade prior-day H/L (re-confirms H6).

## Addendum (same day) — order flow at the level

Per first-touch event (real + placebo): approach delta (5 bars pre-touch), touch-bar delta, prints within ±3t of the
level in [t0, t0+2 min) (volume + aggressor delta), and the last 60-s book checkpoint ≤ t0 (defending-side resting size
level→8t beyond, largest single level, displayed size ±1t; iceberg proxy = traded at level ÷ displayed). Signs
normalized so + = toward the bounce. "Tradeable set" = open, prior VWAP, session-extreme retests (418 first touches,
base respect 62.4 %). Script `orderflow.py`.

| feature (knowable at touch-bar close?) | effect on respect | verdict |
|---|---|---|
| **touch-bar delta toward bounce** (yes) | tradeable: ≤0 → 0.50–0.74, >0 → **0.815** (n 65, z +3.5 vs rest); works on every family incl. "dead" ones (on_low conf 0.80 vs 0.33, z +3.4); placebo+conf only 0.634 → the level adds +18 pp on top of the flow | **best confirmation** — but see cost below |
| approach delta, 5 min (yes) | flat everywhere (z −0.5…+1.1) | drive vs exhaustion into the level: irrelevant |
| resting defend size / wall (yes, ≤60 s stale) | defend >600: +3–6 pp, similar in placebo; wall size: nothing | weak & generic — optional toggle at best (matches [[es-1min-orderflow-scale]]) |
| volume at level, t0..t0+2m (NO — overlaps outcome) | monotone *negative*: q1 0.88 → q4 0.38 (tradeable) | partly mechanical (fast bounce = little volume) — read as: **lingering at the level with churn = failing** |
| aggressor delta at level, t0..t0+2m (NO) | heavy *toward*-bounce flow at the level → 0.12–0.40 respect (trapped aggressors); heavy into-level flow that stalls (absorption) → mild generic lift only | absorption confirms generic bounces, adds ~0 on the strong families |
| iceberg proxy traded/displayed (NO) | monotone negative: <2 → 0.77, >15 → **0.14** | reloading eats the bounce; use as **abort**, not entry |

**The catch — confirmation costs the bounce.** For confirmed events the touch-bar close is already a median **12t off
the level** (p75 17t); median total bounce 21t → median remaining ≈ 10t, while the stop (past the level) stretches to
~14t+. Naive econ of "enter on confirmed close, stop 10t past level, target level+12t" is **−5.4t/trade gross despite a
75.5 % win rate** (all-level pool, n 184, ~5.5 signals/wk; monthly win rate 0.56–0.96, positive every month).
Confirmation improves *odds* and destroys *entry price* at 1-min resolution — the trade-off must be resolved in the
engine: either (i) limit-at-level entry, no confirmation, order-flow used only as an early-abort/exit rule
(churn-at-level, iceberg ratio, ≥2 bars lingering), or (ii) confirmed entry with stop at the touch extreme (confirmed
events' median post-confirmation penetration is 0–2t) and ≥1.5R targets off the fat right tail, or (iii) sub-minute
confirmation in ticks mode (delta in the first seconds at the level) to cut the 12t cost. All three are engine
experiments, not offline conclusions.

**Intra-bar follow-up (`intrabar.py`, 7.0M prints, 1,577 real events):** entering on the delta trigger *before* bar
close does not recover the cost — delta and price arrive together. Cum-delta triggers from the first print at the
level: X=100 within the touch bar → 76 % respect at median cost **+8t** (t≈35 s); X=400 → 80–85 % at **+18t**; the
odds/price ladder is continuous, there is no free window. Worse: **cheap triggers (fill ≤4t from the level) respect
only 33–46 % — below the unconditional 62 % base.** Toward-bounce aggression that fails to move price off the level is
absorption against the bounce (the trapped-aggressor signature from the other side), so early confirmation adversely
selects. Level-anchored econ is negative at every threshold (best −1.8t gross). Design conclusion sharpened: the
choice is *reversion* (limit at level, no confirmation, flow aborts) or *momentum-after-defense* (enter on trigger,
geometry re-anchored to entry, stop past level). Note `max_bounce` is truncated at outcome resolution, so winners'
true MFE is understated offline — the momentum variant's upside can only be measured in the engine.

**Bracket simulation (`rr.py`)** — does earlier entry improve R:R? Full brackets on 1-min bars (entry = actual
trigger price +1t slip, stop = level−10t with −1t slip, targets 8–32t from entry on trade-through, 90-min horizon,
15:58 flatten, $29.50 commission; same-bar ambiguity → stop):

| variant (X=100/200, all real levels) | 8t | 12t | 16t | 24t | 32t |
|---|---|---|---|---|---|
| cheap trigger ≤4t (n 122) | −$68 | −$57 | −$47 | −$25 | **−$5** |
| expensive trigger >4t (n 488) | −$57 | −$46 | −$36 | −$42 | −$38 |
| tradeable expensive X=200 (n 76) | −$27 | **−$15** | −$18 | −$33 | −$18 |
| limit-at-level, trade-through fill (n 1604) | −$70 | −$63 | −$60 | −$54 | −$52 |

Answers: (1) yes, earlier entry improves *relative* R:R at wide targets — cheap-entry 32t cells reach ≈ breakeven
(−$5 / +$6) vs −$18…−$38 for confirmed entries — the ~8t better entry price fully offsets the lower win rate,
but only at 24–32t targets; at tight targets the win-rate collapse dominates. (2) **Nothing clears friction**:
best cells are +$30–40 *gross* but ≈ $54.5/trade of commission+slippage eats every variant at this trade scale.
(3) **The resting-limit reversion entry is adversely selected**: trade-through fills catch every deep breach and miss
the best bounces (win 39–47 %, −$52…−$70 everywhere) — the 62 % respect rate does not convert into 62 % of fills.
Conclusion: level-bounce edges are real but 10–30t-scale, and ES friction kills sub-40t-scale round trips (the same
reason every surviving strategy in the 2026-08-31 study uses 48–80t stops). Levels should enter the platform as
*context* for larger-scale strategies — anchor stops/targets of the IB60/OR5 families at open/pVWAP/session extremes,
and as the confluence/abort layer — not as standalone scalp entries. Unmodeled outs: time-stops (losers rarely need
the full −10t), maker exits in ticks mode (~1–2t back), management — engine territory if pursued.

## Postscript — IB60 champion fails true OOS (evening session)

B13 rebuilt as `a316c13cbe9b` (the original 17d18cc2cd1b was lost in a DB reset) and run in Nautilus ticks mode on
Dec-2025→Mar-2026 — four months ingested *after* the Aug-31 fitting, so genuinely unseen (job `e07ac1e4434b`):
**68 trades, −$9,531, PF 0.596, −0.17R, DD 10.4 %** vs Apr–Jul IS PF 1.93. Fill geometry and cadence match the design
(±64t 1R brackets, ~0.85 trades/day) — this is the strategy failing, not the harness. Regime shift explains only part
of it (Dec–Mar trend-day share 13.5 % vs 17.1 %, median ER 0.13 vs 0.163 — more rotational, the tape B13 always lost
in): the Apr–Jul edge was substantially mined (16 engine variants + offline grids on 72 trades; the 3/3 positive WF
folds all lived inside the same four months the variants were selected on). Dec–Mar is now a **burned window** for the
IB60 family — any variant tuned to pass it cannot claim it as OOS. Program status: no strategy currently stands
validated on unseen data. Full-range run `33dd151c2d76` pending as a rebuild-fidelity check (its Apr–Jul segment
should reproduce ≈ +$12k if the spec is faithful).

## Postscript 2 — the low-win/high-PF profile does not exist here (`sim_lowwin.py`)

Search for asymmetric-payoff designs (hold-to-close / trailing, structural entries, one trade/day, friction 2.36t),
Dec-25→Jul-26, 143–147 trades per variant: IB60 hold-to-close stop 64t = **win 35 %, avgW/avgL 1.9, PF 1.01** — the
desired *shape*, zero edge. IB-opposite stop 0.94; trails 0.63–0.65 (chop tags every trail); 2R-target comparison
0.87. **Prior-day H/L break traded WITH the break: PF 0.66–0.74, −$20–24k** — break-prone ≠ follow-through
(median penetration was only 13t); this retracts the earlier "trade with the break" suggestion. ON-range break
hold-to-close ≈ 0.98–0.99. B11 no-time-stop diagnostic on Dec–Mar (job 0fbc96efbfc2): final: 68 trades, −$14,031, PF 0.56, win 38 %, stops 37 vs targets 21 — $4.5k worse than B13 — the time stop was cushioning, not causing, the OOS loss. Combined with H2/H10/H16 and the
2026-08-31 grid: intraday ES in this period pays mean-reversion (high win rate), not continuation; a low-win/high-PF
edge at 1-min structural scale is not extractable from this sample (~12 more variants mined today — count them).
