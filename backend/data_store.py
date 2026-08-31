"""Market-data access layer v2 — DuckDB over the tiered Parquet layout
(PLATFORM-SPEC.md §4.1, Phase 1). Replaces the single 11 GB mbo.duckdb.

    data/market/trades/root=<R>/date=<D>/part.parquet        trade prints
    data/market/bars_1m/root=<R>/date=<D>/part.parquet       1-minute bars + delta/buy/sell
    data/market/book_checkpoints/root=<R>/date=<D>/...       60-second top-50 book snapshots
    data/market/front_month.parquet                          (root, date, symbol, roll)
    data/market/liquidity_1s.duckdb                          heatmap read model (liquidity_store)

Every query is bounded by time: hive partition pruning on `date` plus a
`ts` predicate, so request cost scales with the visible window, not the
store. Timestamps in Parquet are int64 UNIX ns; pandas frames returned here
carry a UTC DatetimeIndex, records for the API carry unix seconds.

Continuous symbols (`ES1!`, `NQ1!`, …) are derived views: per session the
outright with the highest traded volume (front_month.parquet). The store can
be ingested while the backend is up — `_version()` picks up new partitions
and invalidates the memoised full-series caches.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import duckdb
import pandas as pd
from fastapi import HTTPException

import liquidity_store
from config.instruments import Instruments, RootSpec, load_instruments
from engine import session as sess
from market.paths import get_paths

NS = 1_000_000_000
_INTERVAL_NS = {
    "1min": 60 * NS, "5min": 300 * NS, "15min": 900 * NS, "30min": 1800 * NS,
    "1h": 3600 * NS, "4h": 4 * 3600 * NS, "1D": 86400 * NS,
}
_MAX_FOOTPRINT_SPAN_S = 2 * 86400
_MAX_TRADES_ROWS = 50_000

_duckdb_con: duckdb.DuckDBPyConnection | None = None
_thread_local = threading.local()
_lock = threading.Lock()
_full_bars: dict[tuple, pd.DataFrame] = {}
_full_cvd: dict[tuple, pd.Series] = {}
_front_cache: dict[tuple, pd.DataFrame] = {}


# ----------------------------------------------------------------------------
# Connection, paths, versioning
# ----------------------------------------------------------------------------

def _duck() -> duckdb.DuckDBPyConnection:
    """Per-thread cursor on one in-process DuckDB (memory-capped, UTC)."""
    con = getattr(_thread_local, "con", None)
    if con is None:
        global _duckdb_con
        with _lock:
            if _duckdb_con is None:
                _duckdb_con = duckdb.connect()
                _duckdb_con.execute("SET TimeZone='UTC'")
                _duckdb_con.execute(f"SET memory_limit='{os.environ.get('DUCKDB_MEMORY_LIMIT', '2GB')}'")
                _duckdb_con.execute("SET threads=4")
        con = _thread_local.con = _duckdb_con.cursor()
    return con


def reset() -> None:
    """Drop connections and caches (tests re-point the data dir)."""
    global _duckdb_con
    with _lock:
        _duckdb_con = None
        _thread_local.__dict__.clear()
        _full_bars.clear()
        _full_cvd.clear()
        _front_cache.clear()


def _ins() -> Instruments:
    return load_instruments()


def _glob(base: Path) -> str:
    return str(base / "root=*" / "date=*" / "*.parquet")


def _has_partitions(base: Path) -> bool:
    return base.exists() and any(base.glob("root=*/date=*/*.parquet"))


def _version() -> tuple:
    p = get_paths()
    fm = p.front_month.stat().st_mtime_ns if p.front_month.exists() else 0
    return (str(p.data_dir), fm)


# ----------------------------------------------------------------------------
# Symbols and front month
# ----------------------------------------------------------------------------

def _front_month() -> pd.DataFrame:
    """front_month.parquet as a frame: root, date(date), symbol, volume, roll."""
    key = _version()
    df = _front_cache.get(key)
    if df is None:
        p = get_paths().front_month
        if not p.exists():
            df = pd.DataFrame(columns=["root", "date", "symbol", "volume", "roll"])
        else:
            df = _duck().execute(f"SELECT root, date, symbol, volume, roll FROM read_parquet('{p}') ORDER BY root, date").df()
            df["date"] = pd.to_datetime(df["date"]).dt.date
        _front_cache.clear()
        _front_cache[key] = df
    return df


def _root_spec(symbol: str) -> RootSpec:
    spec = _ins().root_for_symbol(symbol)
    if spec is None:
        raise HTTPException(404, f"unknown symbol '{symbol}'")
    return spec


def resolve(symbol: str) -> tuple[RootSpec, bool]:
    """(root spec, is_continuous)."""
    spec = _root_spec(symbol)
    return spec, symbol == spec.continuous


def front_symbol_for(symbol: str, d: date) -> str | None:
    """The outright a continuous symbol maps to on `d` (or the symbol itself)."""
    spec, cont = resolve(symbol)
    if not cont:
        return symbol
    fm = _front_month()
    rows = fm[(fm["root"] == spec.root) & (fm["date"] == d)]
    return str(rows["symbol"].iloc[0]) if not rows.empty else None


def front_month_ranges(root: str) -> list[tuple[str, int, int]]:
    """Contiguous (symbol, start_s, end_s) runs of the front month — end exclusive, UTC-day aligned."""
    fm = _front_month()
    fm = fm[fm["root"] == root]
    ranges: list[list] = []
    for d, symbol in zip(fm["date"], fm["symbol"]):
        lo = int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp())
        hi = lo + 86400
        if ranges and ranges[-1][0] == symbol and ranges[-1][2] == lo:
            ranges[-1][2] = hi
        else:
            ranges.append([symbol, lo, hi])
    return [tuple(r) for r in ranges]


def _symbol_where(symbol: str, alias: str = "t") -> tuple[str, list]:
    """WHERE fragment over a hive-partitioned trades/bars scan."""
    spec, cont = resolve(symbol)
    if not cont:
        return f"{alias}.root = ? AND {alias}.symbol = ?", [spec.root, symbol]
    fm = get_paths().front_month
    if not fm.exists():
        return "FALSE", []
    return (
        f"{alias}.root = ? AND EXISTS (SELECT 1 FROM read_parquet('{fm}') fm "
        f"WHERE fm.root = {alias}.root AND fm.date = {alias}.date AND fm.symbol = {alias}.symbol)",
        [spec.root],
    )


def _date_where(start: int | None, end: int | None, alias: str = "t") -> tuple[str, list]:
    """Partition pruning on the UTC date, widened by a day on each side."""
    where, params = "", []
    if start is not None:
        where += f" AND {alias}.date >= ?"
        params.append((datetime.fromtimestamp(start, tz=timezone.utc) - timedelta(days=1)).date())
    if end is not None:
        where += f" AND {alias}.date <= ?"
        params.append((datetime.fromtimestamp(end, tz=timezone.utc) + timedelta(days=1)).date())
    return where, params


def list_symbols() -> list[str]:
    """Continuous tickers for every root that has bars on disk."""
    fm = _front_month()
    roots = set(fm["root"].unique()) if not fm.empty else set()
    ins = _ins()
    return sorted(ins.roots[r].continuous for r in roots if r in ins.roots)


def list_dates(symbol: str) -> list[date]:
    spec, _ = resolve(symbol)
    fm = _front_month()
    return sorted(fm[fm["root"] == spec.root]["date"].tolist())


def data_range(symbol: str) -> tuple[int, int]:
    """(first, last) unix seconds — day-aligned from front_month, cheap."""
    spec, cont = resolve(symbol)
    if cont:
        rng = front_month_ranges(spec.root)
        if rng:
            return rng[0][1], rng[-1][2]
        raise HTTPException(404, f"No data for symbol '{symbol}'")
    if not _has_partitions(get_paths().bars_1m_dir):
        raise HTTPException(404, f"No data for symbol '{symbol}'")
    where, params = _symbol_where(symbol)
    row = _duck().execute(
        f"SELECT min(ts), max(ts) FROM read_parquet('{_glob(get_paths().bars_1m_dir)}', hive_partitioning=true) t WHERE {where}",
        params,
    ).fetchone()
    if row is None or row[0] is None:
        raise HTTPException(404, f"No data for symbol '{symbol}'")
    return int(row[0] // NS), int(row[1] // NS)


# ----------------------------------------------------------------------------
# Bars and CVD
# ----------------------------------------------------------------------------

def _bars_sql(symbol: str, interval: str, start: int | None, end: int | None) -> pd.DataFrame:
    step = _INTERVAL_NS.get(interval)
    if step is None:
        raise HTTPException(400, f"unsupported interval '{interval}'")
    p = get_paths()
    if not _has_partitions(p.bars_1m_dir):
        raise HTTPException(404, f"No data for symbol '{symbol}'")
    where, params = _symbol_where(symbol)
    dwhere, dparams = _date_where(start, end)
    where += dwhere
    params += dparams
    # Whole-bucket widening: a bucket is built from all its minutes or none.
    if start is not None:
        where += " AND t.ts >= ?"
        params.append((start * NS // step) * step)
    if end is not None:
        where += " AND t.ts < ?"
        params.append((end * NS // step) * step + step)
    df = _duck().execute(
        f"""
        SELECT (t.ts // {step}) * {step} AS ts,
               first(t.open ORDER BY t.ts) AS open, max(t.high) AS high, min(t.low) AS low,
               last(t.close ORDER BY t.ts) AS close, sum(t.volume)::DOUBLE AS volume, sum(t.delta)::DOUBLE AS delta,
               sum(t.buy_vol)::DOUBLE AS buy_vol, sum(t.sell_vol)::DOUBLE AS sell_vol, sum(t.trades)::BIGINT AS trades
        FROM read_parquet('{_glob(p.bars_1m_dir)}', hive_partitioning=true) t
        WHERE {where}
        GROUP BY 1 ORDER BY 1
        """,
        params,
    ).df()
    if df.empty:
        raise HTTPException(404, f"No trades found for symbol '{symbol}'")
    idx = pd.to_datetime(df["ts"].astype("int64"), unit="ns", utc=True)
    return df.drop(columns=["ts"]).set_index(idx.rename("ts_event"))


def _clip(frame, start: int | None, end: int | None):
    clipped = frame
    if start is not None:
        clipped = clipped[clipped.index >= pd.Timestamp(start, unit="s", tz="UTC")]
    if end is not None:
        clipped = clipped[clipped.index <= pd.Timestamp(end, unit="s", tz="UTC")]
    return clipped.copy() if clipped is frame else clipped


def get_bars(symbol: str, interval: str, start: int | None = None, end: int | None = None) -> pd.DataFrame:
    """OHLCV(+delta) at any interval, clipped to [start, end] unix seconds."""
    key = (symbol, interval, _version())
    cached = _full_bars.get(key)
    if cached is None and (start is not None or end is not None):
        return _clip(_bars_sql(symbol, interval, start, end), start, end)
    if cached is None:
        cached = _bars_sql(symbol, interval, None, None)
        _full_bars.clear()
        _full_bars[key] = cached
    return _clip(cached, start, end)


def get_cvd(symbol: str, interval: str, start: int | None = None, end: int | None = None) -> pd.Series:
    """Cumulative volume delta — computed over the whole series (a running
    total cannot be windowed) and clipped, like the legacy store."""
    key = (symbol, interval, _version())
    series = _full_cvd.get(key)
    if series is None:
        bars = get_bars(symbol, interval)
        series = bars["delta"].cumsum()
        _full_cvd.clear()
        _full_cvd[key] = series
    return _clip(series, start, end)


def bars_to_records(bars: pd.DataFrame) -> list[dict]:
    times = bars.index.values.astype("datetime64[ns]").astype("int64") // NS
    o, h, l, c = (bars[col].to_numpy().round(4) for col in ("open", "high", "low", "close"))
    v = bars["volume"].to_numpy(dtype=float)
    has_delta = "delta" in bars.columns
    d = bars["delta"].to_numpy(dtype=float) if has_delta else [0.0] * len(v)
    return [
        {"time": int(t), "open": float(o_), "high": float(h_), "low": float(l_), "close": float(c_),
         "volume": float(v_), "delta": float(d_), "hasDelta": has_delta}
        for t, o_, h_, l_, c_, v_, d_ in zip(times, o, h, l, c, v, d)
    ]


# ----------------------------------------------------------------------------
# Trades, footprint, volume profile
# ----------------------------------------------------------------------------

def _trades_scan(symbol: str, start: int, end: int, extra: str = "", extra_params: list | None = None) -> tuple[str, list]:
    p = get_paths()
    if not _has_partitions(p.trades_dir):
        raise HTTPException(404, f"No trades for symbol '{symbol}'")
    where, params = _symbol_where(symbol)
    dwhere, dparams = _date_where(start, end)
    sql = (f"FROM read_parquet('{_glob(p.trades_dir)}', hive_partitioning=true) t "
           f"WHERE {where}{dwhere} AND t.ts_event >= ? AND t.ts_event < ? {extra}")
    return sql, params + dparams + [start * NS, end * NS] + (extra_params or [])


def get_trades(symbol: str, start: int, end: int, min_size: int = 0, limit: int = _MAX_TRADES_ROWS) -> list[dict]:
    """Prints in [start, end) unix seconds, oldest first, capped at `limit`."""
    if end <= start:
        raise HTTPException(400, "end must be after start")
    scan, params = _trades_scan(symbol, start, end, "AND t.size >= ?", [min_size])
    df = _duck().execute(
        f"SELECT t.ts_event AS ts, t.price, t.size, t.side {scan} ORDER BY t.ts_event, t.sequence LIMIT {int(limit)}",
        params,
    ).df()
    return [
        {"ts": int(ts), "price": float(px), "size": int(sz), "side": str(sd)}
        for ts, px, sz, sd in zip(df["ts"], df["price"], df["size"], df["side"])
    ]


def get_footprint(symbol: str, tf: str, start: int, end: int) -> dict:
    """Per bar, per price level: volume traded at the bid (sell aggressor,
    side 'A') and at the ask (buy aggressor, side 'B'), plus bar delta,
    volume and POC. Bars are keyed by their open time in unix seconds."""
    step = _INTERVAL_NS.get(tf)
    if step is None:
        raise HTTPException(400, f"unsupported tf '{tf}'")
    if end <= start:
        raise HTTPException(400, "end must be after start")
    end = min(end, start + _MAX_FOOTPRINT_SPAN_S)
    scan, params = _trades_scan(symbol, (start // (step // NS)) * (step // NS), end)
    df = _duck().execute(
        f"""
        SELECT (t.ts_event // {step}) * {step} AS bar, t.price,
               sum(CASE WHEN t.side = 'A' THEN t.size ELSE 0 END)::BIGINT AS bid,
               sum(CASE WHEN t.side = 'B' THEN t.size ELSE 0 END)::BIGINT AS ask,
               sum(t.size)::BIGINT AS volume
        {scan}
        GROUP BY 1, 2 ORDER BY 1, 2
        """,
        params,
    ).df()
    bars: list[dict] = []
    for bar, g in df.groupby("bar", sort=True):
        levels = [{"price": float(p), "bid": int(b), "ask": int(a)} for p, b, a in zip(g["price"], g["bid"], g["ask"])]
        vol = int(g["volume"].sum())
        poc = float(g.loc[g["volume"].idxmax(), "price"]) if vol else None
        bars.append({
            "time": int(bar // NS), "levels": levels, "volume": vol,
            "delta": int(g["ask"].sum() - g["bid"].sum()), "poc": poc,
        })
    return {"symbol": symbol, "tf": tf, "bars": bars}


def _value_area(bins: list[tuple[float, int]], fraction: float = 0.70) -> tuple[float | None, float | None, float | None]:
    """POC and the value area holding `fraction` of volume, expanding from
    the POC toward the larger neighbour (Market Profile convention)."""
    if not bins:
        return None, None, None
    total = sum(v for _, v in bins)
    if total <= 0:
        return None, None, None
    i = max(range(len(bins)), key=lambda k: bins[k][1])
    poc = bins[i][0]
    lo = hi = i
    acc = bins[i][1]
    while acc < fraction * total and (lo > 0 or hi < len(bins) - 1):
        up = bins[hi + 1][1] if hi < len(bins) - 1 else -1
        dn = bins[lo - 1][1] if lo > 0 else -1
        if up >= dn:
            hi += 1
            acc += up
        else:
            lo -= 1
            acc += dn
    return poc, bins[hi][0], bins[lo][0]


def get_volume_profile(symbol: str, start: int, end: int, tick_bins: int = 1) -> dict:
    """Volume-at-price histogram over [start, end) with POC / VAH / VAL."""
    if end <= start:
        raise HTTPException(400, "end must be after start")
    spec, _ = resolve(symbol)
    width = spec.tick_size * max(1, int(tick_bins))
    scan, params = _trades_scan(symbol, start, end)
    df = _duck().execute(
        f"""
        SELECT round(floor(t.price / {width}) * {width}, 6) AS price,
               sum(t.size)::BIGINT AS volume,
               sum(CASE WHEN t.side = 'B' THEN t.size ELSE 0 END)::BIGINT AS buy,
               sum(CASE WHEN t.side = 'A' THEN t.size ELSE 0 END)::BIGINT AS sell
        {scan}
        GROUP BY 1 ORDER BY 1
        """,
        params,
    ).df()
    bins = [(float(p), int(v)) for p, v in zip(df["price"], df["volume"])]
    poc, vah, val = _value_area(bins)
    return {
        "symbol": symbol, "binWidth": width,
        "bins": [{"price": float(p), "volume": int(v), "buy": int(b), "sell": int(s)}
                 for p, v, b, s in zip(df["price"], df["volume"], df["buy"], df["sell"])],
        "poc": poc, "vah": vah, "val": val, "totalVolume": int(df["volume"].sum()) if not df.empty else 0,
    }


# ----------------------------------------------------------------------------
# Session levels
# ----------------------------------------------------------------------------

def _rth(d: date) -> tuple[int, int]:
    s = _ins().session
    lo, hi = sess.rth_bounds_ns(d, s.rth_start, s.rth_end)
    return lo // NS, hi // NS


def _hl(symbol: str, start_s: int, end_s: int) -> dict:
    """High/low/open/close/volume/VWAP from 1m bars in [start, end)."""
    try:
        bars = get_bars(symbol, "1min", start_s, end_s - 1)
    except HTTPException:
        return {}
    if bars.empty:
        return {}
    typical = (bars["high"] + bars["low"] + bars["close"]) / 3
    vol = bars["volume"].sum()
    return {
        "open": float(bars["open"].iloc[0]), "high": float(bars["high"].max()),
        "low": float(bars["low"].min()), "close": float(bars["close"].iloc[-1]),
        "volume": float(vol), "vwap": float((typical * bars["volume"]).sum() / vol) if vol else None,
    }


def get_session_levels(symbol: str, d: date, or_minutes: int = 15, ib_minutes: int = 60) -> dict:
    """OR / IB / session / prior-day levels, VWAP and the session profile."""
    lo, hi = _rth(d)
    session = _hl(symbol, lo, hi)
    if not session:
        raise HTTPException(404, f"No RTH bars for {symbol} on {d.isoformat()}")
    orb = _hl(symbol, lo, lo + or_minutes * 60)
    ib = _hl(symbol, lo, lo + ib_minutes * 60)
    dates = list_dates(symbol)
    prior = None
    prev = [x for x in dates if x < d]
    if prev:
        plo, phi = _rth(prev[-1])
        prior = _hl(symbol, plo, phi)
        if prior:
            prior["date"] = prev[-1].isoformat()
    profile = get_volume_profile(symbol, lo, hi)
    return {
        "symbol": symbol, "date": d.isoformat(), "frontSymbol": front_symbol_for(symbol, d),
        "rth": {"start": lo, "end": hi},
        "openingRange": {"minutes": or_minutes, "high": orb.get("high"), "low": orb.get("low")},
        "initialBalance": {"minutes": ib_minutes, "high": ib.get("high"), "low": ib.get("low")},
        "session": session,
        "priorDay": prior,
        "profile": {"poc": profile["poc"], "vah": profile["vah"], "val": profile["val"]},
    }


# ----------------------------------------------------------------------------
# Order book: DOM snapshot from checkpoints, heatmap from the liquidity store
# ----------------------------------------------------------------------------

def order_book_snapshot(symbol: str, as_of: int | None = None, depth: int = 12) -> dict:
    """Resting book at the last 60-second checkpoint ≤ `as_of` (unix s).

    Tick-exact books come from the replay engine (Phase 5); this endpoint
    serves the static DOM dock, for which a ≤60 s-old snapshot is enough."""
    p = get_paths()
    if as_of is None:
        _, as_of = data_range(symbol)
    d = datetime.fromtimestamp(as_of, tz=timezone.utc).date()
    sym = front_symbol_for(symbol, d)
    spec, _ = resolve(symbol)
    empty = {"bids": [], "asks": [], "lastPrice": None, "asOf": int(as_of), "stateSource": "no book checkpoint"}
    if sym is None or not _has_partitions(p.checkpoints_dir):
        return empty
    con = _duck()
    row = con.execute(
        f"SELECT max(ts) FROM read_parquet('{_glob(p.checkpoints_dir)}', hive_partitioning=true) t "
        f"WHERE t.root = ? AND t.date = ? AND t.symbol = ? AND t.ts <= ?",
        [spec.root, d, sym, as_of],
    ).fetchone()
    if row is None or row[0] is None:
        return empty
    ts = int(row[0])
    df = con.execute(
        f"SELECT side, price, size FROM read_parquet('{_glob(p.checkpoints_dir)}', hive_partitioning=true) t "
        f"WHERE t.root = ? AND t.date = ? AND t.symbol = ? AND t.ts = ?",
        [spec.root, d, sym, ts],
    ).df()
    bids = df[df["side"] == "B"].sort_values("price", ascending=False).head(depth)
    asks = df[df["side"] == "A"].sort_values("price").head(depth)
    last = None
    try:
        prints = get_trades(symbol, max(ts - 300, 0), ts + 1, 0, limit=_MAX_TRADES_ROWS)
        if prints:
            last = prints[-1]["price"]
    except HTTPException:
        pass
    return {
        "bids": [{"price": round(float(p_), 4), "size": int(s)} for p_, s in zip(bids["price"], bids["size"])],
        "asks": [{"price": round(float(p_), 4), "size": int(s)} for p_, s in zip(asks["price"], asks["size"])],
        "lastPrice": last, "asOf": ts, "stateSource": "60-second book checkpoint",
    }


def get_dom_heatmap(symbol: str, start: int, end: int, bucket_seconds: int, depth: int = 30,
                    min_price: float | None = None, max_price: float | None = None) -> dict:
    del depth
    spec, cont = resolve(symbol)
    if cont:
        windows = [(s, lo, hi) for s, lo, hi in front_month_ranges(spec.root) if hi > start and lo <= end]
    else:
        windows = [(symbol, (start // 86_400) * 86_400, end + 1)]
    return liquidity_store.get_heatmap(windows, start, end, bucket_seconds, min_price, max_price)


# ----------------------------------------------------------------------------
# Coverage
# ----------------------------------------------------------------------------

def _dir_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) if path.exists() else 0


def coverage() -> dict:
    p = get_paths()
    fm = _front_month()
    splits = json.loads(p.splits.read_text()) if p.splits.exists() else {"roots": {}}
    manifest = json.loads(p.manifest.read_text()) if p.manifest.exists() else {"files": {}}
    roots = {}
    for root, g in fm.groupby("root"):
        dates = sorted(x.isoformat() for x in g["date"])
        sp = splits.get("roots", {}).get(root, {})
        files = [v for v in manifest["files"].values() if root in (v.get("roots") or {})]
        roots[root] = {
            "sessions": len(dates), "first": dates[0], "last": dates[-1],
            "dates": dates,
            "rolls": [x.isoformat() for x, r in zip(g["date"], g["roll"]) if r],
            "inSample": sp.get("inSampleRange"), "outOfSample": sp.get("outOfSampleRange"),
            "inSampleSessions": len(sp.get("inSample", [])), "outOfSampleSessions": len(sp.get("outOfSample", [])),
            "rawFiles": len(files), "archived": sum(1 for v in files if v.get("archived")),
        }
    cache = []
    if p.replay_cache_dir.exists():
        for rd in sorted(p.replay_cache_dir.glob("root=*/date=*")):
            cache.append({"root": rd.parent.name.split("=", 1)[1], "date": rd.name.split("=", 1)[1],
                          "bytes": _dir_size(rd)})
    return {
        "roots": roots,
        "sizes": {"trades": _dir_size(p.trades_dir), "bars_1m": _dir_size(p.bars_1m_dir),
                  "bookCheckpoints": _dir_size(p.checkpoints_dir),
                  "liquidity": p.liquidity_db.stat().st_size if p.liquidity_db.exists() else 0,
                  "catalog": _dir_size(p.catalog_dir), "replayCache": sum(c["bytes"] for c in cache)},
        "replayCache": cache, "replayCacheMaxGb": get_paths().replay_cache_max_gb,
        "splitsFrozenAt": {r: v.get("frozenAt") for r, v in splits.get("roots", {}).items()},
    }
