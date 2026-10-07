"""Stage 3: robustness checks on the level-importance findings.

1. Parameter sensitivity (B, P, H, A, T) for the leading families.
2. Monthly stability for open / prior_vwap.
3. Approach-side splits.
4. Bounce/penetration magnitudes.
5. HVN persistence by rank + POC migration.
"""
import pathlib

import numpy as np
import pandas as pd

import analyze as az   # reuses sessions, level machinery; runs the primary pass on import

OUT = pathlib.Path(__file__).parent
TICK = az.TICK

sessions, dates, sym_by_date = az.sessions, az.dates, az.sym_by_date
prof_by_date = az.prof_by_date

# ---------------------------------------------------------------- 1. parameter grid
CONFIGS = [
    dict(t_tol=2, arm=10, bounce=8,  pen=8,  horizon=30),
    dict(t_tol=2, arm=10, bounce=12, pen=8,  horizon=30),   # primary
    dict(t_tol=2, arm=10, bounce=16, pen=12, horizon=45),
    dict(t_tol=2, arm=10, bounce=12, pen=8,  horizon=60),
    dict(t_tol=1, arm=16, bounce=12, pen=8,  horizon=30),
]
FAMS = ["open", "prior_vwap", "prior_val", "prior_close", "prior_high", "prior_low"]

# rebuild per-day level sets exactly as analyze.py did (static families only)
day_levels = {}
for i, d in enumerate(dates):
    s = sessions[d]
    prev = dates[i - 1] if i > 0 else None
    pair_ok = (prev is not None
               and (pd.Timestamp(d) - pd.Timestamp(prev)).days <= 5
               and sym_by_date.get(d) == sym_by_date.get(prev))
    fam_levels = {"open": s["open"]}
    if pair_ok:
        ps = sessions[prev]
        fam_levels.update({
            "prior_high": ps["high"], "prior_low": ps["low"],
            "prior_close": ps["close"], "prior_vwap": round(ps["vwap"] / TICK) * TICK,
        })
        pprof = prof_by_date.get(prev)
        if pprof is not None and not pprof.empty:
            poc_t, vah_t, val_t = az.value_area(pprof)
            fam_levels["prior_val"] = val_t * TICK
    day_levels[d] = fam_levels

rows = []
for ci, cfg in enumerate(CONFIGS):
    for d in dates:
        s = sessions[d]
        hi_a, lo_a = s["hi"], s["lo"]
        fams = day_levels[d]
        reals = list(fams.values())
        for fam in FAMS:
            if fam not in fams:
                continue
            L = fams[fam]
            start = 30 if fam == "open" else 0
            for pl, Ls in ((False, [L]),
                           (True, [L + o * TICK for o in az.PLACEBO_OFFS
                                   if not any(abs(L + o * TICK - r) < az.PLACEBO_EXCL * TICK
                                              for r in reals)])):
                for Lx in Ls:
                    evs = az.touch_events(hi_a, lo_a, Lx, start=start, **cfg)
                    if evs:
                        rows.append({"cfg": ci, "family": fam, "placebo": pl,
                                     "outcome": evs[0]["outcome"]})

grid = pd.DataFrame(rows)
print("=== parameter grid: first-touch respect %, real | placebo (n_real/n_placebo), z ===")
for fam in FAMS:
    line = f"{fam:12s}"
    for ci in range(len(CONFIGS)):
        g = grid[(grid["cfg"] == ci) & (grid["family"] == fam)]
        r = g[~g["placebo"]]
        p = g[g["placebo"]]
        rr = (r["outcome"] == "respect").mean()
        pr = (p["outcome"] == "respect").mean()
        z = az.two_prop_z((r["outcome"] == "respect").sum(), len(r),
                          (p["outcome"] == "respect").sum(), len(p))
        line += f" | {rr:.2f}/{pr:.2f} z={z:+.1f}"
    print(line)
print("configs:", *[f"[{i}] {c}" for i, c in enumerate(CONFIGS)], sep="\n")

# ---------------------------------------------------------------- 2. monthly stability
ev = pd.read_parquet(OUT / "events.parquet")
ev["month"] = pd.to_datetime(ev["date"].astype(str)).dt.to_period("M").astype(str)
print("\n=== monthly: first-touch respect real|placebo (n) ===")
for fam in ["open", "prior_vwap"]:
    e = ev[(ev["family"] == fam) & ev["first"]]
    out = []
    for m, g in e.groupby("month"):
        r = g[~g["placebo"]]
        p = g[g["placebo"]]
        out.append(f"{m} {(r['outcome']=='respect').mean():.2f}|{(p['outcome']=='respect').mean():.2f} ({len(r)}/{len(p)})")
    print(fam, ": ", "  ".join(out))

# ---------------------------------------------------------------- 3. side splits
print("\n=== approach side (first touches, real levels): respect% from above / below ===")
for fam in ["open", "prior_vwap", "prior_close", "session_high_retest", "session_low_retest"]:
    e = ev[(ev["family"] == fam) & ev["first"] & ~ev["placebo"]]
    ab = e[e["side"] == 1]
    be = e[e["side"] == -1]
    print(f"{fam:20s} above {(ab['outcome']=='respect').mean():.2f} (n {len(ab)})   "
          f"below {(be['outcome']=='respect').mean():.2f} (n {len(be)})")

# ---------------------------------------------------------------- 4. magnitudes
print("\n=== first-touch magnitudes (ticks): median bounce / median pen, real vs placebo ===")
for fam in ["open", "prior_vwap", "prior_val", "prior_close", "prior_high", "prior_low",
            "on_high", "on_low", "prior_poc", "morning_poc", "round25", "round100"]:
    e = ev[(ev["family"] == fam) & ev["first"]]
    r = e[~e["placebo"]]
    p = e[e["placebo"]]
    msg = (f"{fam:12s} real b {r['max_bounce'].median():5.1f} p {r['max_pen'].median():4.1f} (n {len(r)})")
    if len(p):
        msg += f"   plc b {p['max_bounce'].median():5.1f} p {p['max_pen'].median():4.1f} (n {len(p)})"
    print(msg)

# ---------------------------------------------------------------- 5. HVN by rank, POC migration
hvn = pd.read_parquet(OUT / "hvn.parquet")
print("\n=== HVN persistence by rank (D levels tested on D+1) ===")
for rank, g in hvn[~hvn["placebo"]].groupby("rank"):
    t = g[g["touched"]]
    print(f"rank {rank}: n {len(g)} touch {g['touched'].mean():.2f} "
          f"respect_ft {(t['first_outcome']=='respect').mean():.2f} (n_ft {len(t)}) "
          f"dens_med {g['density_ratio'].median():.2f}")
p = hvn[hvn["placebo"]]
tp = p[p["touched"]]
print(f"placebo: n {len(p)} touch {p['touched'].mean():.2f} "
      f"respect_ft {(tp['first_outcome']=='respect').mean():.2f} (n_ft {len(tp)}) "
      f"dens_med {p['density_ratio'].median():.2f}")

mig = []
for i in range(1, len(dates)):
    d0, d1 = dates[i - 1], dates[i]
    if sym_by_date.get(d0) != sym_by_date.get(d1):
        continue
    p0, p1 = prof_by_date.get(d0), prof_by_date.get(d1)
    if p0 is None or p1 is None or p0.empty or p1.empty:
        continue
    poc0 = az.value_area(p0)[0]
    poc1 = az.value_area(p1)[0]
    rng1 = sessions[d1]["high"] - sessions[d1]["low"]
    mig.append({"d": d1, "dist_ticks": abs(poc1 - poc0), "range_ticks": rng1 / TICK})
mg = pd.DataFrame(mig)
print(f"\nPOC(D+1) vs POC(D): median |move| {mg['dist_ticks'].median():.0f} ticks "
      f"(D+1 range median {mg['range_ticks'].median():.0f}t); "
      f"within 8t: {(mg['dist_ticks']<=8).mean():.2f}, within 16t: {(mg['dist_ticks']<=16).mean():.2f} (n {len(mg)})")

# open-retest gap context: does the open sit inside/outside prior value?
lev = pd.read_parquet(OUT / "levels.parquet")
open_ev = ev[(ev["family"] == "open") & ev["first"] & ~ev["placebo"]].copy()
open_ev["month"] = pd.to_datetime(open_ev["date"].astype(str)).dt.to_period("M").astype(str)
print("\nopen retest minute-of-day: median",
      open_ev["minute"].median(), " p25/p75 ",
      open_ev["minute"].quantile(0.25), open_ev["minute"].quantile(0.75))
