# SSRN literature scan — futures research the platform can use (2026-09-07)

Question: does the Social Science Research Network hold futures research that maps onto what this platform can
test — ES/NQ, 1-minute bars plus prints and book, the Strategy Spec v2 DSL (55 primitives), IS/WF/OOS validation?

Method: ~20 targeted searches of papers.ssrn.com across intraday momentum, opening range, order-flow imbalance,
limit-order-book imbalance, VWAP/value area/market profile, overnight–intraday reversal, time-of-day seasonality,
macro announcements, VPIN, hidden liquidity, spoofing. Abstract-level review only; SSRN returns 403 to automated
fetchers, so abstracts came from search snippets, arXiv mirrors, publisher pages and the Semantic Scholar API.
Nothing below is a full-text replication. Evidence quality is noted per paper (peer-reviewed journal vs. working
paper vs. single-author practitioner note).

Platform facts the mapping relies on: ES front month 2025-12-01 → 2026-07-31, 166 full RTH sessions (no NQ yet);
`vah`/`val`/`poc`/`vwap` primitives are **session-developing** (no prior-day variants in the registry); the
expression tree has comparisons, logic, stateful operators and `within_ticks` but **no arithmetic or `abs`**;
`rel_volume` is a rolling n-bar ratio, not a minute-of-day baseline; `gap_points` = RTH open − prior RTH close;
exits support wide stops, `trailing`, `breakeven`, `timeStop`, `scaleOut`; `session.noTradeWindows` exist.

---

## Ranked shortlist

### Tier A — testable now (or with one small primitive), mechanism-backed

**A1. Last-30-minute intraday momentum.**
Gao, Han, Li, Zhou, *Market Intraday Momentum* (RFS 2018; SPY 1993–2013): the return from the prior close to 10:00
and the 15:00–15:30 return both predict the 15:30–16:00 return, stronger on volatile, high-volume, recession and
macro-news days. Baltussen, Da, Lammers, Martens, *Hedging Demand and Market Intraday Momentum* (JFE 2021; 60+
futures on equities, bonds, commodities, FX, 1974–2020, ES included): the return from the prior close to 15:30
positively predicts the last 30 minutes, reverts over the following days, and is tied to gamma hedging by option
market makers and leveraged ETFs. Peer-reviewed, replicated across markets — the strongest evidence in this scan.

Platform mapping — expressible today:
- entry window 15:30–15:31, `direction: both`, trigger `gt(close, prior_day_close)` (mirrored for shorts), market
  entry, no target (`rr` very large), wide stop, `timeStop` ≈ 28 bars, `flattenAt` 15:58 (already the default).
- size filter for "large day move" without arithmetic: `not(within_ticks(close, prior_day_close, N))`.
- volume filter: `gt(rel_volume(n=390), 1.2)` approximates a high-volume day.
- what it cannot express: the 15:00–15:30 leg as a separate predictor (needs a `return_since(minutes)` primitive
  or a `prior_bar_close(tf=30min)` context read — the 30-minute context timeframe plus `field: close, tf: 30min`
  gets close).

Expectation management: the effect is a few basis points per day (≈ 2–4 ES points at current levels); one
trade per session → ~160 trades on our data; Topstep round trip $3.78 + 1 tick slippage ≈ 0.33 pt. Marginal
per trade, so condition on move size and volume before spending a validation run. Fastest first look: an
offline notebook like `2026-09-07-es-key-levels/` (sign agreement of prior-close→15:30 vs 15:30→16:00,
bucketed by |move| tercile), not the engine.

**A2. Failed-auction reversion and exit design at value-area extremes (Market Profile, ES-specific).**
- Desmon Perry, *Everyone Is a Genius After the Bar Closes* (May 2026, pre-registered): failed weekly value-area
  auction reversion on ES, 5-minute resolution, 1,017 trades Jan 2015–May 2026, mean +0.06 R, bootstrap p = 0.014;
  NQ/GC/ZN replication p = 0.09 (not significant). Small edge, honest reporting.
- Perry, *Beyond the Boundary* (July 2026): daily value-area extremes, 4 instruments, 11 years: ~52 % of probes
  beyond VAH/VAL fail; win rate rises monotonically with within-session test count (22.8 % on the first probe →
  >76 % by the tenth) and with value-area maturity; strengthens under volatility stress; holds from 3-min to 60-min bars.
- Theo Howard, *Stop Distance, Exit Methodology, and Signal Preservation in Intraday Value Area Breakouts* (March
  2026, ES, 13 months): price-based stops are value-destroying at all twelve tested distances relative to the
  unmanaged horizon; a time-aware exit stack (trailing + break-even tightening + no-progress timeout) beats the
  price-based baseline in 11 of 13 months; events in the first 30 minutes are significantly worse.

Both authors are single-author working papers, but Perry pre-registers and both are ES-native. The first-probe
number (22.8 %) is consistent with our own key-levels result that prior VAH/POC look placebo-like at *first*
touch — the papers say the information is in the *count* of tests, which we never conditioned on.

Platform mapping:
- gap: the papers use **prior-day / prior-week** value areas; the registry only has session-developing
  `vah/val/poc`. The key-levels notebook already computes prior-day VAH/VAL/POC offline, so `prior_day_vah`,
  `prior_day_val`, `prior_day_poc` (and `prior_day_vwap`, our second-best level) are cheap to add and should
  also join `LEVELS` for target use.
- gap: a `level_test_count(level, tolTicks)` primitive (touches of a level this session) to express Perry's
  maturity finding; `touched` is boolean and `bars_since` gives recency, neither counts.
- expressible today: "probe beyond then close back inside" = `retest`/`held_below` on the level; Howard's exit
  stack = `exit.trailing` + `exit.breakeven` + `exit.timeStop` with a wide `stop`; his first-30-minute exclusion
  = `noTradeWindows: [09:30–10:00]`.

**A3. Overnight gap → open-hour reversal.**
- Yu, Rentzler, Wolf, *Nasdaq-100 Index Futures: Intraday Momentum or Reversal?* (JOIM 2005): last night's return
  is predominantly followed by intraday reversal; yesterday's return gives both; effects depend on signs and on
  Mondays; "a structural feature of the futures market in the opening hours". Peer-reviewed but old.
- Mathias Mesfin, *A Validated Volatility-Volume-Gap Classifier* (May 2026, MNQ 5-min, 947 days 2021–2025,
  expanding-window thresholds): |gap|, |first-30-min return| and first-bar volume vs a 20-day baseline flag ~4.4 %
  of days that show morning directional continuation then partial reversal into the close — **but no directional
  strategy on them passed walk-forward + costs + year-consistency.** Treat as a regime label, not an edge.
- Boyarchenko, Larsen, Whelan, *The Overnight Drift* (NY Fed SR 917): ES overnight returns are large and positive
  in European opening hours, strongest after US-session selloffs, tied to dealer inventory after close-of-day
  imbalances. Outside our RTH-only session model; relevant only as a filter (prior_session_direction, gap sign).

Platform mapping: `gap_points`, `prior_session_direction`, `rel_volume`, `opening_range_*` exist;
`get_daily_direction_stats` covers prior-day direction but not gap sign/size — a small extension. Our key-levels
result that today's RTH open is the most respected level (58.9 % vs 41.6 % placebo) is the tradable half of
"gap reversal": first pullback to the open after a gap, in the gap direction, rather than a fade to the prior
close (which was placebo-like as a level).

### Tier B — informs the engine and features more than a strategy

**B1. Order-flow imbalance predicts, but only for seconds.** *Returns and Order Flow Imbalances* (arXiv
2508.06788, ES at one-second frequency within 15-minute windows, SVAR identified through heteroskedasticity):
price and flow impacts are significant at one second and shocks dissipate almost entirely within a second; macro
news raises price impact and lowers flow impact. Chen, Kao, Leung (SSRN 966056): trading imbalance explains ES
volatility better than volume at 5-min to daily. Kethan S E (SSRN 7053198): OFI information coefficient ≈ +0.004 at
10-second bars. Implication: `bar_delta` at 1 minute is mostly contemporaneous; keep using delta as a
*confirmation at a level* (as the OR15 study did), not as a standalone trigger, and expect the same delta to
mean more on announcement days.

**B2. Queue imbalance is a one-tick-ahead predictor.** Gould & Bonart (2015): bid/ask queue imbalance strongly
predicts the direction of the *next* mid-price move, strongest for large-tick instruments (ES is large-tick).
`book_imbalance` exists but is None in backtests. Its practical use here is not entries but the execution layer:
fill probability and adverse selection for `orderType: limit` entries with `timeoutBars`.

**B3. Hidden liquidity is 43 % of ES volume.** Phuensane & Williams (SSRN 2852760, 2016): a hidden-order detector
on ES finds 43 % of trade volume involves invisible liquidity; icebergs reduce price impact. Implication: visible
resting size (`large_resting_size_near`, heatmap walls) understates real liquidity; print-based `absorption`
(price fails to move against aggressive volume) is the better iceberg proxy — supports keeping it print-based.
Mittal & Choudhary, *Liquidity-Driven Breakout Reliability* (Dec 2025, 15,000 breakouts, index futures/FX/commodities
2022–2024): breakouts into low-volume areas persist longer than those into high-volume nodes. Our HVN test measured
*support*, not *continuation*; a `volume_ahead(ticks)` primitive over the session or prior-day profile would let a
breakout spec require a thin destination.

**B4. Trade classification and VPIN.** Andersen & Bondarenko (SSRN 2292602, 2305905): on ES, a tick rule on
individual transactions beats bulk-volume classification, and VPIN's early-warning value largely disappears when
built correctly. We use exchange-reported aggressor side from MBO, which is better than either. Do not build VPIN.

**B5. Intraday trading invariance.** Andersen, Bondarenko, Kyle, Obizhaeva (ES 2008–2011): return variance per
transaction is log-linear in trade size with slope −2, stable across the diurnal cycle. Implication for
normalisation: volume, delta and range should be baselined by minute-of-day, not a rolling 20-bar window
(`rel_volume` today). This also matches the ES 1-min scale memory that primitive defaults are miscalibrated.

**B6. Pre-announcement drift.** Kurov, Sancetta, Strasser, Wolfe (JFQA 2019): ES and Treasury futures move in the
"correct" direction starting ~30 minutes before 7 of 21 market-moving releases; the pre-release drift is about half
of the total adjustment. Lucca & Moench pre-FOMC drift (JF 2015) is the famous case, though Kurov, Wolfe, Gilbert
report it weakening. Naveen (SSRN 6631518, 2020–2025): post-release 30-minute realised volatility ×2.5.
Platform gap: no economic calendar; `noTradeWindows` are static per spec. A `minutes_to_release` primitive backed
by a calendar table would serve both as a filter (avoid entries 30 min before/after) and as a hypothesis
(drift into the release).

### Tier C — negative results and cautions worth citing

**C1. OHLCV-only intraday signals do not clear futures friction.** Mesfin, *Structural Limits of OHLCV-Based
Intraday Signals in MNQ Futures* (May 2026; arXiv 2605.04004): 14 signal families, 947 days, 5-minute bars,
validation = walk-forward + t ≥ 2 + ≥ 30 trades + positive net of a 2-point round trip + year consistency. None pass;
gross 0.07–1.50 points per trade against 2 points of friction. Two positive controls did pass (an "RTH confluence"
signal, +15.8 net points, t = 5.8; a London-session signal). The validation stack is almost identical to ours,
which makes this a useful external benchmark for how strict our pass criteria are.

**C2. Published simple S&P futures intraday rules fail under realistic assumptions.** Donninger (SSRN 2488539,
2014): none work as advertised; restricting to regimes via the implied-volatility term structure helps. We have no
VIX/VIX3M feed; `atr` on a daily context timeframe is the local substitute.

**C3. Noise-band intraday momentum (Zarattini/Aziz/Barbon 2024; Maróy 2025; Quantitativo ES/NQ replication).**
Band = open × (1 ± mean |return from open at minute t| over the last 14 days), adjusted for the overnight gap;
enter on a band break, trail on VWAP or the band, flatten at close, volatility-targeted leverage. Reported SPY
Sharpe 1.33; ES replication Sharpe 1.25 with a 90-day band but **flat 2010–2017**, 36 % win rate, +2 bps per trade,
very sensitive to slippage. Not validatable on 8 months of data; recorded as a family. The reusable piece is the
primitive: `expected_move(minutes_since_open, days)` is also the right volatility normaliser for OR/IB breakout
distances and would replace fixed-tick thresholds in several existing specs.

**C4. Not applicable.** Baltussen, Da, Soebhag *End-of-Day Reversal* (cross-sectional single stocks, not index
futures). Spoofing/layering detection (Do & Putniņš; Montgomery) needs book replay, which is Phase 5 and None in
backtests. Deep OFI (Kolm et al.) is equities LOB machine learning.

---

## Proposed experiments, in order

| # | Hypothesis | Source | Needs | First step |
|---|---|---|---|---|
| E1 | Sign of prior-close→15:30 move predicts 15:30→16:00, conditional on move size and volume | A1 | nothing new | **Done 2026-09-07, negative** — see `2026-09-07-intraday-momentum.md`: no predictability on 156 day-pairs, unconditional strategy PF 0.72 net, shorts actively lose; no validation run warranted |
| E2 | Failed probes of **prior-day** VAH/VAL revert, win rate rising with test count | A2 | `prior_day_vah/val/poc`, `level_test_count` | extend the key-levels machinery: outcome by touch index (1st, 2nd, 3rd+) instead of first touch only |
| E3 | Howard's exit stack beats the price stop on the IB60 champion (`17d18cc2cd1b`) | A2 | nothing new | one lineage child: wide stop + `trailing` + `breakeven` + `timeStop`, plus `noTradeWindows` 09:30–10:00; single changed variable per child |
| E4 | Gap-conditioned open retest: after a gap, the first pullback to the RTH open holds in the gap direction | A3 + key-levels | `get_daily_direction_stats` gap conditioning (small) | bucket the existing open-retest events by `gap_points` sign and size |
| E5 | ES drifts into scheduled releases; entries within ±30 min of a release are worse | B6 | calendar table + `minutes_to_release` | ingest a release calendar for Dec 2025–Jul 2026, re-run the OR15 diagnostics split by release proximity |

## Feature gaps this scan surfaced

1. `prior_day_vah`, `prior_day_val`, `prior_day_poc`, `prior_day_vwap` primitives (already computed offline in
   `2026-09-07-es-key-levels/`); add to `LEVELS` for targets.
2. `level_test_count(level, tolTicks)` — Perry's maturity effect.
3. `expected_move(minutes_since_open, days)` — Zarattini noise band; also a normaliser for breakout distances.
4. Arithmetic or `abs`/`distance_ticks(a, b)` in `engine/expr.py`; today only `within_ticks` approximates a
   distance test, and it cannot express "return since X exceeds k × ATR".
5. Economic calendar + `minutes_to_release`; dynamic no-trade windows.
6. `rel_volume` (and delta/range normalisers) baselined by minute-of-day (B5), not a rolling window.
7. `volume_ahead(ticks)` from the session/prior-day profile (B3) for breakout-into-thin-air rules.
8. `get_daily_direction_stats` conditioned on gap sign/size (E4).

## Putting the papers in front of Stratos

The research agent's knowledge store only indexes the pinned ML4T repository. This note is Markdown, so it can be
ingested as a second source without code changes (the ingester walks a directory for `.md/.rst/.txt/.py/.ipynb`;
pass `--revision` explicitly because the path is not a repo root):

```bash
cd backend && python scripts/ingest_knowledge.py ../docs/research \
  --name platform-research-notes \
  --url https://github.com/LeonelAviles/trading-platform/tree/main/docs/research \
  --revision "$(git rev-parse HEAD)"
```

That lets `search_knowledge` cite these findings (and the key-levels / OR15 notes) when proposing hypotheses.
Whether project notes belong in the "approved research" store is the owner's call; not run here.

## Sources

Peer-reviewed / established:
- Gao, Han, Li, Zhou — Market Intraday Momentum. https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2440866
- Baltussen, Da, Lammers, Martens — Hedging Demand and Market Intraday Momentum. https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3760365
- Yu, Rentzler, Wolf — Nasdaq-100 Index Futures: Intraday Momentum or Reversal? https://papers.ssrn.com/sol3/papers.cfm?abstract_id=712168
- Boyarchenko, Larsen, Whelan — The Overnight Drift. https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3546173 (NY Fed SR 917)
- Andersen, Bondarenko, Kyle, Obizhaeva — Intraday Trading Invariance in the E-mini S&P 500 Futures Market. https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2693810
- Andersen, Bondarenko — Assessing Measures of Order Flow Toxicity. https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2292602 ; Reflecting on the VPIN Dispute. https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2305905
- Kurov, Sancetta, Strasser, Wolfe — Price Drift before U.S. Macroeconomic News. https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2637528
- Lucca, Moench — The Pre-FOMC Announcement Drift. https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1923197 ; Kurov, Wolfe, Gilbert — The Disappearing Pre-FOMC Announcement Drift. https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3134546
- Gould, Bonart — Queue Imbalance as a One-Tick-Ahead Price Predictor. https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2702117
- Phuensane, Williams — Term-Structure Analysis of Hidden Order in the LOB: E-mini S&P 500. https://www.ssrn.com/abstract=2852760
- Chen, Kao, Leung — Is Trading Imbalance a Better Explanatory Factor in the Volatility Process? (E-mini). https://papers.ssrn.com/sol3/papers.cfm?abstract_id=966056
- Chakrabarty, Pascual, Shkilko — Evaluating Trade Classification Algorithms. https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2182819

Working papers / practitioner notes (2024–2026):
- Perry — Everyone Is a Genius After the Bar Closes (failed weekly auction reversion, ES). https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6728359
- Perry — Beyond the Boundary (daily value-area extremes). https://papers.ssrn.com/sol3/papers.cfm?abstract_id=7106618
- Howard — Stop Distance, Exit Methodology, and Signal Preservation in Intraday Value Area Breakouts (ES). https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6350238
- Mesfin — A Validated Volatility-Volume-Gap Classifier (MNQ). https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6750442 / https://arxiv.org/abs/2605.11423
- Mesfin — Structural Limits of OHLCV-Based Intraday Signals in MNQ Futures. https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6709401 / https://arxiv.org/abs/2605.04004
- Zarattini, Aziz, Barbon — Beat the Market (SPY intraday momentum). https://ssrn.com/abstract=4824172 ; Maróy — Improvements to Intraday Momentum Strategies. https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5095349 ; Quantitativo ES/NQ replication. https://www.quantitativo.com/p/intraday-momentum-for-es-and-nq
- Zarattini, Aziz — VWAP: The Holy Grail for Day Trading Systems. https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4631351
- Mittal, Choudhary — Liquidity-Driven Breakout Reliability. https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5962358
- Lee — VWAP-Based Regime Classification Model. https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6438039
- Returns and Order Flow Imbalances: Intraday Dynamics and Macroeconomic News Effects (ES, 1-second). https://arxiv.org/abs/2508.06788
- Kethan S E — Predictive Order Flow Imbalance. https://papers.ssrn.com/sol3/papers.cfm?abstract_id=7053198
- Naveen — Scheduled Macroeconomic Announcements and Intraday Volatility (SPY/QQQ 2020–2025). https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6631518
- Donninger — An Investigation of Simple Intraday Trading Strategies (2014). https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2488539
- Rogul — Hybrid Channel Breakout Framework for NQ Futures. https://papers.ssrn.com/sol3/Delivery.cfm/6469419.pdf?abstractid=6469419
- Xu, Li, Singh, Li — Cross-Market Intraday Time-Series Momentum. https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4651331

Seen, judged not applicable: Baltussen, Da, Soebhag — End-of-Day Reversal (single stocks) https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5039009 ; Do, Putniņš — Detecting Layering and Spoofing https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4525036 ; Kolm, Turiel, Westray — Deep Order Flow Imbalance https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3900141 ; Easley, López de Prado, O'Hara — Flow Toxicity and Liquidity https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1695596 ; Eaves, Williams — Are Intraday Volume and Volatility U-Shaped? https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1565236
