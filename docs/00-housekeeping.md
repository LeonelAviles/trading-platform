# Phase 0 — Housekeeping and runtime

Status: **done** (2026-08-30). Spec: PLATFORM-SPEC.md §5 Phase 0.

## What changed

| Area | Before | After |
|---|---|---|
| App entry | `backend/main.py` monolith (348 lines) | `backend/app.py` factory + `backend/routers/{market,strategies,backtests,chat,agent,replay,teaching,research,settings}.py`; `main.py` is a shim (`from app import app`) |
| Metadata DB | Postgres ORM (`models.py`, never wired up) + psycopg | SQLite at `data/platform.db`, WAL mode, §4.7 schema (14 tables), Alembic initial migration `770712788d4b`, applied automatically at startup |
| Config | `.env.example` with Postgres | `.env.example` with reasoning/fast models, budgets, Neo4j, replay cache cap, S3 archive, sqlite URL |
| Runtime | manual venv + npm | `Makefile` (`dev`, `up`, `down`, `ingest`, `test`, `lint`), `docker-compose.yml` (backend 3 GB, frontend 512 MB, neo4j 1.5 GB caps), `backend/Dockerfile`, `frontend/Dockerfile` |
| Tests | none | `backend/tests/` with `synth.py` (synthetic MBO session), `conftest.py` fixtures, 19 scaffold tests; `pytest.ini` |
| CI | none | `.github/workflows/ci.yml`: pytest on Python 3.12, oxlint + vite build (+ vitest when present) on Node 22 |
| Git hygiene | `data/`, `*.duckdb`, `research_cache/` not ignored | ignored; `.DS_Store` untracked |

New routes: `GET /api/health`, `GET/PUT /api/settings`. Every pre-existing
route is unchanged (`tests/test_app_routes.py::test_legacy_routes_registered`
asserts the full list).

## Layout after Phase 0

```
backend/
  app.py            FastAPI factory, lifespan runs database.init_db()
  main.py           shim
  database.py       engine/session, DATABASE_URL resolution, init_db (alembic)
  models.py         §4.7 tables
  alembic/          env.py (sqlite, batch mode) + versions/770712788d4b_*
  routers/          one APIRouter per area
  scripts/migrate_json_to_sqlite.py
  tests/            synth.py, conftest.py, test_synth.py, test_db_schema.py, test_app_routes.py
docker-compose.yml, Makefile, backend/Dockerfile, frontend/Dockerfile
.github/workflows/ci.yml
DECISIONS.md, docs/
```

## The synthetic fixture (`backend/tests/synth.py`)

`generate_mbo(SynthConfig)` builds one RTH session for N outright symbols:
a tick-quantised random walk, Poisson trades with an aggressor side that
consume resting size at the touch, and adds/cancels that keep a ladder of
`depth` levels per side. Output columns match Databento MBO
(`ts_event, ts_recv, symbol, action, side, price, size, order_id, sequence,
flags`). `trades()` and `bars_1m()` derive the two hot tables the Phase 1
ingest will write, and `book_at()` is a brute-force L3 reference for the
Phase 5 book tests. Everything is seeded.

Default session-scoped fixture is a 30-minute session (fast); tests that
need a full day construct their own `SynthConfig(rth_end="16:00")`.

## Running

```bash
make dev        # uvicorn app:app :8123 + vite :5173 (proxies /api and /ws)
make test       # backend pytest (+ frontend vitest when a test script exists)
make lint       # oxlint
make up         # docker compose up --build (needs Docker; see DECISIONS.md #7)
cd backend && .venv/bin/python scripts/migrate_json_to_sqlite.py --dry-run
```

## Acceptance

- `make dev`: both ports respond; `/api/engine/status` through the Vite proxy returns NautilusTrader 1.231.0. ✔
- Existing routes respond against the real DuckDB store (`/api/symbols` → `ES1!`, `/api/ohlcv` 1h → 2003 bars, 3 legacy backtests listed). ✔
- `pytest`: 19 passed. ✔
- `make up`: compose file validated; **not executed on this machine (no Docker binary)** — DECISIONS.md #7.

## Deferred

- Strategies → SQLite import waits for the v1→v2 converter (Phase 3); the
  script already handles it once `engine/v1_to_v2.py` exists.
- Backtest jobs still live in `backtests/<id>/job.json` via `nautilus_runner`;
  the SQLite `backtests` table becomes the job model in Phase 2.
- `SETUP-ENGINE.md` is replaced by `docs/02-backtester.md` in Phase 2.
