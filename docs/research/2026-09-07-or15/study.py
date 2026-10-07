"""Reproduce the fixed OR15 research protocol; run with the repository .venv.

No app state changes, downloads, brokerage connections, or environment secrets.
"""
from __future__ import annotations

import itertools
import json
import math
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
MARKET = ROOT / 'data/market'
TICK = 0.25
MINUTE = 60_000_000_000
INSTRUMENT = yaml.safe_load((ROOT / 'backend/config/instruments.yaml').read_text())['roots']['ES']
MULT = INSTRUMENT['multiplier']
COMMISSION = 2 * INSTRUMENT['commission_per_side']
FAMILIES = ['breakout', 'retest', 'failed_break', 'open_retest']
FILTERS = ['none', 'vwap', 'flow', 'vwap_flow', 'vwap_flow_rvol']
CONFIGS = list(itertools.product(FAMILIES, FILTERS, [2, 3]))


def connect():
    con = duckdb.connect()
    con.execute("SET threads=2")
    con.execute("SET memory_limit='1GB'")
    return con


def load_sessions():
    con = connect()
    bars = con.execute('SELECT * FROM read_parquet(?) ORDER BY ts',
                       [str(MARKET / 'bars_1m/root=ES/date=*/*.parquet')]).df()
    local = pd.to_datetime(bars.ts, unit='ns', utc=True).dt.tz_convert('America/New_York')
    bars['minute'] = local.dt.hour * 60 + local.dt.minute
    bars['day'] = local.dt.strftime('%Y-%m-%d')
    sessions, excluded = [], []
    for day, all_bars in bars.groupby('day', sort=True):
        # Use UTC-midnight through RTH-open volume, all already observable.
        pre = all_bars[(all_bars.minute < 570) & (all_bars.date.astype(str) == day)]
        if pre.empty:
            excluded.append({'day': day, 'reason': 'no preopen data'})
            continue
        sym = pre.groupby('symbol').volume.sum().sort_values(ascending=False).index[0]
        b = all_bars[(all_bars.symbol == sym) & all_bars.minute.between(570, 959)].copy()
        b = b.sort_values('ts').reset_index(drop=True)
        if len(b) != 390 or b.minute.tolist() != list(range(570, 960)):
            excluded.append({'day': day, 'symbol': sym, 'reason': 'incomplete RTH', 'bars': len(b)})
            continue
        assert b.ts.is_unique and (np.diff(b.ts) == MINUTE).all()
        if not ((b.high >= b[['open', 'close', 'low']].max(axis=1)) &
                (b.low <= b[['open', 'close', 'high']].min(axis=1))).all():
            raise ValueError(f'Invalid OHLC {day}')
        path = str(MARKET / f'trades/root=ES/date={day}/*.parquet')
        exact = con.execute('''SELECT (ts_event // 60000000000)*60000000000 AS ts,
                 sum(price*size) AS pv, sum(size) AS trade_volume,
                 sum(CASE WHEN side='B' THEN size WHEN side='A' THEN -size ELSE 0 END) AS trade_delta
                 FROM read_parquet(?) WHERE symbol=? AND ts_event>=? AND ts_event<?
                 GROUP BY 1 ORDER BY 1''', [path, sym, int(b.ts.iloc[0]), int(b.ts.iloc[-1])+MINUTE]).df()
        b = b.merge(exact, on='ts', validate='one_to_one')
        assert len(b) == 390 and (b.volume == b.trade_volume).all(), day
        assert (b.delta == b.trade_delta).all(), day
        b['vwap'] = b.pv.cumsum() / b.trade_volume.cumsum()
        b['flow3'] = b.delta.rolling(3).sum()
        opening = b.iloc[:15]
        volume = float(opening.volume.sum())
        prev = [s['or_volume'] for s in sessions[-20:]]
        sessions.append(dict(day=day, symbol=sym, bars=b,
                             high=float(opening.high.max()), low=float(opening.low.min()),
                             opening=float(opening.open.iloc[0]), or_close=float(opening.close.iloc[-1]),
                             or_vwap=float(opening.vwap.iloc[-1]), or_delta=float(opening.delta.sum()),
                             or_volume=volume,
                             rvol=volume/np.median(prev) if len(prev) >= 10 else float('nan')))
        if len(sessions) % 25 == 0:
            print(f'Loaded and reconciled {len(sessions)} sessions', flush=True)
    con.close()
    pd.DataFrame(excluded).to_csv(HERE / 'excluded_sessions.csv', index=False)
    pd.DataFrame([{k:v for k,v in s.items() if k!='bars'} for s in sessions]).to_csv(HERE/'sessions.csv', index=False)
    return sessions, excluded


def signal(s, family, filt):
    b = s['bars']
    hi, lo, op = s['high'], s['low'], s['opening']
    width = hi-lo
    if width <= 0:
        return None
    state = 0
    for i in range(15, 119):  # 09:45..11:28 signal; execution 09:46..11:29
        row, prev = b.iloc[i], b.iloc[i-1]
        outside = 1 if row.close > hi else -1 if row.close < lo else 0
        direction = 0
        if family == 'breakout':
            direction = outside
        elif family == 'retest':
            if state == 1 and row.low <= hi and row.close > hi:
                direction = 1
            elif state == -1 and row.high >= lo and row.close < lo:
                direction = -1
        elif family == 'failed_break':
            if state and lo <= row.close <= hi:
                direction = -state
        elif family == 'open_retest':
            if prev.close >= op+0.25*width and row.low <= op and row.close > op:
                direction = 1
            elif prev.close <= op-0.25*width and row.high >= op and row.close < op:
                direction = -1
        state = outside
        if not direction:
            continue
        if 'vwap' in filt and direction*(row.close-row.vwap) <= 0:
            continue
        if 'flow' in filt and (direction*row.delta <= 0 or direction*row.flow3 <= 0):
            continue
        if 'rvol' in filt and not s['rvol'] >= 1:
            continue
        return i, direction
    return None


def geometry(entry, direction, width, reward):
    risk = math.ceil(max(4.0, width/2)/TICK)*TICK
    return entry-direction*risk, entry+direction*reward*risk, risk


def bar_exit(b, start, entry, direction, stop, target, slip):
    """Evaluate only bars after entry; ambiguous brackets lose conservatively."""
    for j in range(start, 388):
        r = b.iloc[j]
        stopped = r.low <= stop if direction == 1 else r.high >= stop
        won = r.high >= target+TICK if direction == 1 else r.low <= target-TICK
        if stopped:
            base = min(stop, r.open) if direction == 1 else max(stop, r.open)
            return j, base-direction*slip*TICK, 'stop', bool(won)
        if won:
            return j, target, 'target', False
    return 388, float(b.open.iloc[388])-direction*slip*TICK, 'flatten', False


def simulate_bar(s, config, slip=1):
    family, filt, reward = config
    sig = signal(s, family, filt)
    if sig is None:
        return None
    i, direction = sig
    b = s['bars']
    entry = float(b.open.iloc[i+1])+direction*slip*TICK
    stop, target, risk = geometry(entry, direction, s['high']-s['low'], reward)
    j, price, reason, ambiguous = bar_exit(b, i+1, entry, direction, stop, target, slip)
    return dict(day=s['day'], symbol=s['symbol'], config='|'.join(map(str,config)),
                direction=direction, signal_index=i, entry_index=i+1, exit_index=j,
                entry=entry, exit=price, stop=stop, target=target, reason=reason,
                ambiguous=ambiguous, pnl=direction*(price-entry)*MULT-COMMISSION,
                risk_usd=risk*MULT)


def stats(trades, days):
    vals = np.array([t['pnl'] for t in trades], dtype=float)
    win = vals[vals>0].sum()
    loss = -vals[vals<0].sum()
    byday = {t['day']:t['pnl'] for t in trades}
    daily = np.array([byday.get(d, 0) for d in days])
    equity = np.r_[0, np.cumsum(daily)]
    return dict(trades=len(vals), pf=float(win/loss) if loss else None,
                win_rate=float(np.mean(vals>0)) if len(vals) else None,
                net_usd=float(vals.sum()), avg_usd=float(vals.mean()) if len(vals) else None,
                max_dd_usd=float(np.max(np.maximum.accumulate(equity)-equity)),
                payoff=float(vals[vals>0].mean()/-vals[vals<0].mean()) if win and loss else None)


def tick_replay(s, config, con, slip=1):
    sig = signal(s, *config[:2])
    if sig is None:
        return None
    i, direction = sig
    b = s['bars']
    start = int(b.ts.iloc[i+1])+250_000_000
    flatten = int(b.ts.iloc[388])
    tick = con.execute('''SELECT ts_event, ts_recv, sequence, price FROM read_parquet(?)
         WHERE symbol=? AND ts_event>=? AND ts_event<? ORDER BY ts_event, sequence, ts_recv''',
         [str(MARKET/f"trades/root=ES/date={s['day']}/*.parquet"),s['symbol'],start,flatten+MINUTE]).df()
    assert len(tick)>1, s['day']
    p = tick.price.to_numpy()
    ts = tick.ts_event.to_numpy()
    entry = float(p[0])+direction*slip*TICK
    stop, target, risk = geometry(entry,direction,s['high']-s['low'],config[2])
    if direction==1:
        hit_stop, hit_target = p[1:]<=stop, p[1:]>=target+TICK
    else:
        hit_stop, hit_target = p[1:]>=stop, p[1:]<=target-TICK
    force = ts[1:]>=flatten
    hits = np.flatnonzero(hit_stop|hit_target|force)
    assert len(hits), s['day']
    k = int(hits[0])+1
    if force[k-1]:
        price, reason = float(p[k])-direction*slip*TICK, 'flatten'
    elif hit_stop[k-1]:
        price, reason = float(p[k])-direction*slip*TICK, 'stop'
    else:
        price, reason = target, 'target'
    return dict(day=s['day'], symbol=s['symbol'], direction=direction,
                entry_ts=int(ts[0]), exit_ts=int(ts[k]), entry=entry, exit=price,
                stop=stop, target=target, reason=reason,
                pnl=direction*(price-entry)*MULT-COMMISSION, risk_usd=risk*MULT)


def bootstrap(trades, days):
    pnl = {t['day']:t['pnl'] for t in trades}
    x = np.array([pnl.get(d,0) for d in days])
    rng = np.random.default_rng(20260907)
    pfs, totals = [], []
    for _ in range(5000):
        starts = rng.integers(0,len(x), size=math.ceil(len(x)/5))
        z = x[((starts[:,None]+np.arange(5)) % len(x)).ravel()[:len(x)]]
        loss = -z[z<0].sum()
        if loss:
            pfs.append(z[z>0].sum()/loss)
        totals.append(z.sum())
    return dict(pf_interval_95=np.quantile(pfs,[.025,.975]).tolist(),
                net_interval_95=np.quantile(totals,[.025,.975]).tolist(),
                note='Conditional on selected rule; not adjusted for 40 trials or prior research.')


def directional(sessions):
    records=[]
    for s in sessions:
        width=s['high']-s['low']
        outcome=0
        for row in s['bars'].iloc[15:150].itertuples():
            up, down = row.high>=s['high']+.5*width, row.low<=s['low']-.5*width
            if up or down:
                outcome=2 if up and down else 1 if up else -1
                break
        records.append(dict(day=s['day'], outcome=outcome,
                     close_location=(s['or_close']-s['low'])/width,
                     nearest_barrier=int(np.sign(s['or_close']-(s['high']+s['low'])/2)),
                     opening_direction=int(np.sign(s['or_close']-s['opening'])),
                     vwap_direction=int(np.sign(s['or_close']-s['or_vwap'])),
                     delta_direction=int(np.sign(s['or_delta']))))
    df=pd.DataFrame(records)
    df.to_csv(HERE/'directional_events.csv',index=False)
    out={'resolved':int(df.outcome.isin([-1,1]).sum()),'unresolved':int((df.outcome==0).sum()),
         'ambiguous':int((df.outcome==2).sum())}
    for col in ['opening_direction','vwap_direction','delta_direction','nearest_barrier']:
        z=df[df.outcome.isin([-1,1]) & (df[col]!=0)]
        accuracy=float((z.outcome==z[col]).mean())
        n=len(z)
        center=(accuracy+1.96**2/(2*n))/(1+1.96**2/n)
        half=1.96*math.sqrt(accuracy*(1-accuracy)/n+1.96**2/(4*n*n))/(1+1.96**2/n)
        # Zero-drift, continuous-price barrier model; illustrative, not a
        # fitted market benchmark, and it does not model noon censoring.
        up_probability=(z.close_location+.5)/2
        geometric=np.where(z[col]>0,up_probability,1-up_probability)
        out[col]={'n':len(z),'accuracy':float((z.outcome==z[col]).mean()),
                  'wilson_95':[center-half,center+half],
                  'distance_only_reference':float(geometric.mean()),
                  'always_up_baseline':float((z.outcome==1).mean())}
    return out


def main():
    sessions, excluded = load_sessions()
    days=[s['day'] for s in sessions]
    dev=[d for d in days if d<'2026-05-01']
    check=[d for d in days if d>='2026-05-01']
    outputs, rows=[],[]
    for config in CONFIGS:
        trades=[t for s in sessions if (t:=simulate_bar(s,config)) is not None]
        outputs.extend(trades)
        row={'config':'|'.join(map(str,config))}
        for name, ds in [('dev',dev),('check',check),('all',days)]:
            row.update({f'{name}_{k}':v for k,v in stats([t for t in trades if t['day'] in ds],ds).items()})
        rows.append(row)
    table=pd.DataFrame(rows)
    table.to_csv(HERE/'all_40_variants.csv',index=False)
    pd.DataFrame(outputs).to_csv(HERE/'bar_trades.csv',index=False)
    qualified=table[(table.dev_trades>=30) & table.dev_pf.notna()]
    best=qualified.sort_values(['dev_pf','config'],ascending=[False,True]).iloc[0]
    family,filt,reward=best['config'].split('|')
    config=(family,filt,int(reward))
    print('Selected only by Dec-Apr development:',best.to_dict(),flush=True)
    con=connect()
    tick, stress=[],[]
    for j,s in enumerate(sessions):
        for slippage, dest in [(1,tick),(2,stress)]:
            trade=tick_replay(s,config,con,slippage)
            if trade:
                dest.append(trade)
        if (j+1)%25==0:
            print(f'Tick replay {j+1}/{len(sessions)}',flush=True)
    con.close()
    pd.DataFrame(tick).to_csv(HERE/'selected_tick_trades.csv',index=False)
    pd.DataFrame(stress).to_csv(HERE/'selected_tick_stress_trades.csv',index=False)
    results=dict(config=best['config'],trials=len(CONFIGS),first=days[0],last=days[-1],sessions=len(days),
                 dev_sessions=len(dev),check_sessions=len(check),excluded=len(excluded),commission_round_trip=COMMISSION,
                 selected_bar=best.to_dict(),directional=directional(sessions))
    for name, ds in [('dev',dev),('check',check),('all',days)]:
        tr=[t for t in tick if t['day'] in ds]
        results['tick_'+name]=stats(tr,ds)
        results['stress_'+name]=stats([t for t in stress if t['day'] in ds],ds)
        results['bootstrap_'+name]=bootstrap(tr,ds)
        removed=sorted(tr,key=lambda t:t['pnl'],reverse=True)[5:]
        results['remove_best_five_'+name]=stats(removed,ds)
    results['months']={m:stats([t for t in tick if t['day'].startswith(m)],
                                    [d for d in days if d.startswith(m)])
                       for m in sorted({d[:7] for d in days})}
    (HERE/'results.json').write_text(json.dumps(results,indent=2,allow_nan=False)+'\n')
    print(json.dumps(results,indent=2),flush=True)


if __name__=='__main__':
    main()
