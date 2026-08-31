"""Monte Carlo robustness checks on a trade sequence (PLATFORM-SPEC.md §4.5 item 5).

- `bootstrap(pnls, n=1000)`: resample the trade PnLs with replacement,
  same length, and report the 5th/50th/95th percentiles of max drawdown
  and final equity.
- `skip_test(pnls, frac=0.10, n=200)`: drop a random 10% of trades per run
  — fragility check: a strategy whose edge lives in a handful of trades
  falls apart here.

Pure numpy, seeded, so tests are exact.
"""

from __future__ import annotations

import numpy as np


def max_drawdown(pnls) -> float:
    """Largest peak-to-trough decline of the cumulative PnL curve (≥ 0)."""
    arr = np.asarray(pnls, dtype=float)
    if arr.size == 0:
        return 0.0
    cum = np.cumsum(arr)
    peak = np.maximum.accumulate(np.concatenate([[0.0], cum]))[1:]
    return float(np.max(peak - cum)) if arr.size else 0.0


def _percentiles(values: np.ndarray) -> dict:
    p5, p50, p95 = np.percentile(values, [5, 50, 95])
    return {"p5": float(p5), "p50": float(p50), "p95": float(p95)}


def bootstrap(pnls, n: int = 1000, seed: int = 42) -> dict:
    arr = np.asarray(pnls, dtype=float)
    if arr.size == 0:
        return {"runs": 0, "maxDrawdown": None, "finalEquity": None}
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, arr.size, size=(n, arr.size))
    samples = arr[idx]
    cum = np.cumsum(samples, axis=1)
    peak = np.maximum.accumulate(np.maximum(cum, 0.0), axis=1)
    dd = np.max(peak - cum, axis=1)
    final = cum[:, -1]
    return {
        "runs": int(n), "trades": int(arr.size),
        "maxDrawdown": _percentiles(dd),
        "finalEquity": _percentiles(final),
        "probLoss": float(np.mean(final < 0)),
    }


def skip_test(pnls, frac: float = 0.10, n: int = 200, seed: int = 7) -> dict:
    arr = np.asarray(pnls, dtype=float)
    if arr.size == 0:
        return {"runs": 0, "finalEquity": None}
    rng = np.random.default_rng(seed)
    keep = max(1, int(round(arr.size * (1 - frac))))
    finals, dds = [], []
    for _ in range(n):
        sel = np.sort(rng.choice(arr.size, size=keep, replace=False))
        s = arr[sel]
        finals.append(float(s.sum()))
        dds.append(max_drawdown(s))
    return {
        "runs": int(n), "dropFraction": frac, "tradesKept": keep,
        "finalEquity": _percentiles(np.asarray(finals)),
        "maxDrawdown": _percentiles(np.asarray(dds)),
        "probLoss": float(np.mean(np.asarray(finals) < 0)),
        "baseline": float(arr.sum()),
    }


def run_all(pnls, account_size: float | None = None) -> dict:
    out = {"bootstrap": bootstrap(pnls), "skip": skip_test(pnls)}
    if account_size and out["bootstrap"]["maxDrawdown"]:
        out["bootstrap"]["maxDrawdownPct"] = {k: v / account_size * 100 for k, v in out["bootstrap"]["maxDrawdown"].items()}
    return out
