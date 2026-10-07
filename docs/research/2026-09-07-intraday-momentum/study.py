"""Last-30-minute intraday momentum on ES (Gao et al. 2018; Baltussen, Da, Lammers, Martens 2021).

Hypothesis (pre-specified from the papers): the move from the prior RTH close to 15:30 ET predicts the
15:30 -> 16:00 move. Secondary (Gao): prior close -> 10:00 and 15:00 -> 15:30 also predict the last half hour.

Offline, read-only. Front-month ES 1-min bars from data/market joined to front_month.parquet (same as the
key-levels study). Roll day-pairs (symbol change) and short sessions excluded. No app state changes.
"""
from __future__ import annotations

import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
MARKET = ROOT / "data/market"
ET = "America/New_York"
TICK = 0.25
INST = yaml.safe_load((ROOT / "backend/config/instruments.yaml").read_text())
MULT = INST["roots"]["ES"]["multiplier"]
COMMISSION_RT = 2 * INST["roots"]["ES"]["commission_per_side"]          # $ per round trip
SLIP_PTS = 2 * INST["costs"]["slippage_ticks_market"] * TICK             # market in + market out
COST_PTS = SLIP_PTS + COMMISSION_RT / MULT                               # points per round trip
MIN_RTH_BARS = 370
SEED = 20260907

try:
    from scipy import stats as sps
except Exception:  # pragma: no cover
    sps = None

# ----------------------------------------------------------------------------- load
con = duckdb.connect()
con.execute("SET threads=2"); con.execute("SET memory_limit='1GB'"); con.execute("SET TimeZone='UTC'")
bars = con.execute(f"""
    SELECT b.symbol, b.ts, b.open, b.high, b.low, b.close, b.volume, b.delta
    FROM read_parquet('{MARKET}/bars_1m/root=ES/date=*/*.parquet', hive_partitioning=true) b
    JOIN read_parquet('{MARKET}/front_month.parquet') fm
      ON fm.root = b.root AND fm.date = b.date AND fm.symbol = b.symbol
    ORDER BY b.ts""").df()
con.close()
local = pd.to_datetime(bars["ts"].astype("int64"), unit="ns", utc=True).dt.tz_convert(ET)
bars["et_date"] = local.dt.date
bars["et_min"] = local.dt.hour * 60 + local.dt.minute

# ----------------------------------------------------------------------------- sessions
def close_at(rth: pd.DataFrame, minute: int) -> float:
    """Close of the last bar whose minute <= `minute` (bar minute = bar open time)."""
    g = rth[rth["et_min"] <= minute]
    return float(g["close"].iloc[-1])

def open_at(rth: pd.DataFrame, minute: int) -> float:
    g = rth[rth["et_min"] >= minute]
    return float(g["open"].iloc[0])

rows, excluded = [], []
prev = None
for d, g in bars.groupby("et_date", sort=True):
    rth = g[(g["et_min"] >= 570) & (g["et_min"] < 960)].sort_values("ts")
    if len(rth) == 0:
        continue                                   # no RTH at all (Sunday evening, holiday): keep prior session
    if len(rth) < MIN_RTH_BARS or rth["et_min"].max() < 958 or rth["et_min"].min() > 570:
        excluded.append({"day": str(d), "reason": "short/incomplete RTH", "bars": len(rth)})
        prev = None                                # half day: do not pair across it (as in the key-levels study)
        continue
    sym = rth["symbol"].iloc[0]
    rec = dict(day=str(d), symbol=sym,
               open=float(rth["open"].iloc[0]), close=close_at(rth, 959),
               high=float(rth["high"].max()), low=float(rth["low"].min()),
               p1000=close_at(rth, 599), p1500=close_at(rth, 899), p1530=close_at(rth, 929),
               entry=open_at(rth, 930),                # market fill at the 15:30 bar open
               exit1558=open_at(rth, 958),             # platform flattenAt 15:58
               volume=float(rth["volume"].sum()),
               vol_last30=float(rth[rth["et_min"] >= 930]["volume"].sum()))
    rec["range"] = rec["high"] - rec["low"]
    if prev is None:
        rec["prior_close"] = np.nan; rec["pair_ok"] = False; rec["pair_reason"] = "no prior session"
    elif prev["symbol"] != sym:
        rec["prior_close"] = np.nan; rec["pair_ok"] = False; rec["pair_reason"] = "roll"
    elif (pd.Timestamp(d) - pd.Timestamp(prev["day"])).days > 5:
        rec["prior_close"] = np.nan; rec["pair_ok"] = False; rec["pair_reason"] = "gap > 5 days"
    else:
        rec["prior_close"] = prev["close"]; rec["pair_ok"] = True; rec["pair_reason"] = ""
    rows.append(rec); prev = rec

S = pd.DataFrame(rows)
S["mean_range20"] = S["range"].shift(1).rolling(20, min_periods=10).mean()   # trailing, no lookahead
S["vol_med20"] = S["volume"].shift(1).rolling(20, min_periods=10).median()
P = S[S["pair_ok"]].copy()
P["r_early"] = P["p1530"] - P["prior_close"]      # prior close -> 15:30 (Baltussen et al.)
P["r_first"] = P["p1000"] - P["prior_close"]      # prior close -> 10:00 (Gao et al.)
P["r_12th"] = P["p1530"] - P["p1500"]             # 15:00 -> 15:30 (Gao et al.)
P["r_last"] = P["close"] - P["p1530"]             # 15:30 -> 16:00, the target
P["r_trade"] = P["exit1558"] - P["entry"]         # what the platform would book: 15:30 open -> 15:58 open
P["sgn"] = np.sign(P["r_early"])
P["month"] = P["day"].str[:7]
P["phase"] = np.where(P["day"] < "2026-05-01", "dev(Dec-Apr)", "diag(May-Jul)")
P.to_csv(HERE / "sessions.csv", index=False)
pd.DataFrame(excluded).to_csv(HERE / "excluded_sessions.csv", index=False)

# ----------------------------------------------------------------------------- stats helpers
def ols(x, y):
    x = np.asarray(x, float); y = np.asarray(y, float)
    X = np.column_stack([np.ones_like(x), x])
    beta, res, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    s2 = (resid @ resid) / (len(y) - 2)
    cov = s2 * np.linalg.inv(X.T @ X)
    t = beta[1] / np.sqrt(cov[1, 1])
    r2 = 1 - (resid @ resid) / ((y - y.mean()) @ (y - y.mean()))
    return dict(n=int(len(y)), slope=float(beta[1]), t=float(t), r2=float(r2))

def sign_test(x, y):
    m = (np.sign(x) != 0) & (np.sign(y) != 0)
    k = int((np.sign(x[m]) == np.sign(y[m])).sum()); n = int(m.sum())
    if sps:
        p = float(sps.binomtest(k, n, 0.5).pvalue)
    else:
        from math import erf, sqrt
        z = (k - n / 2) / sqrt(n / 4) if n else 0.0
        p = float(2 * (1 - 0.5 * (1 + erf(abs(z) / sqrt(2)))))
    return dict(n=n, agree=k, rate=k / n if n else float("nan"), p=p)

def boot_mean_p(v, B=20000, rng=np.random.default_rng(SEED)):
    v = np.asarray(v, float)
    if len(v) < 5: return float("nan")
    idx = rng.integers(0, len(v), (B, len(v)))
    means = v[idx].mean(axis=1)
    return float((means <= 0).mean())                 # one-sided: P(mean <= 0)

def summarize(pnl_pts: np.ndarray) -> dict:
    """Per-trade points -> trade statistics (1 contract)."""
    v = np.asarray(pnl_pts, float)
    if len(v) == 0: return dict(n=0)
    wins = v[v > 0]; losses = v[v < 0]
    eq = np.cumsum(v) * MULT
    dd = float((np.maximum.accumulate(eq) - eq).max()) if len(eq) else 0.0
    return dict(n=int(len(v)), mean_pts=float(v.mean()), total_usd=float(v.sum() * MULT),
                win_rate=float((v > 0).mean()),
                pf=float(wins.sum() / -losses.sum()) if len(losses) and losses.sum() < 0 else float("inf"),
                t=float(v.mean() / v.std(ddof=1) * np.sqrt(len(v))) if len(v) > 2 else float("nan"),
                boot_p=boot_mean_p(v), max_dd_usd=dd)

# ----------------------------------------------------------------------------- predictability
out = {"data": dict(sessions=int(len(S)), pairs=int(len(P)), first=P["day"].min(), last=P["day"].max(),
                    excluded=len(excluded), pair_exclusions=S.loc[~S["pair_ok"], "pair_reason"].value_counts().to_dict(),
                    cost_pts_per_round_trip=COST_PTS, commission_rt_usd=COMMISSION_RT, slippage_pts=SLIP_PTS)}
pred = {}
for name in ["r_early", "r_first", "r_12th"]:
    pred[name] = dict(ols=ols(P[name], P["r_last"]), sign=sign_test(P[name].to_numpy(), P["r_last"].to_numpy()))
# control, non-overlapping: does prior close -> 15:00 predict the 15:00 -> 15:30 leg?
# (NOT r_early vs r_12th: r_early contains r_12th, so that regression is mechanically positive.)
P["r_to1500"] = P["p1500"] - P["prior_close"]
pred["control_r_to1500_vs_r_12th"] = dict(ols=ols(P["r_to1500"], P["r_12th"]),
                                         sign=sign_test(P["r_to1500"].to_numpy(), P["r_12th"].to_numpy()))
out["predictability"] = pred

# signed last-30 return (points earned by following the early sign), bucketed
P["signed_last"] = P["sgn"] * P["r_last"]
P["abs_early_norm"] = P["r_early"].abs() / P["mean_range20"]
buckets = {}
Q = P.dropna(subset=["abs_early_norm"])
Q = Q.assign(tercile=pd.qcut(Q["abs_early_norm"], 3, labels=["small", "mid", "large"]))
for lab, g in Q.groupby("tercile", observed=True):
    buckets[str(lab)] = dict(n=int(len(g)), mean_signed_pts=float(g["signed_last"].mean()),
                             win=float((g["signed_last"] > 0).mean()), boot_p=boot_mean_p(g["signed_last"]))
for lab, g in P.groupby(np.where(P["sgn"] > 0, "up_day", "down_day")):
    buckets[lab] = dict(n=int(len(g)), mean_signed_pts=float(g["signed_last"].mean()),
                        win=float((g["signed_last"] > 0).mean()), boot_p=boot_mean_p(g["signed_last"]))
out["signed_last30_buckets"] = buckets

# ----------------------------------------------------------------------------- strategy variants (pre-listed)
def variant(mask, label):
    g = P[mask & (P["sgn"] != 0)]
    gross = (g["sgn"] * g["r_trade"]).to_numpy()
    res = dict(label=label, gross=summarize(gross), net=summarize(gross - COST_PTS))
    res["by_phase"] = {ph: summarize((h["sgn"] * h["r_trade"]).to_numpy() - COST_PTS)
                       for ph, h in g.groupby("phase")}
    res["by_month_net_usd"] = {m: float(((h["sgn"] * h["r_trade"]) - COST_PTS).sum() * MULT)
                               for m, h in g.groupby("month")}
    return res

has_norm = P["abs_early_norm"].notna()
has_vol = P["vol_med20"].notna()
variants = [
    variant(pd.Series(True, index=P.index), "V0 unconditional: follow sign(prior close -> 15:30)"),
    variant(has_norm & (P["abs_early_norm"] >= 0.25), "V1 |move| >= 0.25 x trailing-20 mean range"),
    variant(has_norm & (P["abs_early_norm"] >= 0.50), "V2 |move| >= 0.50 x trailing-20 mean range"),
    variant(has_norm & (P["abs_early_norm"] >= 0.75), "V3 |move| >= 0.75 x trailing-20 mean range"),
    variant(has_vol & (P["volume"] > P["vol_med20"]), "V4 volume > trailing-20 median (full-day volume; not causal at 15:30)"),
    variant(has_norm & has_vol & (P["abs_early_norm"] >= 0.5) & (P["volume"] > P["vol_med20"]), "V5 V2 and V4"),
    variant((P["sgn"] > 0), "V6 up days only (long)"),
    variant((P["sgn"] < 0), "V7 down days only (short)"),
    variant(pd.Series(True, index=P.index) & (np.sign(P["r_12th"]) == P["sgn"]), "V8 15:00->15:30 leg agrees with early sign"),
]
out["variants"] = variants

# permutation control for V0: shuffle the early sign across days
rng = np.random.default_rng(SEED)
real = float((P["sgn"] * P["r_trade"]).mean())
perm = np.array([(rng.permutation(P["sgn"].to_numpy()) * P["r_trade"].to_numpy()).mean() for _ in range(20000)])
out["permutation_V0"] = dict(real_mean_pts=real, perm_p_one_sided=float((perm >= real).mean()))

# unconditional drift of the legs (explains long/short asymmetry)
out["baseline_pts"] = {k: dict(mean=float(P[k].mean()), t=float(P[k].mean() / P[k].std(ddof=1) * np.sqrt(len(P))),
                               pos_rate=float((P[k] > 0).mean())) for k in ["r_last", "r_12th", "r_trade"]}

# half-hour scan: at boundary T, does (P_T - prior close) predict the next 30 minutes?  Diagnostic, 11 tests.
RTH = bars[(bars["et_min"] >= 570) & (bars["et_min"] < 960)]
by_day = {str(d): g for d, g in RTH.groupby("et_date")}
scan = []
for m in range(600, 930 + 1, 30):                       # 10:00 .. 15:30
    x = np.array([close_at(by_day[r.day], m - 1) - r.prior_close for r in P.itertuples()])
    y = np.array([close_at(by_day[r.day], min(m + 29, 959)) - close_at(by_day[r.day], m - 1) for r in P.itertuples()])
    o = ols(x, y); sg = sign_test(x, y)
    scan.append(dict(boundary=f"{m//60:02d}:{m%60:02d}", slope=o["slope"], t=o["t"], sign_rate=sg["rate"], n=o["n"]))
out["halfhour_scan"] = scan

# exploratory (post-hoc, suggested by the control row): the 15:00 leg
pdays = set(P["day"])
P["entry1500"] = P["day"].map({d: open_at(g, 900) for d, g in by_day.items() if d in pdays})
P["exit1530"] = P["day"].map({d: open_at(g, 930) for d, g in by_day.items() if d in pdays})
P["sgn1500"] = np.sign(P["p1500"] - P["prior_close"])
P["sgn12th"] = np.sign(P["r_12th"])
def xvariant(sgn, entry, exit_, mask, label):
    g = P[mask & (sgn != 0)]
    gross = (sgn[g.index] * (g[exit_] - g[entry])).to_numpy()
    res = dict(label=label, gross=summarize(gross), net=summarize(gross - COST_PTS))
    res["by_phase"] = {ph: summarize((sgn[h.index] * (h[exit_] - h[entry])).to_numpy() - COST_PTS) for ph, h in g.groupby("phase")}
    res["by_month_net_usd"] = {mo: float(((sgn[h.index] * (h[exit_] - h[entry])) - COST_PTS).sum() * MULT) for mo, h in g.groupby("month")}
    return res
allm = pd.Series(True, index=P.index)
P["abs1500_norm"] = (P["p1500"] - P["prior_close"]).abs() / P["mean_range20"]
out["exploratory"] = [
    xvariant(P["sgn1500"], "entry1500", "exit1530", allm, "X1 POST-HOC: 15:00 open -> 15:30 open, sign(prior close -> 15:00)"),
    xvariant(P["sgn1500"], "entry1500", "exit1558", allm, "X2 POST-HOC: 15:00 open -> 15:58 open, sign(prior close -> 15:00)"),
    xvariant(P["sgn1500"], "entry1500", "exit1530", P["abs1500_norm"].notna() & (P["abs1500_norm"] >= 0.5), "X3 POST-HOC: X1 with |move| >= 0.5 x mean range"),
    xvariant(P["sgn12th"], "entry", "exit1558", allm, "X4 (Gao 2nd predictor): 15:30 -> 15:58, sign(15:00 -> 15:30)"),
]
rng = np.random.default_rng(SEED + 1)
realx = float((P["sgn1500"] * (P["exit1530"] - P["entry1500"])).mean())
permx = np.array([(rng.permutation(P["sgn1500"].to_numpy()) * (P["exit1530"] - P["entry1500"]).to_numpy()).mean() for _ in range(20000)])
out["permutation_X1"] = dict(real_mean_pts=realx, perm_p_one_sided=float((permx >= realx).mean()))
P.to_csv(HERE / "sessions.csv", index=False)

(HERE / "results.json").write_text(json.dumps(out, indent=1, default=str))

# ----------------------------------------------------------------------------- print
d = out["data"]
print(f"sessions {d['sessions']}  day-pairs {d['pairs']}  {d['first']} -> {d['last']}  pair exclusions {d['pair_exclusions']}")
print(f"cost per round trip: {COST_PTS:.3f} pts (${COST_PTS*MULT:.2f})  = slippage {SLIP_PTS} pts + commission ${COMMISSION_RT}")
print("\nPREDICTABILITY of 15:30->16:00 (points on points)")
for k, v in pred.items():
    o, s = v["ols"], v["sign"]
    print(f"  {k:28s} n={o['n']:3d} slope={o['slope']:+.3f} t={o['t']:+.2f} R2={o['r2']:.3f} | sign agree {s['agree']}/{s['n']} = {s['rate']:.1%} p={s['p']:.3f}")
print("\nSIGNED last-30 move (pts) by bucket")
for k, v in buckets.items():
    print(f"  {k:10s} n={v['n']:3d} mean={v['mean_signed_pts']:+.2f} win={v['win']:.1%} boot_p={v['boot_p']:.3f}")
print(f"\nSTRATEGY: enter 15:30 open in early-move direction, flat 15:58 (platform default). Net = after {COST_PTS:.2f} pts.")
for v in variants:
    g, n = v["gross"], v["net"]
    if g.get("n", 0) == 0: print(f"  {v['label']}: no trades"); continue
    print(f"  {v['label']}")
    print(f"     gross n={g['n']:3d} mean={g['mean_pts']:+.2f}pt win={g['win_rate']:.1%} PF={g['pf']:.2f} t={g['t']:+.2f} | "
          f"NET mean={n['mean_pts']:+.2f}pt total=${n['total_usd']:+,.0f} PF={n['pf']:.2f} t={n['t']:+.2f} boot_p={n['boot_p']:.3f} maxDD=${n['max_dd_usd']:,.0f}")
    ph = v["by_phase"]
    print("     net by phase: " + "  ".join(f"{k}: n={x['n']} mean={x['mean_pts']:+.2f} PF={x['pf']:.2f}" for k, x in ph.items() if x.get('n')))
    print("     net by month $: " + "  ".join(f"{m}:{x:+,.0f}" for m, x in v["by_month_net_usd"].items()))
print("\nBASELINE drift (pts): " + "  ".join(f"{k}: mean={v['mean']:+.2f} t={v['t']:+.2f} pos={v['pos_rate']:.0%}" for k, v in out["baseline_pts"].items()))
print("\nHALF-HOUR SCAN: (P_T - prior close) -> next 30 min   [diagnostic, 11 tests]")
for r in out["halfhour_scan"]:
    print(f"  {r['boundary']}  slope={r['slope']:+.3f}  t={r['t']:+.2f}  sign agree={r['sign_rate']:.1%}  n={r['n']}")
print("\nEXPLORATORY (post-hoc unless marked; same data, no holdout)")
for v in out["exploratory"]:
    g, n = v["gross"], v["net"]
    print(f"  {v['label']}")
    print(f"     gross n={g['n']:3d} mean={g['mean_pts']:+.2f}pt win={g['win_rate']:.1%} PF={g['pf']:.2f} t={g['t']:+.2f} | "
          f"NET mean={n['mean_pts']:+.2f}pt total=${n['total_usd']:+,.0f} PF={n['pf']:.2f} t={n['t']:+.2f} boot_p={n['boot_p']:.3f} maxDD=${n['max_dd_usd']:,.0f}")
    print("     net by phase: " + "  ".join(f"{k}: n={x['n']} mean={x['mean_pts']:+.2f} PF={x['pf']:.2f}" for k, x in v["by_phase"].items() if x.get('n')))
    print("     net by month $: " + "  ".join(f"{m}:{x:+,.0f}" for m, x in v["by_month_net_usd"].items()))
px = out["permutation_X1"]
print(f"  Permutation control for X1 (gross mean {px['real_mean_pts']:+.3f} pts): one-sided p = {px['perm_p_one_sided']:.3f}")
pv = out["permutation_V0"]
print(f"\nPermutation control (V0 gross mean {pv['real_mean_pts']:+.3f} pts): one-sided p = {pv['perm_p_one_sided']:.3f}")
