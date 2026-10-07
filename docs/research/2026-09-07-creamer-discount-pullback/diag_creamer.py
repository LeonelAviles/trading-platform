"""How often does each Creamer condition hold on 5-min bars, 09:30-11:00 ET, Apr-Jul 2026 (front month)?
Re-implements leg_retracement / swing logic in pandas so the platform's definitions are what is counted."""
import duckdb
import numpy as np
import pandas as pd

BASE = "/Users/leonelaviles/Desktop/trading-platform/data/market"
con = duckdb.connect()
con.execute("SET TimeZone='UTC'")
b1 = con.execute(f"""
SELECT b.date, b.ts, b.open, b.high, b.low, b.close, b.volume, b.delta
FROM read_parquet('{BASE}/bars_1m/root=*/date=*/*.parquet', hive_partitioning=true) b
JOIN read_parquet('{BASE}/front_month.parquet') fm ON fm.root=b.root AND fm.date=b.date AND fm.symbol=b.symbol
WHERE b.root='ES' AND b.date BETWEEN '2026-03-25' AND '2026-07-31' ORDER BY b.ts""").df()
b1["t"] = pd.to_datetime(b1["ts"], unit="ns", utc=True)
b1["et"] = b1["t"].dt.tz_convert("America/New_York")


def agg(df, minutes):
    g = df.set_index("t").resample(f"{minutes}min", label="left", closed="left")
    o = g.agg(open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"),
              volume=("volume", "sum"), delta=("delta", "sum")).dropna(subset=["open"])
    o["et"] = o.index.tz_convert("America/New_York")
    return o.reset_index()


b5, b15, b60 = agg(b1, 5), agg(b1, 15), agg(b1, 60)


def swings(df, n):
    """Indices of confirmed swing highs/lows (platform definition)."""
    h, l = df["high"].to_numpy(), df["low"].to_numpy()
    sh, sl = [], []
    for i in range(n, len(df) - n):
        if all(h[j] < h[i] for j in range(i - n, i)) and all(h[j] <= h[i] for j in range(i + 1, i + n + 1)):
            sh.append(i)
        if all(l[j] > l[i] for j in range(i - n, i)) and all(l[j] >= l[i] for j in range(i + 1, i + n + 1)):
            sl.append(i)
    return np.array(sh), np.array(sl)


def leg_retr(df, n, use_extreme=False):
    """Signed retracement per closed bar, using only swings confirmed by that bar (bar i confirms swing i-n)."""
    sh, sl = swings(df, n)
    out = np.full(len(df), np.nan)
    hi_ptr = lo_ptr = -1
    hs, ls = list(sh), list(sl)
    hi_i = lo_i = None
    for i in range(len(df)):
        while hi_ptr + 1 < len(hs) and hs[hi_ptr + 1] + n <= i:
            hi_ptr += 1
            hi_i = hs[hi_ptr]
        while lo_ptr + 1 < len(ls) and ls[lo_ptr + 1] + n <= i:
            lo_ptr += 1
            lo_i = ls[lo_ptr]
        if hi_i is None or lo_i is None:
            continue
        H, L = df["high"].iat[hi_i], df["low"].iat[lo_i]
        if H <= L:
            continue
        if hi_i > lo_i:
            c = df["low"].iat[i] if use_extreme else df["close"].iat[i]
            out[i] = (H - c) / (H - L)
        else:
            c = df["high"].iat[i] if use_extreme else df["close"].iat[i]
            out[i] = -(c - L) / (H - L)
    return out


b15["retr"] = leg_retr(b15, 3)
b15["retrx"] = leg_retr(b15, 3, True)
b5["retr5"] = leg_retr(b5, 3)
b5["retr5x"] = leg_retr(b5, 3, True)
b60["ema20"] = b60["close"].ewm(span=20, adjust=False).mean()
b60["trend_up"] = b60["close"] > b60["ema20"]

# Prior-day RTH value area (bars-mode approximation: bar volume spread across its range)
rth = b1[(b1["et"].dt.hour * 60 + b1["et"].dt.minute).between(570, 959)]
va = {}
for d, g in rth.groupby("date"):
    prof = {}
    for o, h, l, v in zip(g["open"], g["high"], g["low"], g["volume"]):
        lo, hi = round(l / 0.25) * 0.25, round(h / 0.25) * 0.25
        k = int(round((hi - lo) / 0.25)) + 1
        for j in range(k):
            px = round(lo + j * 0.25, 2)
            prof[px] = prof.get(px, 0) + v / k
    bins = sorted(prof.items())
    tot = sum(v for _, v in bins)
    i = max(range(len(bins)), key=lambda k: bins[k][1])
    lo_i = hi_i = i
    acc = bins[i][1]
    while acc < 0.7 * tot and (lo_i > 0 or hi_i < len(bins) - 1):
        up = bins[hi_i + 1][1] if hi_i < len(bins) - 1 else -1
        dn = bins[lo_i - 1][1] if lo_i > 0 else -1
        if up >= dn:
            hi_i += 1; acc += up
        else:
            lo_i -= 1; acc += dn
    va[pd.Timestamp(d).date()] = (bins[i][0], bins[hi_i][0], bins[lo_i][0])
dates = sorted(va)
prior = {dates[k]: va[dates[k - 1]] for k in range(1, len(dates))}

# Join onto 5-min bars in the entry window
b5["date"] = b5["et"].dt.date
w = b5[(b5["et"].dt.hour * 60 + b5["et"].dt.minute).between(570, 655) & (b5["date"] >= pd.Timestamp("2026-04-01").date())].copy()
w["retr15"] = pd.merge_asof(w[["t"]], b15[["t", "retr"]].assign(t=b15["t"] + pd.Timedelta(minutes=15)), on="t")["retr"].to_numpy()
w["retr15x"] = pd.merge_asof(w[["t"]], b15[["t", "retrx"]].assign(t=b15["t"] + pd.Timedelta(minutes=15)), on="t")["retrx"].to_numpy()
w["trend_up"] = pd.merge_asof(w[["t"]], b60[["t", "trend_up"]].assign(t=b60["t"] + pd.Timedelta(minutes=60)), on="t")["trend_up"].to_numpy()
w["pd_val"] = [prior.get(d, (np.nan,) * 3)[2] for d in w["date"]]
w["pd_vah"] = [prior.get(d, (np.nan,) * 3)[1] for d in w["date"]]
w["bull"] = w["close"] > w["open"]
w["sellers"] = w["delta"] < 0
w["disc15"] = w["retr15"].between(0.705, 0.886)
w["disc15_wide"] = w["retr15"].between(0.618, 0.886)
w["disc5"] = w["retr5"].between(0.705, 0.886)
w["disc15x"] = w["retr15x"].between(0.705, 0.886)
w["disc5x"] = w["retr5x"].between(0.705, 0.886)
w["below_val"] = w["close"] < w["pd_val"]
w["above_vah"] = w["close"] > w["pd_vah"]
w["vol10k"] = w["volume"] >= 10000
n = len(w)
print(f"5-min bars in 09:30-11:00, Apr-Jul: {n} over {w['date'].nunique()} sessions")
for c in ["trend_up", "disc15", "disc15x", "disc15_wide", "disc5", "disc5x", "below_val", "above_vah", "bull", "sellers", "vol10k"]:
    print(f"  {c:12s} {w[c].mean()*100:5.1f} %")
print("retr15 distribution:", np.nanpercentile(w["retr15"], [10, 25, 50, 75, 90]).round(2), "NaN share", w["retr15"].isna().mean().round(2))
print("joint (long side):")
for label, m in [
    ("disc15 & trend_up", w["disc15"] & w["trend_up"]),
    ("disc15 & below_val", w["disc15"] & w["below_val"]),
    ("disc15 & trend_up & below_val", w["disc15"] & w["trend_up"] & w["below_val"]),
    ("+ sellers & bull (absorption bar)", w["disc15"] & w["trend_up"] & w["below_val"] & w["sellers"] & w["bull"]),
    ("disc15 & trend_up & sellers & bull (no VAL)", w["disc15"] & w["trend_up"] & w["sellers"] & w["bull"]),
    ("disc15_wide & trend_up & sellers & bull", w["disc15_wide"] & w["trend_up"] & w["sellers"] & w["bull"]),
    ("disc5 & trend_up & sellers & bull", w["disc5"] & w["trend_up"] & w["sellers"] & w["bull"]),
    ("disc15x(wick) & trend_up & sellers & bull", w["disc15x"] & w["trend_up"] & w["sellers"] & w["bull"]),
    ("disc15x(wick) & trend_up & below_val & sellers & bull", w["disc15x"] & w["trend_up"] & w["below_val"] & w["sellers"] & w["bull"]),
    ("disc5x(wick) & trend_up & sellers & bull", w["disc5x"] & w["trend_up"] & w["sellers"] & w["bull"]),
    ("disc15x(wick) & trend_up (zone touched)", w["disc15x"] & w["trend_up"]),
    ("sellers & bull (anywhere)", w["sellers"] & w["bull"]),
]:
    print(f"  {label:45s} {int(m.sum()):4d} bars, {w.loc[m, 'date'].nunique():3d} sessions")
# short side mirror for reference
m = w["retr15"].between(-0.886, -0.705) & (~w["trend_up"]) & (w["delta"] > 0) & (~w["bull"])
print(f"  {'short mirror: disc & trend_down & buyers & bear':45s} {int(m.sum()):4d} bars, {w.loc[m, 'date'].nunique():3d} sessions")
