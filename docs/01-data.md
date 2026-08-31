# Phase 1 — Data layer v2

Status: **done** (2026-08-30). Spec: PLATFORM-SPEC.md §4.1, §4.2, §5 Phase 1.

## Layout

```
market-data/                              gitignored, raw source of truth
  raw/<ROOT>/<YYYY-MM-DD>.<schema>.dbn.zst    mbo | trades | ohlcv-1m (mbp-10 kept, not ingested)
  manifest.json                               per file: root, date, schema, size, sha256, outputs, archived, archive_uri

data/market/                              gitignored, derived, small, always local
  trades/root=ES/date=2026-04-01/part.parquet     ts_event, ts_recv (int64 ns), symbol, price, size, side(A/B/N), sequence
  bars_1m/root=ES/date=.../part.parquet          symbol, ts (int64 ns, bucket start), o,h,l,c, volume, delta, buy_vol, sell_vol, trades
  book_checkpoints/root=ES/date=.../part.parquet ts (s), symbol, side, price, size, n_orders — top 50/side every 60 s
  liquidity_1s.duckdb                            heatmap read model (liquidity_store schema, multi-root)
  front_month.parquet                            root, date, symbol, volume, roll
  splits.json                                    per root: inSample[], outOfSample[] (70/30 by session count, frozen)
  regimes.parquet                                root, date, symbol, trend, er, vol, range_pct, day_type, bars
  catalog/                                       NautilusTrader ParquetDataCatalog: FuturesContract, TradeTick, 1m Bar

data/replay_cache/                        Phase 5 (LRU, REPLAY_CACHE_MAX_GB)
```

Rules that hold from here on:

- `market-data/raw` is never read at request time. Only `scripts/ingest.py`
  (and, from Phase 5, the replay-cache warmer) decode `.dbn.zst`.
- DuckDB reads Parquet directly with hive partitioning; every request is
  bounded by `date` (partition pruning) and `ts`. There is no big database
  file and no exclusive-lock restart dance: ingest runs while the backend is up.
  (`liquidity_1s.duckdb` is the one DuckDB file; ingest holds its write lock
  only for the seconds a day's commit takes.)
- Timestamps in Parquet are int64 UNIX nanoseconds (DECISIONS.md #13).
- Sessions are `America/New_York`, DST-safe (`engine/session.py`).

## Sizes (Apr–Jul 2026, ES, 105 sessions)

| Tier | Size |
|---|---|
| raw MBO (`market-data/raw/ES`) | 23 GB |
| `trades/` | 394 MB |
| `bars_1m/` | 2.9 MB |
| `liquidity_1s.duckdb` | 1.4 GB (carried over) |
| `catalog/` | 961M (TradeTick + 1m Bar, 105 days) |
| legacy `mbo-data/mbo.duckdb` (deleted) | 17 GB |

## Modules

| Module | Role |
|---|---|
| `config/instruments.yaml`, `config/instruments.py` | roots (tick size/value, multiplier, commission, margin, continuous symbol, outright regex), RTH session, cost model; `/api/instruments` |
| `market/paths.py` | every tier's location; `DATA_DIR`, `MARKET_DATA_DIR`, `REPLAY_CACHE_MAX_GB` |
| `market/ingest.py` | one streamed decode pass per file → trades + bars partitions, book checkpoints + liquidity (front month), manifest; `finalize()` → front month, splits, regimes |
| `market/book_materializer.py` | the L3 state machine (port of the legacy liquidity materializer) that emits the heatmap change stream and 60-s checkpoints from one pass |
| `market/catalog.py` | `FuturesContract` from YAML + CME quarterly rules (venue `SIM`), `TradeTick`/`Bar` per day into the Nautilus catalog, incremental via `catalog_manifest.json` |
| `engine/session.py` | ET session math (`rth_bounds_ns`, `session_date`, `flatten_ns`, …) |
| `engine/regimes.py` | session tags: trend/range (efficiency ratio ≥ 0.3), vol tercile vs trailing 60 sessions, day type (open_drive / trend_day / rotational) |
| `data_store.py` | DuckDB-over-Parquet reads: bars, CVD, trades, footprint, volume profile (POC/VAH/VAL), session levels, DOM snapshot (checkpoints), heatmap (liquidity store), coverage |
| `liquidity_store.py` | unchanged schema, now at `data/market/liquidity_1s.duckdb` |

## Scripts

```bash
cd backend
.venv/bin/python scripts/ingest.py --all            # organize raw/, ingest new files, finalize
.venv/bin/python scripts/ingest.py --all --schema trades   # Databento `trades` files → same trades/ + bars_1m/
.venv/bin/python scripts/build_catalog.py           # Nautilus catalog (incremental)
.venv/bin/python scripts/verify_ingest.py           # compare bars with the legacy mbo.duckdb (before deleting it)
.venv/bin/python scripts/archive.py [--free-local]  # raw → S3 (GLACIER_IR), manifest-driven
.venv/bin/python scripts/restore.py ES 2026-06-12   # one day back for replay
```

`scripts/ingest.py --all` is idempotent (manifest sha256/size/mtime + outputs
present → skip). Options: `--roots`, `--schema`, `--no-book`, `--rebuild`,
`--rebuild-liquidity`, `--recompute-splits`, `--finalize-only`, `--limit`, `--dry-run`.

## API added

```
GET /api/instruments
GET /api/data/coverage
GET /api/trades?symbol&start&end&min_size&limit
GET /api/footprint?symbol&tf&start&end            # per bar: levels [{price, bid, ask}], volume, delta, poc
GET /api/volume-profile?symbol&start&end&bins     # bins [{price, volume, buy, sell}], poc, vah, val
GET /api/session-levels?symbol&date               # OR, IB, session OHLC/VWAP, prior day, profile
```

Existing routes (`/api/symbols`, `/api/ohlcv`, `/api/range`, `/api/cvd`,
`/api/dom`, `/api/dom-heatmap`) keep their shapes; `/api/dom` now serves the
last 60-second checkpoint (DECISIONS.md #14, #15).

## Verification

- `scripts/verify_ingest.py`: 148 symbol-days compared against the legacy `mbo.duckdb` bars_1m, 0 mismatches (bar counts, volume, delta and OHLC sums identical).
- `/api/ohlcv?symbol=ES1!&interval=1h`: same bars as the legacy store (verified per symbol-day above); 1h series of the whole range answers in ~20 ms.
- `/api/footprint` 5-minute window on the live store: ~20 ms.
- Ingest of all 105 Apr–Jul MBO days (1.41 B events, 23 GB compressed): 45 min wall clock, peak RSS ≈ 1.0 GB on the 8.6 GB machine, ≈26 s (max 65 s) per day.
- Tests: `tests/test_instruments.py`, `test_session_tz.py` (January vs July, DST edges),
  `test_regimes.py`, `test_ingest.py` (partitions == generator, checkpoints == brute-force book,
  liquidity rows, splits frozen/forced, raw relocation), `test_data_store.py`, `test_catalog.py`.

## Deferred

- NQ files: the pipeline is root-agnostic (roots come from symbols via the
  YAML regexes); `NQ1!` appears in `/api/symbols` as soon as NQ files are
  dropped into `market-data/` and `make ingest` runs. Not verified on real NQ data yet.
- Book checkpoints for the 105 legacy-liquidity days (DECISIONS.md #14).
- Replay cache + `OrderBookDelta` in the catalog: Phase 5.
