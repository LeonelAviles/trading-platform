import numpy as np
import pytest

from engine.causality import assert_prefix_causal


def test_trailing_feature_with_nan_warmup():
    def trailing(rows):
        return [[np.nan if i < 2 else sum(rows[i-2:i+1]) / 3]
                for i in range(len(rows))]
    assert_prefix_causal([2, 5, -3, 1000, -999], trailing)


@pytest.mark.parametrize("calculate", [
    lambda rows: [[x / max(rows)] for x in rows],
    lambda rows: [[rows[min(i+1, len(rows)-1)]] for i in range(len(rows))],
    lambda rows: [[np.mean(rows[max(0, i-1):i+2])] for i in range(len(rows))],
])
def test_detects_global_scaling_forward_shift_and_centered_window(calculate):
    with pytest.raises(AssertionError, match="prefix changed"):
        assert_prefix_causal([2, 5, 3, 1000], calculate)


def test_input_mutation_does_not_escape_check():
    rows = [[1], [2], [3]]
    def mutate(values):
        values[0][0] = 8
        return values
    assert_prefix_causal(rows, mutate)
    assert rows == [[1], [2], [3]]


@pytest.mark.parametrize("calculate", [lambda rows: [1] * len(rows),
    lambda rows: [[1]] * (len(rows)-1), lambda rows: [[] for _ in rows],
    lambda rows: [[float("inf")]] * len(rows)])
def test_rejects_invalid_outputs(calculate):
    with pytest.raises(AssertionError):
        assert_prefix_causal([1, 2], calculate)


def test_rejects_empty_fixture_and_invalid_tolerances():
    with pytest.raises(ValueError):
        assert_prefix_causal([], lambda rows: rows)
    for tolerance in [-1, float("nan"), float("inf")]:
        with pytest.raises(ValueError):
            assert_prefix_causal([1], lambda rows: [[1]], atol=tolerance)


def test_nan_warmup_cannot_hide_future_availability():
    with pytest.raises(AssertionError, match="prefix changed"):
        assert_prefix_causal([1, 2, 3], lambda rows: [[np.nan if len(rows) < 3 else x] for x in rows])
