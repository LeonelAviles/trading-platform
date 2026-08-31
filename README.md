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
| 1 | Data layer v2 (tiered Parquet, instruments) | next |
| 2 | Backtester v2 and validation | |
| 3 | Strategy DSL v2 and primitive registry | |
| 4 | Agent v2: runs, knowledge graph, research | |
| 5 | Chart, tick replay, order-flow visuals | |
| 6 | Teaching mode | |
| 7 | Desk view and packaging | |

## What works today

- **Candlestick chart** (lightweight-charts v5) with volume, intervals 1m–1D,
  drawing tools, CVD, DOM snapshot and a Bookmap-style liquidity heatmap.
- **Bar replay** of a backtest: play/pause/step/speed, engine trades revealed
  as the clock passes them.
- **Strategies** (v1 JSON format) and **NautilusTrader backtests** run in a
  subprocess (see [SETUP-ENGINE.md](SETUP-ENGINE.md) until Phase 2 replaces it).
- **Chat analyst** and strategy generation over the Anthropic API
  (`ANTHROPIC_API_KEY`), plus the same tools exposed to the Hermes plugin.

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

`make dev` runs `uvicorn app:app` from `backend/`; `uvicorn main:app` still
works as an alias.

## Stack

Python 3.12+ / FastAPI · NautilusTrader · DuckDB + Parquet (market data) ·
SQLite via SQLAlchemy + Alembic (`data/platform.db`, platform metadata) ·
Neo4j + Graphiti (knowledge graph, Phase 4) · React 19 + Vite, plain JS.

## Data

Until Phase 1 lands, market data is the legacy DuckDB store:

- `market-data/` (gitignored): raw Databento `.dbn.zst` MBO files.
- `mbo-data/` (gitignored): `mbo.duckdb` (full MBO + materialised
  `bars_1m`) and `liquidity.duckdb` (1-second resting-liquidity read model
  for the heatmap), built by `backend/scripts/ingest_dbn_to_duckdb.py`
  (`make ingest`) with the backend stopped.

Phase 1 replaces this with the tiered Parquet layout in PLATFORM-SPEC.md §4.1
(`data/market/`), ingestable while the backend is up.

## Tests

`backend/tests/synth.py` generates a seeded synthetic MBO session (random-walk
price, Poisson trades with aggressor side, a consistent L3 book) so every
engine test runs without real data. CI (`.github/workflows/ci.yml`) runs
pytest, oxlint and the Vite build on every push.
