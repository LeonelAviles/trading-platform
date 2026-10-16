import pytest
from research_ml.consistency import assess


def trades(pnls):
    return [{"pnlUsd": p, "exitTime": i+1, "sessionDate": f"2026-07-{13+i//5:02}"} for i,p in enumerate(pnls)]


def test_concentrated_winners_cannot_hide_losses():
    r=assess(trades([100,100,100]+[1]*27+[-10]*10))
    assert r['topThreePositiveShare']==pytest.approx(300/327)
    assert r['netExcludingTopThree']==-73
    assert not r['passes'] and 'top_three_concentration' in r['reasons']


def test_diversified_positive_result_and_chronology():
    r=assess(trades([10]*40),sessions=['2026-07-30'])
    assert r['passes'] and r['status']=='pass'
    assert r['topThreePositiveShare']==0.075
    assert r['chronological']['sessions'][-1]['netPnl']==0
    assert sum(b['sessions'] for b in r['chronological']['blocks'])==9


@pytest.mark.parametrize('pnls', [[],[-5,-2],[0,0],[1,2],[1,2,3]])
def test_no_or_few_winners_are_explicit(pnls):
    r=assess(trades(pnls))
    assert r['status']=='insufficient_sample' and not r['passes']
    assert r['topWinnersUsed']==min(3,sum(x>0 for x in pnls))
    if not any(x>0 for x in pnls): assert r['topThreePositiveShare'] is None


def test_policy_and_finite_pnl():
    r=assess(trades([10]*40),policy={'maxTopThreePositiveShare':0.05})
    assert r['status']=='flagged'
    with pytest.raises(ValueError): assess(trades([float('nan')]))
    with pytest.raises(ValueError): assess([],policy={'maxTopThreePositiveShare':0})


def test_custom_profile_is_only_descriptive_and_stability_can_fail():
    result=assess(trades([10]*10+[-1]*30),policy={'propProfile':{'label':'Custom test limits',
        'dailyLossLimitUsd':1000,'maxClosedEquityDrawdownUsd':1000,'profitTargetUsd':50,'minActiveSessions':5}})
    assert result['propFit']=='descriptive_profile_checks_only'
    assert result['profileAssessment']['profitTargetReached']
    assert 'chronological_instability' in result['reasons']
