from engine import verdict as vd


def _is(**kw):
    base = {"trades": 150, "profitFactor": 1.6, "expectancyR": 0.3, "maxDrawdownPct": 5.0, "netPnl": 8000}
    base.update(kw)
    return base


def test_untestable_below_minimum():
    v = vd.evaluate({"inSample": _is(trades=40)}, None)
    assert v.untestable and not v.passes and v.status == "untestable"
    assert "untestable" in v.failures[0]


def test_pass_with_defaults_before_finalize():
    job = {"inSample": _is(), "walkForward": [{"netPnl": 100}, {"netPnl": 50}, {"netPnl": -10}],
           "monteCarlo": {"bootstrap": {"maxDrawdownPct": {"p5": 1, "p50": 4, "p95": 9}}}}
    v = vd.evaluate(job, None)
    assert v.passes and v.status == "pass" and v.failures == []
    names = {c["name"]: c for c in v.checks}
    assert names["outOfSample"]["ok"] is None          # hidden until finalize
    assert names["minDeflatedSharpeProb"]["ok"] is None  # report-only by default
    assert names["minWalkForwardWindowsPositive"]["value"] == 2


def test_failures_are_listed():
    job = {"inSample": _is(profitFactor=1.1, maxDrawdownPct=12.0), "walkForward": [{"netPnl": -1}, {"netPnl": -1}, {"netPnl": 5}],
           "monteCarlo": {"bootstrap": {"maxDrawdownPct": {"p5": 5, "p50": 10, "p95": 20}}}}
    v = vd.evaluate(job, None)
    assert not v.passes and v.status == "fail"
    joined = " ".join(v.failures)
    assert "profit factor" in joined and "drawdown 12.0%" in joined and "walk-forward" in joined and "Monte Carlo" in joined
    assert 0 < v.score < 1


def test_user_overrides_and_oos_and_dsr_gate():
    risk = {"passCriteria": {"minTradesInSample": 50, "minDeflatedSharpeProb": 0.95, "minOosProfitFactor": 1.5}}
    job = {"inSample": _is(trades=60), "walkForward": [{"netPnl": 1}, {"netPnl": 1}, {"netPnl": 1}],
           "outOfSample": {"trades": 35, "profitFactor": 1.2}, "deflatedSharpe": {"dsr": 0.6}}
    v = vd.evaluate(job, risk)
    assert not v.passes
    assert any("OOS profit factor" in f for f in v.failures)
    assert any("deflated Sharpe" in f for f in v.failures)
    merged = vd.with_defaults(risk)
    assert merged["accountSize"] == 100000 and merged["passCriteria"]["minTradesInSample"] == 50
    assert merged["passCriteria"]["maxDrawdownPct"] == 10
