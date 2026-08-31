"""Session regime tagging (PLATFORM-SPEC.md §4.5 item 7).

Each RTH session is tagged once at ingest and every trade inherits its tags:

- `trend`: efficiency ratio of 15-minute closes over the RTH session,
  ER = |close_last − close_first| / Σ|Δclose|; `trend` if ER ≥ 0.3 else `range`.
- `vol`: session range % (high − low) / open × 100, ranked against the
  trailing 60 sessions of the same root → `low` / `mid` / `high` terciles
  (fewer than `MIN_HISTORY` prior sessions → `mid`).
- `dayType` from opening-range extension (OR = first 15 minutes):
  `open_drive` — price leaves the OR within 30 minutes after it forms and
  never trades back inside it; `trend_day` — one-sided extension ≥ 2× the
  OR range with the close in the extreme quartile of the session range;
  `rotational` — everything else.

Pure functions over 1-minute bars (columns ts[int ns], open, high, low,
close); `tag_session()` is what ingest calls, `tag_all()` recomputes every
session with the trailing-volatility ranking.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date

import numpy as np
import pandas as pd

from engine.session import NS, rth_bounds_ns

ER_TREND_THRESHOLD = 0.3
OR_MINUTES = 15
OPEN_DRIVE_WINDOW_MINUTES = 30
TREND_DAY_EXTENSION = 2.0
TREND_DAY_CLOSE_QUARTILE = 0.25
VOL_LOOKBACK_SESSIONS = 60
MIN_HISTORY = 6


@dataclass
class SessionRegime:
    root: str
    date: str
    symbol: str
    trend: str            # trend | range
    er: float
    vol: str              # low | mid | high
    range_pct: float
    day_type: str         # open_drive | trend_day | rotational
    bars: int

    def to_dict(self) -> dict:
        return asdict(self)

    def tags(self) -> list[str]:
        return [self.trend, f"vol_{self.vol}", self.day_type]


def _rth_slice(bars: pd.DataFrame, d: date, rth_start: str, rth_end: str) -> pd.DataFrame:
    lo, hi = rth_bounds_ns(d, rth_start, rth_end)
    return bars[(bars["ts"] >= lo) & (bars["ts"] < hi)].sort_values("ts")


def efficiency_ratio(closes: np.ndarray) -> float:
    if len(closes) < 2:
        return 0.0
    net = abs(float(closes[-1]) - float(closes[0]))
    path = float(np.abs(np.diff(closes)).sum())
    return net / path if path > 0 else 0.0


def closes_15m(rth: pd.DataFrame) -> np.ndarray:
    if rth.empty:
        return np.array([])
    bucket = (rth["ts"] // (15 * 60 * NS)) * (15 * 60 * NS)
    return rth.groupby(bucket)["close"].last().to_numpy(dtype=float)


def session_range_pct(rth: pd.DataFrame) -> float:
    if rth.empty:
        return 0.0
    open_px = float(rth["open"].iloc[0])
    return (float(rth["high"].max()) - float(rth["low"].min())) / open_px * 100 if open_px else 0.0


def day_type(rth: pd.DataFrame, d: date, rth_start: str = "09:30") -> str:
    if rth.empty:
        return "rotational"
    lo, _ = rth_bounds_ns(d, rth_start, "16:00")
    or_end = lo + OR_MINUTES * 60 * NS
    orb = rth[rth["ts"] < or_end]
    after = rth[rth["ts"] >= or_end]
    if orb.empty or after.empty:
        return "rotational"
    or_high, or_low = float(orb["high"].max()), float(orb["low"].min())
    or_range = or_high - or_low
    s_high, s_low = float(rth["high"].max()), float(rth["low"].min())
    s_range = s_high - s_low
    close = float(rth["close"].iloc[-1])

    # Open drive: leaves the OR within 30 minutes and never trades back inside.
    early = after[after["ts"] < or_end + OPEN_DRIVE_WINDOW_MINUTES * 60 * NS]
    left_up = bool((early["low"] > or_high).any())
    left_dn = bool((early["high"] < or_low).any())
    if left_up and not (after["low"] <= or_high).any():
        return "open_drive"
    if left_dn and not (after["high"] >= or_low).any():
        return "open_drive"

    up_ext = max(0.0, s_high - or_high)
    dn_ext = max(0.0, or_low - s_low)
    if or_range > 0 and s_range > 0:
        one_sided = max(up_ext, dn_ext) >= TREND_DAY_EXTENSION * or_range and min(up_ext, dn_ext) <= 0.5 * or_range
        pos = (close - s_low) / s_range
        extreme = pos >= 1 - TREND_DAY_CLOSE_QUARTILE or pos <= TREND_DAY_CLOSE_QUARTILE
        if one_sided and extreme:
            return "trend_day"
    return "rotational"


def vol_tercile(value: float, history: list[float]) -> str:
    hist = [h for h in history[-VOL_LOOKBACK_SESSIONS:] if h is not None and not np.isnan(h)]
    if len(hist) < MIN_HISTORY:
        return "mid"
    lo_q, hi_q = np.quantile(hist, [1 / 3, 2 / 3])
    if value <= lo_q:
        return "low"
    if value >= hi_q:
        return "high"
    return "mid"


def tag_session(bars: pd.DataFrame, root: str, symbol: str, d: date,
                history_range_pct: list[float] | None = None,
                rth_start: str = "09:30", rth_end: str = "16:00") -> SessionRegime:
    rth = _rth_slice(bars, d, rth_start, rth_end)
    er = efficiency_ratio(closes_15m(rth))
    rp = session_range_pct(rth)
    return SessionRegime(
        root=root, date=d.isoformat(), symbol=symbol,
        trend="trend" if er >= ER_TREND_THRESHOLD else "range", er=round(er, 4),
        vol=vol_tercile(rp, history_range_pct or []), range_pct=round(rp, 4),
        day_type=day_type(rth, d, rth_start), bars=int(len(rth)),
    )


def tag_all(sessions: list[tuple[str, str, date, pd.DataFrame]],
            rth_start: str = "09:30", rth_end: str = "16:00") -> list[SessionRegime]:
    """`sessions` = [(root, symbol, date, bars_1m)], any order. Volatility
    terciles are computed against the trailing sessions of the same root."""
    out: list[SessionRegime] = []
    history: dict[str, list[float]] = {}
    for root, symbol, d, bars in sorted(sessions, key=lambda s: (s[0], s[2])):
        hist = history.setdefault(root, [])
        reg = tag_session(bars, root, symbol, d, hist, rth_start, rth_end)
        hist.append(reg.range_pct)
        out.append(reg)
    return out
