"""FastAPI backend that aggregates Databento MBO data into OHLCV bars.

MBO (market-by-order) is event-level data: every order add/modify/cancel/
trade/fill, one row per event. It has no open/high/low/close of its own, so
candlestick bars are derived by taking the executed trades (action == 'T')
and resampling them into fixed-interval OHLCV bars.
"""

from pathlib import Path

import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

DATA_DIR = Path(__file__).resolve().parent.parent / "mbo-data"

# Databento fixed-point prices are integers scaled by 1e-9.
PRICE_SCALE = 1e-9

app = FastAPI(title="MBO Candlestick API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _load_ohlcv_bars(symbol: str) -> pd.DataFrame:
    """Read Databento's own pre-aggregated 1-minute OHLCV export.

    This covers the full session (unlike the MBO sample, which is only a
    ~68s slice), so it's the primary source for 1min-and-up bars; anything
    finer than that still has to come from raw MBO trades.
    """
    csv_files = sorted(DATA_DIR.glob("*.ohlcv-1m.csv"))
    if not csv_files:
        raise HTTPException(404, f"No OHLCV CSV files found in {DATA_DIR}")

    frames = [pd.read_csv(f) for f in csv_files]
    df = pd.concat(frames, ignore_index=True)
    df = df[df["symbol"] == symbol]
    if df.empty:
        raise HTTPException(404, f"No OHLCV bars found for symbol '{symbol}'")

    for col in ("open", "high", "low", "close"):
        df[col] = df[col] * PRICE_SCALE
    df["ts_event"] = pd.to_datetime(df["ts_event"], unit="ns", utc=True)
    return df.set_index("ts_event").sort_index()[["open", "high", "low", "close", "volume"]]


def _resample_bars(bars: pd.DataFrame, interval: str) -> pd.DataFrame:
    """Roll already-aggregated OHLCV bars up to a coarser interval."""
    agg = bars.resample(interval).agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    )
    return agg.dropna(subset=["open"])


def _load_trades(symbol: str) -> pd.DataFrame:
    csv_files = sorted(DATA_DIR.glob("*.mbo.csv"))
    if not csv_files:
        raise HTTPException(404, f"No MBO CSV files found in {DATA_DIR}")

    frames = []
    for f in csv_files:
        df = pd.read_csv(f, usecols=["ts_event", "action", "price", "size", "symbol"])
        frames.append(df)
    df = pd.concat(frames, ignore_index=True)

    df = df[(df["action"] == "T") & (df["symbol"] == symbol)]
    if df.empty:
        raise HTTPException(404, f"No trades found for symbol '{symbol}'")

    df["price"] = df["price"] * PRICE_SCALE
    df["ts_event"] = pd.to_datetime(df["ts_event"], unit="ns", utc=True)
    return df.sort_values("ts_event")


def _resample(df: pd.DataFrame, interval: str) -> pd.DataFrame:
    df = df.set_index("ts_event")
    bars = df["price"].resample(interval).ohlc()
    bars["volume"] = df["size"].resample(interval).sum()
    return bars.dropna(subset=["open"])


@app.get("/api/symbols")
def list_symbols():
    csv_files = sorted(DATA_DIR.glob("*.mbo.csv")) + sorted(DATA_DIR.glob("*.ohlcv-1m.csv"))
    if not csv_files:
        return {"symbols": []}
    symbols: set[str] = set()
    for f in csv_files:
        df = pd.read_csv(f, usecols=["symbol"])
        symbols.update(df["symbol"].unique())
    return {"symbols": sorted(symbols)}


@app.get("/api/ohlcv")
def get_ohlcv(
    symbol: str = Query(..., description="Ticker symbol, e.g. MSFT"),
    interval: str = Query("1s", description="Pandas offset alias: 1s, 1min, 5min, 1D, ..."),
):
    if interval in ("1s", "5s"):
        trades = _load_trades(symbol)
        bars = _resample(trades, interval)
    else:
        bars = _resample_bars(_load_ohlcv_bars(symbol), interval)

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
