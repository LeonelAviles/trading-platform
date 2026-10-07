"""Stage 5: intra-bar delta confirmation — what price do you actually get?

For each real first-touch event: from the first print at the level, accumulate
signed delta print by print; 'enter' when it crosses +X contracts (within the
touch bar, and a 120 s variant). Record trigger price, time, and outcome.
"""
import pathlib

import duckdb
import numpy as np
import pandas as pd

OUT = pathlib.Path(__file__).parent
BASE = "/Users/leonelaviles/Desktop/trading-platform/data/market"
TICK = 0.25

feat = pd.read_parquet(OUT / "event_features.parquet")
ev = feat[~feat["placebo"]].copy()          # real levels only
ev = ev[ev["family"] != "morning_poc"].copy()
ev["utc_date"] = pd.to_datetime(ev["ts0"], unit="s", utc=True).dt.date

con = duckdb.connect()
con.execute("SET memory_limit='4GB'")
con.execute("SET threads=4")
con.register("e", ev[["eid", "utc_date", "ts0"]])
pr = con.execute(f"""
SELECT e.eid, t.ts_event, t.price, t.size, t.side
FROM read_parquet('{BASE}/trades/root=*/date=*/*.parquet', hive_partitioning=true) t
JOIN read_parquet('{BASE}/front_month.parquet') fm
  ON fm.root = t.root AND fm.date = t.date AND fm.symbol = t.symbol
JOIN e ON t.date = e.utc_date
      AND t.ts_event >= e.ts0 * 1000000000
      AND t.ts_event <  (e.ts0 + 120) * 1000000000
WHERE t.root = 'ES'
ORDER BY e.eid, t.ts_event, t.sequence
""").df()
print("prints:", len(pr), "events with prints:", pr["eid"].nunique(), "of", len(ev))

meta = ev.set_index("eid")[["level", "side", "ts0", "outcome", "max_bounce", "max_pen", "family"]]

rows = []
for eid, g in pr.groupby("eid", sort=False):
    m = meta.loc[eid]
    side, level, ts0 = int(m["side"]), float(m["level"]), int(m["ts0"])
    px = g["price"].to_numpy()
    sz = g["size"].to_numpy(float)
    sd = np.where(g["side"].to_numpy() == "B", 1.0, -1.0)
    ts = g["ts_event"].to_numpy() / 1e9 - ts0          # seconds since bar start
    # touch moment: first print within 2t of the level (toward it counts)
    dist = (px - level) / TICK * side                  # + = still on approach side
    at = np.flatnonzero(dist <= 2)
    if len(at) == 0:
        continue
    i0 = at[0]
    cum = np.cumsum(sd[i0:] * sz[i0:]) * side          # + = toward bounce
    t_rel = ts[i0:]
    p_rel = px[i0:]
    for X in (100, 200, 400):
        for wname, wend in (("bar", 60.0), ("120s", 120.0)):
            ok = np.flatnonzero((cum >= X) & (t_rel <= wend))
            hit = len(ok) > 0
            j = ok[0] if hit else -1
            rows.append({
                "eid": eid, "family": m["family"], "X": X, "win": wname,
                "trigger": hit,
                "t_trig": float(t_rel[j]) if hit else np.nan,
                "cost": float((p_rel[j] - level) / TICK * side) if hit else np.nan,
                "outcome": m["outcome"], "max_bounce": m["max_bounce"],
                "touch_s": float(ts[i0]),
            })

tr = pd.DataFrame(rows)
tr.to_parquet(OUT / "intrabar.parquet")

TRADEABLE = ["open", "prior_vwap", "session_low_retest", "session_high_retest"]
tr["grp"] = np.where(tr["family"].isin(TRADEABLE), "tradeable", "other")

print("\n=== intra-bar delta trigger: enter when cum signed delta >= X after first print at level ===")
for (grp, X, w), g in tr.groupby(["grp", "X", "win"]):
    trig = g[g["trigger"]]
    if not len(trig):
        continue
    resp = (trig["outcome"] == "respect").mean()
    resp_no = (g[~g["trigger"]]["outcome"] == "respect").mean()
    rem = (trig["max_bounce"] - trig["cost"])
    print(f"{grp:9s} X={X:3d} win={w:4s}  trig {len(trig)/len(g):.2f} ({len(trig)})  "
          f"respect {resp:.2f} | untrig {resp_no:.2f}  "
          f"cost med {trig['cost'].median():+.1f}t p75 {trig['cost'].quantile(.75):+.1f}  "
          f"t med {trig['t_trig'].median():.0f}s  remaining bounce med {rem.median():.1f}t")

# share of triggers still 'at' the level (cost <= 4t) and their respect
print("\n=== cheap triggers (cost <= 4t) ===")
for (grp, X), g in tr[tr["win"] == "bar"].groupby(["grp", "X"]):
    trig = g[g["trigger"]]
    cheap = trig[trig["cost"] <= 4]
    if len(trig):
        print(f"{grp:9s} X={X:3d}  cheap share {len(cheap)/len(trig):.2f} ({len(cheap)})  "
              f"respect cheap {((cheap['outcome']=='respect').mean() if len(cheap) else float('nan')):.2f}  "
              f"vs expensive {((trig[trig['cost']>4]['outcome']=='respect').mean()):.2f}")

# economics: enter at trigger, stop 10t past level, target = level + 12t (bounce threshold), bar window
print("\n=== econ sketch (bar window): stop level-10t, target level+12t, entry at trigger ===")
for (grp, X), g in tr[tr["win"] == "bar"].groupby(["grp", "X"]):
    trig = g[g["trigger"]].copy()
    if len(trig) < 20:
        continue
    win = trig["outcome"] == "respect"
    evt = np.where(win, 12 - trig["cost"], -(10 + trig["cost"]))
    print(f"{grp:9s} X={X:3d}  n {len(trig)}  mean {evt.mean():+.2f}t (${evt.mean()*12.5:+.0f} gross/trade)  "
          f"win {win.mean():.2f}")
