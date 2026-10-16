# Bounded real-data discovery and execution

This workflow builds on commit `46808b11c0f37766f00e00bbae17699c168e2519`.
It separates a successful technical workflow from an economically successful
strategy. Failed hypotheses remain results; no profitable strategy is promised.
No live/paper orders, infrastructure, deployment or final-holdout evaluation is
part of this workflow.

## Scope and frozen experiment

The first run uses ESU6 and 12 RTH weekdays, July 13–28, 2026. Discovery uses
July 13–17 and 20–22 (8 sessions), validation July 23–24, and the conditional
later internal test July 27–28. December 2025–March 2026 is the repository's
**sealed historical holdout**. It predates training and cannot be described as
a chronological forward test. It stays sealed. The previous market aggregates
were inspected only for availability; no holdout fitting/scoring was performed.

The frozen search space is one deterministic 3-cluster fit, maximum 50 Lloyd
iterations, minimum 50 samples per cluster, one interpreted hypothesis and one
execution configuration. There are no random restarts or alternative k values.
Standardization uses discovery-only means and population standard deviations;
constant columns get scale 1. Initial centers use deterministic farthest-first
selection with lexicographic tie breaking. IDs follow sorted standardized
centers. Every cluster, including undesirable or sparse groups, is retained in
the feature-only review. Future returns are unavailable to that review.

The run records tonight's bounded user authorization and its expiry in the
immutable discovery document. Exact approval records identify the **assistant
acting under that authorization**, rather than claiming the user manually
reviewed generated hypotheses. Normal API approval and separate execution
steps remain in place. The runner stops after its first hypothesis result, a
review stop, or a technical failure. It never resets consumed reservations.

Preparing a hypothesis reserves **both** later partitions on evaluation, even
when validation fails and internal-test metrics remain sealed. These dates
cannot be reused by another staged hypothesis. The diagnostic execution is
restricted to already exposed validation sessions; it never opens the internal
test to rescue a failed screen. The normal candidate-export path still rejects
`no_edge` results. Existing external/legacy exposure cannot be retrospectively
proved absent; this is documented rather than labeled pristine evidence.

## Data, formulas and quality

Postgres contains application/research metadata, not raw market events. Raw
GLBX.MDP3 MBO is retrieved through existing local AWS access using the local
manifest. No credential setup or AWS permission changes are needed. The bounded
preparer HEADs selected missing objects, enforces a 6 GiB raw-cache limit and
20 GiB free-space floor by default, verifies expected checksums when available,
and never deletes raw files. It records observed SHA-256 for every source. Some
archive manifest entries lack a reference checksum: an observed download hash
pins the bytes used but is **not independent vendor-integrity verification**.

The complete archive is 45.73 GiB compressed and does not fit the approximately
37 GiB starting free space. This run retrieves only its selected research days.
S3 Glacier Instant Retrieval permits immediate reads; retrieval/request/network
charges may apply. No account-specific bill estimate is claimed.

Actual contract, instrument ID, publisher ID and channel ID are read from each
source's opening chunk and pinned in the feature request. Ambiguity or a missing
selected contract fails closed; identifiers are never invented. DBN decoding
streams in 100,000-record chunks. A complete F_LAST event becomes visible
atomically on receive time. Minute windows are [open, close); events at close
belong to the next minute. Trades and fills do not mutate resting orders.

The ordered six-feature contract is:

| Feature | Definition |
|---|---|
| spread_ticks | (best ask − best bid) / tick size at minute boundary |
| depth_imbalance | (bid size − ask size) / (bid size + ask size), best 10 price levels per side |
| aggressive_buy_rate | sum of buy-aggressor T-record size / 60 seconds |
| aggressive_sell_rate | sum of sell-aggressor T-record size / 60 seconds |
| add_rate | number of A records / 60 seconds (event count, not volume) |
| cancel_reduction_rate | sum of C-record reduced size / 60 seconds |

C reductions include execution-related removal and are not pure voluntary
cancellations. Spread/depth are boundary observations, not minute averages.
OHLCV is built from receive-available trades. Missing/stale/crossed books and
no-trade minutes are omitted; they are not filled. This run uses 5-second
book-age/event-lag limits and at most 1% unknown aggressor volume. The extractor
rejects unsupported flags, corrupt order transitions, absent snapshots,
incomplete terminal events and regressing per-instrument sequences. It cannot
prove full-channel gap continuity from an instrument-filtered feed.

Verified prepared contract/day Parquet partitions include extraction settings,
source identifiers/hashes, dependency/code hashes, quality counters and a content
hash. An immutable experiment snapshot pins its selected rows. Neither licensed
raw files nor source feature/trade rows are committed to Git.

## Interpretation and hypotheses

`GET .../ml-discoveries/{id}/review-context?digest=...` exports only the question,
contract, discovery dates and feature summaries. The assistant interprets that
evidence in the current conversation. It can reject patterns, or propose a
falsifiable direction/cluster selection and explain alternatives it rejected.
`POST .../{id}/assistant-review` records a `ClusterReview`, its exact input digest
and supplied reviewer attribution, using the same one-review budget as the
optional OpenAI API reviewer. Attribution is caller-attested, not cryptographic
proof of model identity. This path needs no OpenAI API credential and cannot
approve or execute a hypothesis. The UI exposes export and record controls.

The first run fixes the holding horizon at 5 minutes before seeing clusters.
The label screen enters at next-minute open, exits at horizon close, and charges
4.36 ticks round trip: four slippage ticks plus $4.50 / $12.50 commission. It
requires at least 30 signals, positive mean net ticks, and strict improvement over
the better of always signaling and never signaling on the same per-bar denominator.
Labels cannot span session gaps or chronological partition boundaries. Overlapping
labels are not independent trades. Later data never refits the scaler or geometry.

Iteration means interpreting/revising a training-only idea and then rejecting or
retaining the frozen hypothesis on evidence. It does **not** mean repeatedly
trying heldout dates until a winner appears. An additional economic hypothesis
would require separately chosen unconsumed later sessions and a new bounded scope.

## Deterministic Nautilus diagnostic

Frozen MBO signals now enter at the **next contiguous minute's open**, after the
signal's complete feature minute. The replay sends a synthetic opening-price event
one nanosecond after the preceding bar completes. These events are explicitly
execution-price events, not observed trade ticks: they do not update features,
volume or trailing indicators. A missing next minute drops the pending signal.
The one-nanosecond timestamp encodes event ordering; it is not an observed first-trade timestamp or a measured latency. No additional measured feed/order latency is simulated. The time-stop index matches the label horizon. A deliberately gapped-price test
checks entry time/price and label/engine horizon parity. Legacy strategies retain
their earlier execution convention.

The single diagnostic uses one ES contract, $100,000 reference capital, fixed
20-tick stop, 30-tick target, 5-minute time stop, entry window 09:31–15:45 ET,
flat by 15:58, max 10 trades/day, 5-bar cooldown and halt after 3 consecutive
losses. Limits include 1% daily and 3% weekly configured loss limits. These are
exploratory configuration values, not evidence that a prop firm would accept them.
The existing engine's implemented risk behavior is retained; configured weekly
limits must not be assumed enforced without checking engine support.

Stop wins if stop and target are touched in the same bar. The execution uses the
existing Nautilus fill model and $2.25 commission per side. In addition, it deducts
an explicit **four-tick round-trip stress charge ($50 per ES contract)** from
each closed trade's modeled net PnL. This avoids calling Nautilus's probabilistic
one-tick fill model an exact configurable two-tick fill. Base modeled PnL,
commission and additional stress remain separate in each trade. Consequently
execution is more conservative than the fixed-cost label screen and its stop,
target, overlapping-signal suppression and risk path differ from a simple
five-minute return. Label metrics and actual closed-trade metrics are separate.
No tick-fill, latency, queue-position or live execution accuracy is claimed.

## Consistency and optional custom profiles

For closed trades, use after-commission and after-stress PnL:

* `topThreePositiveShare` = sum of the largest up-to-three positive trade PnLs /
  sum of **all positive trade PnLs**. It is null when there are no winners.
* `netExcludingTopThree` = total net PnL minus those largest winners.
* Flag share above 50% **or** remaining net at/below zero. Both checks are
  configurable; the default asks whether results survive removing three wins.
* Require 30 closed trades, at least 3 winners, 5 sessions and at least 2 positive
  chronological blocks. Sessions, including zero-trade sessions, are split into
  three contiguous blocks, never sorted by performance. Few winners, zero
  denominators and tiny samples are explicitly insufficient rather than passing.

These are configurable **research heuristics**, not significance tests or prop
rules. With two validation sessions and at most 20 trades, this initial technical
sample cannot pass the default consistency sample gate, regardless of profit.
The UI shows concentration, net without the winners, daily/block stability,
configuration, experiment link and rejection/blocked reasons. Changing thresholds
reassesses the same recorded trades without fitting or rerunning a backtest.

No firm was specified, so prop fit is unassessed. An optional user-supplied custom
profile can evaluate descriptive profit target, daily net loss, closed-equity
maximum drawdown and minimum active sessions. Intraday equity, trailing limits,
payout restrictions and actual firm qualification remain unassessed. Example
schema (illustrative limits, not a recommendation):

```json
{"label":"Custom research limits","dailyLossLimitUsd":1000,
 "maxClosedEquityDrawdownUsd":3000,"profitTargetUsd":5000,"minActiveSessions":5}
```

## Reproduction and evidence

Use the checked-out implementation, Python 3.12 environment with backend
requirements, existing AWS profile, and existing authorized local database URL.
Set `DATA_DIR` to a separate runtime directory and `MARKET_DATA_DIR` to its raw
archive folder. Copy the existing `data/market/splits.json` and local raw manifest
into that runtime, preserving their original files. Never point tests at the
application database.

```bash
# From backend/, with DATA_DIR, MARKET_DATA_DIR and DATABASE_URL exported.
python scripts/bounded_mbo.py --contract ESU6 --manifest /absolute/path/manifest.json \
  --days 2026-07-13 2026-07-14 2026-07-15 2026-07-16 2026-07-17 \
         2026-07-20 2026-07-21 2026-07-22 2026-07-23 2026-07-24 \
         2026-07-27 2026-07-28
python scripts/research_workflow.py discover --input /path/discovery-plan.json \
  --state /path/immutable-state.json --authorization-reason 'Actual bounded user grant' \
  --authorization-expires 'ACTUAL-UNEXPIRED-UTC-DEADLINE'
# Interpret the exported training-only review context before creating review.json.
python scripts/research_workflow.py evaluate --input /path/review.json \
  --state /path/immutable-state.json --authorization-reason 'Same actual bounded user grant' \
  --authorization-expires 'SAME-UTC-DEADLINE'
```

The actual run's selected source request, frozen discovery plan, supplied review
and state are retained locally for exact replay. The runner refuses state-file
replacement and a consumed review/evaluation cannot silently rerun. A reproduction
against the same database reads existing results; a separate disposable research
database is needed to rerun computations without altering the exposure ledger.

`DATA_DIR/preparation/attempts.jsonl` records every preparation attempt, source
hash/IDs, row counts, quality and failures. `research_runs/<run>.json` is a
write-once digest-verified report; its linked Nautilus artifact is saved under
`research_runs/artifacts/`. Reports include the full hypothesis, every label
metric actually exposed, stress-adjusted trades, consistency, frozen scope and
artifact hashes. `/api/ml/features/runs` and the ML discovery page expose these
results. Raw/feature/trade artifacts and credentials remain local and ignored.

To view the isolated branch locally, run its API on loopback with the same
runtime paths/database, and its frontend with `VITE_API_PROXY` targeting that
loopback API. Do not bind Postgres or the application to public interfaces.

## Previous-run archival

The user authorized clearing previous-run strategies. The exact 14 existing
strategy IDs were backed up locally, then soft archived with prior status,
time and reason. Their 7 linked backtests, lineage and audit records were
preserved. The normal list hides only explicit archive markers; use
`GET /api/strategies?include_archived=true` or direct ID lookup for history.
`research_ml.archive.restore(backup_path)` restores status/spec only if no edits
were made after archival. It refuses to overwrite later changes. No broad database
wipe, market-data deletion or irreversible purge occurred.

## Actual run ledger

The source preparation and real discovery/execution outcomes are appended here
when complete, including failures. QA failures are distinguished from economic
rejections. No synthetic reviewer fixture is used as real-run evidence.
