"""Data access layer over the DuckDB store (backend/duckdb_store.py — see
scripts/ingest_dbn_to_duckdb.py for ingestion).

Charts are served from the materialised `bars_1m` read model, not from raw
ticks. Deriving bars from 1.35B tick rows on every request cost 16-100s on
this host to produce ~120k bars; rolling them up from ~146k pre-aggregated
minute rows is ~0.1s, and every interval the UI offers is a whole multiple
of a minute, so the result is identical. The tick path is kept as a
fallback for a store ingested before bars_1m existed — run
scripts/build_bars_1m.py to backfill one.

Moved off Postgres/TimescaleDB after real, measured evidence on the same
file: 18+ minutes and still unfinished there vs. 22.2 seconds end-to-end in
DuckDB + polars + Parquet, at ~3x better compression.

The continuous contract (ES1!) is a *derived view* over those ticks, not
stored data — the front month is chosen per-day by traded volume in SQL
(see _FRONT_MONTH_CTE), so it updates automatically as more days are
ingested and the roll moves. The older mbo-data/*.ohlcv-1m.csv path is
still supported as a fallback for any symbol DuckDB doesn't cover.
"""

import threading
from pathlib import Path

import pandas as pd
from fastapi import HTTPException

import duckdb_store

DATA_DIR = Path(__file__).resolve().parent.parent / "mbo-data"

# Databento fixed-point prices in the *.ohlcv-1m.csv files are still raw
# integers scaled by 1e-9 (unlike the DuckDB tick store, which holds already-
# scaled real floats — see scripts/ingest_dbn_to_duckdb.py).
PRICE_SCALE = 1e-9

# Synthetic ticker for the continuous front-month series (TradingView's
# convention). Not a real contract and never present in the raw feed — it's
# assembled on the fly from whichever outright contract led volume each day.
CONTINUOUS_SYMBOL = "ES1!"

_INTERVAL_SQL = {
    "1min": "INTERVAL 1 MINUTE", "5min": "INTERVAL 5 MINUTE", "15min": "INTERVAL 15 MINUTE",
    "30min": "INTERVAL 30 MINUTE", "1h": "INTERVAL 1 HOUR", "4h": "INTERVAL 4 HOUR",
    "1D": "INTERVAL 1 DAY",
}

_cache: dict[str, tuple[tuple, pd.DataFrame]] = {}

# Whole-store bars/CVD per (symbol, interval), memoised for the life of the
# process. Aggregating every tick costs ~16s no matter how few bars come
# back, and nothing about the answer can change while we're running, so it
# is worth holding onto.
#
# Unlike _cache above (which re-checks CSV mtimes) these need no
# invalidation key: the DuckDB handle is opened read-only once and pinned to
# that snapshot, and DuckDB takes an *exclusive* file lock for writes — so
# scripts/ingest_dbn_to_duckdb.py cannot run at all until this process
# exits. New data therefore always implies a restart.
_full_bars: dict[tuple[str, str], pd.DataFrame] = {}
_full_cvd: dict[tuple[str, str], pd.Series] = {}

_duckdb_con = None
_thread_local = threading.local()
_front_month_cache: list[tuple[str, object, object]] | None = None
_bars_1m_available: bool | None = None


def _front_month_ranges() -> list[tuple[str, object, object]]:
    """Contiguous (symbol, start_ts, end_ts) runs of whichever outright
    contract led traded volume each day — the raw (unadjusted) continuous
    series is those runs spliced end to end.

    Deliberately returns *ranges* rather than a per-day list: a day-by-day
    join needs `ts_event::DATE` on every row, which casts all ~250M ticks
    and can't use the (symbol, ts_event) index — measured at multiple hours.
    Because the front month rolls forward and stays put, contiguous days
    collapse into a couple of ranges, so the filter becomes a plain indexed
    range scan instead.
    """
    global _front_month_cache
    if _front_month_cache is not None:
        return _front_month_cache

    # Off bars_1m this is a groupby over ~146k rows instead of a ts_event::DATE
    # cast across every tick, and gives the same answer: a minute bar's volume
    # is exactly the traded size within it, and a minute never straddles a day.
    if _bars_1m_ready():
        df = _duck().execute("""
            SELECT day, symbol FROM (
                SELECT ts::DATE AS day, symbol,
                       row_number() OVER (PARTITION BY ts::DATE
                                          ORDER BY sum(volume) DESC, symbol) AS rn
                FROM bars_1m GROUP BY 1, 2
            ) WHERE rn = 1 ORDER BY day
        """).df()
    else:
        df = _duck().execute("""
            SELECT day, symbol FROM (
                SELECT ts_event::DATE AS day, symbol,
                       row_number() OVER (PARTITION BY ts_event::DATE
                                          ORDER BY sum(size) DESC, symbol) AS rn
                FROM mbo_events WHERE action = 'T' GROUP BY 1, 2
            ) WHERE rn = 1 ORDER BY day
        """).df()

    ranges: list[list] = []
    for day, symbol in zip(df["day"], df["symbol"]):
        end = pd.Timestamp(day) + pd.Timedelta(days=1)
        if ranges and ranges[-1][0] == symbol:
            ranges[-1][2] = end       # extend the current run
        else:
            ranges.append([symbol, pd.Timestamp(day), end])
    _front_month_cache = [tuple(r) for r in ranges]
    return _front_month_cache


def _symbol_filter(symbol: str, ts_col: str = "ts_event") -> tuple[str, list]:
    """SQL WHERE fragment + params selecting one symbol, or — for the
    synthetic continuous ticker — the front-month ranges above.

    `ts_col` names the time column of the table being filtered: raw ticks
    call it ts_event, the materialised bars_1m read model calls it ts.
    """
    if symbol != CONTINUOUS_SYMBOL:
        return "symbol = ?", [symbol]
    ranges = _front_month_ranges()
    if not ranges:
        return "FALSE", []
    clause = " OR ".join([f"(symbol = ? AND {ts_col} >= ? AND {ts_col} < ?)"] * len(ranges))
    return f"({clause})", [v for r in ranges for v in r]


def _bars_1m_ready() -> bool:
    """Whether the materialised 1-minute read model has been built.

    Everything the charts ask for is served from bars_1m when it exists and
    re-derived from raw ticks when it doesn't, so a store ingested before
    the table existed still works — just slowly, until
    scripts/build_bars_1m.py has run. Checked once per process: the table
    cannot appear underneath us, because building it needs a write
    connection and DuckDB will not grant one while this process holds the
    file open.
    """
    global _bars_1m_available
    if _bars_1m_available is None:
        try:
            _bars_1m_available = _duck().execute("SELECT 1 FROM bars_1m LIMIT 1").fetchone() is not None
        except Exception:          # table absent entirely (pre-bars_1m store)
            _bars_1m_available = False
    return _bars_1m_available


def _duck():
    """A DuckDB handle private to the calling thread.

    FastAPI runs sync endpoints in a threadpool, so several requests hit
    this concurrently — and a single DuckDB connection can't service
    concurrent queries (the second one fails with "No open result set" as
    it clobbers the first one's result). .cursor() hands out an independent
    handle onto the same database, which is DuckDB's supported way to use
    one database from multiple threads.
    """
    con = getattr(_thread_local, "con", None)
    if con is None:
        global _duckdb_con
        if _duckdb_con is None:
            _duckdb_con = duckdb_store.get_connection(read_only=True)
        con = _thread_local.con = _duckdb_con.cursor()
    return con


def _read_all(kind: str, pattern: str, usecols=None) -> pd.DataFrame:
    """Concat every CSV matching `pattern`, cached until the file set changes."""
    files = sorted(DATA_DIR.glob(pattern))
    if not files:
        raise HTTPException(404, f"No {pattern} files found in {DATA_DIR}")
    sig = tuple((f.name, f.stat().st_mtime_ns, f.stat().st_size) for f in files)
    hit = _cache.get(kind)
    if hit and hit[0] == sig:
        return hit[1]
    df = pd.concat([pd.read_csv(f, usecols=usecols) for f in files], ignore_index=True)
    _cache[kind] = (sig, df)
    return df


def load_ohlcv_bars(symbol: str) -> pd.DataFrame:
    """Databento's pre-aggregated 1-minute OHLCV bars for one symbol.

    Primary source for 1min-and-up intervals. Returns a copy, so callers may
    mutate freely without corrupting the cache.
    """
    df = _read_all("ohlcv", "*.ohlcv-1m.csv")
    df = df[df["symbol"] == symbol]
    if df.empty:
        raise HTTPException(404, f"No OHLCV bars found for symbol '{symbol}'")
    df = df.copy()
    for col in ("open", "high", "low", "close"):
        df[col] = df[col] * PRICE_SCALE
    df["ts_event"] = pd.to_datetime(df["ts_event"], unit="ns", utc=True)
    return df.set_index("ts_event").sort_index()[["open", "high", "low", "close", "volume"]]


def load_trades(symbol: str) -> pd.DataFrame:
    """Executed trades (action == 'T') from the raw MBO event stream.

    Keeps `side`, which on a trade record is the *aggressor's* side, per
    Databento's convention — 'A' (Ask) is a sell aggressor, 'B' (Bid) a buy
    aggressor, 'N' no identifiable initiator. Used for CVD classification
    below. On an Add/Cancel/Modify record the same column means the side of
    the resting order instead; see order_book_snapshot().
    """
    where, params = _symbol_filter(symbol)
    df = _duck().execute(
        f"SELECT ts_event, action, side, price, size, symbol FROM mbo_events "
        f"WHERE action = 'T' AND {where} ORDER BY ts_event",
        params,
    ).df()
    if df.empty:
        raise HTTPException(404, f"No trades found for symbol '{symbol}'")
    df["ts_event"] = pd.to_datetime(df["ts_event"], utc=True)
    return df


def resample_trades(df: pd.DataFrame, interval: str) -> pd.DataFrame:
    """OHLCV bars derived from individual trades, at any interval."""
    df = df.set_index("ts_event")
    bars = df["price"].resample(interval).ohlc()
    bars["volume"] = df["size"].resample(interval).sum()
    return bars.dropna(subset=["open"])


def resample_cvd(df: pd.DataFrame, interval: str) -> pd.Series:
    """Cumulative Volume Delta: running total of signed trade size (buyer-
    initiated minus seller-initiated), bucketed at `interval`. Only buckets
    with at least one trade are kept, so it lines up with `resample_trades`.
    """
    df = df.set_index("ts_event")
    signed = df["size"] * df["side"].map({"B": 1.0, "A": -1.0}).fillna(0.0)
    count = df["size"].resample(interval).count()
    delta = signed.resample(interval).sum()
    return delta[count > 0].cumsum()


def _duck_cvd(symbol: str, interval: str) -> pd.Series:
    """CVD bucketed inside DuckDB — same reasoning as _duck_bars: summing
    signed size per bucket at the source returns thousands of rows instead
    of loading every tick into pandas (which exhausted DuckDB's memory
    budget outright on a multi-million-row symbol).

    Matches resample_cvd's semantics exactly: 'B' (buy aggressor) counts
    positive, 'A' (sell aggressor) negative, 'N' zero, and only buckets that
    actually contain trades appear, so it lines up bar-for-bar with the
    OHLCV series.

    That sign convention was corrected on 2026-08-24; it had been inverted,
    counting 'A' as buying. `side` on a trade record is the *aggressor's*
    side, not the resting order's — Databento's documented meaning, and
    independently confirmed against this data (see the sweep-direction note
    in duckdb_store.BARS_1M_SELECT). Any CVD reading or strategy calibrated
    before that date was reading the series upside down.
    """
    bucket = _INTERVAL_SQL.get(interval)
    if bucket is None:
        raise HTTPException(400, f"unsupported interval '{interval}'")
    if _bars_1m_ready():
        where, params = _symbol_filter(symbol, "ts")
        df = _duck().execute(
            f"""
            SELECT time_bucket({bucket}, ts) AS ts_event, sum(delta) AS delta
            FROM bars_1m WHERE {where} GROUP BY 1 ORDER BY 1
            """,
            params,
        ).df()
        if df.empty:
            raise HTTPException(404, f"No trades found for symbol '{symbol}'")
        idx = pd.to_datetime(df["ts_event"], utc=True)
        return pd.Series(df["delta"].cumsum().values, index=idx)

    where, params = _symbol_filter(symbol)
    df = _duck().execute(
        f"""
        SELECT time_bucket({bucket}, ts_event) AS ts_event,
               sum(CASE WHEN side = 'B' THEN size
                        WHEN side = 'A' THEN -size
                        ELSE 0 END) AS delta
        FROM mbo_events
        WHERE action = 'T' AND {where}
        GROUP BY 1 ORDER BY 1
        """,
        params,
    ).df()
    if df.empty:
        raise HTTPException(404, f"No trades found for symbol '{symbol}'")
    idx = pd.to_datetime(df["ts_event"], utc=True)
    return pd.Series(df["delta"].cumsum().values, index=idx)


def get_cvd(symbol: str, interval: str, start: int | None = None, end: int | None = None) -> pd.Series:
    """CVD series at any interval, optionally clipped to [start, end] unix secs.

    [start, end] are deliberately *not* pushed down into SQL the way
    get_bars() pushes them: CVD is a running total, so a window computed in
    isolation would restart the cumulative sum at zero inside that window
    and report the wrong level. The full series is computed once, memoised,
    and then clipped — which keeps the levels honest and makes every
    subsequent window free.
    """
    series = _full_cvd.get((symbol, interval))
    if series is None:
        series = _full_cvd[(symbol, interval)] = _duck_cvd(symbol, interval)
    return _clip(series, start, end)


def load_book_events(symbol: str) -> pd.DataFrame:
    """Every book-affecting MBO event (Add/Cancel/Fill/Trade) for one symbol,
    in time order — raw material for reconstructing the resting order book.
    Cached under its own key: it needs `order_id`, which `load_trades` (a
    different column subset) doesn't load.
    """
    where, params = _symbol_filter(symbol)
    df = _duck().execute(
        f"SELECT ts_event, action, side, price, size, order_id, symbol FROM mbo_events "
        f"WHERE {where} ORDER BY ts_event",
        params,
    ).df()
    if df.empty:
        raise HTTPException(404, f"No book events found for symbol '{symbol}'")
    df["ts_event"] = pd.to_datetime(df["ts_event"], utc=True)
    return df.reset_index(drop=True)


# How far back to replay book events for a DOM snapshot. This is synthetic
# data whose resting orders never naturally expire, so replaying the full
# history produces a nonsensical book spanning the entire multi-month price
# range; a bounded lookback keeps the reconstruction fast and the resulting
# ladder tight around the current price, like a real DOM.
DOM_LOOKBACK_MINUTES = 60


def order_book_snapshot(symbol: str, as_of: int | None = None, depth: int = 12) -> dict:
    """Reconstruct an approximate resting order book from Add/Cancel/Fill
    events in the `DOM_LOOKBACK_MINUTES` window ending at `as_of` (unix
    seconds; defaults to the latest event). Approximate, not exchange-
    certified: orders added before the window are invisible to it, same as
    a DOM that only ever showed you the last hour of activity.
    """
    events = load_book_events(symbol)
    cutoff = pd.Timestamp(as_of, unit="s", tz="UTC") if as_of is not None else events["ts_event"].iloc[-1]
    window = events[
        (events["ts_event"] <= cutoff)
        & (events["ts_event"] > cutoff - pd.Timedelta(minutes=DOM_LOOKBACK_MINUTES))
    ]

    book: dict[int, tuple[str, float, float]] = {}  # order_id -> (side, price, size)
    last_price = None
    last_time = None
    for row in window.itertuples(index=False):
        if row.action == "A":
            book[row.order_id] = (row.side, row.price, row.size)
        elif row.action == "F":
            existing = book.get(row.order_id)
            if existing:
                side, price, size = existing
                remaining = size - row.size
                if remaining > 0:
                    book[row.order_id] = (side, price, remaining)
                else:
                    del book[row.order_id]
        elif row.action == "C":
            book.pop(row.order_id, None)
        elif row.action == "T":
            last_price, last_time = row.price, row.ts_event

    bids: dict[float, float] = {}
    asks: dict[float, float] = {}
    # `side` here is the resting order's own side — not the aggressor sense
    # it carries on trade records (see load_trades).
    for side, price, size in book.values():
        levels = bids if side == "B" else asks
        levels[price] = levels.get(price, 0) + size

    # This synthetic data has no matching-engine consistency guarantee, so
    # resting orders can leave a "crossed" book (best bid >= best ask).
    # Anchor both sides to the last traded price instead — the only price
    # point we know actually happened — which also centers the ladder where
    # a DOM should be.
    if last_price is not None:
        bids = {p: s for p, s in bids.items() if p <= last_price}
        asks = {p: s for p, s in asks.items() if p >= last_price}

    bid_levels = sorted(bids.items(), key=lambda kv: -kv[0])[:depth]
    ask_levels = sorted(asks.items(), key=lambda kv: kv[0])[:depth]

    return {
        "bids": [{"price": round(p, 4), "size": int(s)} for p, s in bid_levels],
        "asks": [{"price": round(p, 4), "size": int(s)} for p, s in ask_levels],
        "lastPrice": round(last_price, 4) if last_price is not None else None,
        "asOf": int(cutoff.timestamp()),
        "windowMinutes": DOM_LOOKBACK_MINUTES,
    }


def _bars_from_1m(symbol: str, bucket: str, interval: str,
                  start: int | None, end: int | None) -> pd.DataFrame:
    """Roll materialised 1-minute bars up to `interval`.

    Exact for every interval the UI offers, all of which are whole multiples
    of a minute: the bucket's open is the first minute's open, its close the
    last minute's close, its high/low the extremes of the minute extremes,
    and its volume the sum. Reading ~146k bar rows instead of 1.35B ticks is
    what makes this milliseconds rather than a full table scan.
    """
    where, params = _symbol_filter(symbol, "ts")
    span = pd.Timedelta(interval)
    # Same whole-bucket widening as the tick path: a bucket must be built
    # from all of its minutes or none of them, or its OHLC is simply wrong.
    if start is not None:
        where += " AND ts >= ?"
        params.append(pd.Timestamp(start, unit="s").floor(span))
    if end is not None:
        where += " AND ts < ?"
        params.append(pd.Timestamp(end, unit="s").floor(span) + span)
    df = _duck().execute(
        f"""
        SELECT time_bucket({bucket}, ts) AS ts_event,
               first(open ORDER BY ts)  AS open,
               max(high)                AS high,
               min(low)                 AS low,
               last(close ORDER BY ts)  AS close,
               sum(volume)              AS volume,
               sum(delta)               AS delta
        FROM bars_1m
        WHERE {where}
        GROUP BY 1 ORDER BY 1
        """,
        params,
    ).df()
    if df.empty:
        raise HTTPException(404, f"No trades found for symbol '{symbol}'")
    df["ts_event"] = pd.to_datetime(df["ts_event"], utc=True)
    return df.set_index("ts_event")


def _duck_bars(symbol: str, interval: str, start: int | None = None,
               end: int | None = None) -> pd.DataFrame:
    """OHLCV bars from the tick table — the fallback path, used only when
    bars_1m has not been built (see _bars_from_1m for the fast path).

    Bucketing happens inside DuckDB rather than by pulling every tick into
    pandas and resampling there: a day of ticks is millions of rows but only
    ~1,380 one-minute bars, so aggregating at the source is the difference
    between shipping millions of rows over the wire and thousands.

    [start, end] (unix seconds) are pushed down into the WHERE clause rather
    than applied to the result, which is the difference between reading the
    whole table and reading a slice of it. Measured on this store: the
    unfiltered aggregate takes 15.8s, one day 0.64s, a week 0.35s, a month
    1.7s. There is deliberately no index on (symbol, ts_event) — see
    duckdb_store.SCHEMA_SQL — but DuckDB's per-rowgroup zone maps still let
    a range predicate skip almost every rowgroup, which is where the win
    comes from.
    """
    bucket = _INTERVAL_SQL.get(interval)
    if bucket is None:
        raise HTTPException(400, f"unsupported interval '{interval}'")
    if _bars_1m_ready():
        return _bars_from_1m(symbol, bucket, interval, start, end)
    where, params = _symbol_filter(symbol)
    # Widen the tick predicate to whole buckets before filtering. Cutting the
    # tick stream at an arbitrary instant would hand time_bucket() a bucket
    # it can only half-fill, and a half-filled bucket is a wrong bar, not a
    # clipped one — its open/high/low/close would come from part of the
    # period. The caller clips the *bars* back to [start, end] afterwards.
    span = pd.Timedelta(interval)
    if start is not None:
        where += " AND ts_event >= ?"
        # ts_event is a naive UTC TIMESTAMP, so keep the params naive too.
        params.append(pd.Timestamp(start, unit="s").floor(span))
    if end is not None:
        where += " AND ts_event < ?"
        params.append(pd.Timestamp(end, unit="s").floor(span) + span)
    df = _duck().execute(
        f"""
        SELECT time_bucket({bucket}, ts_event) AS ts_event,
               first(price ORDER BY ts_event) AS open,
               max(price) AS high,
               min(price) AS low,
               last(price ORDER BY ts_event) AS close,
               sum(size) AS volume,
               -- Signed order flow, same convention as duckdb_store's
               -- BARS_1M_SELECT: aggressive buys minus aggressive sells.
               sum(CASE WHEN side = 'B' THEN size
                        WHEN side = 'A' THEN -size
                        ELSE 0 END) AS delta
        FROM mbo_events
        WHERE action = 'T' AND {where}
        GROUP BY 1 ORDER BY 1
        """,
        params,
    ).df()
    if df.empty:
        raise HTTPException(404, f"No trades found for symbol '{symbol}'")
    df["ts_event"] = pd.to_datetime(df["ts_event"], utc=True)
    return df.set_index("ts_event")


def resample_bars(bars: pd.DataFrame, interval: str) -> pd.DataFrame:
    """Roll already-aggregated OHLCV bars up to a coarser interval.

    `delta` (signed order flow) sums like volume does, but only the DuckDB
    paths carry it — the ohlcv-1m.csv fallback has no side information at
    all, so it is aggregated only when present rather than assumed.
    """
    how = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    if "delta" in bars.columns:
        how["delta"] = "sum"
    agg = bars.resample(interval).agg(how)
    return agg.dropna(subset=["open"])


def _clip(frame, start: int | None, end: int | None):
    """Restrict a time-indexed frame/series to [start, end] unix seconds.

    Always hands back a *new* object, even when neither bound is given: the
    input is usually a memoised whole-store result (see _full_bars), and
    returning the cached object itself would let one caller's in-place edit
    corrupt every later request. Boolean-mask indexing already copies, so
    the explicit .copy() only kicks in on the unclipped path.
    """
    clipped = frame
    if start is not None:
        clipped = clipped[clipped.index >= pd.Timestamp(start, unit="s", tz="UTC")]
    if end is not None:
        clipped = clipped[clipped.index <= pd.Timestamp(end, unit="s", tz="UTC")]
    return clipped.copy() if clipped is frame else clipped


def get_bars(symbol: str, interval: str, start: int | None = None, end: int | None = None) -> pd.DataFrame:
    """OHLCV bars at any interval, optionally clipped to [start, end] unix secs.

    Served from the materialised bars_1m read model where it exists,
    re-derived from raw ticks where it doesn't, and finally from the
    pre-aggregated ohlcv-1m.csv for a symbol DuckDB doesn't cover at all.
    """
    cached = _full_bars.get((symbol, interval))

    # A windowed request with nothing memoised: let DuckDB read just that
    # slice instead of aggregating the whole store and throwing most of it
    # away. An empty result here is ambiguous (empty window vs. a symbol
    # DuckDB doesn't cover), so fall through to the full path, which
    # distinguishes the two and populates the memo.
    if cached is None and (start is not None or end is not None):
        try:
            return _clip(_duck_bars(symbol, interval, start, end), start, end)
        except HTTPException:
            pass

    if cached is None:
        try:
            cached = _duck_bars(symbol, interval)
        except HTTPException:
            cached = resample_bars(load_ohlcv_bars(symbol), interval)
        _full_bars[(symbol, interval)] = cached

    return _clip(cached, start, end)


def data_range(symbol: str) -> tuple[int, int]:
    """(first, last) unix seconds of available data for a symbol.

    Kept cheap on purpose — the UI calls this to decide which slice of
    history to request first, so it must not be the thing that takes 16
    seconds. For the continuous ticker the answer is already implied by the
    cached front-month runs, at no query cost at all; the bound is the day
    boundary those runs are built on rather than the exact last tick, which
    is close enough for sizing a window.
    """
    if symbol == CONTINUOUS_SYMBOL:
        ranges = _front_month_ranges()
        if ranges:
            return int(ranges[0][1].timestamp()), int(ranges[-1][2].timestamp())

    if _bars_1m_ready():
        where, params = _symbol_filter(symbol, "ts")
        first, last = _duck().execute(
            f"SELECT min(ts), max(ts) FROM bars_1m WHERE {where}", params
        ).fetchone()
    else:
        where, params = _symbol_filter(symbol)
        first, last = _duck().execute(
            f"SELECT min(ts_event), max(ts_event) FROM mbo_events WHERE action = 'T' AND {where}",
            params,
        ).fetchone()
    if first is not None:
        return int(pd.Timestamp(first, tz="UTC").timestamp()), int(pd.Timestamp(last, tz="UTC").timestamp())

    bars = load_ohlcv_bars(symbol)  # raises 404 if the symbol is unknown here too
    return int(bars.index[0].timestamp()), int(bars.index[-1].timestamp())


def list_symbols() -> list[str]:
    """Only continuous tickers (e.g. "ES1!") are surfaced — individual
    dated contract months and calendar spreads (ESH0, ESM6-ESU6, ...) are
    just the raw material continuous series are built from, not something
    a user picks directly."""
    symbols: set[str] = set()
    # ES1! isn't a stored symbol — it's derived from whatever outright
    # contracts are in the tick store, so it's offered whenever there are
    # any ticks at all to build it from.
    # LIMIT 1, not count(*): we only need "is there anything at all", and
    # counting every row scanned the whole table for ~8s on this store.
    if _bars_1m_ready() or _duck().execute("SELECT 1 FROM mbo_events LIMIT 1").fetchone() is not None:
        symbols.add(CONTINUOUS_SYMBOL)
    try:
        symbols.update(_read_all("ohlcv", "*.ohlcv-1m.csv")["symbol"].unique())
    except HTTPException:
        pass
    return sorted(s for s in symbols if s.endswith("!"))


def bars_to_records(bars: pd.DataFrame) -> list[dict]:
    """Row-wise `.iterrows()` reconstructs a full pandas Series per row —
    with 100k+ bars that per-row object overhead dominates the request. All
    five fields come out as flat numpy arrays instead, then zip() just
    walks them in parallel with no per-row Series/label lookups.

    The index's datetime64 resolution isn't guaranteed — DuckDB round-trips
    give datetime64[us], the old CSV/pandas path gave datetime64[ns] — so
    naively dividing the raw int64 view by 1e9 (assuming nanoseconds) silently
    produces timestamps ~1000x too small whenever the resolution is actually
    microseconds. Cast to a fixed, known resolution first.
    """
    times = bars.index.values.astype("datetime64[ns]").astype("int64") // 1_000_000_000
    o, h, l, c = (bars[col].to_numpy().round(4) for col in ("open", "high", "low", "close"))
    v = bars["volume"].to_numpy(dtype=float)
    # Signed order flow (aggressive buys minus sells), from the MBO ticks.
    # Absent on the ohlcv-1m.csv fallback path, which has no side data — zero
    # there rather than None so arithmetic downstream doesn't have to branch;
    # `hasDelta` is what tells a caller the difference between "flat flow" and
    # "no flow data".
    has_delta = "delta" in bars.columns
    d = bars["delta"].to_numpy(dtype=float) if has_delta else [0.0] * len(v)
    return [
        {"time": int(t), "open": float(o_), "high": float(h_), "low": float(l_), "close": float(c_),
         "volume": float(v_), "delta": float(d_), "hasDelta": has_delta}
        for t, o_, h_, l_, c_, v_, d_ in zip(times, o, h, l, c, v, d)
    ]
