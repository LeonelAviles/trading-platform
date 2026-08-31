import math

import numpy as np

from engine import deflated_sharpe as ds


def test_norm_helpers():
    assert abs(ds._norm_cdf(0.0) - 0.5) < 1e-12
    assert abs(ds._norm_ppf(0.975) - 1.959964) < 1e-5
    assert abs(ds._norm_ppf(0.5)) < 1e-9
    assert abs(ds._norm_cdf(ds._norm_ppf(0.3)) - 0.3) < 1e-7


def test_moments_hand_case():
    m = ds.moments([1.0, 2.0, 3.0, 4.0])
    assert m["n"] == 4 and m["mean"] == 2.5
    assert abs(m["std"] - math.sqrt(5 / 3)) < 1e-12
    assert abs(m["skew"]) < 1e-12
    assert abs(m["sharpe"] - 2.5 / math.sqrt(5 / 3)) < 1e-12


def test_expected_max_sharpe_grows_with_trials():
    v = 0.01
    assert ds.expected_max_sharpe(v, 1) == 0.0
    a, b, c = (ds.expected_max_sharpe(v, n) for n in (2, 10, 100))
    assert 0 < a < b < c


def test_dsr_deflates_with_more_trials():
    rng = np.random.default_rng(3)
    returns = rng.normal(0.001, 0.01, size=250)   # positive drift daily returns
    one = ds.deflated_sharpe(returns, trials=1)
    many = ds.deflated_sharpe(returns, trials=50)
    assert one["observations"] == 250
    assert abs(one["sharpeAnnualized"] - one["sharpe"] * math.sqrt(252)) < 1e-12
    assert one["expectedMaxSharpe"] == 0.0 and many["expectedMaxSharpe"] > 0
    assert 0 <= many["dsr"] < one["dsr"] <= 1


def test_dsr_degenerate_inputs():
    assert ds.deflated_sharpe([], trials=5)["dsr"] == 0.0
    flat = ds.deflated_sharpe([0.0] * 30, trials=5)
    assert flat["sharpe"] == 0.0 and flat["dsr"] <= 0.5
