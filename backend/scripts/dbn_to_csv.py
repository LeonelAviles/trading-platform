"""Decode a single day of Databento .dbn.zst market data and write it to CSV via polars."""

import sys
from pathlib import Path

import databento as db
import polars as pl


def main(dbn_path: str, csv_path: str) -> None:
    store = db.DBNStore.from_file(dbn_path)
    df = pl.from_pandas(store.to_df(price_type="fixed", pretty_ts=True))
    df.write_csv(csv_path)
    print(f"Wrote {df.height} rows, {df.width} columns -> {csv_path}")


if __name__ == "__main__":
    dbn_path, csv_path = sys.argv[1], sys.argv[2]
    Path(csv_path).parent.mkdir(parents=True, exist_ok=True)
    main(dbn_path, csv_path)
