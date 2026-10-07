"""Stage 4: order-flow conditioning of level-touch events.

For every first-touch event (real + placebo) attach:
  bar features   : approach delta (5 min), touch-bar delta, touch-bar range,
                   touch-bar relative volume
  print features : volume and aggressor delta within +-3t of the level in the
                   touch minute (t0..t0+2min), traded volume at the exact
                   level (+-1t) for the iceberg proxy
  book features  : last checkpoint <= t0: defending-side resting size in the
                   8t band beyond the level, displayed size at the level
                   (+-1t), max single defending level (wall), its n_orders
Signs are normalized so + = toward the bounce; then respect-rate by bucket.
"""
import pathlib

import duckdb
import numpy as np
import pandas as pd

OUT = pathlib.Path(__file__).parent
BASE = "/Users/leonelaviles/Desktop/trading-platform/data/market"
TICK = 0.25
ET = "America/New_York"

con = duckdb.connect()
con.execute("SET TimeZone='UTC'")
con.execute("SET memory_limit='4GB'")
con.execute("SET threads=4")

# ---------------------------------------------------------------- 1-min x 1-tick print table (RTH, front month)
vap1 = OUT / "vap1m.parquet"
if not vap1.exists():
    con.execute(f"""
    COPY (
      SELECT t.date,
             (t.ts_event // 60000000000) * 60 AS min_s,          -- minute start, unix s
             round(t.price / 0.25)::BIGINT AS tickp,
             sum(t.size)::BIGINT AS vol,
             sum(CASE WHEN t.side = 'B' THEN t.size ELSE 0 END)::BIGINT AS buy,
             sum(CASE WHEN t.side = 'A' THEN t.size ELSE 0 END)::BIGINT AS sell
      FROM read_parquet('{BASE}/trades/root=*/date=*/*.parquet', hive_partitioning=true) t
      JOIN read_parquet('{BASE}/front_month.parquet') fm
        ON fm.root = t.root AND fm.date = t.date AND fm.symbol = t.symbol
      WHERE t.root = 'ES'
      GROUP BY 1, 2, 3
    ) TO '{vap1}' (FORMAT PARQUET)
    """)
print("vap1m rows:", con.execute(f"SELECT count(*) FROM read_parquet('{vap1}')").fetchone()[0])

# ---------------------------------------------------------------- events -> timestamps
ev = pd.read_parquet(OUT / "events.parquet")
ev = ev[ev["first"]].reset_index(drop=True).copy()
ev["eid"] = ev.index
d = pd.to_datetime(ev["date"].astype(str))
ts_local = d.dt.tz_localize(ET) + pd.to_timedelta(ev["minute"], unit="m")
ev["ts0"] = (ts_local.dt.tz_convert("UTC") - pd.Timestamp(0, tz="UTC")) // pd.Timedelta(seconds=1)
ev["utc_date"] = ts_local.dt.tz_convert("UTC").dt.date
ev["tickp"] = (ev["level"] / TICK).round().astype("int64")
con.register("ev", ev[["eid", "utc_date", "ts0", "tickp", "side"]])

# ---------------------------------------------------------------- print features (joins on the small table)
pf = con.execute(f"""
WITH v AS (SELECT * FROM read_parquet('{vap1}'))
SELECT e.eid,
       sum(CASE WHEN abs(v.tickp - e.tickp) <= 3 THEN v.vol END)  AS vol_lvl,
       sum(CASE WHEN abs(v.tickp - e.tickp) <= 3 THEN v.buy - v.sell END) AS delta_lvl,
       sum(CASE WHEN abs(v.tickp - e.tickp) <= 1 THEN v.vol END)  AS vol_exact
FROM ev e
JOIN v ON v.date = e.utc_date AND v.min_s >= e.ts0 AND v.min_s < e.ts0 + 120
GROUP BY 1
""").df()

# ---------------------------------------------------------------- book features (asof <= t0, within 120 s)
bf = con.execute(f"""
WITH cp AS (
  SELECT c.date, c.ts, c.side AS bside, round(c.price / 0.25)::BIGINT AS tickp,
         c.size, c.n_orders
  FROM read_parquet('{BASE}/book_checkpoints/root=*/date=*/*.parquet', hive_partitioning=true) c
  JOIN read_parquet('{BASE}/front_month.parquet') fm
    ON fm.root = c.root AND fm.date = c.date AND fm.symbol = c.symbol
  WHERE c.root = 'ES'
),
last_ts AS (
  SELECT e.eid, max(cp.ts) AS cts
  FROM ev e JOIN cp ON cp.date = e.utc_date AND cp.ts <= e.ts0 AND cp.ts > e.ts0 - 120
  GROUP BY 1
)
SELECT e.eid,
       sum(CASE WHEN cp.bside = (CASE WHEN e.side = 1 THEN 'B' ELSE 'A' END)
                 AND (CASE WHEN e.side = 1 THEN e.tickp - cp.tickp ELSE cp.tickp - e.tickp END) BETWEEN -2 AND 8
                THEN cp.size END) AS defend_size,
       max(CASE WHEN cp.bside = (CASE WHEN e.side = 1 THEN 'B' ELSE 'A' END)
                 AND (CASE WHEN e.side = 1 THEN e.tickp - cp.tickp ELSE cp.tickp - e.tickp END) BETWEEN -2 AND 8
                THEN cp.size END) AS wall_size,
       sum(CASE WHEN abs(cp.tickp - e.tickp) <= 1 AND cp.bside = (CASE WHEN e.side = 1 THEN 'B' ELSE 'A' END)
                THEN cp.size END) AS disp_exact,
       sum(CASE WHEN abs(cp.tickp - e.tickp) <= 1 AND cp.bside = (CASE WHEN e.side = 1 THEN 'B' ELSE 'A' END)
                THEN cp.n_orders END) AS disp_orders
FROM ev e
JOIN last_ts lt ON lt.eid = e.eid
JOIN cp ON cp.date = e.utc_date AND cp.ts = lt.cts
GROUP BY 1
""").df()

# ---------------------------------------------------------------- bar features
bars = pd.read_parquet(OUT / "bars.parquet")
bars["min_s"] = (bars["ts"] // 10**9 // 60 * 60).astype("int64")
bars = bars.set_index("min_s").sort_index()
delta_s = bars["delta"]
vol_s = bars["volume"]
rng_s = ((bars["high"] - bars["low"]) / TICK)

appr, tdelta, trange, tvol = [], [], [], []
didx = delta_s.index.to_numpy()
dval = delta_s.to_numpy(float)
vval = vol_s.to_numpy(float)
rval = rng_s.to_numpy(float)
pos = {int(t): i for i, t in enumerate(didx)}
for t0 in ev["ts0"].to_numpy():
    i = pos.get(int(t0))
    if i is None:
        appr.append(np.nan); tdelta.append(np.nan); trange.append(np.nan); tvol.append(np.nan)
        continue
    lo = max(0, i - 5)
    appr.append(dval[lo:i].sum())
    tdelta.append(dval[i])
    trange.append(rval[i])
    tvol.append(vval[i])
ev["appr_delta"] = appr
ev["touch_delta"] = tdelta
ev["touch_range"] = trange
ev["touch_vol"] = tvol

feat = ev.merge(pf, on="eid", how="left").merge(bf, on="eid", how="left")
# + = toward the bounce (side=+1: approached from above, bounce is up)
for c in ["appr_delta", "touch_delta", "delta_lvl"]:
    feat["s_" + c] = feat[c] * feat["side"]
feat["iceberg_ratio"] = feat["vol_exact"] / feat["disp_exact"].clip(lower=1)
feat["respect"] = (feat["outcome"] == "respect").astype(int)
feat.to_parquet(OUT / "event_features.parquet")

# ---------------------------------------------------------------- conditional respect rates
TRADEABLE = ["open", "prior_vwap", "session_low_retest", "session_high_retest"]
feat["grp"] = np.where(feat["family"].isin(TRADEABLE) & ~feat["placebo"], "tradeable",
               np.where(~feat["placebo"], "other_real", "placebo"))

def z2(k1, n1, k2, n2):
    if min(n1, n2) == 0:
        return np.nan
    p = (k1 + k2) / (n1 + n2)
    se = np.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    return ((k1 / n1) - (k2 / n2)) / se if se > 0 else np.nan

def bucket_report(df, col, splits, labels, title):
    print(f"\n--- {title} ({col}) ---")
    for grp, g in df.groupby("grp"):
        g = g[g[col].notna()]
        if len(g) < 40:
            continue
        cats = pd.cut(g[col], splits, labels=labels)
        parts = []
        for lab in labels:
            sub = g[cats == lab]
            if len(sub):
                parts.append(f"{lab}: {sub['respect'].mean():.2f} (n {len(sub)})")
        lo_ = g[cats == labels[0]]; hi_ = g[cats == labels[-1]]
        z = z2(hi_["respect"].sum(), len(hi_), lo_["respect"].sum(), len(lo_))
        print(f"{grp:10s} " + "  ".join(parts) + f"   [last-vs-first z={z:+.2f}]")

q = lambda s, ps: [s.quantile(p) for p in ps]
inf = float("inf")

# 1. touch-bar delta toward the bounce
bucket_report(feat, "s_touch_delta", [-inf, -400, -50, 50, 400, inf],
              ["<<0", "<0", "~0", ">0", ">>0"], "touch-bar delta toward bounce (contracts)")
# 2. approach delta (5 min) toward the bounce: negative = market was driving INTO the level
bucket_report(feat, "s_appr_delta", [-inf, -1500, -300, 300, 1500, inf],
              ["<<0", "<0", "~0", ">0", ">>0"], "approach delta (5m) toward bounce")
# 3. near-level aggressor delta in touch window
bucket_report(feat, "s_delta_lvl", [-inf, -300, -50, 50, 300, inf],
              ["<<0", "<0", "~0", ">0", ">>0"], "aggressor delta within 3t of level, t0..t0+2m")
# 4. volume at level (absorption magnitude)
vsplit = q(feat["vol_lvl"].dropna(), [.25, .5, .75])
bucket_report(feat, "vol_lvl", [-inf] + vsplit + [inf], ["q1", "q2", "q3", "q4"],
              "traded volume within 3t of level (quartiles)")
# 5. absorption: heavy into-level flow that did NOT run (touch bar range below median)
med_r = feat["touch_range"].median()
feat["absorb"] = ((feat["s_delta_lvl"] < -100) & (feat["touch_range"] <= med_r)).astype(int)
bucket_report(feat, "absorb", [-inf, .5, inf], ["no", "yes"],
              "absorption flag (into-level delta < -100 AND touch-bar range <= median)")
# 6. defending book size
bucket_report(feat, "defend_size", [-inf, 150, 300, 600, inf], ["<150", "150-300", "300-600", ">600"],
              "resting defend-side size, level..8t beyond (contracts)")
# 7. wall
bucket_report(feat, "wall_size", [-inf, 100, 200, inf], ["<100", "100-200", ">200"],
              "largest single defending level (p90=100 per calibration)")
# 8. iceberg proxy
bucket_report(feat, "iceberg_ratio", [-inf, 2, 5, 15, inf], ["<2", "2-5", "5-15", ">15"],
              "traded at level / displayed at level (iceberg proxy)")

# ---------------------------------------------------------------- combined rule on the tradeable set
print("\n=== combined: tradeable families, first touch ===")
tr = feat[feat["grp"] == "tradeable"].copy()
base = tr["respect"].mean()
print(f"base: {base:.3f} (n {len(tr)})")
conds = {
    "touch_delta toward bounce > 0": tr["s_touch_delta"] > 0,
    "touch_delta > +200": tr["s_touch_delta"] > 200,
    "appr exhaust (s_appr_delta > -300)": tr["s_appr_delta"] > -300,
    "absorb flag": tr["absorb"] == 1,
    "defend_size > 300": tr["defend_size"] > 300,
    "vol_lvl q4": tr["vol_lvl"] > vsplit[-1],
    "touch_delta>0 AND defend>300": (tr["s_touch_delta"] > 0) & (tr["defend_size"] > 300),
    "touch_delta>0 AND vol_lvl>med": (tr["s_touch_delta"] > 0) & (tr["vol_lvl"] > vsplit[1]),
}
for name, m in conds.items():
    sub = tr[m.fillna(False)]
    zz = z2(sub["respect"].sum(), len(sub),
            tr["respect"].sum() - sub["respect"].sum(), len(tr) - len(sub))
    print(f"{name:38s} {sub['respect'].mean():.3f} (n {len(sub)})  vs rest z={zz:+.2f}")

# per-family for the winner conditions
print("\n=== per family: s_touch_delta > 0 ===")
for fam in TRADEABLE + ["prior_close", "prior_poc", "on_high", "on_low"]:
    g = feat[(feat["family"] == fam) & ~feat["placebo"]]
    g = g[g["s_touch_delta"].notna()]
    a = g[g["s_touch_delta"] > 0]; b = g[g["s_touch_delta"] <= 0]
    if len(g) < 30:
        continue
    print(f"{fam:20s} conf {a['respect'].mean():.2f} (n {len(a)})  unconf {b['respect'].mean():.2f} (n {len(b)})  z={z2(a['respect'].sum(),len(a),b['respect'].sum(),len(b)):+.2f}")
