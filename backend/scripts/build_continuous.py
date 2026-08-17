"""Build a raw (unadjusted) continuous ES front-month series from the daily
MBO .dbn.zst files in market-data/apr-jul-databento/.

For each day: decode trades only (streamed in chunks to bound memory), pick
the outright ES contract month with the highest traded volume that day as
the front month, resample its trades into 1-minute OHLCV bars, and tag them
with the synthetic continuous symbol. Concatenating each day's front-month
bars (no price adjustment at rolls) gives a continuous series matching how
the raw price actually traded, jumps included.

Output lands in mbo-data/<CONTINUOUS_SYMBOL>.ohlcv-1m.csv, which
backend/data_store.py already knows how to serve (falls back to
*.ohlcv-1m.csv for any symbol without its own tick file).
"""

import re
import sys
from pathlib import Path

import databento as db
import pandas as pd

RAW_DIR = Path(__file__).resolve().parent.parent.parent / "market-data" / "apr-jul-databento"
OUT_DIR = Path(__file__).resolve().parent.parent.parent / "mbo-data"
CONTINUOUS_SYMBOL = "ES1!"
CHUNK_ROWS = 2_000_000

# Outright quarterly ES contracts only (ESH6, ESM6, ...) — excludes calendar
# spreads like ESM6-ESU6, which trade under their own combined symbol and
# would otherwise pollute the front-month volume comparison.
OUTRIGHT_RE = re.compile(r"^ES[HMUZ]\d$")


def day_trades(path: Path) -> pd.DataFrame | None:
    """Trades (action == 'T') across the whole file, symbol/price/size/ts_event
    only, streamed in chunks so peak memory doesn't scale with file size."""
    store = db.DBNStore.from_file(path)
    parts = []
    for chunk in store.to_df(price_type="fixed", pretty_ts=False, count=CHUNK_ROWS):
        t = chunk.loc[chunk["action"] == "T", ["ts_event", "symbol", "price", "size"]]
        if not t.empty:
            parts.append(t)
    return pd.concat(parts, ignore_index=True) if parts else None


def front_month_bars(trades: pd.DataFrame) -> pd.DataFrame | None:
    outright = trades[trades["symbol"].str.match(OUTRIGHT_RE)]
    if outright.empty:
        return None
    front = outright.groupby("symbol")["size"].sum().idxmax()
    df = outright[outright["symbol"] == front].copy()
    df.index = pd.to_datetime(df["ts_event"], unit="ns", utc=True)
    bars = df["price"].resample("1min").ohlc()
    bars["volume"] = df["size"].resample("1min").sum()
    bars = bars.dropna(subset=["open"])
    bars["ts_event"] = bars.index.view("int64")
    bars["symbol"] = CONTINUOUS_SYMBOL
    bars["source_contract"] = front
    return bars.reset_index(drop=True)[
        ["ts_event", "symbol", "open", "high", "low", "close", "volume", "source_contract"]
    ]


def main():
    files = sorted(RAW_DIR.glob("2026-*/glbx-mdp3-*.mbo.dbn.zst"))
    if not files:
        print("No source files found", file=sys.stderr)
        sys.exit(1)

    all_bars = []
    last_front = None
    for i, path in enumerate(files, 1):
        trades = day_trades(path)
        bars = front_month_bars(trades) if trades is not None else None
        if bars is None or bars.empty:
            print(f"[{i}/{len(files)}] {path.name}: no outright trades, skipped")
            continue
        front = bars["source_contract"].iloc[0]
        roll_marker = " <- ROLL" if last_front and front != last_front else ""
        print(f"[{i}/{len(files)}] {path.name}: front={front} bars={len(bars)}{roll_marker}", flush=True)
        last_front = front
        all_bars.append(bars)

    full = pd.concat(all_bars, ignore_index=True).sort_values("ts_event")
    OUT_DIR.mkdir(exist_ok=True)
    out_path = OUT_DIR / f"{CONTINUOUS_SYMBOL.replace('!', '')}.ohlcv-1m.csv"
    full.to_csv(out_path, index=False)
    print(f"\nWrote {len(full)} bars ({full['source_contract'].nunique()} contracts) -> {out_path}")


if __name__ == "__main__":
    main()
