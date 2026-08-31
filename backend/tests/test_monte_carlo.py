import numpy as np

from engine import monte_carlo as mc


def test_max_drawdown_hand_cases():
    assert mc.max_drawdown([]) == 0.0
    assert mc.max_drawdown([1, 1, 1]) == 0.0
    # +10, -4, -3, +20: peak 10 -> trough 3 = 7
    assert mc.max_drawdown([10, -4, -3, 20]) == 7.0
    # Starts losing: drawdown from the 0 peak.
    assert mc.max_drawdown([-5, -5, 3]) == 10.0


def test_bootstrap_constant_series_is_exact():
    r = mc.bootstrap([10.0] * 50, n=100)
    assert r["runs"] == 100 and r["trades"] == 50
    assert r["maxDrawdown"] == {"p5": 0.0, "p50": 0.0, "p95": 0.0}
    assert r["finalEquity"] == {"p5": 500.0, "p50": 500.0, "p95": 500.0}
    assert r["probLoss"] == 0.0


def test_bootstrap_is_seeded_and_ordered():
    pnls = np.random.default_rng(1).normal(5, 50, size=200)
    a = mc.bootstrap(pnls, n=300)
    b = mc.bootstrap(pnls, n=300)
    assert a == b
    assert a["maxDrawdown"]["p5"] <= a["maxDrawdown"]["p50"] <= a["maxDrawdown"]["p95"]
    assert a["finalEquity"]["p5"] <= a["finalEquity"]["p50"] <= a["finalEquity"]["p95"]
    assert 0 <= a["probLoss"] <= 1


def test_skip_test_detects_fragility():
    # 99 tiny losses and one huge win: dropping 10% often removes the win.
    fragile = [-1.0] * 99 + [500.0]
    r = mc.skip_test(fragile, n=200)
    assert r["tradesKept"] == 90 and r["baseline"] == 401.0
    assert r["probLoss"] > 0.05
    robust = [5.0] * 100
    assert mc.skip_test(robust)["probLoss"] == 0.0
    assert mc.run_all(robust, account_size=100_000)["bootstrap"]["maxDrawdownPct"]["p95"] == 0.0
