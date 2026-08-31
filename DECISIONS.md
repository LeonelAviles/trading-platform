# Decisions log

Choices made while implementing PLATFORM-SPEC.md that the spec left open.
Each entry is the simplest reversible option consistent with §4 "Locked
decisions". Newest at the bottom.

## Phase 0 — Housekeeping and runtime (2026-08-30)

1. **`main.py` stays as a one-line shim.** The spec splits `main.py` into
   `app.py` + `routers/`. Existing docs, the Hermes plugin's hints and muscle
   memory all say `uvicorn main:app`, so `main.py` re-exports `app` instead of
   disappearing. Remove it whenever nothing references it.
2. **Relative sqlite `DATABASE_URL` paths resolve against the repo root.**
   The spec's `sqlite+pysqlite:///./data/platform.db` would otherwise create
   `backend/data/platform.db` when uvicorn runs from `backend/` (as
   `make dev` does) and `<repo>/data/platform.db` when run from the root.
   `database.resolve_url()` pins it to `<repo>/data/platform.db` in both
   cases; docker-compose passes an absolute path.
3. **Schema is applied with `alembic upgrade head` at startup** (not
   `create_all`), so the one initial migration is the source of truth and
   later schema changes are migrations. `render_as_batch=True` is on because
   SQLite cannot `ALTER` most things in place.
4. **Timestamps are ISO-8601 UTC strings in `String(32)` columns**, not
   SQLite datetimes: they sort correctly, match what `job.json` already
   carries, and avoid SQLAlchemy/SQLite naive-vs-aware surprises.
5. **Placeholder routers for replay/teaching/research are empty modules.**
   Creating them now fixes the include order and file layout; each phase
   fills its own file. `settings` is the exception — the `settings` table
   exists from Phase 0, so `GET/PUT /api/settings` ship now.
6. **`/api/health` added** (not in the spec's route list) so compose health
   checks, `make dev` smoke tests and CI have a data-free endpoint.
7. **Docker cannot be verified on this machine** — no `docker` binary is
   installed. `docker-compose.yml` was validated as YAML and against the
   compose schema by inspection; memory caps are 3 GB backend + 512 MB
   frontend + 1.5 GB neo4j = 5 GB, leaving >2 GB on the 8.6 GB host. First
   `make up` on a machine with Docker should be treated as the real
   acceptance run for that criterion.
8. **Backend image uses Python 3.12** per the spec; the local venv is
   Python 3.14 (what the owner already had). `requirements.txt` is
   unpinned so both resolve; the CI job pins 3.12.
9. **`scripts/migrate_json_to_sqlite.py` ships now but imports strategies
   only once `engine.v1_to_v2` exists** (Phase 3). Backtest `job.json`
   rows import immediately with `strategy_id` left NULL when the strategy
   row is absent, and the legacy id kept in `metrics_json.legacyStrategyId`
   for the Phase 3 backfill.
10. **`SETUP-ENGINE.md` is kept until Phase 2** replaces the worker it
    documents; `docs/02-backtester.md` will supersede it then.
11. **`backend/.pgdata` (the old local Postgres cluster) is left on disk**,
    gitignored. Nothing reads it any more; deleting it is the owner's call.

## Phase 1 — Data layer v2 (2026-08-30)

12. **Partition `date` = the raw file's UTC date.** Databento splits batch
    jobs by UTC day and every RTH session (09:30–16:00 ET = 13:30–21:00 UTC)
    lies inside one UTC date, so partition date == RTH session date; the
    Globex overnight before 00:00 UTC lives in the previous partition, which
    strategies never trade anyway (RTH-only rule, §4.2).
13. **Timestamps in Parquet are int64 UNIX ns** (trades `ts_event`/`ts_recv`,
    bars `ts` = bucket start, checkpoints `ts` in seconds), converted at the
    edge. Databento and NautilusTrader are ns-native and it avoids DuckDB's
    local-timezone TIMESTAMP casts entirely.
14. **The legacy `liquidity.duckdb` is copied, not rebuilt.** The 105
    already-materialised days keep their heatmap rows; the L3 pass (which
    also emits `book_checkpoints`) only runs for days absent from
    `liquidity_files`. Consequence: those 105 days have no ingest-time
    checkpoints — the Phase 5 replay warmer writes checkpoints for any day
    it caches, so replay seeks are unaffected; the static `/api/dom`
    snapshot returns "no book checkpoint" for them until `scripts/ingest.py
    --rebuild-liquidity` is run (≈10 min/day of Python book replay).
15. **`/api/dom` is served from 60-second book checkpoints** (≤60 s stale)
    instead of replaying MBO from the last clear. Tick-exact books belong
    to the replay engine (Phase 5); the dock is fine with a snapshot.
16. **`splits.json` keeps a frozen in-sample set.** Re-running ingest adds
    new sessions to out-of-sample only; `--recompute-splits` re-freezes at
    70/30. This keeps "the agent never saw OOS" true as data grows.
17. **Catalog venue is `SIM`** (`ESM6.SIM`), matching §4.3's backtest venue,
    so instruments and ticks load into the engine without id rewriting.
    Contract multiplier is the real one (ES 50, NQ 20) so Nautilus PnL is in
    dollars. Bars are stamped at bar *close* (Nautilus convention).
18. **Trades with side `N` (no aggressor) map to SELLER in the catalog**
    because the wrangler's aggressor column is boolean; the count is kept in
    `catalog_manifest.json` per day. Bars/CVD treat `N` as zero delta.
19. **MBP-10 files are relocated but not ingested** (schema not in §4.1);
    the manifest records them so archive/restore still covers them.
