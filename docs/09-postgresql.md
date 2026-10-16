# PostgreSQL metadata and pgloader migration

The application supports PostgreSQL 17 with psycopg 3 and SQLite for standalone
development/tests. S3 raw archives, Parquet market/features, DuckDB liquidity and
backtest artifacts stay in their existing locations. Only application metadata
and the knowledge-search index move to PostgreSQL.

## Local service

`make postgres-up` starts the persistent PostgreSQL service on loopback port
5432. Credentials are generated once in the ignored, owner-readable
`.postgres.env`. `make postgres-shell` opens psql in its initial database,
`trading_features`. The migrated app uses a separate database, `trading_platform`,
in that same PostgreSQL service. `make postgres-down` keeps the data volume.

## Transfer using pgloader

1. Install the backend requirements (including `psycopg[binary]`) in `.venv`.
2. Start PostgreSQL and build the migration utility:

   ```bash
   make postgres-up
   make pgloader-image
   ```

3. Create an empty destination using `make postgres-shell`, then:

   ```sql
   CREATE DATABASE trading_platform;
   ```

   Rehearse first with a different database, such as
   `trading_migration_rehearsal`. Do not repurpose a populated destination.
4. For the final cutover, stop API/worker writers and leave them stopped through
   verification and the configuration change. The rehearsal may use the live
   SQLite backup API; the final snapshot must be taken after writers stop.
5. Run:

   ```bash
   make postgres-migrate DB=trading_platform
   ```

The orchestration script uses SQLite's online backup API to produce a consistent
snapshot including committed WAL data. It checks integrity, foreign keys and
the source Alembic revision, refuses a nonempty destination, and runs existing
Alembic migrations against PostgreSQL. The historical FTS creation migration
now selects the appropriate database dialect; its SQLite schema is unchanged.

**pgloader performs every row transfer.** The script invokes its packaged
Debian version in Docker, one table at a time in foreign-key dependency order.
It uses `data only`, `include no drop`, `no truncate`, `create no tables`,
`create no indexes` and `reset no sequences`. Foreign-key checks stay enabled;
SQLite FTS virtual/shadow tables and `alembic_version` are excluded. The latter
is managed by Alembic, and PostgreSQL's native search index is rebuilt from
the authoritative knowledge chunks.

All application table rows are then compared in primary-key order using
SHA-256 over canonicalized values, including JSON and SQL-NULL distinctions.
Any row mismatch fails verification. The snapshot, private per-table pgloader
logs and `verification.json` remain under `data/migrations/<UTC timestamp>/`.
Temporary pgloader password files are owner-only and removed after transfer.
Nothing changes the existing SQLite file or deletes a populated destination.
An interrupted transfer must use a fresh destination for its next attempt.

## Cutover and rollback

After verification, preserve a private copy of `backend/.env`, then set its
`DATABASE_URL` to:

```text
postgresql+psycopg://trading:<password-from-.postgres.env>@127.0.0.1:5432/trading_platform
```

Restart `make backend` (or `make dev`). `/api/health` and the frontend's
`/api/health` proxy should both report PostgreSQL with the password hidden.
Check strategy/history pages and knowledge search before resuming work. Keep
the original SQLite database and snapshot with the migration report.

To revert before new writes, stop the backend, restore the prior `DATABASE_URL`,
and restart. Once PostgreSQL receives new writes, returning to the older SQLite
copy would lose those writes; a reverse transfer or reconciliation is required.
The migration utility does not perform automatic rollback or reverse sync.

For a fully containerized app using the migrated database:

```bash
docker compose --env-file .postgres.env -p trading-features \
  -f docker-compose.yml -f docker-compose.postgres.yml \
  -f docker-compose.postgres-app.yml up -d --build
```

This reuses the PostgreSQL service/volume created by `postgres-up`. Stop the
bare-metal dev servers first because the app ports remain 8123 and 5173.

## Concurrency, search and tests

Approval/queue/stop/model-run reservations use a common
`pg_advisory_xact_lock` key under READ COMMITTED isolation. This preserves the
original cross-process serialization contract: the winner commits before the
next reservation reads recorded state. Commit, rollback and disconnected
transactions release the lock. SQLite retains `BEGIN IMMEDIATE`.

Knowledge search uses a generated weighted `tsvector`, GIN index and native
PostgreSQL ranking, with English stemming. Query terms are sanitized and passed
as bound parameters. Strict AND matching falls back to OR matching as before.
Ranking is native PostgreSQL ranking, not SQLite BM25; passage IDs, source
versions and citations are preserved. Index writes and knowledge records share
one transaction.

The default pytest run uses temporary SQLite databases. Set
`TEST_POSTGRES_URL` to a **test** database URL to exercise the same schema,
knowledge, approvals, ML and HTTP tests against PostgreSQL. Each fixture creates
and drops a unique `test_<uuid>` schema. PostgreSQL CI uses a separate service
and runs this mode; explicit SQLite-only tests remain SQLite. Never use app
credentials with production data as the test target.

References: [pgloader SQLite support](https://pgloader.readthedocs.io/en/latest/ref/sqlite.html),
[Alembic data migration guidance](https://alembic.sqlalchemy.org/en/latest/cookbook.html#data-migrations-general-techniques),
[PostgreSQL advisory locks](https://www.postgresql.org/docs/17/explicit-locking.html#ADVISORY-LOCKS),
[native text-search indexes](https://www.postgresql.org/docs/17/textsearch-tables.html).
