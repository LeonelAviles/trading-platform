"""Stage 6: does entering earlier improve risk/reward?

Bracket simulation per triggered event (X=100/200, touch-bar window):
  entry  = actual trigger price (from intrabar sim) + 1t slippage
  stop   = level - 10t (thesis: the level holds), stop fill -1t slippage
  target = entry + {8,12,16,24,32}t, limit fills on trade-through (+1t beyond)
  walk 1-min bars from the bar after the touch bar; same-bar stop+target = stop
  horizon 90 min, capped at 15:58 ET; unresolved -> exit at close
Benchmark: reversion entry — resting limit at the level, filled on the first
bar that trades 1t through it (platform trade-through rule), same brackets.
$29.50 round-trip commission. All real first-touch events, cheap = fill <= 4t.
"""
import pathlib

import numpy as np
import pandas as pd

OUT = pathlib.Path(__file__).parent
TICK = 0.25
ET = "America/New_York"
COMM_T = 29.50 / 12.50          # commission in ticks
HORIZON = 90                    # minutes

bars = pd.read_parquet(OUT / "bars.parquet")
bars["min_s"] = (bars["ts"] // 10**9 // 60 * 60).astype("int64")
t_et = pd.to_datetime(bars["min_s"], unit="s", utc=True).dt.tz_convert(ET)
ok = (t_et.dt.hour * 60 + t_et.dt.minute) < (15 * 60 + 58)
bars = bars[ok.to_numpy()]
HI = dict(zip(bars["min_s"], bars["high"]))
LO = dict(zip(bars["min_s"], bars["low"]))
CL = dict(zip(bars["min_s"], bars["close"]))

feat = pd.read_parquet(OUT / "event_features.parquet")
meta = feat[~feat["placebo"]].set_index("eid")[["level", "side", "ts0", "family"]]
ib = pd.read_parquet(OUT / "intrabar.parquet")
ib = ib[(ib["win"] == "bar") & ib["trigger"]].copy()

TRADEABLE = ["open", "prior_vwap", "session_low_retest", "session_high_retest"]
TARGETS = [8, 12, 16, 24, 32]


def bracket(ts0, side, entry, stop, target, start_offset=60):
    """P&L in ticks from entry (before commission). Walk 1-min bars."""
    for k in range(start_offset, HORIZON * 60 + 1, 60):
        ms = ts0 + k
        h, l = HI.get(ms), LO.get(ms)
        if h is None:
            c = CL.get(ms - 60)
            if k > HORIZON * 60 - 120 and c is not None:
                break
            continue
        if side == 1:
            if l <= stop:                       # stop first on ambiguity
                return (stop - entry) / TICK - 1
            if h >= target + TICK:              # trade-through fill
                return (target - entry) / TICK
        else:
            if h >= stop:
                return (stop - entry) / TICK * -1 - 1
            if l <= target - TICK:
                return (entry - target) / TICK * -1 * -1
        last = ms
    c = CL.get(last if "last" in dir() else ts0 + start_offset)
    # flatten at horizon (or data end) at last close
    for k in range(HORIZON * 60, 0, -60):
        c = CL.get(ts0 + k)
        if c is not None:
            break
    if c is None:
        return 0.0
    return (c - entry) / TICK * side


def simulate(events, entry_col, label):
    out = []
    for tgt in TARGETS:
        pl = []
        for _, r in events.iterrows():
            m = r    # every caller passes rows carrying level/side/ts0
            side = int(m["side"])
            level = float(m["level"])
            entry = float(r[entry_col]) + TICK * side          # 1t entry slippage
            stop = level - 10 * TICK * side
            target = entry + tgt * TICK * side
            pl.append(bracket(int(m["ts0"]), side, entry, stop, target))
        pl = np.array(pl) - COMM_T
        risk_med = np.median((events["cost_t"] if "cost_t" in events else 0) + 10) + 2
        out.append({
            "variant": label, "target_t": tgt, "n": len(pl),
            "win%": (pl > 0).mean() * 100,
            "avg_t": pl.mean(), "avg_$": pl.mean() * 12.5,
            "med_risk_t": risk_med,
        })
    return out


# ---------- confirmed entries: cheap vs expensive ----------
res = []
for X in (100, 200):
    g = ib[ib["X"] == X].copy()
    g["cost_t"] = g["cost"]
    mm = meta.loc[g["eid"]]
    g[["level", "side", "ts0", "family"]] = mm.to_numpy()
    g["entry_px"] = g["level"] + g["cost"] * TICK * g["side"]
    for scope, sg in (("all", g), ("tradeable", g[g["family"].isin(TRADEABLE)])):
        for name, sub in (("cheap<=4t", sg[sg["cost"] <= 4]), ("expensive>4t", sg[sg["cost"] > 4])):
            if len(sub) < 15:
                continue
            res += simulate(sub, "entry_px", f"X={X} {scope} {name}")

# ---------- reversion benchmark: limit at level, trade-through fill ----------
rev_rows = []
for eid, m in meta.iterrows():
    side, level, ts0 = int(m["side"]), float(m["level"]), int(m["ts0"])
    filled = None
    for k in range(0, 31 * 60, 60):          # fill window: 30 min from touch
        ms = ts0 + k
        h, l = HI.get(ms), LO.get(ms)
        if h is None:
            continue
        through = (l <= level - TICK) if side == 1 else (h >= level + TICK)
        if through:
            filled = k
            break
    if filled is None:
        continue
    rev_rows.append({"eid": eid, "family": m["family"], "level": level, "side": side,
                     "ts0": ts0 + filled, "entry_px": level, "cost_t": 0.0})
rev = pd.DataFrame(rev_rows)
for scope, sg in (("all", rev), ("tradeable", rev[rev["family"].isin(TRADEABLE)])):
    res += simulate(sg, "entry_px", f"limit-at-level {scope}")

df = pd.DataFrame(res)
piv = df.pivot_table(index="variant", columns="target_t", values="avg_$")
win = df.pivot_table(index="variant", columns="target_t", values="win%")
ns = df.groupby("variant")["n"].first()
pd.set_option("display.width", 220)
print("=== avg $ per trade after commission+slippage, by target (ticks from entry) ===")
print(piv.round(0).to_string())
print("\n=== win % ===")
print(win.round(0).to_string())
print("\nn per variant:\n", ns.to_string())
