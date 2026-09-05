import { useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import { Link } from 'react-router-dom';
import { fetchBacktests, fetchStrategies } from '../api';
import { HeaderSlotContext } from '../headerSlot';
import { Card, EmptyState, PageHeader, StatusChip } from '../components/ui';
import { fmtWhen, signed } from '../format';

// /backtests — a read-only list of every run; each opens on its review chart.
// Backtests are started from a strategy page, never from here.
export default function BacktestsPage() {
  const { leading: leadingSlot } = useContext(HeaderSlotContext);
  const [strategies, setStrategies] = useState([]);
  const [backtests, setBacktests] = useState([]);
  const [loading, setLoading] = useState(true);
  const [filterStrategy, setFilterStrategy] = useState('');
  const [filterStatus, setFilterStatus] = useState('');

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

  return (
    <div className="page">
      {leadingSlot && createPortal(<div className="hdr-title">Backtests</div>, leadingSlot)}
      <div className="page-scroll"><div className="page-inner">
        <PageHeader
          title="Backtests"
          subtitle={`${backtests.length} run${backtests.length === 1 ? '' : 's'}${running ? ` · ${running} running` : ''}. A review opens the run on its chart with the trades, validation, Monte Carlo and regimes.`}
          actions={<Link className="btn" to="/strategies">Strategies</Link>}
        />

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
            <EmptyState title="No backtests" text="Runs started from a strategy page will appear here." />
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
                      <td className="actions"><Link className="btn btn-sm" to={`/review/${b.id}`}>Review</Link></td>
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
