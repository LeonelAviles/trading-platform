import pytest
from research_ml import discovery,service,execution
from research_agent import memory
from tests.test_mbo_research import raw_context  # noqa: F401
from tests.test_cluster_discovery import reviewed,hypothesis_request


def test_rejected_hypothesis_diagnostic_preserves_export_gate(raw_context):
    tid,_=raw_context
    row=reviewed(tid)
    e=discovery.propose_hypothesis(tid,row['id'],hypothesis_request(row,minMeanNetTicks=1000000.0))
    service.decide(tid,e['id'],e['digest'],'approve','Synthetic scoped execution diagnostic')
    row=service.run(tid,e['id'])
    assert row['status']=='no_edge' and 'test' not in row['result']['metrics']
    result=execution.run(tid,row['id'])
    assert result['execution']['meta']['researchDiagnostic'] is True
    assert result['execution']['meta']['sessions']==1
    assert result['outcome']=='rejected'
    assert result['consistency']['propFit']=='unassessed_no_firm_profile'
    from research_ml import frozen
    with pytest.raises(ValueError,match='approved completed'):
        frozen.load(result['strategy'])
    with pytest.raises(memory.ResearchConflict,match='only a completed candidate'):
        service.candidate_proposal(tid,row['id'],{}, {})


def test_diagnostic_cannot_open_internal_test_after_failed_validation(raw_context):
    tid,_=raw_context; row=reviewed(tid)
    e=discovery.propose_hypothesis(tid,row['id'],hypothesis_request(row,minMeanNetTicks=1000000.0))
    service.decide(tid,e['id'],e['digest'],'approve','Scoped synthetic diagnostic')
    row=service.run(tid,e['id']); strategy=execution.strategy_for(row)
    from research_ml import frozen
    from tests.test_mbo_research import DAYS
    with pytest.raises(ValueError,match='restricted to already exposed validation'):
        frozen.replay(strategy,DAYS[2],DAYS[2],allow_rejected=True)


def test_stress_reaggregates_winners_aliases_and_daily_results():
    executed={'trades':[{'contracts':1,'pnlUsd':40,'pnl':40,'sessionDate':'2026-07-23','exitTime':1},
        {'contracts':1,'pnlUsd':100,'pnl':100,'sessionDate':'2026-07-23','exitTime':2}],
        'summary':{'wins':2,'winRate':100,'totalPnl':140},'meta':{}}
    execution.apply_execution_stress(executed,['2026-07-23'])
    assert executed['summary']=={'wins':1,'winRate':50.0,'totalPnl':40}
    assert [t['pnl'] for t in executed['trades']]==[-10,50]
    assert executed['dailyReturns'][0]['pnlUsd']==40
    with pytest.raises(ValueError,match='already applied'):
        execution.apply_execution_stress(executed,['2026-07-23'])
