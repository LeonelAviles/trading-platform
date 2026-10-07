"""Stage 2: which intraday reference levels matter for ES.

Reads bars.parquet + vap30.parquet (front-month, from extract.py).
Writes events.parquet, family_summary.csv, hvn_summary.csv, misc JSON.
"""
import json
import pathlib

import numpy as np
import pandas as pd

OUT = pathlib.Path(__file__).parent
TICK = 0.25
ET = "America/New_York"

# --- event machinery parameters (primary) ---
T = 2      # touch tolerance, ticks
A = 10     # must be this far away before a touch arms
B = 12     # bounce ticks => respect
P = 8      # penetration ticks tolerated before break
H = 30     # outcome window, minutes

MIN_RTH_BARS = 370
PLACEBO_OFFS = [-41, -23, 23, 41]     # ticks
PLACEBO_EXCL = 8                      # ticks: placebo dropped if near a real level

# ----------------------------------------------------------------- load
bars = pd.read_parquet(OUT / "bars.parquet")
idx = pd.to_datetime(bars["ts"].astype("int64"), unit="ns", utc=True).dt.tz_convert(ET)
bars = bars.set_index(idx.rename("t")).sort_index()
bars["et_date"] = bars.index.date
bars["et_min"] = bars.index.hour * 60 + bars.index.minute

vap = pd.read_parquet(OUT / "vap30.parquet")
bt = pd.to_datetime(vap["bucket"].astype("int64"), unit="ns", utc=True).dt.tz_convert(ET)
vap["et_date"] = bt.dt.date
vap["et_min"] = bt.dt.hour * 60 + bt.dt.minute
vap["tickp"] = (vap["price"] / TICK).round().astype("int64")

RTH0, RTH1, NOON = 9 * 60 + 30, 16 * 60, 12 * 60

# front-month symbol per UTC date, for roll exclusion
fm = pd.read_parquet("/Users/leonelaviles/Desktop/trading-platform/data/market/front_month.parquet")
fm = fm[fm["root"] == "ES"]
sym_by_date = dict(zip(pd.to_datetime(fm["date"]).dt.date, fm["symbol"]))

# ----------------------------------------------------------------- sessions
sessions = {}
for d, g in bars.groupby("et_date", sort=True):
    rth = g[(g["et_min"] >= RTH0) & (g["et_min"] < RTH1)]
    if len(rth) < MIN_RTH_BARS:
        continue
    typ = (rth["high"] + rth["low"] + rth["close"]) / 3
    v = rth["volume"].to_numpy(float)
    sessions[d] = {
        "rth": rth,
        "hi": rth["high"].to_numpy(),
        "lo": rth["low"].to_numpy(),
        "mins": rth["et_min"].to_numpy(),
        "open": float(rth["open"].iloc[0]),
        "high": float(rth["high"].max()),
        "low": float(rth["low"].min()),
        "close": float(rth["close"].iloc[-1]),
        "vwap": float((typ.to_numpy() * v).sum() / v.sum()),
        "volume": float(v.sum()),
    }

dates = sorted(sessions)

# overnight 18:00 (prev calendar day) -> 09:30 (session date), from all bars
bars_on = bars.copy()
for i, d in enumerate(dates):
    d_ts = pd.Timestamp(d, tz=ET)
    on_start = d_ts - pd.Timedelta(hours=6)          # 18:00 previous calendar day
    on_end = d_ts + pd.Timedelta(minutes=RTH0)
    seg = bars_on.loc[(bars_on.index >= on_start) & (bars_on.index < on_end)]
    if len(seg) > 100:
        sessions[d]["on_high"] = float(seg["high"].max())
        sessions[d]["on_low"] = float(seg["low"].min())

# RTH / morning volume profiles per session (tick-indexed Series)
vap_rth = vap[(vap["et_min"] >= RTH0) & (vap["et_min"] < RTH1)]
prof_by_date = {d: g.groupby("tickp")["volume"].sum() for d, g in vap_rth.groupby("et_date") if d in sessions}
vap_am = vap_rth[vap_rth["et_min"] < NOON]
vap_pm = vap_rth[vap_rth["et_min"] >= NOON]
prof_am = {d: g.groupby("tickp")["volume"].sum() for d, g in vap_am.groupby("et_date") if d in sessions}
prof_pm = {d: g.groupby("tickp")["volume"].sum() for d, g in vap_pm.groupby("et_date") if d in sessions}


def value_area(prof: pd.Series, fraction=0.70):
    """POC/VAH/VAL by market-profile expansion. prof indexed by tick int."""
    if prof.empty:
        return None, None, None
    full = prof.reindex(range(prof.index.min(), prof.index.max() + 1), fill_value=0)
    vals = full.to_numpy(float)
    total = vals.sum()
    i = int(vals.argmax())
    lo = hi = i
    acc = vals[i]
    while acc < fraction * total and (lo > 0 or hi < len(vals) - 1):
        up = vals[hi + 1] if hi < len(vals) - 1 else -1
        dn = vals[lo - 1] if lo > 0 else -1
        if up >= dn:
            hi += 1
            acc += up
        else:
            lo -= 1
            acc += dn
    base = full.index[0]
    return base + i, base + hi, base + lo   # tick ints


def hvn_peaks(prof: pd.Series, max_n=3, min_sep=16, min_frac=0.25, smooth=7):
    """Top-N smoothed local maxima (tick ints), tallest first. [0] ~ POC."""
    if prof.empty:
        return []
    full = prof.reindex(range(prof.index.min(), prof.index.max() + 1), fill_value=0)
    s = full.rolling(smooth, center=True, min_periods=1).mean()
    v = s.to_numpy(float)
    peak_floor = min_frac * v.max()
    cand = []
    for i in range(len(v)):
        w0, w1 = max(0, i - min_sep // 2), min(len(v), i + min_sep // 2 + 1)
        if v[i] >= peak_floor and v[i] == v[w0:w1].max():
            cand.append((v[i], i))
    cand.sort(reverse=True)
    picked = []
    for h, i in cand:
        if all(abs(i - j) >= min_sep for j in picked):
            picked.append(i)
        if len(picked) == max_n:
            break
    base = full.index[0]
    return [base + i for i in picked]


def density_ratio(prof: pd.Series, level_price: float, band=2):
    """Mean vol/tick within +-band ticks of level / mean vol/tick across range."""
    if prof.empty:
        return None
    lt = int(round(level_price / TICK))
    lo_t, hi_t = prof.index.min(), prof.index.max()
    if lt < lo_t or lt > hi_t:
        return None
    n_range = hi_t - lo_t + 1
    band_vol = prof.reindex(range(lt - band, lt + band + 1), fill_value=0).sum()
    avg = prof.sum() / n_range
    return float((band_vol / (2 * band + 1)) / avg) if avg > 0 else None


# ----------------------------------------------------------------- event machinery
def touch_events(hi, lo, L, start=0, t_tol=T, arm=A, bounce=B, pen=P, horizon=H):
    """All armed touches of static level L on minute arrays hi/lo from `start`.

    Returns list of dicts: t0, side (+1 approached from above), outcome,
    max_bounce, max_pen (ticks, within horizon)."""
    events = []
    tol = t_tol * TICK
    away_side = 0          # +1 above, -1 below, 0 unknown/touching
    max_away = 0.0         # ticks, extreme distance since last event / side flip
    n = len(hi)
    t = start
    while t < n:
        if lo[t] > L + tol:
            side = 1
            dist = (lo[t] - L) / TICK
        elif hi[t] < L - tol:
            side = -1
            dist = (L - hi[t]) / TICK
        else:
            side, dist = 0, 0.0
        if side != 0:
            if side != away_side:
                away_side, max_away = side, dist
            else:
                max_away = max(max_away, dist)
            t += 1
            continue
        # touching
        if away_side != 0 and max_away >= arm:
            ev = _outcome(hi, lo, L, t, away_side, bounce, pen, horizon)
            events.append(ev)
            # skip past the outcome window before re-arming
            t = ev["t_end"]
            away_side, max_away = 0, 0.0
            continue
        t += 1
    return events


def _outcome(hi, lo, L, t0, side, bounce, pen, horizon):
    """side=+1: approached from above (respect = up-bounce, break = down)."""
    outcome = "flat"
    max_b = 0.0
    max_p = max(0.0, (L - lo[t0]) / TICK if side == 1 else (hi[t0] - L) / TICK)
    t_end = min(t0 + horizon, len(hi) - 1)
    if max_p > pen:
        return {"t0": t0, "side": side, "outcome": "break", "max_bounce": 0.0,
                "max_pen": max_p, "t_end": t0 + 1}
    for t in range(t0 + 1, t_end + 1):
        b = (hi[t] - L) / TICK if side == 1 else (L - lo[t]) / TICK
        p = (L - lo[t]) / TICK if side == 1 else (hi[t] - L) / TICK
        max_b, max_p = max(max_b, b), max(max_p, p)
        hit_b, hit_p = b >= bounce, p > pen
        if hit_p:                      # ambiguous same-bar counts as break
            outcome = "break"
            t_end = t
            break
        if hit_b:
            outcome = "respect"
            t_end = t
            break
    return {"t0": t0, "side": side, "outcome": outcome, "max_bounce": max_b,
            "max_pen": max_p, "t_end": max(t_end, t0 + 1)}


def extreme_retests(hi, lo, which, arm=A, t_tol=T, bounce=B, pen=P, horizon=H):
    """Retests of the developing session high (which=+1) or low (-1)."""
    events = []
    tol = t_tol * TICK
    n = len(hi)
    ext = hi[0] if which == 1 else lo[0]
    max_away = 0.0
    t = 1
    while t < n:
        if which == 1:
            if hi[t] > ext:                     # new high: reset
                ext, max_away = hi[t], 0.0
                t += 1
                continue
            dist = (ext - hi[t]) / TICK
            touching = hi[t] >= ext - tol
        else:
            if lo[t] < ext:
                ext, max_away = lo[t], 0.0
                t += 1
                continue
            dist = (lo[t] - ext) / TICK
            touching = lo[t] <= ext + tol
        if touching and max_away >= arm:
            ev = _outcome(hi, lo, ext, t, -which, bounce, pen, horizon)
            ev["level"] = ext
            events.append(ev)
            t = ev["t_end"]
            # a break made a new extreme; retreat resets arming either way
            j = min(ev["t_end"], n - 1)
            if which == 1 and hi[j] > ext:
                ext = hi[j]
            if which == -1 and lo[j] < ext:
                ext = lo[j]
            max_away = 0.0
            continue
        max_away = max(max_away, dist)
        t += 1
    return events


# ----------------------------------------------------------------- level sets
rows = []          # event rows
lev_rows = []      # one row per (date, family, level) incl. untouched
pair_meta = []

for i, d in enumerate(dates):
    s = sessions[d]
    prev = dates[i - 1] if i > 0 else None
    pair_ok = (
        prev is not None
        and (pd.Timestamp(d) - pd.Timestamp(prev)).days <= 5
        and sym_by_date.get(d) == sym_by_date.get(prev)
    )
    levels = {"open": [s["open"]]}
    if "on_high" in s:
        levels["on_high"] = [s["on_high"]]
        levels["on_low"] = [s["on_low"]]
    if pair_ok:
        ps = sessions[prev]
        pprof = prof_by_date.get(prev, pd.Series(dtype=float))
        poc_t, vah_t, val_t = value_area(pprof)
        peaks = hvn_peaks(pprof)
        levels.update({
            "prior_high": [ps["high"]], "prior_low": [ps["low"]],
            "prior_close": [ps["close"]], "prior_vwap": [round(ps["vwap"] / TICK) * TICK],
        })
        if poc_t is not None:
            levels["prior_poc"] = [poc_t * TICK]
            levels["prior_vah"] = [vah_t * TICK]
            levels["prior_val"] = [val_t * TICK]
        if len(peaks) > 1:
            levels["prior_hvn2"] = [p * TICK for p in peaks[1:]]
    # round numbers known at open: inside ON range +-40 ticks
    if "on_high" in s:
        r_lo = s["on_low"] - 40 * TICK
        r_hi = s["on_high"] + 40 * TICK
        grid = np.arange(np.ceil(r_lo / 25) * 25, r_hi + 1e-9, 25.0)
        levels["round100"] = [g for g in grid if g % 100 == 0]
        levels["round25"] = [g for g in grid if g % 100 != 0]
    # morning POC, tested in the afternoon only
    am = prof_am.get(d, pd.Series(dtype=float))
    am_poc_t = value_area(am)[0] if not am.empty else None

    hi_a, lo_a, mins = s["hi"], s["lo"], s["mins"]
    noon_idx = int(np.searchsorted(mins, NOON))
    prof_d = prof_by_date.get(d, pd.Series(dtype=float))
    pm_d = prof_pm.get(d, pd.Series(dtype=float))

    real_levels_flat = [L for lst in levels.values() for L in lst]
    if am_poc_t is not None:
        real_levels_flat.append(am_poc_t * TICK)

    def emit(fam, L, start, placebo, profile, prof_tag):
        evs = touch_events(hi_a, lo_a, L, start=start)
        dr = density_ratio(profile, L)
        lev_rows.append({
            "date": d, "family": fam, "level": L, "placebo": placebo,
            "touched": bool(evs), "n_events": len(evs),
            "density_ratio": dr, "prof": prof_tag,
            "dist_open_ticks": abs(L - s["open"]) / TICK,
        })
        for k, ev in enumerate(evs):
            rows.append({
                "date": d, "family": fam, "level": L, "placebo": placebo,
                "first": k == 0, "minute": int(mins[ev["t0"]]),
                "side": ev["side"], "outcome": ev["outcome"],
                "max_bounce": ev["max_bounce"], "max_pen": ev["max_pen"],
            })

    for fam, lst in levels.items():
        start = 30 if fam == "open" else 0   # open: skip the opening drive itself
        for L in lst:
            emit(fam, L, start, False, prof_d, "rth")
            if fam.startswith("round"):
                continue                      # rounds: grid is its own control
            for off in PLACEBO_OFFS:
                Lp = L + off * TICK
                if any(abs(Lp - r) < PLACEBO_EXCL * TICK for r in real_levels_flat):
                    continue
                emit(fam, Lp, start, True, prof_d, "rth")

    if am_poc_t is not None:
        L = am_poc_t * TICK
        emit("morning_poc", L, noon_idx, False, pm_d, "pm")
        for off in PLACEBO_OFFS:
            Lp = L + off * TICK
            if any(abs(Lp - r) < PLACEBO_EXCL * TICK for r in real_levels_flat):
                continue
            emit("morning_poc", Lp, noon_idx, True, pm_d, "pm")

    # developing extremes (no placebo possible; report descriptively)
    for which, fam in ((1, "session_high_retest"), (-1, "session_low_retest")):
        for k, ev in enumerate(extreme_retests(hi_a, lo_a, which)):
            rows.append({
                "date": d, "family": fam, "level": ev["level"], "placebo": False,
                "first": k == 0, "minute": int(mins[ev["t0"]]),
                "side": ev["side"], "outcome": ev["outcome"],
                "max_bounce": ev["max_bounce"], "max_pen": ev["max_pen"],
            })

    pair_meta.append({"date": d, "pair_ok": pair_ok})

events = pd.DataFrame(rows)
lev = pd.DataFrame(lev_rows)
events.to_parquet(OUT / "events.parquet")
lev.to_parquet(OUT / "levels.parquet")

# ----------------------------------------------------------------- summaries
def wilson(k, n, z=1.96):
    if n == 0:
        return (np.nan, np.nan)
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    hw = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (c - hw, c + hw)


def two_prop_z(k1, n1, k2, n2):
    if min(n1, n2) == 0:
        return np.nan
    p = (k1 + k2) / (n1 + n2)
    se = np.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    return ((k1 / n1) - (k2 / n2)) / se if se > 0 else np.nan


summary = []
for fam in sorted(set(events["family"]) | set(lev["family"] if not lev.empty else [])):
    ev_f = events[events["family"] == fam]
    lv_f = lev[lev["family"] == fam] if not lev.empty else pd.DataFrame()
    for pl in (False, True):
        e = ev_f[ev_f["placebo"] == pl]
        l = lv_f[lv_f["placebo"] == pl] if not lv_f.empty else pd.DataFrame()
        fe = e[e["first"]]
        n_days = l["date"].nunique() if not l.empty else e["date"].nunique()
        n_lv = len(l) if not l.empty else np.nan
        touch_rate = l["touched"].mean() if not l.empty else np.nan
        resp = (fe["outcome"] == "respect").sum()
        brk = (fe["outcome"] == "break").sum()
        n_ft = len(fe)
        resp_all = (e["outcome"] == "respect").sum()
        n_all = len(e)
        dens = l.loc[l["density_ratio"].notna(), "density_ratio"] if not l.empty else pd.Series(dtype=float)
        summary.append({
            "family": fam, "placebo": pl, "days": n_days, "levels": n_lv,
            "touch_rate": touch_rate, "first_touches": n_ft,
            "respect_rate_ft": resp / n_ft if n_ft else np.nan,
            "break_rate_ft": brk / n_ft if n_ft else np.nan,
            "events_all": n_all,
            "respect_rate_all": resp_all / n_all if n_all else np.nan,
            "touches_per_touched_day": e.groupby("date").size().mean() if n_all else np.nan,
            "density_ratio_med": dens.median() if len(dens) else np.nan,
            "density_ratio_n": len(dens),
        })

summ = pd.DataFrame(summary)
# attach lift + z vs matched placebo
out = []
for fam, g in summ.groupby("family"):
    r = g[~g["placebo"]].iloc[0].to_dict()
    p = g[g["placebo"]]
    if len(p):
        pr = p.iloc[0]
        r["placebo_respect_ft"] = pr["respect_rate_ft"]
        r["placebo_first_touches"] = pr["first_touches"]
        r["placebo_density_med"] = pr["density_ratio_med"]
        r["z_respect"] = two_prop_z(
            int(round(r["respect_rate_ft"] * r["first_touches"])) if r["first_touches"] else 0,
            int(r["first_touches"]),
            int(round(pr["respect_rate_ft"] * pr["first_touches"])) if pr["first_touches"] else 0,
            int(pr["first_touches"]),
        )
        r["placebo_touch_rate"] = pr["touch_rate"]
    out.append(r)
fam_summary = pd.DataFrame(out).sort_values("z_respect", ascending=False)
fam_summary.to_csv(OUT / "family_summary.csv", index=False)

# ----------------------------------------------------------------- HVN persistence D -> D+1
hvn_rows = []
prof_corr = []
for i in range(1, len(dates)):
    d0, d1 = dates[i - 1], dates[i]
    if (pd.Timestamp(d1) - pd.Timestamp(d0)).days > 5 or sym_by_date.get(d0) != sym_by_date.get(d1):
        continue
    p0, p1 = prof_by_date.get(d0), prof_by_date.get(d1)
    if p0 is None or p1 is None or p0.empty or p1.empty:
        continue
    s1 = sessions[d1]
    peaks = hvn_peaks(p0)
    hi_a, lo_a = s1["hi"], s1["lo"]
    for rank, pk in enumerate(peaks, start=1):
        L = pk * TICK
        evs = touch_events(hi_a, lo_a, L)
        dr = density_ratio(p1, L)
        hvn_rows.append({
            "date": d1, "rank": rank, "level": L, "placebo": False,
            "touched": bool(evs), "density_ratio": dr,
            "first_outcome": evs[0]["outcome"] if evs else None,
        })
        for off in PLACEBO_OFFS:
            Lp = L + off * TICK
            if any(abs(Lp - q * TICK) < PLACEBO_EXCL * TICK for q in peaks):
                continue
            evs_p = touch_events(hi_a, lo_a, Lp)
            hvn_rows.append({
                "date": d1, "rank": rank, "level": Lp, "placebo": True,
                "touched": bool(evs_p), "density_ratio": density_ratio(p1, Lp),
                "first_outcome": evs_p[0]["outcome"] if evs_p else None,
            })
    # profile overlap correlation on the shared price range
    lo_t = max(p0.index.min(), p1.index.min())
    hi_t = min(p0.index.max(), p1.index.max())
    if hi_t - lo_t >= 40:
        a = p0.reindex(range(lo_t, hi_t + 1), fill_value=0)
        b = p1.reindex(range(lo_t, hi_t + 1), fill_value=0)
        overlap_frac = (hi_t - lo_t) / max(
            p0.index.max() - p0.index.min(), p1.index.max() - p1.index.min())
        prof_corr.append({
            "date": d1, "spearman": a.rank().corr(b.rank()),
            "overlap_frac": overlap_frac,
        })

hvn = pd.DataFrame(hvn_rows)
hvn.to_parquet(OUT / "hvn.parquet")
hv_sum = []
for pl in (False, True):
    hset = hvn[hvn["placebo"] == pl]
    touched = hset[hset["touched"]]
    hv_sum.append({
        "placebo": pl, "n_levels": len(hset), "touch_rate": hset["touched"].mean(),
        "respect_ft": (touched["first_outcome"] == "respect").mean() if len(touched) else np.nan,
        "n_ft": len(touched),
        "density_med": hset["density_ratio"].median(),
    })
hvn_summary = pd.DataFrame(hv_sum)
hvn_summary.to_csv(OUT / "hvn_summary.csv", index=False)

pc = pd.DataFrame(prof_corr)
misc = {
    "sessions_total": len(dates),
    "date_range": [str(dates[0]), str(dates[-1])],
    "pairs_ok": int(sum(m["pair_ok"] for m in pair_meta)),
    "profile_spearman_median": float(pc["spearman"].median()) if len(pc) else None,
    "profile_spearman_q25_q75": [float(pc["spearman"].quantile(q)) for q in (0.25, 0.75)] if len(pc) else None,
    "overlap_frac_median": float(pc["overlap_frac"].median()) if len(pc) else None,
    "params": {"T": T, "A": A, "B": B, "P": P, "H": H,
               "placebo_offsets": PLACEBO_OFFS},
}
(OUT / "misc.json").write_text(json.dumps(misc, indent=2))

pd.set_option("display.width", 250)
print(json.dumps(misc, indent=2))
cols = ["family", "days", "levels", "touch_rate", "first_touches", "respect_rate_ft",
        "placebo_respect_ft", "placebo_first_touches", "z_respect", "break_rate_ft",
        "density_ratio_med", "placebo_density_med", "touches_per_touched_day", "placebo_touch_rate"]
print(fam_summary[[c for c in cols if c in fam_summary.columns]].to_string(index=False,
      float_format=lambda x: f"{x:.3f}"))
print()
print(hvn_summary.to_string(index=False, float_format=lambda x: f"{x:.3f}"))
