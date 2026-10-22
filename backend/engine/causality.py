"""Synthetic-test helper: prefix invariance is necessary, not sufficient, for causality."""
from __future__ import annotations

from collections.abc import Callable, Sequence
from copy import deepcopy

import numpy as np


def assert_prefix_causal(rows: Sequence, calculate: Callable, *, rtol: float = 0.0,
                         atol: float = 0.0) -> None:
    """Compare every prefix with a full-run numeric (rows, features) output.

    Callback must construct fresh state per call; inputs and outputs are copied.
    NaN warm-up is allowed, infinity is not. O(n²): use small synthetic fixtures.
    Cannot certify timestamps, model fitting, or behavior on untested inputs.
    """
    if len(rows) == 0:
        raise ValueError("provide a nonempty synthetic fixture")
    if not np.isfinite([rtol, atol]).all() or rtol < 0 or atol < 0:
        raise ValueError("tolerances must be finite and nonnegative")

    def run(prefix):
        values = np.array(calculate(deepcopy(prefix)), dtype=float, copy=True)
        if values.ndim != 2 or values.shape[0] != len(prefix) or values.shape[1] == 0:
            raise AssertionError("features must have shape (input rows, nonzero features)")
        if np.isinf(values).any():
            raise AssertionError("features contain infinity")
        return values

    full = run(rows)
    for end in range(1, len(rows) + 1):
        actual, expected = run(rows[:end]), full[:end]
        if actual.shape != expected.shape or not np.allclose(
            actual, expected, rtol=rtol, atol=atol, equal_nan=True
        ):
            raise AssertionError(f"feature prefix changed at prefix length {end}")
