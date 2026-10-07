import { useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import { Link } from 'react-router-dom';
import { fetchBacktests, fetchStrategies } from '../api';
import { HeaderSlotContext } from '../headerSlot';
import { Card, EmptyState, PageHeader, StatTile, StatusChip } from '../components/ui';
import { fmtWhen, signed } from '../format';

function shortDuration(seconds) {
  if (seconds == null) return null;
  if (seconds < 60) return `${Math.max(1, Math.round(seconds))}s`;
  return `${Math.round(seconds / 60)}m`;
}

function RunProgress({ job }) {
  const p = job.progress;
  if (!['queued', 'running'].includes(job.status)) return null;
  const pct = p?.percent ?? 0;
  return (
    <div className="run-progress" title={p?.updatedAt ? `Last update ${p.updatedAt}` : 'Waiting for the worker'}>
      <div className="run-progress-label">
        <span>{p ? `${p.sessionsCompleted}/${p.sessionsTotal} sessions` : 'Waiting…'}</span>
        <strong>{Math.round(pct)}%</strong>
      </div>
      <div className="run-progress-track"><i style={{ width: `${pct}%` }} /></div>
      {p?.currentDate && <div className="run-progress-date">{p.currentDate}{p.etaSeconds != null ? ` · about ${shortDuration(p.etaSeconds)} left` : ''}</div>}
    </div>
  );
}

// /backtests — a read-only list of every run; each opens on its review chart.
// Backtests are started from a strategy page, never from here.
export default function BacktestsPage() {
  const { leading: leadingSlot } = useContext(HeaderSlotContext);
  const [strategies, setStrategies] = useState([]);
  const [backtests, setBacktests] = useState([]);
  const [loading, setLoading] = useState(true);
  const [filterStrategy, setFilterStrategy] = useState('');
  const [filterStatus, setFilterStatus] = useState('');
  const [error, setError] = useState('');

  const refresh = useCallback(async () => {
    try {
      const [s, b] = await Promise.all([fetchStrategies(), fetchBacktests()]);
      setStrategies(s);
      setBacktests(b);
      setError('');
    } catch (e) {
      setError(e.message || 'Could not load backtests');
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => { refresh(); const id = setInterval(refresh, 2500); return () => clearInterval(id); }, [refresh]);

  const names = useMemo(() => new Map(strategies.map((s) => [s.id, s.name])), [strategies]);
  const rows = backtests
    .filter((b) => !filterStrategy || b.strategyId === filterStrategy)
    .filter((b) => !filterStatus || (filterStatus === 'running' ? ['queued', 'running'].includes(b.status) : b.status === filterStatus));
  const running = backtests.filter((b) => ['queued', 'running'].includes(b.status)).length;

  return (
    <div className="page workspace-page backtests-page">
      {leadingSlot && createPortal(<div className="hdr-title">Backtests</div>, leadingSlot)}
      <div className="page-scroll"><div className="page-inner">
        <PageHeader
          eyebrow="Execution history"
          title="Backtests"
          subtitle="Inspect every run, compare outcomes, and open the full chart-level evidence."
          actions={<Link className="btn" to="/strategies">Strategies</Link>}
        />
        {error && <div className="review-error">{error}</div>}

        <div className="list-summary">
          <StatTile label="Total runs" value={backtests.length} sub="Complete execution history" />
          <StatTile label="Running now" value={running} sub={running ? 'Compute jobs in progress' : 'No active jobs'} tone={running ? 'warn' : ''} />
          <StatTile label="Completed" value={backtests.filter((b) => b.status === 'done').length} sub="Available for review" tone="good" />
          <StatTile label="Errors" value={backtests.filter((b) => b.status === 'error').length} sub="Runs requiring attention" tone={backtests.some((b) => b.status === 'error') ? 'bad' : ''} />
        </div>

        <Card title="Run history" sub={`${rows.length} visible run${rows.length === 1 ? '' : 's'} across ${strategies.length} strategies.`} className="list-card">
          <div className="toolbar-row">
            <select value={filterStrategy} onChange={(e) => setFilterStrategy(e.target.value)}>
              <option value="">All strategies</option>
              {strategies.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
            <select value={filterStatus} onChange={(e) => setFilterStatus(e.target.value)}>
              <option value="">All statuses</option><option value="done">done</option><option value="running">running</option><option value="error">error</option>
            </select>
            <span className="toolbar-count">{rows.length} shown</span>
          </div>
          {loading ? <div className="review-card-empty">Loading…</div> : rows.length === 0 ? (
            <EmptyState title="No backtests" text="Runs started from a strategy page will appear here." />
          ) : (
            <div className="table-wrap">
              <table className="data-table mobile-cards">
                <thead><tr><th>Strategy</th><th>When</th><th>Window</th><th>Mode</th><th>Status</th><th className="num">Trades</th><th className="num">Win %</th><th className="num">Net PnL</th><th className="num">PF</th><th /></tr></thead>
                <tbody>
                  {rows.map((b) => (
                    <tr key={b.id}>
                      <td data-label="Strategy"><Link className="row-link" to={`/review/${b.id}`}>{names.get(b.strategyId) || b.strategyName || 'Deleted strategy'}</Link><div className="inline-note">{b.symbol} · {b.interval || '1min'}</div></td>
                      <td data-label="When" className="inline-note">{fmtWhen(b.createdAt)}</td>
                      <td data-label="Window">{(b.windowKind || 'full').toUpperCase()}{b.dateFrom ? <div className="inline-note">{b.dateFrom} → {b.dateTo}</div> : null}</td>
                      <td data-label="Mode">{b.mode}</td>
                      <td data-label="Status">{b.metrics?.verdict ? <StatusChip status={b.metrics.verdict.status} kind="verdict" /> : <StatusChip status={b.status} />}<RunProgress job={b} />{b.status === 'error' && <div className="inline-note" title={b.message}>{(b.message || '').slice(0, 40)}</div>}</td>
                      <td data-label="Trades" className="num">{b.summary?.trades ?? b.progress?.tradeCount ?? '—'}</td>
                      <td data-label="Win rate" className="num">{b.summary?.winRate != null ? `${b.summary.winRate}%` : '—'}</td>
                      <td data-label="Net PnL" className={`num ${b.summary?.totalPnl >= 0 ? 'pos' : 'neg'}`}>{b.summary ? signed(b.summary.totalPnl) : '—'}</td>
                      <td data-label="Profit factor" className="num">{b.metrics?.profitFactor ?? '—'}</td>
                      <td className="actions" data-label="Actions"><Link className="btn btn-sm" to={`/review/${b.id}`}>Review</Link></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div></div>
    </div>
  );
}
