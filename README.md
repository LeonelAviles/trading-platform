# trading-platform

One backtest engine (NautilusTrader) fed by Strategy Spec v2 documents you
write in the editor, and one way to judge the result: in-sample /
walk-forward / out-of-sample validation with Monte Carlo, regime tagging and
a per-strategy risk profile. The review chart (tick replay + order-flow
layers with the engine's trades drawn on it) is the human's window onto the
same data and feature engine.

There is no LLM in the loop and no teaching mode: everything the platform
does is deterministic and driven from the UI.

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
| Settings | `/settings` | Data on disk, instruments |

## What works today

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
- **Strategies** as Strategy Spec v2 (expression tree over a registry of 55
  primitives, `/strategies/:id` editor with plain-English rendering and schema
  validation — [docs/03-dsl.md](docs/03-dsl.md)) and **NautilusTrader backtests**
  in a subprocess with futures PnL, commissions, slippage, ET sessions,
  in-sample / walk-forward / out-of-sample windows, Monte Carlo, deflated
  Sharpe and a risk-profile verdict (see [docs/02-backtester.md](docs/02-backtester.md)).
## Running

```bash
cp backend/.env.example backend/.env
python3 -m venv backend/.venv && backend/.venv/bin/pip install -r backend/requirements.txt
cd frontend && npm install && cd ..

make dev        # backend on :8123, frontend on :5173 (proxies /api, /ws)
make test       # pytest (+ vitest when present)
make lint       # oxlint
make up         # docker compose: backend + frontend (memory-capped)
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

## Stack

Python 3.12+ / FastAPI · NautilusTrader · DuckDB + Parquet (market data) ·
SQLite via SQLAlchemy + Alembic (`data/platform.db`, platform metadata) ·
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
