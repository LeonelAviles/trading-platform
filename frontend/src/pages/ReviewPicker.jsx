import { useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import { Link, useNavigate } from 'react-router-dom';
import { createBacktest, deleteBacktest, fetchBacktests, fetchStrategies } from '../api';
import { HeaderSlotContext } from '../headerSlot';
import { describeExpr, describeStop, describeTarget } from '../spec/describe';
import AgentRuns from '../components/AgentRuns';

function formatWhen(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function signed(n) {
  return `${n >= 0 ? '+' : ''}${n.toFixed(2)}`;
}

// The front door. A chart is always a review of one strategy, so this is the
// only way in: pick the run you want to look at, or start one for a strategy
// that has none yet. Nothing here opens an unattached chart.
export default function ReviewPicker() {
  const { leading: leadingSlot } = useContext(HeaderSlotContext);
  const navigate = useNavigate();
  const [strategies, setStrategies] = useState([]);
  const [backtests, setBacktests] = useState([]);
  const [loading, setLoading] = useState(true);
  // Strategy id whose run we just kicked off, so its button can show progress.
  const [starting, setStarting] = useState(null);
  const [error, setError] = useState('');

  const refresh = useCallback(async () => {
    const [strats, jobs] = await Promise.all([
      fetchStrategies().catch(() => []),
      fetchBacktests().catch(() => []),
    ]);
    setStrategies(strats);
    setBacktests(jobs);
    setLoading(false);
  }, []);
  useEffect(() => { refresh(); }, [refresh]);

  // Jobs that are still preparing/running belong on the card too — they're
  // reviewable the moment the engine emits trades, and the review route polls
  // them to completion.
  const byStrategy = useMemo(() => {
    const map = new Map();
    for (const b of backtests) {
      if (b.status === 'error') continue;
      const list = map.get(b.strategyId) || [];
      list.push(b);
      map.set(b.strategyId, list);
    }
    return map;
  }, [backtests]);

  // A run whose strategy has since been deleted still describes a real review,
  // so it gets its own card rather than disappearing.
  const orphans = useMemo(() => {
    const known = new Set(strategies.map((s) => s.id));
    return backtests.filter((b) => b.status !== 'error' && !known.has(b.strategyId));
  }, [backtests, strategies]);

  async function run(strategyId) {
    setStarting(strategyId);
    setError('');
    try {
      const job = await createBacktest(strategyId);
      // Land with the assistant already open — there's somewhere to talk
      // about the run the moment it's on screen.
      navigate(`/review/${job.id}`, { state: { openChat: true } });
    } catch (e) {
      setError(e.message || 'Could not start the backtest');
      setStarting(null);
    }
  }

  async function remove(id) {
    await deleteBacktest(id).catch(() => {});
    refresh();
  }

  function renderRuns(runs) {
    if (!runs.length) return null;
    return (
      <ul className="review-run-list">
        {runs.map((b) => (
          <li key={b.id} className="review-run">
            <button className="review-run-open" onClick={() => navigate(`/review/${b.id}`)}>
              <span className="review-run-tf">{b.interval || '1min'}</span>
              <span className="review-run-when">{formatWhen(b.createdAt)}</span>
              {b.windowKind && b.windowKind !== 'full' && <span className="review-chip window">{b.windowKind.toUpperCase()}</span>}
              {b.mode && <span className="review-chip mode">{b.mode}</span>}
              {b.metrics?.verdict && (
                <span className={`review-chip verdict ${b.metrics.verdict.status}`} title={(b.metrics.verdict.failures || []).join('\n')}>
                  {b.metrics.verdict.status}
                </span>
              )}
              {b.status === 'done' && b.summary ? (
                <span className="review-run-stats">
                  <span>{b.summary.trades} trades</span>
                  <span>{b.summary.winRate}% win</span>
                  <span className={b.summary.totalPnl >= 0 ? 'pos' : 'neg'}>{signed(b.summary.totalPnl)}</span>
                </span>
              ) : (
                <span className="review-run-stats"><span className="review-running">Running…</span></span>
              )}
            </button>
            <button className="icon-btn" title="Delete this run" onClick={() => remove(b.id)}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7"><path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13" /></svg>
            </button>
          </li>
        ))}
      </ul>
    );
  }

  return (
    <div className="page review-page">
      {leadingSlot && createPortal(
        <div className="hdr-title">Strategy reviews</div>,
        leadingSlot,
      )}

      <div className="review-body">
        <div className="review-intro">
          <h1>Pick a strategy to review</h1>
          <p>Review charts open inside a run, so what you see is tied to the strategy that produced it. The free chart replays any session tick by tick.</p>
        </div>

        {error && <div className="review-error">{error}</div>}

        <AgentRuns />
        <div className="review-links"><Link to="/">Desk →</Link><Link to="/chart/ES1!">Chart &amp; tick replay →</Link><Link to="/research">Research &amp; knowledge →</Link></div>

        {loading ? (
          <div className="review-empty">Loading…</div>
        ) : strategies.length === 0 && orphans.length === 0 ? (
          <div className="review-empty">
            No strategies yet. Create one with the assistant, then run it here to review it on a chart.
          </div>
        ) : (
          <div className="review-grid">
            {strategies.map((s) => {
              const runs = byStrategy.get(s.id) || [];
              return (
                <section key={s.id} className="review-card">
                  <header className="review-card-head">
                    <div>
                      <div className="review-card-name"><Link to={`/strategies/${s.id}`}>{s.name || 'Untitled'}</Link></div>
                      <div className="review-card-sub">
                        {s.instrument?.symbol || s.symbol} · {s.timeframes?.primary || s.interval || '1min'} · <span className={`review-dir ${s.direction}`}>{s.direction}</span>
                        {' · '}<span className={`review-chip status-${s.status}`}>{s.status}</span>
                      </div>
                    </div>
                    <button
                      className="btn btn-primary"
                      disabled={starting === s.id}
                      onClick={() => run(s.id)}
                    >
                      {starting === s.id ? 'Starting…' : runs.length ? 'New run' : 'Run backtest'}
                    </button>
                  </header>

                  <div className="review-card-spec">
                    <span title={describeExpr(s.entry?.trigger)}>Enter when {describeExpr(s.entry?.trigger)}</span>
                    <span>Stop {describeStop(s.exit?.stop)}</span>
                    <span>Target {describeTarget(s.exit?.target)}</span>
                  </div>

                  {runs.length
                    ? renderRuns(runs)
                    : <div className="review-card-empty">No runs yet — run the backtest to review it on a chart.</div>}
                </section>
              );
            })}

            {orphans.length > 0 && (
              <section className="review-card">
                <header className="review-card-head">
                  <div>
                    <div className="review-card-name">Deleted strategies</div>
                    <div className="review-card-sub">Runs whose strategy no longer exists</div>
                  </div>
                </header>
                {renderRuns(orphans)}
              </section>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
