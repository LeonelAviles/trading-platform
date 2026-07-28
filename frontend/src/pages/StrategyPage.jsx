import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  fetchStrategies, saveStrategy, deleteStrategy,
  fetchEngineStatus, fetchBacktest, runBacktest, runDemoBacktest,
} from '../api';

// Mirrors CONDITION_DEFS in backend/strategy_spec.py — keep in sync.
const CONDITION_DEFS = {
  price_above: { label: 'Price above level', params: [{ name: 'value', label: 'Level', def: 100 }] },
  price_below: { label: 'Price below level', params: [{ name: 'value', label: 'Level', def: 100 }] },
  sma_cross_above: { label: 'SMA cross above (fast > slow)', params: [{ name: 'fast', label: 'Fast', def: 9 }, { name: 'slow', label: 'Slow', def: 21 }] },
  sma_cross_below: { label: 'SMA cross below (fast < slow)', params: [{ name: 'fast', label: 'Fast', def: 9 }, { name: 'slow', label: 'Slow', def: 21 }] },
  rsi_above: { label: 'RSI above', params: [{ name: 'period', label: 'Period', def: 14 }, { name: 'value', label: 'Value', def: 70 }] },
  rsi_below: { label: 'RSI below', params: [{ name: 'period', label: 'Period', def: 14 }, { name: 'value', label: 'Value', def: 30 }] },
  breaks_high: { label: 'Breaks N-bar high', params: [{ name: 'lookback', label: 'Bars', def: 20 }] },
  breaks_low: { label: 'Breaks N-bar low', params: [{ name: 'lookback', label: 'Bars', def: 20 }] },
  consecutive: { label: 'N consecutive candles', params: [{ name: 'count', label: 'Count', def: 3 }, { name: 'color', label: 'Color', def: 'green', options: ['green', 'red'] }] },
};

const STOP_TYPES = [
  { value: 'percent', label: '% from entry', params: [{ name: 'value', label: '%', def: 0.5 }] },
  { value: 'fixed_points', label: 'Fixed points', params: [{ name: 'value', label: 'Points', def: 1 }] },
  { value: 'atr', label: 'ATR multiple', params: [{ name: 'value', label: '(unused)', def: 1, hidden: true }, { name: 'period', label: 'ATR period', def: 14 }, { name: 'mult', label: 'Multiplier', def: 1.5 }] },
];
const TARGET_TYPES = [
  { value: 'rr', label: 'R multiple', params: [{ name: 'value', label: 'R', def: 2 }] },
  { value: 'percent', label: '% from entry', params: [{ name: 'value', label: '%', def: 1 }] },
  { value: 'fixed_points', label: 'Fixed points', params: [{ name: 'value', label: 'Points', def: 2 }] },
];

function blankStrategy(symbol) {
  return {
    name: '',
    symbol: symbol || 'MSFT',
    interval: '1min',
    direction: 'long',
    conditions: [{ type: 'breaks_high', lookback: 20 }],
    stop: { type: 'percent', value: 0.5 },
    target: { type: 'rr', value: 2 },
    session: { start: '13:30', end: '19:55' },
    sizing: { type: 'percent_equity', value: 95 },
  };
}

function ParamInputs({ obj, defs, onChange }) {
  return defs.filter((p) => !p.hidden).map((p) => (
    <label key={p.name} className="param-field">
      {p.label}
      {p.options ? (
        <select value={obj[p.name] ?? p.def} onChange={(e) => onChange({ ...obj, [p.name]: e.target.value })}>
          {p.options.map((o) => <option key={o} value={o}>{o}</option>)}
        </select>
      ) : (
        <input
          type="number" step="any" value={obj[p.name] ?? p.def}
          onChange={(e) => onChange({ ...obj, [p.name]: e.target.value === '' ? '' : Number(e.target.value) })}
        />
      )}
    </label>
  ));
}

export default function StrategyPage({ symbol, setSymbol }) {
  const navigate = useNavigate();
  const [strategies, setStrategies] = useState([]);
  const [draft, setDraft] = useState(() => blankStrategy(symbol));
  const [engineStatus, setEngineStatus] = useState(null);
  const [job, setJob] = useState(null);
  const [error, setError] = useState('');
  const pollRef = useRef(null);

  // Front-door → chart: open the chart with this strategy's symbol and, once
  // there, its most recent backtest auto-selected (see CandlestickPage).
  function openInChart(strategy) {
    if (setSymbol && strategy.symbol) setSymbol(strategy.symbol);
    navigate('/chart', { state: { strategyId: strategy.id, symbol: strategy.symbol } });
  }

  const refresh = useCallback(() => {
    fetchStrategies().then(setStrategies).catch(() => setStrategies([]));
  }, []);

  useEffect(() => {
    refresh();
    fetchEngineStatus().then(setEngineStatus).catch(() => setEngineStatus(null));
    return () => clearInterval(pollRef.current);
  }, [refresh]);

  function edit(strategy) {
    setDraft(JSON.parse(JSON.stringify(strategy)));
    setError('');
    setJob(null);
  }

  function setCondition(i, next) {
    setDraft((d) => ({ ...d, conditions: d.conditions.map((c, j) => (j === i ? next : c)) }));
  }

  function setConditionType(i, type) {
    const cond = { type };
    for (const p of CONDITION_DEFS[type].params) cond[p.name] = p.def;
    setCondition(i, cond);
  }

  function setExit(key, patch, defs) {
    setDraft((d) => {
      let next = { ...d[key], ...patch };
      if (patch.type) {
        next = { type: patch.type };
        for (const p of defs.find((t) => t.value === patch.type).params) next[p.name] = p.def;
      }
      return { ...d, [key]: next };
    });
  }

  async function handleSave() {
    setError('');
    try {
      const saved = await saveStrategy(draft);
      setDraft(saved);
      refresh();
    } catch (e) {
      setError(e.message);
    }
  }

  async function handleDelete(s) {
    await deleteStrategy(s.id).catch(() => {});
    if (draft.id === s.id) setDraft(blankStrategy(symbol));
    refresh();
  }

  function pollJob(id) {
    clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      try {
        const j = await fetchBacktest(id);
        setJob(j);
        if (j.status === 'done' || j.status === 'error') clearInterval(pollRef.current);
      } catch {
        clearInterval(pollRef.current);
      }
    }, 2000);
  }

  async function handleRun() {
    setError('');
    setJob(null);
    try {
      let saved = draft;
      if (!draft.id) {
        saved = await saveStrategy(draft);
        setDraft(saved);
        refresh();
      }
      const j = await runBacktest(saved.id);
      setJob(j);
      if (j.status !== 'done' && j.status !== 'error') pollJob(j.id);
    } catch (e) {
      setError(e.message);
    }
  }

  async function handleDemo() {
    setError('');
    try {
      setJob(await runDemoBacktest(draft.symbol));
    } catch (e) {
      setError(e.message);
    }
  }

  return (
    <div className="page strategy-page">
      <div className="strategy-list">
        <div className="strategy-list-head">
          <h2>Strategies</h2>
          <span className="strategy-count">{strategies.length}</span>
        </div>
        <button className="btn new-strategy-btn" onClick={() => edit(blankStrategy(symbol))}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M12 5v14M5 12h14" /></svg>
          New strategy
        </button>
        <button className="btn btn-ghost open-chart-btn" onClick={() => navigate('/chart')}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M3 3v18h18" /><path d="M7 14l3-4 3 3 4-6" /></svg>
          Open blank chart
        </button>
        {strategies.length > 0 && <div className="strategy-list-hint">Click a strategy to open it in the chart</div>}
        {strategies.length === 0 && <div className="strategy-empty">No strategies yet.<br />Create one to get started.</div>}
        {strategies.map((s) => (
          <div
            key={s.id}
            className={`strategy-item ${draft.id === s.id ? 'active' : ''}`}
            onClick={() => openInChart(s)}
            title="Open in chart"
          >
            <div className="strategy-item-body">
              <div className="strategy-item-name">{s.name}</div>
              <div className="strategy-item-sub">{s.symbol} · {s.direction} · {s.conditions.length} condition{s.conditions.length === 1 ? '' : 's'}</div>
            </div>
            <div className="strategy-item-actions">
              <button className="icon-btn" title="Edit strategy" onClick={(e) => { e.stopPropagation(); edit(s); }}>
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6"><path d="M12 20h9M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4z" /></svg>
              </button>
              <button className="icon-btn" title="Delete strategy" onClick={(e) => { e.stopPropagation(); handleDelete(s); }}>
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13" /></svg>
              </button>
            </div>
          </div>
        ))}
        {engineStatus && !engineStatus.installed && (
          <div className="lean-warning">
            NautilusTrader not installed — real backtests unavailable.
            <br />Run <code>pip install nautilus_trader</code> (see SETUP-ENGINE.md). Demo mode works without it.
          </div>
        )}
        {engineStatus && engineStatus.installed && (
          <div className="engine-badge">
            <span className="engine-dot" /> Engine: NautilusTrader {engineStatus.version}
          </div>
        )}
      </div>

      <div className="strategy-main">
        <div className="strategy-scroll">
          <div className="page-head">
            <div>
              <h1 className="page-title">{draft.id ? 'Edit strategy' : 'New strategy'}</h1>
              <p className="page-sub">Deterministic entry rules, run through the NautilusTrader engine.</p>
            </div>
            <div className="page-head-actions">
              <button className="btn btn-primary" onClick={handleSave}>Save</button>
            </div>
          </div>

          <section className="card">
            <div className="card-head"><h2>Overview</h2></div>
            <div className="form-grid">
              <label className="param-field grow">Name
                <input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} placeholder="e.g. Opening range breakout" />
              </label>
              <label className="param-field">Symbol
                <input value={draft.symbol} onChange={(e) => setDraft({ ...draft, symbol: e.target.value.toUpperCase() })} />
              </label>
              <label className="param-field">Direction
                <div className="dir-toggle">
                  <button className={draft.direction === 'long' ? 'active-long' : ''} onClick={() => setDraft({ ...draft, direction: 'long' })}>Long</button>
                  <button className={draft.direction === 'short' ? 'active-short' : ''} onClick={() => setDraft({ ...draft, direction: 'short' })}>Short</button>
                </div>
              </label>
            </div>
          </section>

          <section className="card">
            <div className="card-head"><h2>Entry</h2><span className="card-hint">All conditions must be true</span></div>
            {draft.conditions.map((cond, i) => (
              <div key={i}>
                {i > 0 && <div className="cond-and">AND</div>}
                <div className="cond-row">
                  <span className="cond-index">{i + 1}</span>
                  <select className="chevron" value={cond.type} onChange={(e) => setConditionType(i, e.target.value)}>
                    {Object.entries(CONDITION_DEFS).map(([t, d]) => <option key={t} value={t}>{d.label}</option>)}
                  </select>
                  <ParamInputs obj={cond} defs={CONDITION_DEFS[cond.type].params} onChange={(next) => setCondition(i, next)} />
                  <button
                    className="icon-btn" title="Remove condition"
                    disabled={draft.conditions.length === 1}
                    onClick={() => setDraft({ ...draft, conditions: draft.conditions.filter((_, j) => j !== i) })}
                  >
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7"><path d="M6 6l12 12M18 6L6 18" /></svg>
                  </button>
                </div>
              </div>
            ))}
            <button className="btn btn-ghost add-cond" onClick={() => setDraft({ ...draft, conditions: [...draft.conditions, { type: 'breaks_high', lookback: 20 }] })}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M12 5v14M5 12h14" /></svg>
              Add condition
            </button>
          </section>

          <section className="card">
            <div className="card-head"><h2>Exit</h2></div>
            <div className="form-grid">
              <label className="param-field">Stop loss
                <select className="chevron" value={draft.stop.type} onChange={(e) => setExit('stop', { type: e.target.value }, STOP_TYPES)}>
                  {STOP_TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
                </select>
              </label>
              <ParamInputs obj={draft.stop} defs={STOP_TYPES.find((t) => t.value === draft.stop.type).params} onChange={(next) => setDraft({ ...draft, stop: next })} />
            </div>
            <div className="form-grid" style={{ marginTop: 14 }}>
              <label className="param-field">Take profit
                <select className="chevron" value={draft.target.type} onChange={(e) => setExit('target', { type: e.target.value }, TARGET_TYPES)}>
                  {TARGET_TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
                </select>
              </label>
              <ParamInputs obj={draft.target} defs={TARGET_TYPES.find((t) => t.value === draft.target.type).params} onChange={(next) => setDraft({ ...draft, target: next })} />
            </div>
          </section>

          <section className="card">
            <div className="card-head"><h2>Session &amp; sizing</h2><span className="card-hint">Session times in UTC</span></div>
            <div className="form-grid">
              <label className="param-field">Session start
                <input type="time" value={draft.session.start} onChange={(e) => setDraft({ ...draft, session: { ...draft.session, start: e.target.value } })} />
              </label>
              <label className="param-field">Session end
                <input type="time" value={draft.session.end} onChange={(e) => setDraft({ ...draft, session: { ...draft.session, end: e.target.value } })} />
              </label>
              <label className="param-field">Sizing
                <select className="chevron" value={draft.sizing.type} onChange={(e) => setDraft({ ...draft, sizing: { type: e.target.value, value: e.target.value === 'fixed_qty' ? 100 : 95 } })}>
                  <option value="percent_equity">% of equity</option>
                  <option value="fixed_qty">Fixed quantity</option>
                </select>
              </label>
              <label className="param-field">{draft.sizing.type === 'fixed_qty' ? 'Shares' : '%'}
                <input type="number" step="any" value={draft.sizing.value} onChange={(e) => setDraft({ ...draft, sizing: { ...draft.sizing, value: Number(e.target.value) } })} />
              </label>
            </div>
          </section>

          {error && <div className="form-error">{error}</div>}

          <div className="run-bar">
            <span className="run-label">Run this strategy over the loaded data.</span>
            <button className="btn" onClick={handleDemo} title="Built-in sample logic, not the engine — a fast pipeline check">Run demo</button>
            <button
              className="btn btn-primary" onClick={handleRun}
              disabled={engineStatus && !engineStatus.installed}
              title={engineStatus && !engineStatus.installed ? 'Install nautilus_trader first' : 'Run through the NautilusTrader engine'}
            >
              Run backtest
            </button>
          </div>

          {job && (
            <div className={`job-status job-${job.status}`}>
              <b>{job.strategyName}</b> — {job.status}
              {job.source === 'nautilus' ? ' · NautilusTrader' : job.source === 'demo' ? ' · demo' : ''}
              {job.message ? ` · ${job.message}` : ''}
              {job.summary && (
                <span> · {job.summary.trades} trades · {job.summary.winRate}% win · PnL {job.summary.totalPnl}</span>
              )}
              {job.status === 'done' && <span> · click the strategy in the list to open it in the chart</span>}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
