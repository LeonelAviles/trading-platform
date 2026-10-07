import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location('topstep_eval', Path(__file__).with_name('topstep_eval.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def trade(points, min_points=None, risk=4):
    return dict(points=points,min_points=points if min_points is None else min_points,risk_points=risk)


def test_eod_floor_never_falls_and_locks_at_starting_balance():
    assert m.next_floor(-2000,500)==-1500
    assert m.next_floor(-1500,0)==-1500
    assert m.next_floor(-1500,2500)==0
    assert m.next_floor(0,9000)==0


def test_unrealized_breach_cannot_be_cured_by_winning_exit():
    r=m.evaluate(['2026-01-02'],{'2026-01-02':trade(60,-41)},None,1)
    assert r['status']=='breached'


def test_unrealized_peak_does_not_raise_eod_floor_intraday():
    # Intraday highs aren't needed for Topstep's EOD rule. Only this day's
    # trough and eventual close matter while the prior floor stays fixed.
    r=m.evaluate(['2026-01-02'],{'2026-01-02':trade(5,-30)},None,1)
    assert r['status']=='unfinished'


def test_large_winning_day_raises_required_target():
    assert not m.passes(3000,2000,2)
    assert m.passes(4000,2000,2)
    assert not m.passes(4000,2000,1)


def test_budget_smaller_than_one_micro_skips():
    assert m.contracts(50,0,-2000,100,1)==0


def test_risk_uses_available_drawdown_and_accounts_for_costs():
    # $500 headroom => $50 allowance, ~$22.47 per micro => two.
    assert m.contracts(4,-1500,-2000,200,1)==2
    assert m.contracts(4,-1800,-2000,200,1)==0


def test_losing_day_does_not_reset_consistency_best_day():
    days=['2026-01-02','2026-01-05','2026-01-06']
    tr={days[0]:trade(40),days[1]:trade(-5),days[2]:trade(26)}
    r=m.evaluate(days,tr,None,1)
    assert r['closed_net_before_breach']>3000
    assert r['required_profit_usd']>3900
    assert r['status']=='unfinished'
