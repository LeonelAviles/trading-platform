"""Checks for the execution and causality assumptions that can invent an edge."""
import importlib.util
from pathlib import Path

import pandas as pd
import pytest

spec = importlib.util.spec_from_file_location('or15_study', Path(__file__).with_name('study.py'))
study = importlib.util.module_from_spec(spec)
spec.loader.exec_module(study)


def bars():
    return pd.DataFrame([dict(open=100.,high=100.,low=100.,close=100.,
                              delta=1,flow3=3,vwap=99.) for _ in range(390)])


@pytest.mark.parametrize('direction,stop,target,expected', [(1,98,104,97.75),(-1,102,96,102.25)])
def test_ambiguous_entry_bar_is_stop(direction,stop,target,expected):
    b=bars()
    b.loc[16,['high','low']]=[105,95]
    j,price,reason,ambiguous=study.bar_exit(b,16,100,direction,stop,target,1)
    assert (j,price,reason,ambiguous)==(16,expected,'stop',True)


def test_gap_stop_uses_worse_open():
    b=bars()
    b.loc[17,['open','high','low','close']]=[96,97,95,96]
    assert study.bar_exit(b,16,100,1,98,104,1)[:3]==(17,95.75,'stop')


def test_target_touch_does_not_fill():
    b=bars()
    b.loc[16,'high']=104
    b.loc[17,'high']=104.25
    assert study.bar_exit(b,16,100,1,98,104,1)[:3]==(17,104,'target')


def test_future_bars_cannot_change_signal():
    b=bars()
    b.loc[15,['close','high']]=[103,103]
    s=dict(bars=b,high=102,low=98,opening=100,rvol=1)
    assert study.signal(s,'breakout','vwap_flow')==(15,1)
    b.loc[16:,['close','vwap','delta','flow3']]=[-100,10000,-99999,-99999]
    assert study.signal(s,'breakout','vwap_flow')==(15,1)


def test_opening_range_bar_is_not_entry_signal():
    b=bars()
    b.loc[14,'close']=103
    s=dict(bars=b,high=102,low=98,opening=100,rvol=1)
    assert study.signal(s,'breakout','none') is None
