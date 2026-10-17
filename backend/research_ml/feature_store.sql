-- Additive dedicated feature schema v1. Requires PostgreSQL 16+.
CREATE TABLE IF NOT EXISTS prepared_mbo_batches (
    partition_id text PRIMARY KEY CHECK (partition_id ~ '^[0-9a-f]{64}$'),
    contract text NOT NULL,
    source_day date NOT NULL,
    metadata jsonb NOT NULL CHECK (octet_length(metadata::text) <= 128000),
    rows_sha256 text NOT NULL CHECK (rows_sha256 ~ '^[0-9a-f]{64}$'),
    CHECK ((metadata->'document'->>'rows')::integer BETWEEN 1 AND 390)
);
CREATE INDEX IF NOT EXISTS prepared_mbo_batches_day ON prepared_mbo_batches(contract,source_day);
CREATE TABLE IF NOT EXISTS prepared_mbo_rows (
    partition_id text NOT NULL REFERENCES prepared_mbo_batches(partition_id),
    timestamp_ns bigint NOT NULL,
    open double precision NOT NULL,
    high double precision NOT NULL,
    low double precision NOT NULL,
    close double precision NOT NULL,
    volume double precision NOT NULL,
    spread_ticks double precision NOT NULL,
    depth_imbalance double precision NOT NULL,
    aggressive_buy_rate double precision NOT NULL,
    aggressive_sell_rate double precision NOT NULL,
    add_rate double precision NOT NULL,
    cancel_reduction_rate double precision NOT NULL,
    PRIMARY KEY (partition_id,timestamp_ns)
);
