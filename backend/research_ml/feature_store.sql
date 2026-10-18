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

-- v2 is additive: old rows/checksums and v1 column ordering are untouched.
CREATE TABLE IF NOT EXISTS prepared_mbo_v2_rows (
    LIKE prepared_mbo_rows INCLUDING ALL,
    session_vwap double precision NOT NULL,
    distance_to_session_vwap_ticks double precision NOT NULL,
    session_vwap_slope_ticks double precision NOT NULL,
    signed_aggressive_flow_rate double precision NOT NULL,
    aggressive_flow_imbalance double precision NOT NULL,
    buy_absorption_proxy double precision NOT NULL,
    sell_absorption_proxy double precision NOT NULL,
    ask_replenishment_rate_proxy double precision NOT NULL,
    bid_replenishment_rate_proxy double precision NOT NULL,
    buy_absorption_replenishment_proxy double precision NOT NULL,
    sell_absorption_replenishment_proxy double precision NOT NULL,
    iceberg_repeat_replenishment_proxy double precision NOT NULL,
    FOREIGN KEY (partition_id) REFERENCES prepared_mbo_batches(partition_id)
);
