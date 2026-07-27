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


def load_trades(symbol: str) -> pd.DataFrame:
    """Executed trades (action == 'T') from the raw MBO event stream."""
    df = _read_all("mbo", "*.mbo.csv", usecols=["ts_event", "action", "price", "size", "symbol"])
    df = df[(df["action"] == "T") & (df["symbol"] == symbol)]
    if df.empty:
        raise HTTPException(404, f"No trades found for symbol '{symbol}'")
    df = df.copy()
    df["price"] = df["price"] * PRICE_SCALE
    df["ts_event"] = pd.to_datetime(df["ts_event"], unit="ns", utc=True)
    return df.sort_values("ts_event")


def resample_trades(df: pd.DataFrame, interval: str) -> pd.DataFrame:
    """OHLCV bars derived from individual trades (for sub-minute intervals)."""
    df = df.set_index("ts_event")
    bars = df["price"].resample(interval).ohlc()
    bars["volume"] = df["size"].resample(interval).sum()
    return bars.dropna(subset=["open"])


def resample_bars(bars: pd.DataFrame, interval: str) -> pd.DataFrame:
    """Roll already-aggregated OHLCV bars up to a coarser interval."""
    agg = bars.resample(interval).agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    )
    return agg.dropna(subset=["open"])


def get_bars(symbol: str, interval: str, start: int | None = None, end: int | None = None) -> pd.DataFrame:
    """OHLCV bars at any interval, optionally clipped to [start, end] unix secs."""
    if interval in ("1s", "5s", "15s"):
        bars = resample_trades(load_trades(symbol), interval)
    else:
        bars = resample_bars(load_ohlcv_bars(symbol), interval)
    if start is not None:
        bars = bars[bars.index >= pd.Timestamp(start, unit="s", tz="UTC")]
    if end is not None:
        bars = bars[bars.index <= pd.Timestamp(end, unit="s", tz="UTC")]
    return bars


def list_symbols() -> list[str]:
    symbols: set[str] = set()
    for kind, pattern in (("mbo", "*.mbo.csv"), ("ohlcv", "*.ohlcv-1m.csv")):
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
