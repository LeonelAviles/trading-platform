"""Hand-built sessions with unambiguous shapes."""

from datetime import date

import numpy as np
import pandas as pd

from engine import regimes
from engine.session import NS, rth_bounds_ns

D = date(2026, 7, 15)


def _bars(closes: list[float], wick: float = 0.0, opens: list[float] | None = None) -> pd.DataFrame:
    lo, _ = rth_bounds_ns(D)
    ts = lo + np.arange(len(closes)) * 60 * NS
    c = np.asarray(closes, dtype=float)
    o = np.asarray(opens, dtype=float) if opens is not None else np.concatenate([[c[0]], c[:-1]])
    return pd.DataFrame({"ts": ts, "open": o, "high": np.maximum(o, c) + wick, "low": np.minimum(o, c) - wick, "close": c})


def test_efficiency_ratio_trend_vs_range():
    assert regimes.efficiency_ratio(np.array([1, 2, 3, 4, 5.0])) == 1.0
    assert regimes.efficiency_ratio(np.array([1, 2, 1, 2, 1.0])) == 0.0
    # 390 minutes straight up: trend; alternating: range.
    up = _bars([5000 + i * 0.25 for i in range(390)])
    reg = regimes.tag_session(up, "ES", "ESU6", D)
    assert reg.trend == "trend" and reg.er == 1.0
    zig = _bars([5000 + (i % 2) * 0.25 for i in range(390)])
    assert regimes.tag_session(zig, "ES", "ESU6", D).trend == "range"


def test_day_types():
    # Open drive: OR 5000–5001 for 15 min, then straight up, never back in.
    closes = [5000 + (i % 2) for i in range(15)] + [5001.5 + (i + 1) * 0.5 for i in range(375)]
    assert regimes.day_type(_bars(closes, opens=closes), D) == "open_drive"
    # Trend day: chops inside the OR for 45 min, then extends 3× OR range and closes near the high.
    closes = [5000 + (i % 2) for i in range(45)] + [5000 + (i + 1) * (3 / 345) for i in range(345)]
    assert regimes.day_type(_bars(closes), D) == "trend_day"
    # Rotational: back and forth across the OR all day.
    closes = [5000 + (i % 10) * 0.25 for i in range(390)]
    assert regimes.day_type(_bars(closes), D) == "rotational"


def test_vol_terciles_use_trailing_history():
    assert regimes.vol_tercile(1.0, []) == "mid"
    hist = [0.5, 0.6, 0.7, 1.0, 1.1, 1.2, 1.5, 1.6, 1.7]
    assert regimes.vol_tercile(0.4, hist) == "low"
    assert regimes.vol_tercile(1.05, hist) == "mid"
    assert regimes.vol_tercile(2.0, hist) == "high"


def test_tag_all_orders_by_date_and_ranks_vol():
    sessions = []
    for i in range(10):
        d = date(2026, 7, 1 + i)
        lo, _ = rth_bounds_ns(d)
        ts = lo + np.arange(390) * 60 * NS
        amp = 1 + i  # each day wider than the last
        c = 5000 + amp * np.sin(np.linspace(0, 6.28, 390))
        bars = pd.DataFrame({"ts": ts, "open": c, "high": c + 0.25, "low": c - 0.25, "close": c})
        sessions.append(("ES", "ESU6", d, bars))
    tags = regimes.tag_all(sessions[::-1])
    assert [t.date for t in tags] == [s[2].isoformat() for s in sessions]
    assert tags[-1].vol == "high" and tags[0].vol == "mid"
    assert all(t.bars == 390 for t in tags)
    assert set(tags[-1].tags()) == {tags[-1].trend, "vol_high", tags[-1].day_type}
