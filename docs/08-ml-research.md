# Approval-gated multivariate MBO research

For **discover first → AI review → user-approved hypothesis → later-data
evaluation**, use the app's **ML discovery** page at `/ml`. See the
[staged discovery workflow](10-cluster-discovery.md). The one-shot API described
below remains available for previously specified hypotheses.

This is an opt-in **API workflow**, not a running research program. It freezes one
ES experiment, requires exact user approval before fitting, records explicit stop/no-edge
outcomes, and exports passing candidates into the existing separately approved Nautilus
workflow. No schedules, final-holdout access, paper/live execution or profitability claims
are added. Existing chat, strategies, charts, ingestion and storage remain available.
All implementation checks use **synthetic data**, including synthetic compressed DBN files.
The user's reported eight months of ES MBO has not been inspected or quality-verified here.

## Methods and features

The current MBO path jointly consumes the same six ordered features in both methods:

| Order | Feature | Definition at each one-minute close |
|---|---|---|
| 1 | `spread_ticks` | Best ask minus best bid, divided by configured ES tick size |
| 2 | `depth_imbalance` | `(bid size - ask size)/(bid size + ask size)` across up to the explicitly chosen N best levels on each side |
| 3 | `aggressive_buy_rate` | Contracts in T records with buyer aggressor B, divided by 60 seconds |
| 4 | `aggressive_sell_rate` | Contracts in T records with seller aggressor A, divided by 60 seconds |
| 5 | `add_rate` | Non-snapshot A record count divided by 60 seconds |
| 6 | `cancel_reduction_rate` | Size removed by C records divided by 60 seconds, including execution-related reductions |

`mbo_ridge_v1` fits one joint ridge regression to training forward outcomes. The user
specifies the regularization and predicted-net-tick signal threshold before fitting.
There is no search over thresholds or per-column winner selection.

`mbo_kmeans_v1` fits multivariate k-means to training features only. It never receives
outcomes or label timestamps. Deterministic farthest-first initialization starts from the
lexicographically first distinct standardized vector; ties use lexicographic order.
Lloyd iterations stop at unchanged assignments. Final cluster IDs follow lexicographic
standardized-center order, **not profitability or a scalar volume ranking**. The user
chooses active IDs and their interpretation before fitting; no automatic re-labeling,
restart, k search or return-based cluster selection occurs.

Both standardize each column with its training population mean and standard deviation.
A constant training column uses scale 1. The frozen feature order, scaler, coefficients or
centers, threshold/active IDs, feature version and model version are retained. Validation
and test data never change preprocessing or parameters. An undersized/empty cluster,
insufficient distinct vectors or exhausted convergence budget is a technical `blocked`
result. Failed economic criteria return `no_edge` under that exact plan.

Earlier `volume_threshold_v1` and `volume_regimes_v1` remain supported for compatibility
with existing proposals and tests. They are explicitly volume-only legacy baselines,
not the multivariate MBO path. Their existing request schemas remain visible in OpenAPI.

## Source, timing and quality contract

The MBO path supports raw extraction and reusable prepared features. The default
`dataSource: "raw_DBN_MBO"` reads local
`market-data/raw/ES/YYYY-MM-DD.mbo.dbn.zst` files directly. With
`dataSource: "prepared_MBO_features"`, it reads verified contract/day partitions
from PostgreSQL through `FEATURE_DATABASE_URL`; raw files need not remain local.
Retain original prepared files during migration; see the
[cutover and recovery runbook](11-bounded-real-data-workflow.md#prepared-feature-storage-postgres-cutover).
There is no fallback to OHLCV zero-filled flow, the identity-stripped replay cache,
end-of-second aggregated checkpoints, or the heatmap feed that omits small levels.
One fixed outright ES contract and daily publisher/instrument/channel IDs are approved
upfront. Raw DBN symbology must match those IDs and the chosen contract. No full-day-volume
front-month selection is used. Automatic contract rolls are deferred; select a suitable
fixed-contract date range explicitly.

The extractor starts from a complete vendor snapshot and maintains the full resting
order map using `replay.book.L3Book`. Snapshot adds initialize state without counting as
activity. T and F records do not alter the book; C performs the size reduction once.
Unknown trade sides contribute to OHLCV/unknown-volume audit, never to sell flow.
The approved maximum unknown-volume fraction applies to every minute with trades.

Rows cover `[minute open, minute close)` in **receive time**. Only complete F_LAST events
are visible. If an event spans the boundary, all its records become available with its
completion in the later minute. Late exchange events are never backfilled into earlier
features; their receive-time availability controls inclusion. Both price bars and feature
rows come from this same stream, and are available at the minute close. RTH is 09:30–16:00
America/New_York. Snapshot synthetic receive timestamps are allowed only for initialization;
ordinary bad receive timestamps, bad-book flags, aggregated/top-of-book inputs, regressing
receive times/sequences, duplicate/unknown orders and inconsistent reductions fail closed.
Intraday resets/snapshots require a separately reviewed dataset and currently fail closed.

The user supplies maximum book age (since the last resting mutation received),
event/receive clock difference and unknown-trade
fraction. Missing, stale, crossed or empty books and no-trade minutes are omitted and
counted; gaps are purged when constructing targets, never filled with zeros. N is a maximum
level count: a sparse side uses its actually available levels. Sequence checks establish
monotonicity per selected instrument; they **do not prove full channel packet continuity**.
Vendor gap flags are checked, but this is not an independent exchange-feed audit.

Semantics follow the vendor's [MBO schema](https://databento.com/docs/schemas-and-data-formats/mbo),
[record flags/actions](https://databento.com/docs/standards-and-conventions/common-fields-enums-types),
and [snapshot format](https://databento.com/docs/standards-and-conventions/mbo-snapshot).

## Workflow and approval boundaries

1. Create a thread with `POST /api/agent/threads`.
2. Submit a complete plan to `POST /api/agent/threads/{threadId}/ml-experiments`.
   This reads/validates raw records and freezes derived inputs; **no model fits**.
3. Review the immutable document/digest, including source file SHA-256 hashes, daily
   IDs, quality counters, contract, feature contract, code/config/dependency hashes,
   snapshot identity, partition counts and every model/target/evaluation choice.
4. `POST .../{id}/decision` with `digest`, `action: "approve"` and a nonempty reason
   records exact user approval. `reject_stop` stops the thread.
5. `POST .../{id}/run` reserves one fitting attempt durably before fitting. Retries,
   concurrent requests and stopped/running experiments cannot fit again. Technical
   failures are `blocked`; failed evaluation is `no_edge`. No automatic retries occur.
6. `GET .../ml-experiments` retains decisions, diagnostics, frozen model, metrics and
   terminal results. A revision requires a new thread and new approval; global attempt
   history remains visible, and external/manual trial counts remain unknown.
7. For `candidate`, `POST .../{id}/candidate-proposal` accepts `{strategy, testPlan}`.
   Supply all session, exit, sizing, cost and risk choices. Export inserts the frozen
   trigger and provenance, creating an **unapproved** ordinary research proposal.
8. The existing proposal approve/queue actions are required for Nautilus validation.
   MBO exports require bars mode, primary 1min, ES1!, the same direction, no entry
   sequence, no filters and no context timeframes. Their actual contract is the pinned
   outright displayed in the dataset and result. No catalog/front-month substitution occurs.

Nautilus reads the hash-verified snapshot's receive-time price bars and feature rows,
using the same bounded numeric `FrozenModel.predict` implementation as discovery.
The `frozen_mbo_signal` primitive refuses missing or mistimed rows. It accepts validated
JSON numeric parameters; no pickle or executable model payload is loaded. Code/config,
model, result and snapshot drift block execution. The spec alone is **not self-contained**:
its experiment record and snapshot must accompany it. There is no paper/live adapter.

For MBO proposals, `testPlan.dataPolicy` is `immutable_MBO_receive_time_snapshot`.
`is`, `wf1`, and `wf2` denote that experiment's training, validation and internal test
session ranges. They are repeated execution checks on already-used data, **not new OOS
evidence or rolling model refits**. Existing non-MBO proposals keep their original windows.
The earlier one-shot plans are entered through the API/OpenAPI `/docs`. Staged
clustering has a dedicated `/ml` form and separate discovery/review endpoints.

## Prepare once and reuse local feature rows

Preparation is a separate data operation. It does not fit a model, inspect forward
returns, approve an experiment, or queue a backtest. Both `mbo_kmeans_v1` and
`mbo_ridge_v1` can consume the same prepared rows.

1. Choose the contract and source days you want to retain, plus the extraction
   and quality settings. Restore only those raw days from S3 if needed.
2. Save a JSON request with `contract`, `sources`, `depthLevels`,
   `featureWindowSeconds`, `maxBookAgeMs`, `maxEventLagMs` and
   `maxUnknownTradeFraction`. Source identifiers must come from your actual DBN
   data. There are no inferred source IDs or default quality tolerances.
3. From the repository root run:

   ```bash
   make prepare-mbo-features REQUEST=/absolute/path/feature-request.json
   # Also restore missing selected raw days through the archive manifest:
   make prepare-mbo-features REQUEST=/absolute/path/feature-request.json RESTORE=1
   ```

   Equivalent CLI:

   ```bash
   cd backend
   ../.venv/bin/python scripts/prepare_mbo_features.py --request /path/feature-request.json --restore
   ```

   The file can contain one request or an array of requests for different
   contracts. Existing verified partitions are reused and do not trigger S3
   downloads. Each day is published atomically; if a later day fails, completed
   days remain reusable when the command is run again. `--restore` downloads
   selected missing raw files; it does not change the bucket or delete raw files.
4. List available partitions using
   `GET /api/ml/features?contract=ESU6&start=2026-07-13&end=2026-07-15`.
   The response includes extraction settings, original raw hashes, quality
   counters, row counts and partition identities. Inventory reads metadata;
   experiment loading also verifies the row bytes.
5. Submit an ordinary ML plan with `dataSource: "prepared_MBO_features"`.
   Supply the same contract, daily source IDs and extraction settings used in
   preparation, along with your training/validation/test and model choices.
   No raw files are read or downloaded on this path, and there is no silent raw
   fallback. Missing/mismatched partitions produce an error naming the day.
6. Review the returned experiment document and approve its exact digest before
   running. The experiment freezes its own selected rows and records all source
   and partition hashes. Subsequent changes to or removal of the reusable store
   cannot change that snapshot or its frozen Nautilus replay.

Preparation is also available through `POST /api/ml/features/prepare` in `/docs`.
This endpoint processes local raw files only; S3 restoration is explicit through
the CLI. Requests may contain one day, while experiment plans still require
three or more source days and valid chronological partitions.

Example preparation request using **synthetic test identifiers** (replace the
contract, dates, IDs and quality limits with reviewed values for your data):

```json
{
  "contract": "ESU6",
  "sources": [
    {"day": "2026-07-13", "instrumentId": 10, "publisherId": 1, "channelId": 0},
    {"day": "2026-07-14", "instrumentId": 10, "publisherId": 1, "channelId": 0},
    {"day": "2026-07-15", "instrumentId": 10, "publisherId": 1, "channelId": 0}
  ],
  "depthLevels": 2,
  "featureWindowSeconds": 60,
  "maxBookAgeMs": 120000,
  "maxEventLagMs": 120000,
  "maxUnknownTradeFraction": 0.0
}
```

Each immutable contract/day partition lives in PostgreSQL
`prepared_mbo_batches` and `prepared_mbo_rows`. Configure the separate
`FEATURE_DATABASE_URL` native PostgreSQL DSN and initialize/import explicitly
using `scripts/import_mbo_features.py`; dry-run validation is the default.
Original `data/market/mbo_features/.../{rows.parquet,metadata.json}` files are
read-only import sources and remain retained. The rows contain bigint
minute-open nanoseconds, OHLCV and the six causal features, with no outcomes or
model parameters. Reads verify ordered rows, counts and checksums. The identity includes the daily
source mapping, contract, tick size, extraction/quality settings, feature
version, extractor source hashes and dependency versions. Changing any of those
requires preparation under a new identity. Existing partitions are not overwritten;
preparation rejects raw files that differ from an existing partition's source hash.

The store can contain many contracts and dates, including overlapping days.
Each experiment still chooses **one fixed contract** and stays inside approved
platform in-sample sessions. Automatic rolls and joint multi-contract fitting
remain unsupported. Preparation can be done in batches; the 50,000-row and 12 MB
experiment snapshot limits still apply to each selected experiment.

## Explicit plan inputs

All common fields are required; there is no executable default request:

| Fields | Meaning |
|---|---|
| `hypothesis`, `method` | User hypothesis and `mbo_ridge_v1` or `mbo_kmeans_v1` |
| `symbol`, `interval`, `direction` | ES1!, 1min, and explicitly long or short |
| `start`, `trainEnd`, `validationEnd`, `testEnd` | Ordered disjoint inclusive ET session partitions, entirely within frozen platform IS |
| `target`, `horizonBars` | `next_open_to_horizon_close_ticks`, explicit 1–60 minute bars; no implicit 30-second conversion |
| `roundTripCostTicks` | Nonnegative total estimated round-trip cost |
| `minEvaluationSignals`, `minMeanNetTicks`, `minImprovementOverBaselineTicks` | Explicit evaluation/stop criteria |
| `multipleTestingPolicy` | `descriptive_only_no_significance_claim` |
| `contract`, `contractSelection` | Outright such as ESU6; `fixed_contract_before_experiment` |
| `dataSource` | Optional for compatibility: `raw_DBN_MBO` (default) or `prepared_MBO_features` |
| `sources` | Sorted entries `{day, instrumentId, publisherId, channelId}` for every frozen session in the date range |
| `depthLevels`, `featureWindowSeconds` | Explicit 1–50 best price levels; 60-second windows |
| `maxBookAgeMs`, `maxEventLagMs`, `maxUnknownTradeFraction` | Explicit freshness/clock/unknown-aggressor quality tolerances |

Ridge additionally requires positive `ridgeAlpha`, `predictionThresholdTicks` and positive
`minTrainSignals`. Clustering instead requires `clusterCount` (2–8), `maxIterations`
(1–100), positive `minClusterSamples`, unique `activeClusters` IDs and an explicit
`interpretation`. Unknown fields/methods, missing choices and non-finite values fail closed.

The 366-day, 50,000-bar, 12 MB snapshot and 50-million-record-per-source-day ceilings
plus 100,000-record event and 500,000-resting-order memory ceilings are software limits,
not trading recommendations. Raw input is streamed and
only per-minute outputs plus the current full book/event are held; model matrices are
bounded. Snapshot capture is synchronous and may be expensive on real MBO. It has not
been benchmarked on the user's dataset; eight months of full RTH bars will exceed the
50,000-row ceiling. A larger/rolling/asynchronous research design needs explicit review.

## Chronology and reproducibility

Partitions precede label creation. At bar i close the feature vector is known. The
label is `(close[i+h] - open[i+1]) / tickSize`, multiplied by the chosen side, minus
round-trip cost. Label availability is close i+h. Targets must remain within the same
partition and ET RTH session, spanning h contiguous minute bars. Missing rows, session
endings and partition endings are purged. Neither labels nor their availability enter
unsupervised fitting.

Validation must meet the approved signal count/mean outcome and strictly improve upon
the better of always-signaling and no-signaling on the same per-observation denominator,
plus the approved improvement requirement. Only passing validation opens internal test
metrics. The model never refits after validation, and failed tests never trigger another
search. Overlapping forward labels are not independent observations or trade PnL.

The platform's final holdout stays unavailable. Nested walk-forward model selection,
purged CV, final-holdout release and statistical significance remain deferred. Different
execution exits/sizing/costs may change results: frozen signal parity does not imply PnL
parity between forward labels and a strategy backtest.

`data/research_cache/ml/{experimentId}.json` holds hash-pinned ordered price/feature rows.
Raw file hashes and quality/source metadata live in the approved experiment document.
Code fingerprints include the extractor, replay book, model/inference, engine and source
reader; dependency versions are pinned. Source file changes after capture do not change
the approved snapshot. Missing/tampered snapshots fail closed. Back up metadata and
snapshots together; no automatic cleanup or retention policy is added. A process crash
can leave a run `running`; it never auto-resumes.

## Separate account assessments

`POST .../{id}/accounts` accepts `jobId`, `propFirm` and `personal`. Each policy is
explicit or `null`; a missing policy returns `unassessed`, never a pass. The job must
belong to an approved strategy proposal exported from this discovery and thread.
The existing evidence verifier enforces completed ES IS/WF data scope, timestamp
consistency and artifact bounds. The response pins the trade artifact SHA-256.

Each supplied policy requires `name`, `startingEquity`, `minNetPnlUsd`,
`maxClosedTradeDrawdownPct`, `maxDailyNetLossUsd` and `minTrades`. Starting equity must
match the actual Nautilus run; different capital/sizing needs a newly approved run.
The policies are evaluated independently. Metrics are computed from net trade PnL,
chronological exits, and ET session groups, not from discovery labels. Missing trade,
fee, timestamp or session evidence remains `unassessed`.

`criteria_met` means only those specified observed metrics met the policy. It is
**not prop-firm eligibility**: unrealized/intraday loss, trailing equity floors,
consistency, payout and firm-specific rules are explicitly unassessed. Neither
policy is selected or populated for the user. Full rule semantics and the personal
account objective need user input before a broader evaluator is implemented.

## Current data pipeline and compatible medallion design

Current main was inspected at `d1cda50587a0b2d80ed7884ac2e2d6720e79395c` (merged PRs
#1 and #2). There were no `AGENTS.md` or local `.agents/skills/SKILL.md` files in the
checkout or workspace. Existing ingestion/storage is retained; PostgreSQL and S3
migration are not prerequisites. The architecture maps naturally to medallion layers:

| Layer | Existing implementation | Increment / next contract |
|---|---|---|
| Bronze | Databento DBN raw files, manifest hashes; optional existing S3 archive scripts | Preserve immutable vendor bytes and license/provenance metadata |
| Silver | Instrument config, front-month mapping, minute bars/trades/book Parquet, DuckDB queries | MBO research derives complete-event receive-time features directly from identified raw DBN; legacy tiers remain available |
| Gold | Frozen splits, Nautilus catalog, research evidence and strategy artifacts | Add content-hashed per-experiment feature-input snapshots and deterministic learned triggers |
| Metadata | SQLite/Alembic threads, proposals, decisions, strategies, backtests, evidence | Add `ml_experiments`; keep all completed, stopped and failed discovery history |

```mermaid
erDiagram
    agent_threads ||--o| ml_experiments : contains
    agent_threads ||--o{ research_proposals : contains
    ml_experiments ||--|| derived_snapshot : pins_sha256
    ml_experiments ||--o{ research_proposals : referenced_in_spec_meta
    research_proposals ||--o| research_decisions : requires
    research_proposals ||--o{ research_evidence : records
    backtests ||--o| research_evidence : identifies
    strategies ||--o{ backtests : validates
```

Only `ml_experiments` is a new physical table. It has a foreign key/unique constraint
on `thread_id`, immutable `document_json` + `digest`, immutable `decision_json`,
`status`, `result_json`, and creation time. The local derived snapshot is a hash-pinned
file; the experiment-to-proposal reference lives in `spec.meta.mlDiscovery`. No
speculative options tables are created.

Future GEX/Silver data needs stable underlying/option instrument identifiers,
contract expiration/strike/put-call, multiplier, venue, vendor observation time,
**available-at time**, revision and license/source hash. Gold features must record
calculation version and join to ES using available-at time, never later revised or
end-of-day values. Snapshot lineage should link raw inputs → normalized records →
feature calculations → approved experiment. These are proposed extension contracts;
there are no option/GEX feeds or inferred exposures in this increment.

Meaningful real ML work remains gated by licensed ES coverage and quality. This
checkout supplied no real market dataset. Existing raw quality, contract-roll effects,
bar corrections and point-in-time availability were not audited. The new checks
catch structural defects and missing sessions, not all market-data defects. Assess
these before interpreting the new feature models. Keep SQLite while local serialized approvals
suffice; consider PostgreSQL/object storage only with an explicit multi-user,
concurrency, retention and deployment requirement.

## Decisions still needed

- Which hypotheses, side, fixed contract, source IDs, depth/freshness policies, model parameters, prediction targets and horizons
  deserve explicit tests; these reference plugins do not settle them.
- Exact dates and minimum evidence, cost assumptions, search/stop budget, uncertainty
  policy and how/when a final holdout may be released.
- Firm/version-specific drawdown and loss semantics versus personal-account goals.
- Licensed data quality/coverage, point-in-time joins, retention and future options data.
- Dedicated discovery UI and any future scheduled learning or execution promotion.

The separate brainstorming conversation was represented by the parent-provided
excerpts, including the medallion/GEX goal; no access to its full private transcript
is claimed. None of its future aspirations authorize autonomous research or trading.
