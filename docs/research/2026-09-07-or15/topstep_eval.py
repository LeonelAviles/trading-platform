"""Apply Topstep's numerical Combine objectives to the frozen OR15 candidate.

ES fills are from study.py. MES runs are position-sizing proxies using ES prices,
not executed-MES backtests. No strategy search and no trading API connection.
Run after study.py. Evaluation starts overlap; frequencies are not independent
pass probabilities. Full-session research sample excludes shortened sessions.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
PROFILE = json.loads((HERE/'topstep_50k.json').read_text())


def next_floor(previous, closing_net):
    return max(previous, min(PROFILE['drawdown_floor_cap_net_usd'],
                             closing_net-PROFILE['drawdown_usd']))


def passes(balance, best_day, traded_days):
    return (traded_days >= PROFILE['minimum_trading_days']
            and balance >= PROFILE['profit_target_usd']
            and best_day <= PROFILE['best_day_max_fraction']*balance + 1e-8)


def contracts(risk_points, balance, floor, budget, slippage):
    remaining = balance-floor
    settings = PROFILE['research_sizing']
    if remaining <= settings['minimum_remaining_drawdown_usd']:
        return 0
    allowance = min(budget, settings['max_fraction_of_remaining_drawdown']*remaining)
    cost = (risk_points+slippage*.25)*5+PROFILE['round_trip_commission_usd']['MES']
    return min(PROFILE['max_micro_contracts'], math.floor(allowance/cost))


def load_trades(filename):
    con = duckdb.connect()
    con.execute("SET threads=2")
    df = pd.read_csv(HERE/filename)
    trades = {}
    for i, t in enumerate(df.to_dict('records')):
        # Exclude exit timestamp: multiple messages may share that timestamp.
        # Include exact exit fill below, plus the triggering stop print. This
        # prevents looking at prints after a resting target has already filled.
        prices = con.execute('''SELECT min(price), max(price) FROM read_parquet(?)
               WHERE symbol=? AND ts_event>=? AND ts_event<?''',
               [str(ROOT/f"data/market/trades/root=ES/date={t['day']}/*.parquet"),
                t['symbol'],int(t['entry_ts']),int(t['exit_ts'])]).fetchone()
        assert all(p is not None for p in prices), t['day']
        worst = prices[0] if t['direction']==1 else prices[1]
        points = t['direction']*(t['exit']-t['entry'])
        t['points'] = points
        t['min_points'] = min(points, t['direction']*(worst-t['entry']))
        t['risk_points'] = abs(t['entry']-t['stop'])
        trades[t['day']] = t
        if (i+1)%40==0:
            print(f'{filename}: marked {i+1}/{len(df)} trades',flush=True)
    con.close()
    return trades


def evaluate(days, trades, budget, slippage):
    """EOD floor stays fixed intraday; check adverse excursion before closure.

    Full round-trip commission is reserved throughout each trade. This is
    slightly conservative versus charging half at entry and half at exit.
    """
    balance, floor, best = 0., -float(PROFILE['drawdown_usd']), 0.
    traded, skipped, lowest_cushion, max_qty = 0, 0, 2000., 0
    status, end = 'unfinished', days[-1]
    for day in days:
        t=trades.get(day)
        if t is None:
            continue
        if budget is None:
            qty, multiplier, fee = 1, 50, PROFILE['round_trip_commission_usd']['ES']
        else:
            qty=contracts(t['risk_points'],balance,floor,budget,slippage)
            multiplier, fee = 5, PROFILE['round_trip_commission_usd']['MES']
        if not qty:
            skipped+=1
            continue
        traded+=1
        max_qty=max(max_qty,qty)
        mark_min=balance+qty*(t['min_points']*multiplier-fee)
        lowest_cushion=min(lowest_cushion,mark_min-floor)
        if mark_min <= floor:
            # No false precision about liquidation fills. Status is determined
            # by the observed threshold breach, not the eventual strategy exit.
            status,end='breached',day
            break
        pnl=qty*(t['points']*multiplier-fee)
        balance+=pnl
        best=max(best,pnl)
        floor=next_floor(floor,balance)
        if passes(balance,best,traded):
            status,end='passed_numeric_rules',day
            break
    return dict(status=status,end=end,closed_net_before_breach=round(balance,2),
                trades=traded,skipped=skipped,max_qty=max_qty,
                minimum_cushion_usd=round(lowest_cushion,2),best_day_usd=round(best,2),
                required_profit_usd=round(max(3000,best/.5),2),
                sessions_elapsed=days.index(end)+1,
                calendar_days_elapsed=(pd.Timestamp(end)-pd.Timestamp(days[0])).days+1)


def main():
    days=pd.read_csv(HERE/'sessions.csv').day.tolist()
    rows=[]
    first_start=[]
    # Fixed before outcomes: original 1 ES diagnosis plus $100/$150/$200 MES
    # risk caps, normal/stressed fills, 20 and 60 eligible-session horizons.
    for slip,filename in [(1,'selected_tick_trades.csv'),(2,'selected_tick_stress_trades.csv')]:
        trades=load_trades(filename)
        for budget in [None,*PROFILE['research_sizing']['risk_budgets_usd']]:
            label='1_ES_baseline' if budget is None else f'MES_proxy_risk_{budget}'
            first_start.append(dict(sizing=label,slippage_ticks=slip,start=days[0],
                                    **evaluate(days,trades,budget,slip)))
            for horizon in [20,60]:
                for start in range(len(days)-horizon+1):
                    window=days[start:start+horizon]
                    rows.append(dict(sizing=label,slippage_ticks=slip,horizon=horizon,
                                     start=window[0],window_end=window[-1],
                                     **evaluate(window,trades,budget,slip)))
    df=pd.DataFrame(rows)
    df.to_csv(HERE/'topstep_rolling_evaluations.csv',index=False)
    summary=[]
    for keys,g in df.groupby(['sizing','slippage_ticks','horizon'],sort=False):
        success=g[g.status=='passed_numeric_rules']
        summary.append(dict(sizing=keys[0],slippage_ticks=int(keys[1]),horizon=int(keys[2]),
                            windows=len(g),passed=int(len(success)),
                            breached=int((g.status=='breached').sum()),
                            unfinished=int((g.status=='unfinished').sum()),
                            historical_pass_fraction=float(len(success)/len(g)),
                            median_sessions_to_pass=float(success.sessions_elapsed.median()) if len(success) else None,
                            minimum_cushion_usd=float(g.minimum_cushion_usd.min())))
    result=dict(profile='topstep_50k.json',candidate='failed_break|flow|3',
                frozen_entry_and_exit_rules=True,
                interpretation='Overlapping historical scenarios on previously researched data; not independent pass probabilities.',
                micro_limitation='ES prices scaled to MES point value and current MES fees; MES execution not validated.',
                horizons='20/60 retained full RTH sessions, NOT provider deadlines; shortened days are not traded.',
                billing='Monthly subscription/API/activation fees excluded from trading account P&L and personal net economics.',
                first_start=first_start,summary=summary)
    (HERE/'topstep_results.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    print(pd.DataFrame(summary).to_string(index=False),flush=True)
    print('First start:',json.dumps(first_start,indent=2),flush=True)


if __name__=='__main__':
    main()
