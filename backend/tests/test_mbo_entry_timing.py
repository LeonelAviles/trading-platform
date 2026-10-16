"""A gapped next-open fixture catches signal-close fills and horizon shifts."""
from datetime import date
import copy
import pandas as pd
import pytest
from engine import backtest_worker,spec
from engine.mbo_features import FEATURES
from research_ml import frozen,core
from tests.test_mbo_research import plan
from tests.test_spec_validation import ORB


@pytest.mark.parametrize("horizon", [1, 3])
def test_frozen_mbo_enters_next_open_and_exits_exact_label_horizon(monkeypatch, horizon):
    day=date(2026,7,15)
    idx=pd.date_range('2026-07-15 13:30',periods=6,freq='min',tz='UTC')
    frame=pd.DataFrame({'open':[99,110,120,130,140,150],'high':[101,112,122,132,142,152],
        'low':[98,109,119,129,139,149],'close':[100,111,121,131,141,151],'volume':[100]*6},index=idx)
    for name in FEATURES: frame[name]=0.0
    monkeypatch.setattr(frozen,'uses_mbo',lambda strategy:True)
    monkeypatch.setattr(frozen,'replay',lambda *a,**kw:('ESU6',frame,{'scope':'gapped_synthetic_test'}))
    raw=copy.deepcopy(ORB)
    raw.update(direction='long',session={'entryWindow':{'start':'09:32','end':'09:33'},'flattenAt':'15:58'},
        entry={'trigger':{'op':'gt','args':[{'field':'close'},0]},'sequence':[],'orderType':'market'},
        exit={'stop':{'type':'ticks','value':1000},'target':{'type':'ticks','value':1000},'timeStop':{'bars':horizon}},
        sizing={'type':'fixed_contracts','value':1,'maxContracts':1},execution={'mode':'bars','slippageTicksOverride':0})
    result=backtest_worker.run_backtest(spec.normalize(raw),day,day,'bars')
    assert len(result['trades'])==1
    trade=result['trades'][0]
    assert trade['entryPrice']==120 and trade['entryPrice']!=111
    assert trade['entryTime']==int(idx[2].timestamp())
    assert trade['exitPrice']==float(frame.iloc[1+horizon].close)
    assert trade['exitTime']==int(idx[2+horizon].timestamp())
    assert trade['barsHeld']==horizon
    # Same forward convention as the chronological label screen, before costs.
    all_days=[]
    for d in (13,14,15):
        f=frame.copy(); f.index=f.index+pd.Timedelta(days=d-15); all_days.append(f)
    parts=core.samples(pd.concat(all_days),plan(horizonBars=horizon),0.25)
    assert parts['test'].iloc[1]['netTicks']==(float(frame.iloc[1+horizon].close)-120)/.25
    assert trade['pnlTicks']==parts['test'].iloc[1]['netTicks']
    assert trade['pnlUsd']==pytest.approx(trade['pnlTicks']*12.50-trade['commissionUsd'])
    assert result['meta']['entryConvention']=='next_contiguous_minute_open_after_complete_signal'
