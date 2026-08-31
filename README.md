# trading-platform

One backtest engine (NautilusTrader) fed two ways — prompt → agent →
strategy variants, or teaching mode (tick replay + your own trades → inferred
strategy) — and one way to judge the result: in-sample / walk-forward /
out-of-sample validation with Monte Carlo, regime tagging and a per-strategy
risk profile. The chart page is the human's window onto the same data and
feature engine.

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
| 4 | Agent v2: runs, knowledge graph, research | done — [docs/04-agent.md](docs/04-agent.md) |
| 5 | Chart, tick replay, order-flow visuals | done — [docs/05-chart.md](docs/05-chart.md) |
| 6 | Teaching mode | done — [docs/06-teaching.md](docs/06-teaching.md) |
| 7 | Desk view and packaging | done — [docs/07-desk.md](docs/07-desk.md) |

## Navigating the app

A persistent sidebar (collapses to an icon rail on chart pages):

| Page | Route | What you do there |
|---|---|---|
| Desk | `/` | Stat tiles + candidates, what is testing, teaching sessions, research budget, data coverage, lineage trees |
| Strategies | `/strategies` | The list with validation status and last run; **+ New strategy** → *Describe it* (agent), *Teach it on the chart*, or *Write the spec* (template + editor); agent runs below |
| Strategy | `/strategies/:id` | Overview (rules, risk, validation), Spec editor, Lineage (compare two nodes), Runs; toolbar: status, Package, Validate, Run backtest |
| Backtests | `/backtests` | Every run in one table, run a new one, open any on its review chart (`/review/:id`) |
| Chart & replay | `/chart/:symbol` | Tick replay with order-flow layers; `?teaching=1` starts with Teaching on |
| Teaching | `/teaching` | Sessions so far, the three steps, **Start a teaching session**; a session opens at `/teach/:id` |
| Research | `/research` | Queue, Sources (hand it a link / PDF / text), Knowledge search, primitive requests |
| Knowledge graph | `/knowledge` | Interactive concept graph with clusters, central concepts and content gaps |
| Settings | `/settings` | Budget & prices, self-study schedule, trusted domains, data on disk, instruments |

## What works today

- **Desk** at `/`: candidates with verdict / OOS PF / Monte Carlo DD95 /
  regime notes and a **Forward test →** transition, what is testing now
  (agent runs, backtests), teaching sessions, the research budget, data
  coverage (per-root sessions, IS/OOS split, raw files archived, replay
  cache) and every lineage tree with its champion starred. **Package**
  exports a strategy as a zip (spec, risk, validation report, lineage,
  evidence, `nautilus_config.json`) that `POST /api/strategies/import`
  re-creates; the strategy page compares any two lineage nodes
  ([docs/07-desk.md](docs/07-desk.md)).
- **Teaching mode** on the free chart: trade the replay with hotkeys or
  buttons, the agent snapshots every fill (bars, levels, profile, CVD,
  footprints, book, the full primitive feature vector), keeps a hypothesis
  of your rules, asks questions (pausing the replay), detects skipped setups
  three ways, and on End session compiles a Strategy Spec v2 with a
  similarity report (precision/recall, exit similarity) and up to three
  refinements to pick from — `/teach/:sessionId` ([docs/06-teaching.md](docs/06-teaching.md)).
- **Free chart with tick replay** at `/chart/:symbol`: pick a session (date,
  ET start time, "RTH open" / "Latest"), replay over `/ws/replay` at
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
- **Agent runs** (prompt → variants → in-sample/walk-forward → ≤5 single-variable
  experiments → one out-of-sample look → verdict; pause-to-ask; resumable),
  **research worker** (web search → scored sources → knowledge facts; a
  self-study schedule reads the queue on its own within the daily budget,
  you can hand it a URL / PDF / pasted text, and trusted-domain lists pin
  source tiers; `/knowledge` draws the knowledge graph — concepts, sources,
  strategies, clusters, central concepts and content gaps — interactively)
  and the **chat analyst**, all over the Anthropic API with a budget guard
  ([docs/04-agent.md](docs/04-agent.md)); the same tools are exposed to the Hermes plugin.

## Running

```bash
cp backend/.env.example backend/.env    # add ANTHROPIC_API_KEY (optional)
python3 -m venv backend/.venv && backend/.venv/bin/pip install -r backend/requirements.txt
cd frontend && npm install && cd ..

make dev        # backend on :8123, frontend on :5173 (proxies /api, /ws)
make test       # pytest (+ vitest when present)
make lint       # oxlint
make up         # docker compose: backend + frontend + neo4j (memory-capped)
```

To run the compose stack next to `make dev` (which owns :8123 / :5173), use the
ports override — backend on :8124, frontend on :5174, Neo4j Browser on :7474
(user `neo4j`, password `NEO4J_PASSWORD` or `change-me-neo4j`):

```bash
docker compose -f docker-compose.yml -f docker-compose.ports.yml up -d --build
backend/.venv/bin/python backend/scripts/kg_bootstrap.py   # once: Graphiti indices on the Neo4j container
```

Both stacks share `data/platform.db`, `data/market/` and `backtests/` through
bind mounts. With Neo4j reachable the backend switches its knowledge backend
to Graphiti at startup (`GET /api/research/status` → `knowledge.backend`);
Graphiti's entity extraction needs Anthropic credits, so without them facts
still land in the local store and the graph stays empty.

`make dev` runs `uvicorn app:app` from `backend/`; `uvicorn main:app` still
works as an alias.

## Stack

Python 3.12+ / FastAPI · NautilusTrader · DuckDB + Parquet (market data) ·
SQLite via SQLAlchemy + Alembic (`data/platform.db`, platform metadata) ·
Neo4j + Graphiti (knowledge graph, Phase 4) · React 19 + Vite, plain JS.

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
make research TOPICS=2                     # read the next research topics now (the Self-study switch on /research does this on a schedule)
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
