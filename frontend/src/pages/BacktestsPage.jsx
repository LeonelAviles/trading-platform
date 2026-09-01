import { useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import { Link, useNavigate } from 'react-router-dom';
import { createBacktest, deleteBacktest, fetchBacktests, fetchStrategies } from '../api';
import { HeaderSlotContext } from '../headerSlot';
import { Card, EmptyState, PageHeader, StatusChip } from '../components/ui';
import { fmtWhen, signed } from '../format';

const WINDOWS = [['full', 'Full range'], ['is', 'In-sample'], ['wf1', 'Walk-forward 1'], ['wf2', 'Walk-forward 2'], ['wf3', 'Walk-forward 3']];

// /backtests — every run in one table; each opens on its review chart.
export default function BacktestsPage() {
  const { leading: leadingSlot } = useContext(HeaderSlotContext);
  const navigate = useNavigate();
  const [strategies, setStrategies] = useState([]);
  const [backtests, setBacktests] = useState([]);
  const [loading, setLoading] = useState(true);
  const [filterStrategy, setFilterStrategy] = useState('');
  const [filterStatus, setFilterStatus] = useState('');
  const [pick, setPick] = useState('');
  const [mode, setMode] = useState('');
  const [windowKind, setWindowKind] = useState('full');
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState('');

  const refresh = useCallback(async () => {
    const [s, b] = await Promise.all([fetchStrategies().catch(() => []), fetchBacktests().catch(() => [])]);
    setStrategies(s);
    setBacktests(b);
    setLoading(false);
  }, []);
  useEffect(() => { refresh(); const id = setInterval(refresh, 8000); return () => clearInterval(id); }, [refresh]);

  const names = useMemo(() => new Map(strategies.map((s) => [s.id, s.name])), [strategies]);
  const rows = backtests
    .filter((b) => !filterStrategy || b.strategyId === filterStrategy)
    .filter((b) => !filterStatus || (filterStatus === 'running' ? ['queued', 'running'].includes(b.status) : b.status === filterStatus));
  const running = backtests.filter((b) => ['queued', 'running'].includes(b.status)).length;

  async function start() {
    if (!pick) return;
    setStarting(true);
    setError('');
    try {
      const job = await createBacktest(pick, { windowKind, ...(mode ? { mode } : {}) });
      navigate(`/review/${job.id}`);
    } catch (e) {
      setError(e.message);
      setStarting(false);
    }
  }
  async function remove(id) {
    await deleteBacktest(id).catch(() => {});
    refresh();
  }

  return (
    <div className="page">
      {leadingSlot && createPortal(<div className="hdr-title">Backtests</div>, leadingSlot)}
      <div className="page-scroll"><div className="page-inner">
        <PageHeader
          title="Backtests"
          subtitle={`${backtests.length} run${backtests.length === 1 ? '' : 's'}${running ? ` · ${running} running` : ''}. A review opens the run on its chart with the trades, validation, Monte Carlo and regimes.`}
          actions={<Link className="btn" to="/strategies">Strategies</Link>}
        />
        {error && <div className="review-error">{error}</div>}

        <Card title="Run a backtest" sub="Pick a strategy and a window. Validate (IS + WF1–3) lives on the strategy page.">
          <div className="toolbar-row">
            <select value={pick} onChange={(e) => setPick(e.target.value)}>
              <option value="">Choose a strategy…</option>
              {strategies.map((s) => <option key={s.id} value={s.id}>{s.name} · {s.instrument?.symbol}</option>)}
            </select>
            <select value={windowKind} onChange={(e) => setWindowKind(e.target.value)}>{WINDOWS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
            <select value={mode} onChange={(e) => setMode(e.target.value)}>
              <option value="">Mode: cheapest that fits</option><option value="bars">bars</option><option value="ticks">ticks</option>
            </select>
            <button className="btn btn-primary" disabled={!pick || starting} onClick={start}>{starting ? 'Starting…' : 'Run and open the review'}</button>
            <span className="inline-note">No strategy yet? <Link to="/strategies?new=1">Create one</Link>.</span>
          </div>
        </Card>

        <Card>
          <div className="toolbar-row">
            <select value={filterStrategy} onChange={(e) => setFilterStrategy(e.target.value)}>
              <option value="">All strategies</option>
              {strategies.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
            <select value={filterStatus} onChange={(e) => setFilterStatus(e.target.value)}>
              <option value="">All statuses</option><option value="done">done</option><option value="running">running</option><option value="error">error</option>
            </select>
            <span className="inline-note">{rows.length} shown</span>
          </div>
          {loading ? <div className="review-card-empty">Loading…</div> : rows.length === 0 ? (
            <EmptyState title="No backtests" text="Run one above, or open a strategy and press Run backtest." />
          ) : (
            <div className="table-wrap">
              <table className="data-table">
                <thead><tr><th>Strategy</th><th>When</th><th>Window</th><th>Mode</th><th>Status</th><th className="num">Trades</th><th className="num">Win %</th><th className="num">Net PnL</th><th className="num">PF</th><th /></tr></thead>
                <tbody>
                  {rows.map((b) => (
                    <tr key={b.id}>
                      <td><Link className="row-link" to={`/review/${b.id}`}>{names.get(b.strategyId) || b.strategyName || 'Deleted strategy'}</Link><div className="inline-note">{b.symbol} · {b.interval || '1min'}</div></td>
                      <td className="inline-note">{fmtWhen(b.createdAt)}</td>
                      <td>{(b.windowKind || 'full').toUpperCase()}{b.dateFrom ? <div className="inline-note">{b.dateFrom} → {b.dateTo}</div> : null}</td>
                      <td>{b.mode}</td>
                      <td>{b.metrics?.verdict ? <StatusChip status={b.metrics.verdict.status} kind="verdict" /> : <StatusChip status={b.status} />}{b.status === 'error' && <div className="inline-note" title={b.message}>{(b.message || '').slice(0, 40)}</div>}</td>
                      <td className="num">{b.summary?.trades ?? '—'}</td>
                      <td className="num">{b.summary?.winRate != null ? `${b.summary.winRate}%` : '—'}</td>
                      <td className={`num ${b.summary?.totalPnl >= 0 ? 'pos' : 'neg'}`}>{b.summary ? signed(b.summary.totalPnl) : '—'}</td>
                      <td className="num">{b.metrics?.profitFactor ?? '—'}</td>
                      <td className="actions">
                        <Link className="btn btn-sm" to={`/review/${b.id}`}>Review</Link>
                        <button className="icon-btn" title="Delete this run" onClick={() => remove(b.id)}>
                          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7"><path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13" /></svg>
                        </button>
                      </td>
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
