# trading-platform

One backtest engine (NautilusTrader) fed by Strategy Spec v2 documents you
write in the editor or ask Stratos Research to create, and one way to judge the result: in-sample /
walk-forward / out-of-sample validation with Monte Carlo, regime tagging and
a per-strategy risk profile. The review chart (tick replay + order-flow
layers with the engine's trades drawn on it) is the human's window onto the
same data and feature engine.

Stratos Research is a small, tool-limited agent: it searches a pinned local
research library, creates schema-valid strategies only when asked, and can
queue the same deterministic validation the UI uses. There is no teaching mode.

The full design and phase plan is in [PLATFORM-SPEC.md](PLATFORM-SPEC.md);
implementation notes per phase are under [docs/](docs/); open choices are
logged in [DECISIONS.md](DECISIONS.md).

## Status

| Phase | Name | State |
|---|---|---|
| 0 | Housekeeping and runtime | done — [docs/00-housekeeping.md](docs/00-housekeeping.md) |
| 1 | Data layer v2 (tiered Parquet, instruments) | done — [docs/01-data.md](docs/01-data.md) |
| 2 | Backtester v2 and validation | done — [docs/02-backtester.md](docs/02-backtester.md) |
| 3 | Strategy DSL v2 and primitive registry | done — [docs/03-dsl.md](docs/03-dsl.md) |
| 4 | Agent v2: runs, knowledge graph, research | **removed** (2026-08-31) — see [DECISIONS.md](DECISIONS.md) |
| New | Stratos Research agent | done — clean replacement built from scratch; [docs/04-agent.md](docs/04-agent.md) |
| 5 | Chart, tick replay, order-flow visuals | done — [docs/05-chart.md](docs/05-chart.md) |
| 6 | Teaching mode | **removed** (2026-08-31) — see [DECISIONS.md](DECISIONS.md) |
| 7 | Desk view and packaging | done — [docs/07-desk.md](docs/07-desk.md) |

## Navigating the app

A persistent sidebar (collapses to an icon rail on chart pages):

| Page | Route | What you do there |
|---|---|---|
| Desk | `/` | Stat tiles + candidates, what is testing, data coverage, lineage trees |
| Strategies | `/strategies` | The list with validation status and last run; **+ New strategy** → a draft from the template, opened in the spec editor |
| Strategy | `/strategies/:id` | Overview (rules, risk, validation), Spec editor, Lineage (compare two nodes), Runs; toolbar: status, Package, Validate, Run backtest |
| Backtests | `/backtests` | Every run in one table, run a new one, open any on its review chart (`/review/:id`) — tick replay with order-flow layers and the engine's trades drawn on it, revealed as the replay clock passes them, plus the analysis dock |
| Research | `/agent` | Ask research questions, create valid Strategy Specs, and queue validation with source citations |
| Settings | `/settings` | Data on disk, instruments |

## What works today

- **Approval-gated ML discovery API**: opt-in ES joint MBO ridge regression and
  multivariate regime clustering over spread, depth imbalance, classified flow and
  order activity; training-only scaling, chronological internal validation/test,
  immutable raw-source/feature provenance, and frozen Nautilus candidate handoff into the existing proposal
  approval workflow. No autonomous experiments or final-holdout access.
  See [docs/08-ml-research.md](docs/08-ml-research.md) for explicit inputs, API steps,
  separate account assessments, and the compatible Bronze/Silver/Gold design.

- **Stratos Research** at `/agent`: OpenAI Responses API with a pinned local
  Machine Learning for Trading library (1,550+ documents), auditable citations,
  persistent conversations and typed tools for Strategy Spec creation and
  in-sample/walk-forward validation. It has no shell, web browser, arbitrary
  code execution or OOS tool ([docs/04-agent.md](docs/04-agent.md)).

- **Desk** at `/`: candidates with verdict / OOS PF / Monte Carlo DD95 /
  regime notes and a **Forward test →** transition, what is testing now
  (backtests), data coverage (per-root sessions, IS/OOS split, raw files
  archived, replay cache) and every lineage tree with its
  champion starred. **Package**
  exports a strategy as a zip (spec, risk, validation report, lineage,
  `nautilus_config.json`) that `POST /api/strategies/import`
  re-creates; the strategy page compares any two lineage nodes
  ([docs/07-desk.md](docs/07-desk.md)).
- **Review chart with tick replay** at `/review/:backtestId`: pick a session
  (date, ET start time, "RTH open" / "Latest"), replay over `/ws/replay` at
  0.25–100× with step-print / step-bar / jump-to-ET-time, an ET clock and a
  "book approximate" badge above 25×. Layers: DOM ladder (tick-exact L3 book
  from MBO), liquidity heatmap (now-edge = replay clock), footprint (bid×ask,
  imbalances, stacked outlines, POC), delta bubbles, volume profile
  (POC/VAH/VAL), live CVD, Time & Sales. Thresholds under Settings → Layers.
  The first replay of a day with the book on decodes it into
  `data/replay_cache` (~100 s for ES, LRU-capped by `REPLAY_CACHE_MAX_GB`);
  trades-only replay works on every ingested day without the cache.

- **Candlestick chart** (lightweight-charts v5) with volume, intervals 1m–1D,
  drawing tools, CVD, DOM snapshot and a Bookmap-style liquidity heatmap.
- **Bar replay** of a backtest: play/pause/step/speed, engine trades revealed
  as the clock passes them.
- **Strategies** as Strategy Spec v2 (expression tree over a registry of 61
  primitives, `/strategies/:id` editor with plain-English rendering and schema
  validation — [docs/03-dsl.md](docs/03-dsl.md)) and **NautilusTrader backtests**
  in a subprocess with futures PnL, commissions, slippage, ET sessions,
  in-sample / walk-forward / out-of-sample windows, Monte Carlo, deflated
  Sharpe and a risk-profile verdict (see [docs/02-backtester.md](docs/02-backtester.md)).
## Running

```bash
cp backend/.env.example backend/.env
# Add OPENAI_API_KEY to backend/.env for Stratos Research.
python3 -m venv backend/.venv && backend/.venv/bin/pip install -r backend/requirements.txt
cd frontend && npm install && cd ..

make dev        # backend on :8123, frontend on :5173 (proxies /api, /ws)
make test       # pytest (+ vitest when present)
make lint       # oxlint
make up         # docker compose: backend + frontend (memory-capped)
make knowledge-ml4t-bootstrap  # first download/index; safe to re-run for updates
make prepare-mbo-features REQUEST=/path/feature-request.json # reusable contract/day ML features
```

To run the compose stack next to `make dev` (which owns :8123 / :5173), use the
ports override — backend on :8124, frontend on :5174:

```bash
docker compose -f docker-compose.yml -f docker-compose.ports.yml up -d --build
```

Both stacks share `data/platform.db`, `data/market/` and `backtests/` through
bind mounts.

`make dev` runs `uvicorn app:app` from `backend/`; `uvicorn main:app` still
works as an alias.

MBO research reuses prepared PostgreSQL features instead of rereading raw files
for each experiment. Configure `FEATURE_DATABASE_URL` and initialize/import with
[the prepared-feature cutover runbook](docs/11-bounded-real-data-workflow.md#prepared-feature-storage-postgres-cutover)
before preparation. Add `RESTORE=1` to the preparation command to download only
missing selected days from S3, then select `dataSource: "prepared_MBO_features"`
in the experiment plan. Preparation does not train a model. See
[ML research preparation and API workflow](docs/08-ml-research.md#prepare-once-and-reuse-local-feature-rows).

Open **ML discovery** (`/ml`) to discover clusters first, request an AI review,
then approve a frozen hypothesis before evaluation on later data. The AI sees
discovery feature summaries only; it cannot approve or run an experiment.
See the [staged discovery workflow](docs/10-cluster-discovery.md).

### Local PostgreSQL with Docker

```bash
make postgres-up      # generate local credentials, start PostgreSQL, wait for health
make postgres-status
make postgres-shell   # open psql inside the container
make postgres-down    # stop the container; retain its named data volume
```

The optional service uses the official `postgres:17-bookworm` image and binds
only to `127.0.0.1:5432`. Its default database is `trading_features`, user `trading`.
`make postgres-env` creates a random password in the gitignored `.postgres.env`
with owner-only permissions; repeated calls retain the existing credentials.
Set `POSTGRES_PORT` there before startup if 5432 is occupied. PostgreSQL data
persists in the Docker volume `trading-features_postgres_data`.

The API supports PostgreSQL through `postgresql+psycopg://...` in
`backend/.env`'s `DATABASE_URL`; SQLite remains available for standalone dev and
tests. Existing SQLite records are transferred using pgloader into an empty
Alembic-created database, then verified before cutover. PostgreSQL uses native
full-text search and transaction advisory locks for research approvals. See the
[migration and rollback runbook](docs/09-postgresql.md). Raw MBO archives and
large Parquet datasets remain separate from the operational metadata database.

The `POSTGRES_*` initialization values apply when the volume is first created;
editing the env file later does not change credentials inside an existing
database. Normal `postgres-down` preserves data; avoid adding `-v` to Compose
down unless you intend to delete that volume.

## Stack

Python 3.12+ / FastAPI · NautilusTrader · DuckDB + Parquet (market data) ·
PostgreSQL or SQLite via SQLAlchemy + Alembic (metadata + research index) ·
OpenAI Responses API (`gpt-5.6-sol`, configurable) ·
React 19 + Vite, plain JS.

## Data

Tiered layout (PLATFORM-SPEC.md §4.1, details in [docs/01-data.md](docs/01-data.md)):

- `market-data/raw/<ROOT>/<date>.<schema>.dbn.zst` (gitignored): raw Databento
  files, never read at request time; `manifest.json` tracks sha256/outputs/archive.
- `data/market/` (gitignored, small): `trades/` and `bars_1m/` Parquet partitions
  per root and UTC date, `book_checkpoints/`, `liquidity_1s.duckdb` (heatmap),
  `front_month.parquet`, `splits.json` (frozen 70/30 in-/out-of-sample),
  `regimes.parquet`, and the NautilusTrader `catalog/`.

```bash
# drop Databento files anywhere under market-data/, then:
make ingest     # organizes raw/, decodes once per file, writes the tiers (runs with the backend up)
make catalog    # NautilusTrader ParquetDataCatalog (incremental)
make warm ROOT_SYMBOL=ES DATE=2026-06-12   # pre-decode a day for tick replay (the UI does this on first use)
```

Instruments, session (09:30–16:00 America/New_York, DST-safe) and the cost
model live in `backend/config/instruments.yaml` (`GET /api/instruments`).

## Tests

`backend/tests/synth.py` generates a seeded synthetic MBO session (random-walk
price, Poisson trades with aggressor side, a consistent L3 book) so every
engine test runs without real data — including the L3 book (checked against a
brute-force reference) and the replay session (fake clock). CI
(`.github/workflows/ci.yml`) runs pytest, oxlint, vitest and the Vite build on
every push.

## Bounded real-data workflow

See [the bounded research runbook](docs/11-bounded-real-data-workflow.md) for the real-data run ledger, scoped assistant review, frozen chronological partitions, Nautilus execution convention, consistency diagnostics, recoverable archival and reproduction commands.

### Versioned causal MBO features

Preparation and research accept `featureVersion: "mbo_receive_minute_v2"` for
18 ordered inputs: the original six plus the twelve observations below. The
default remains `mbo_receive_minute_v1`; existing six-input frozen models,
prepared identities and snapshots are retained. V2 requires raw MBO extraction;
importing six-column prepared data does not create unavailable v2 observations.
The discovery dataset selector shows each version separately.

All windows use completed F_LAST events at receive time in `[minute open, close)`.
Signals become available only at minute close. VWAP resets at 09:30 New York
each session; snapshots and premarket events do not contribute. `T` is a trade
counted once, `F` is not another execution. No options/GEX inputs are available.
Let `B/S` be known-side aggressive buy/sell contracts in the minute, `q` its
last-minus-first trade price in ticks, and `Ra/Rb` the matched replenishment
contracts defined below. Feature order after the original six is:

| Input | Definition and units |
|---|---|
| `session_vwap` | Cumulative sum(trade price × contracts) / sum(contracts), price points; includes unknown-side trades and trades in omitted-quality minutes |
| `distance_to_session_vwap_ticks` | (Minute last trade − session VWAP) / tick size, signed ticks |
| `session_vwap_slope_ticks` | Change since the preceding minute boundary / tick size, ticks per minute; zero at the first nonempty session boundary |
| `signed_aggressive_flow_rate` | `(B − S) / 60`, contracts per second |
| `aggressive_flow_imbalance` | `(B − S) / (B + S)`, dimensionless; zero when no known-side trades |
| `buy_absorption_proxy` | `B / (1 + max(q, 0))`, contracts per tick-regularized favorable buy response |
| `sell_absorption_proxy` | `S / (1 + max(-q, 0))`, contracts per tick-regularized favorable sell response |
| `ask_replenishment_rate_proxy` | `Ra / 60`, observed matched ask-add contracts per second |
| `bid_replenishment_rate_proxy` | `Rb / 60`, observed matched bid-add contracts per second |
| `buy_absorption_replenishment_proxy` | `Ra / (1 + max(q, 0))`, matched ask contracts per tick-regularized response |
| `sell_absorption_replenishment_proxy` | `Rb / (1 + max(-q, 0))`, matched bid contracts per tick-regularized response |
| `iceberg_repeat_replenishment_proxy` | Matched contracts from the second and later displayed adds in a same-price trade episode / `(B + S)`; zero denominator gives zero |

Replenishment matches an ordinary displayed `A` against a known-side `T` at the
same price and opposite resting side from an **earlier complete event with a
strictly earlier receive timestamp**, at most one second earlier. Same-event
adds, snapshots and `M` size modifications are excluded. A side/price episode
expires after a gap exceeding one second since its last trade; matching credit
uses FIFO unconsumed trade credits that each expire after one second, even
when later trades keep the same-price episode active. Repeated adds cannot
inflate credited contracts beyond eligible trades. Credits/counts reset
every minute and every session. No future price response, execution outcomes,
subsequent minute or later snapshot enters these inputs.

These are **proxies, not evidence of hidden/iceberg liquidity or true passive
absorption**. The feed does not establish reserve size, order ownership or why
an order replenished. Independent displayed orders can produce the same pattern;
modifications, same-event replenishment and boundary-crossing episodes are
intentionally missed. High ratios can reflect mechanically flat prices, and
unknown aggressors reduce observed signed flow. Absorption uses the minute's
first/last trade response, not a causal intervention or future outcome.

No-trade, stale/missing/crossed-book minutes retain the existing omission policy;
no features or outcomes are imputed. Session VWAP still incorporates valid
completed trades from omitted minutes, and slope compares adjacent clock-minute
boundaries. Unknown-only eligible minutes retain VWAP with zero known-flow
ratios/proxies. Non-finite or incomplete inputs fail closed. Training-only
population standardization and frozen inference enforce the chosen version's
exact order; v1/v2 vectors cannot be substituted. Predictive value and real-data
behavior remain unverified until data/runtime access and separate experiment
approval. See [storage and batch limits](docs/11-bounded-real-data-workflow.md#prepared-feature-storage-postgres-cutover).

Feed semantics follow Databento's [trade-side definition](https://databento.com/docs/schemas-and-data-formats/trades)
and [MBO order tracking/F_LAST rules](https://databento.com/docs/examples/order-book/order-tracking):
trade side identifies the aggressor, while T/F do not independently mutate the
book. CME documents that native iceberg refresh can retain an order ID while
synthetic refresh can receive a new one in its [MBO FAQ](https://www.cmegroup.com/articles/faqs/market-by-order-mbo.html).
This implementation does **not** classify those mechanisms or track native
same-ID refresh; its repeated-add observation is a deliberately narrower proxy.

V2 intentionally includes correlated inputs: signed flow is algebraically
derivable from the existing buy/sell rates, and VWAP/absorption ratios share
trade observations. Their joint use changes ridge penalties and cluster geometry;
there is no automatic feature selection or claim that eighteen inputs improve
prediction. That comparison requires a separately approved real-data experiment.
