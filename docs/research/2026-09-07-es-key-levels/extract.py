"""Stage 1: extract front-month ES datasets for the intraday-levels study.

Outputs (scratchpad):
  bars.parquet   front-month 1-min bars, all hours
  vap30.parquet  front-month volume-at-price per 30-min UTC bucket (1-tick bins)
"""
import duckdb, time, pathlib

BASE = "/Users/leonelaviles/Desktop/trading-platform/data/market"
OUT = pathlib.Path(__file__).parent
FM = f"{BASE}/front_month.parquet"

con = duckdb.connect()
con.execute("SET TimeZone='UTC'")
con.execute("SET memory_limit='4GB'")
con.execute("SET threads=4")

t0 = time.time()
con.execute(f"""
COPY (
  SELECT b.date, b.ts, b.open, b.high, b.low, b.close, b.volume, b.delta
  FROM read_parquet('{BASE}/bars_1m/root=*/date=*/*.parquet', hive_partitioning=true) b
  JOIN read_parquet('{FM}') fm
    ON fm.root = b.root AND fm.date = b.date AND fm.symbol = b.symbol
  WHERE b.root = 'ES'
  ORDER BY b.ts
) TO '{OUT}/bars.parquet' (FORMAT PARQUET)
""")
print(f"bars.parquet {time.time()-t0:.1f}s")

t0 = time.time()
con.execute(f"""
COPY (
  SELECT t.date,
         (t.ts_event // 1800000000000) * 1800000000000 AS bucket,
         round(round(t.price / 0.25) * 0.25, 4) AS price,
         sum(t.size)::BIGINT AS volume
  FROM read_parquet('{BASE}/trades/root=*/date=*/*.parquet', hive_partitioning=true) t
  JOIN read_parquet('{FM}') fm
    ON fm.root = t.root AND fm.date = t.date AND fm.symbol = t.symbol
  WHERE t.root = 'ES'
  GROUP BY 1, 2, 3
  ORDER BY 1, 2, 3
) TO '{OUT}/vap30.parquet' (FORMAT PARQUET)
""")
print(f"vap30.parquet {time.time()-t0:.1f}s")

for f in ["bars.parquet", "vap30.parquet"]:
    n = con.execute(f"SELECT count(*), count(DISTINCT date) FROM read_parquet('{OUT}/{f}')").fetchone()
    print(f, "rows:", n[0], "dates:", n[1])
