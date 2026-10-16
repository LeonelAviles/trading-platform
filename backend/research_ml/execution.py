"""Deterministic diagnostic execution of an approved, frozen hypothesis.

A failed label-screen hypothesis can be replayed on already exposed validation
sessions for debugging. It cannot become a candidate, use final holdout or open
its unscored internal test. Normal candidate-export approval remains separate.
"""
from datetime import date, timedelta
import copy
import database
from models import MLExperiment
from research_agent import memory
from research_ml import service, consistency
from engine import spec, backtest_worker


def strategy_for(row, *, stop_ticks=20, target_ticks=30):
    plan=row['document']['plan']; result=copy.deepcopy(row['result'])
    digest=result.pop('resultDigest',None)
    if not digest or memory.digest(result)!=digest:
        raise ValueError('experiment result digest mismatch')
    if (row['status'] not in {'candidate','no_edge'} or result.get('experimentDigest')!=row['digest']
            or memory.digest(row['document'])!=row['digest'] or (row.get('decision') or {}).get('action')!='approve'):
        raise ValueError('approved completed hypothesis required')
    raw={'schemaVersion':2,'name':'Recorded ES cluster research diagnostic',
        'instrument':{'root':'ES','symbol':'ES1!'},'timeframes':{'primary':'1min','context':[]},
        'direction':plan['direction'],
        'session':{'entryWindow':{'start':'09:31','end':'15:45'},'flattenAt':'15:58'},
        'entry':{'trigger':result['model']['trigger'],'sequence':[],'orderType':'market','timeoutBars':1},
        'filters':[], 'exit':{'stop':{'type':'ticks','value':stop_ticks},'target':{'type':'ticks','value':target_ticks},
            'timeStop':{'bars':plan['horizonBars']},'trailing':None,'breakeven':None,'scaleOut':[]},
        'sizing':{'type':'fixed_contracts','value':1,'maxContracts':1},
        'constraints':{'maxTradesPerDay':10,'cooldownBars':5,'stopAfterConsecutiveLosses':3,'maxConcurrentPositions':1},
        'execution':{'mode':'bars','slippageTicksOverride':2},
        'risk':{'accountSize':100000,'riskPerTradePct':0.25,'maxContracts':1,'dailyLossLimitPct':1,
            'weeklyLossLimitPct':3,'maxTradesPerDay':10,'stopAfterConsecutiveLosses':3},
        'meta':{'mlDiscovery':{'experimentId':row['id'],'experimentDigest':row['digest'],
            'resultDigest':digest,'method':plan['method'],'target':plan['target'],'horizonBars':plan['horizonBars']},
            'scope':'diagnostic execution; no candidate or fresh OOS claim'}}
    errors=spec.validate_spec(raw)
    if errors: raise ValueError('; '.join(errors))
    return spec.normalize(raw)


def run(thread_id, experiment_id, policy=None):
    with database.session_scope() as db:
        row=service._row(db,thread_id,experiment_id)
        record=copy.deepcopy(service._public(row))
    strategy=strategy_for(record)
    plan=record['document']['plan']
    # Always diagnose validation only. The final and internal test are not used
    # to tune execution assumptions, even after successful label screening.
    start=date.fromisoformat(plan['trainEnd'])+timedelta(days=1)
    end=date.fromisoformat(plan['validationEnd'])
    sessions=[d for d in record['document']['dataset']['sessions'] if str(start)<=d<=str(end)]
    if not sessions: raise ValueError('no approved validation sessions')
    executed=backtest_worker.run_backtest(strategy,start,end,'bars',research_diagnostic=True)
    # Apply an explicit additional two-tick-per-side stress charge rather
    # than treating Nautilus's probabilistic one-tick FillModel as a precise
    # configurable two-tick fill. Keep raw fills/commissions for audit.
    for trade in executed['trades']:
        stress=4 * 12.50 * trade['contracts']
        trade['baseModeledNetPnlUsd']=trade['pnlUsd']
        trade['executionStressUsd']=stress
        trade['pnlUsd']=round(trade['pnlUsd']-stress,2)
    executed['summary']['totalPnl']=round(sum(t['pnlUsd'] for t in executed['trades']),2)
    executed['meta']['additionalExecutionStressTicksRoundTrip']=4
    executed['meta']['stressScope']='additional to modeled fill slippage and commission; not observed fills'
    from engine.backtest_worker import daily_returns
    executed['dailyReturns']=daily_returns(executed['trades'],sessions,100000)
    assessment=consistency.assess(executed['trades'],sessions,policy)
    return {'strategy':strategy,'execution':executed,'consistency':assessment,'sessions':sessions,
        'outcome':'rejected' if record['status']=='no_edge' or not assessment['passes'] else 'descriptive_candidate',
        'limitations':['validation replay reuses exposed sessions; not fresh OOS',
            'bar execution cannot prove tick fills or queue position',
            'small sample and no statistical significance','prop fit unassessed: no firm profile']}
