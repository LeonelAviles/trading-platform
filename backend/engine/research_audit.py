"""Read-only diagnostics for a selected local single-root bar sequence.

No repair, back-adjustment, market I/O, model fitting or economic scoring.
"""
from __future__ import annotations

import pandas as pd

from research_ml.core import validate_bars


def audit_bar_sequence(bars: pd.DataFrame, contracts: list[str], *, stale_bars: int = 5) -> dict:
    """Reuse OHLCV validation; flag same-contract flat runs and contract switches.

    ``contracts`` is the actual dated contract per row, not a continuous alias.
    Timestamps are bar opens; gaps are observations, not a holiday-calendar verdict.
    Flat runs are warnings, not proof of a feed defect. Roll jumps are not returns.
    """
    bars = validate_bars(bars)
    if isinstance(stale_bars, bool) or not isinstance(stale_bars, int) or stale_bars < 2:
        raise ValueError("stale_bars must be an integer >= 2")
    if len(contracts) != len(bars) or any(not isinstance(c, str) or not c.strip() for c in contracts):
        raise ValueError("provide one nonempty actual contract per bar")
    flat_rows, gaps, switches = [], [], []
    run = 1
    for i in range(1, len(bars)):
        same = contracts[i] == contracts[i-1]
        delta = bars.index[i] - bars.index[i-1]
        contiguous = delta == pd.Timedelta(minutes=1)
        if not contiguous:
            gaps.append(i)
        if not same:
            switches.append({"row": i, "from": contracts[i-1], "to": contracts[i],
                             "rawCloseGapPoints": float(bars.close.iloc[i] - bars.close.iloc[i-1])})
        run = run + 1 if same and contiguous and bars.close.iloc[i] == bars.close.iloc[i-1] else 1
        if run >= stale_bars:
            flat_rows.append(i)
    return {"rows": len(bars), "zeroVolumeRows": [i for i, v in enumerate(bars.volume) if v == 0],
            "flatCloseRows": flat_rows, "nonMinuteBoundaryRows": gaps, "contractSwitches": switches,
            "note": "diagnostic only; no repairs, roll adjustment, or economic evaluation"}
