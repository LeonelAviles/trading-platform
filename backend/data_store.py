"""Data access layer over the Databento CSV files in mbo-data/.

Every loader reads *all* matching files in the data directory and filters by
symbol, so adding more days/symbols is just dropping files into mbo-data/ —
no code changes. Parsed frames are cached in memory keyed by the file set's
names/mtimes/sizes, so the cache invalidates itself when files are added,
replaced, or removed.
"""

from pathlib import Path

import pandas as pd
from fastapi import HTTPException

DATA_DIR = Path(__file__).resolve().parent.parent / "mbo-data"

# Databento fixed-point prices are integers scaled by 1e-9.
PRICE_SCALE = 1e-9

_cache: dict[str, tuple[tuple, pd.DataFrame]] = {}


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


def _tick_pattern() -> str:
    """Resolve which raw tick (MBO) file set backs the chart, preferring the
    broadest dataset available. These are independent synthetic generations
    (not supersets of each other), so exactly one pattern is used at a time —
    never merge them, or trades would double-count or overlap inconsistently."""
    for pattern in ("*price-anchored-synthetic.csv", "*3months-synthetic.csv", "*.mbo-extended-dummy.csv", "*.mbo.csv"):
        if list(DATA_DIR.glob(pattern)):
            return pattern
    return "*.mbo.csv"


def load_trades(symbol: str) -> pd.DataFrame:
    """Executed trades (action == 'T') from the raw MBO event stream.

    Keeps `side`, which on a trade record is the side of the resting order
    that was hit: 'A' (ask lifted) means a buyer was aggressing, 'B' (bid
    hit) means a seller was aggressing. Used for CVD classification below.
    """
    df = _read_all("mbo", _tick_pattern(), usecols=["ts_event", "action", "side", "price", "size", "symbol"])
    df = df[(df["action"] == "T") & (df["symbol"] == symbol)]
    if df.empty:
        raise HTTPException(404, f"No trades found for symbol '{symbol}'")
    df = df.copy()
    df["price"] = df["price"] * PRICE_SCALE
    df["ts_event"] = pd.to_datetime(df["ts_event"], unit="ns", utc=True)
    return df.sort_values("ts_event")


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
    signed = df["size"].where(df["side"] == "A", -df["size"])
    count = df["size"].resample(interval).count()
    delta = signed.resample(interval).sum()
    return delta[count > 0].cumsum()


def get_cvd(symbol: str, interval: str, start: int | None = None, end: int | None = None) -> pd.Series:
    """CVD series at any interval, optionally clipped to [start, end] unix secs."""
    series = resample_cvd(load_trades(symbol), interval)
    if start is not None:
        series = series[series.index >= pd.Timestamp(start, unit="s", tz="UTC")]
    if end is not None:
        series = series[series.index <= pd.Timestamp(end, unit="s", tz="UTC")]
    return series


def load_book_events(symbol: str) -> pd.DataFrame:
    """Every book-affecting MBO event (Add/Cancel/Fill/Trade) for one symbol,
    in time order — raw material for reconstructing the resting order book.
    Cached under its own key: it needs `order_id`, which `load_trades` (a
    different column subset) doesn't load.
    """
    df = _read_all(
        "mbo_full", _tick_pattern(),
        usecols=["ts_event", "action", "side", "price", "size", "order_id", "symbol"],
    )
    df = df[df["symbol"] == symbol]
    if df.empty:
        raise HTTPException(404, f"No book events found for symbol '{symbol}'")
    df = df.copy()
    df["price"] = df["price"] * PRICE_SCALE
    df["ts_event"] = pd.to_datetime(df["ts_event"], unit="ns", utc=True)
    return df.sort_values("ts_event").reset_index(drop=True)


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


def resample_bars(bars: pd.DataFrame, interval: str) -> pd.DataFrame:
    """Roll already-aggregated OHLCV bars up to a coarser interval."""
    agg = bars.resample(interval).agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    )
    return agg.dropna(subset=["open"])


def get_bars(symbol: str, interval: str, start: int | None = None, end: int | None = None) -> pd.DataFrame:
    """OHLCV bars at any interval, optionally clipped to [start, end] unix secs.

    Derives bars from raw ticks whenever a tick file covers the symbol —
    that's the real source of truth and works at every interval, from 1s up
    to 1D. Falls back to the pre-aggregated ohlcv-1m.csv only for a symbol
    that has no tick file at all.
    """
    try:
        bars = resample_trades(load_trades(symbol), interval)
    except HTTPException:
        bars = resample_bars(load_ohlcv_bars(symbol), interval)
    if start is not None:
        bars = bars[bars.index >= pd.Timestamp(start, unit="s", tz="UTC")]
    if end is not None:
        bars = bars[bars.index <= pd.Timestamp(end, unit="s", tz="UTC")]
    return bars


def list_symbols() -> list[str]:
    symbols: set[str] = set()
    for kind, pattern in (("mbo", _tick_pattern()), ("ohlcv", "*.ohlcv-1m.csv")):
        try:
            symbols.update(_read_all(kind, pattern)["symbol"].unique())
        except HTTPException:
            pass
    return sorted(symbols)


def bars_to_records(bars: pd.DataFrame) -> list[dict]:
    return [
        {
            "time": int(ts.timestamp()),
            "open": round(row.open, 4),
            "high": round(row.high, 4),
            "low": round(row.low, 4),
            "close": round(row.close, 4),
            "volume": float(row.volume),
        }
        for ts, row in bars.iterrows()
    ]
