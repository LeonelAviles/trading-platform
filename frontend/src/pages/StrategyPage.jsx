import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  fetchStrategies, saveStrategy, deleteStrategy, generateStrategy,
  fetchEngineStatus, fetchBacktest, runBacktest, runDemoBacktest, fetchSymbols,
} from '../api';
import { describeCondition, describeStop, describeTarget } from '../strategyDefs';

function blankStrategy(symbol) {
  return {
    name: '',
    description: '',
    symbol: symbol || 'MSFT',
    interval: '1min',
    direction: 'long',
    riskPerTradePercent: 1,
    conditions: [{ type: 'breaks_high', lookback: 20 }],
    stop: { type: 'percent', value: 0.5 },
    target: { type: 'rr', value: 2 },
    session: { start: '13:30', end: '19:55' },
    sizing: { type: 'percent_equity', value: 95 },
  };
}

// The backend only includes session/sizing/interval when explicitly given
// (agent-generated strategies often omit them, relying on the worker's own
// defaults) — but the form always reads draft.session.start etc, so any
// strategy loaded from outside this component needs those filled in first.
function normalizeStrategy(s) {
  return {
    ...s,
    description: s.description || '',
    riskPerTradePercent: s.riskPerTradePercent ?? 1,
    interval: s.interval || '1min',
    session: s.session || { start: '13:30', end: '19:55' },
    sizing: s.sizing || { type: 'percent_equity', value: 95 },
  };
}

const COMPARISON_METRICS = [
  ['trades', 'Trades'],
  ['winRate', 'Win rate (%)'],
  ['profitFactor', 'Profit factor'],
  ['expectancyR', 'Expectancy (R)'],
  ['netPnl', 'Net P&L ($)'],
  ['maxDrawdown', 'Max drawdown ($)'],
  ['sharpe', 'Sharpe'],
  ['sqn', 'SQN'],
];

function ComparisonTable({ comparison }) {
  const { a, b, metrics, winner, verdict, warnings } = comparison;
  return (
    <div className="compare-table-wrap">
      <table className="compare-table">
        <thead>
          <tr>
            <th />
            <th className={winner === 'a' ? 'compare-winner' : ''}>{a.strategyName}{winner === 'a' ? ' ✓' : ''}</th>
            <th className={winner === 'b' ? 'compare-winner' : ''}>{b.strategyName}{winner === 'b' ? ' ✓' : ''}</th>
          </tr>
        </thead>
        <tbody>
          {COMPARISON_METRICS.map(([key, label]) => (
            <tr key={key}>
              <td>{label}</td>
              <td className={winner === 'a' ? 'compare-winner' : ''}>{metrics[key]?.a ?? '—'}</td>
              <td className={winner === 'b' ? 'compare-winner' : ''}>{metrics[key]?.b ?? '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {warnings?.length > 0 && (
        <ul className="compare-warnings">
          {warnings.map((w, i) => <li key={i}>⚠ {w}</li>)}
        </ul>
      )}
      <p className="compare-verdict">{verdict}</p>
    </div>
  );
}

function GoalVerdict({ goal }) {
  // The trader's bar: 2-5% in the majority of weeks traded, positive overall.
  // Backend-computed from the champion's backtest, not the agent's summary of it.
  const { meetsGoal, verdict, weeksTraded, weeksAtOrAboveTarget, positiveWeeks, netReturnPct, warnings } = goal;
  return (
    <div className={`goal-verdict ${meetsGoal ? 'goal-met' : 'goal-missed'}`}>
      <div className="goal-headline">
        {meetsGoal ? '✓ Meets the goal' : '✗ Does not meet the goal'} — {verdict}
      </div>
      <div className="goal-numbers">
        {weeksAtOrAboveTarget}/{weeksTraded} weeks at 2%+ · {positiveWeeks}/{weeksTraded} weeks positive · {netReturnPct}% overall
      </div>
      {warnings?.length > 0 && (
        <ul className="compare-warnings">{warnings.map((w, i) => <li key={i}>⚠ {w}</li>)}</ul>
      )}
    </div>
  );
}

function RunTable({ label, rows, championId, showHypothesis }) {
  // Every strategy the run actually built and backtested — the Phase 1
  // candidates, or the Phase 2 one-variable experiments. compare_backtests is
  // pairwise, so the comparison table alone never shows all of them.
  return (
    <div className="compare-table-wrap">
      <div className="derived-label">{label}</div>
      <table className="compare-table">
        <thead>
          <tr>
            <th>{showHypothesis ? 'Experiment' : 'Approach'}</th>
            {showHypothesis && <th className="hypothesis-col">Variable changed</th>}
            <th>Trades</th><th>Win rate</th><th>Net P&amp;L</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const sum = r.backtest?.summary;
            const won = r.id === championId;
            return (
              <tr key={r.id} className={won ? 'compare-winner' : ''}>
                <td>{r.name}{won ? ' ✓' : ''}</td>
                {showHypothesis && <td className="hypothesis-col">{r.rationale || '—'}</td>}
                <td>{sum ? sum.trades : '—'}</td>
                <td>{sum ? `${sum.winRate}%` : '—'}</td>
                <td>{sum ? sum.totalPnl : '—'}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export default function StrategyPage({ symbol, setSymbol }) {
  const navigate = useNavigate();
  const [strategies, setStrategies] = useState([]);
  const [draft, setDraft] = useState(() => blankStrategy(symbol));
  const [symbols, setSymbols] = useState([]);
  const [engineStatus, setEngineStatus] = useState(null);
  const [genDirection, setGenDirection] = useState('long');
  const [generating, setGenerating] = useState(false);
  const [genError, setGenError] = useState('');
  const [genResult, setGenResult] = useState(null); // { explanation, championId, goal?, variants?, experiments?, comparison?, note? }
  const [job, setJob] = useState(null);
  const [error, setError] = useState('');
  const pollRef = useRef(null);

  // Front-door → dashboard: clicking a strategy opens its analytics
  // dashboard, where "Run Backtest" hands off to the chart (see
  // StrategyDashboardPage).
  function openDashboard(strategy) {
    navigate(`/strategy/${strategy.id}`);
  }

  const refresh = useCallback(() => {
    fetchStrategies().then(setStrategies).catch(() => setStrategies([]));
  }, []);

  useEffect(() => {
    refresh();
    fetchEngineStatus().then(setEngineStatus).catch(() => setEngineStatus(null));
    fetchSymbols().then(setSymbols).catch(() => setSymbols([]));
    return () => clearInterval(pollRef.current);
  }, [refresh]);

  // A never-saved draft's default symbol can predate the symbols fetch
  // resolving (or just be the 'MSFT' fallback) — snap it to a real one once
  // we know what's actually available. Never touches a loaded/edited draft.
  useEffect(() => {
    if (!draft.id && symbols.length && !symbols.includes(draft.symbol)) {
      setDraft((d) => ({ ...d, symbol: symbols[0] }));
    }
  }, [symbols, draft.id, draft.symbol]);

  function edit(strategy) {
    setDraft(normalizeStrategy(JSON.parse(JSON.stringify(strategy))));
    setGenDirection(strategy.direction === 'short' ? 'short' : 'long');
    setError('');
    setJob(null);
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

  async function handleGenerate() {
    setGenError('');
    setGenResult(null);
    setGenerating(true);
    try {
      const name = draft.name || 'Untitled strategy';
      const result = await generateStrategy(
        name, draft.symbol, genDirection, draft.description, draft.interval, draft.riskPerTradePercent,
      );
      // create_strategy only stores the rule spec, so the description and risk
      // the trader typed have to be carried back onto whatever comes out of it.
      const load = (g) => setDraft(normalizeStrategy({
        ...g, description: draft.description, riskPerTradePercent: draft.riskPerTradePercent,
      }));
      // The champion — after narrowing the candidates and tuning the survivor —
      // goes in the editor; "both" is create_strategy's long/short pair, where
      // the long side is loaded and the short sibling is just noted.
      const champion = result.strategy || result.long;
      if (champion) load(champion);
      setGenResult({
        explanation: result.explanation,
        championId: champion?.id,
        goal: result.goal,
        variants: result.variants,
        experiments: result.experiments,
        comparison: result.comparison,
        note: result.long ? `Also created "${result.short.name}" for the short side.` : null,
      });
      refresh();
    } catch (e) {
      setGenError(e.message);
    } finally {
      setGenerating(false);
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
        {strategies.length > 0 && <div className="strategy-list-hint">Click a strategy to open its dashboard</div>}
        {strategies.length === 0 && <div className="strategy-empty">No strategies yet.<br />Create one to get started.</div>}
        {strategies.map((s) => (
          <div
            key={s.id}
            className={`strategy-item ${draft.id === s.id ? 'active' : ''}`}
            onClick={() => openDashboard(s)}
            title="Open dashboard"
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
              <p className="page-sub">Describe the strategy and your risk — the agent derives the entry and exit rules, then runs them through the NautilusTrader engine.</p>
            </div>
            <div className="page-head-actions">
              <button className="btn btn-primary" onClick={handleSave}>Save</button>
            </div>
          </div>

          <section className="card">
            <div className="card-head"><h2>Strategy</h2></div>
            <div className="form-grid">
              <label className="param-field grow">Name
                <input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} placeholder="e.g. Opening range breakout" />
              </label>
              <label className="param-field">Symbol
                <select className="chevron" value={draft.symbol} onChange={(e) => setDraft({ ...draft, symbol: e.target.value })}>
                  {!symbols.includes(draft.symbol) && <option value={draft.symbol}>{draft.symbol}</option>}
                  {symbols.map((s) => <option key={s} value={s}>{s}</option>)}
                </select>
              </label>
              <label className="param-field">Direction
                <div className="dir-toggle">
                  <button className={genDirection === 'long' ? 'active-long' : ''} onClick={() => { setGenDirection('long'); setDraft((d) => ({ ...d, direction: 'long' })); }}>Long</button>
                  <button className={genDirection === 'short' ? 'active-short' : ''} onClick={() => { setGenDirection('short'); setDraft((d) => ({ ...d, direction: 'short' })); }}>Short</button>
                  <button className={genDirection === 'both' ? 'active-both' : ''} onClick={() => setGenDirection('both')}>Both</button>
                </div>
              </label>
              <label className="param-field">Risk per trade (%)
                <input
                  type="number" step="any" min="0" value={draft.riskPerTradePercent}
                  onChange={(e) => setDraft({ ...draft, riskPerTradePercent: e.target.value === '' ? '' : Number(e.target.value) })}
                />
              </label>
            </div>
            <label className="param-field grow description-field">Description
              <span className="field-hint">Leave the entry or exit open — or name a few approaches — and the agent backtests each, keeps the best, then tunes it one variable at a time until it clears the target: 2–5% in most weeks traded, positive overall. If it can't get there, it says so.</span>
              <textarea
                className="idea-textarea" rows={5} value={draft.description}
                onChange={(e) => setDraft({ ...draft, description: e.target.value })}
                placeholder="Describe the setup, what triggers an entry, and how the trade should be exited. The more precise the description, the more accurately the agent can build your rules."
              />
            </label>
            <button className="btn btn-primary gen-btn" onClick={handleGenerate} disabled={generating || !draft.description.trim()}>
              {generating ? 'Working… (building, backtesting and tuning takes a few minutes)' : 'Generate strategy'}
            </button>
            {genError && <div className="form-error">{genError}</div>}
            {genResult && (
              <div className="gen-result">
                {genResult.goal && <GoalVerdict goal={genResult.goal} />}
                {genResult.explanation && <p>{genResult.explanation}</p>}
                {genResult.note && <p className="gen-note">{genResult.note}</p>}
                {genResult.variants && (
                  <RunTable label="Approaches tested" rows={genResult.variants} championId={genResult.championId} />
                )}
                {genResult.experiments && (
                  <RunTable
                    label="Experiments — one variable at a time" rows={genResult.experiments}
                    championId={genResult.championId} showHypothesis
                  />
                )}
                {genResult.comparison && <ComparisonTable comparison={genResult.comparison} />}
                <p className="gen-hint">
                  {genResult.goal && !genResult.goal.meetsGoal
                    ? 'The best version found is loaded below, but it did not reach the target — saving it is up to you.'
                    : genResult.comparison
                      ? 'The best-performing version is loaded below — review, then Save. Everything else the agent tried stays in the list on the left.'
                      : 'Review the rules below, then Save.'}
                </p>
              </div>
            )}
          </section>

          <section className="card">
            <div className="card-head">
              <h2>Entry &amp; exit</h2>
              <span className="card-hint">Derived from the description — regenerate to change them</span>
            </div>
            <div className="derived-rules">
              <div className="derived-block">
                <div className="derived-label">Entry {draft.conditions.length > 1 ? '(all must be true)' : ''}</div>
                <ul className="derived-list">
                  {draft.conditions.map((cond, i) => <li key={i}>{describeCondition(cond)}</li>)}
                </ul>
              </div>
              <div className="derived-block">
                <div className="derived-label">Exit</div>
                <ul className="derived-list">
                  <li>Stop loss — {describeStop(draft.stop)}</li>
                  <li>Take profit — {describeTarget(draft.target)}</li>
                </ul>
              </div>
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
