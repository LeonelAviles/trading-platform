import { useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import { Link, useNavigate } from 'react-router-dom';
import { createBacktest, createValidation, fetchBacktests, fetchStrategies } from '../api';
import { HeaderSlotContext } from '../headerSlot';
import { describeExpr } from '../spec/describe';
import { Card, EmptyState, PageHeader, StatusChip } from '../components/ui';
import { fmtWhen } from '../format';

const STATUSES = ['draft', 'testing', 'candidate', 'forward_test', 'live', 'rejected', 'retired'];

// /strategies — a read-only list. Strategies are authored on disk (VS Code), not here.
export default function StrategiesPage() {
  const { leading: leadingSlot } = useContext(HeaderSlotContext);
  const navigate = useNavigate();
  const [strategies, setStrategies] = useState([]);
  const [backtests, setBacktests] = useState([]);
  const [loading, setLoading] = useState(true);
  const [q, setQ] = useState('');
  const [status, setStatus] = useState('');
  const [busy, setBusy] = useState('');
  const [toast, setToast] = useState('');
  const [error, setError] = useState('');

  const refresh = useCallback(async () => {
    const [s, b] = await Promise.all([fetchStrategies().catch(() => []), fetchBacktests().catch(() => [])]);
    setStrategies(s);
    setBacktests(b);
    setLoading(false);
  }, []);
  useEffect(() => { refresh(); const id = setInterval(refresh, 10000); return () => clearInterval(id); }, [refresh]);
  useEffect(() => { if (!toast) return undefined; const t = setTimeout(() => setToast(''), 4000); return () => clearTimeout(t); }, [toast]);

  const byStrategy = useMemo(() => {
    const m = new Map();
    for (const b of backtests) {
      const list = m.get(b.strategyId) || [];
      list.push(b);
      m.set(b.strategyId, list);
    }
    return m;
  }, [backtests]);

  const rows = strategies
    .filter((s) => !status || s.status === status)
    .filter((s) => !q || (s.name || '').toLowerCase().includes(q.toLowerCase()))
    .map((s) => {
      const runs = byStrategy.get(s.id) || [];
      const latestIs = runs.find((b) => b.windowKind === 'is' && b.status === 'done' && b.metrics?.verdict);
      const running = runs.filter((b) => ['queued', 'running'].includes(b.status)).length;
      return { s, runs, latestIs, running, last: runs[0] };
    });

  async function run(id) {
    setBusy(id);
    setError('');
    try {
      const job = await createBacktest(id, { windowKind: 'full' });
      navigate(`/review/${job.id}`);
    } catch (e) {
      setError(e.message);
      setBusy('');
    }
  }
  async function validate(id) {
    setBusy(id);
    try {
      await createValidation(id);
      setToast('Validation queued: in-sample + 3 walk-forward windows. Results land on the strategy page.');
      refresh();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy('');
    }
  }

  return (
    <div className="page">
      {leadingSlot && createPortal(<div className="hdr-title">Strategies</div>, leadingSlot)}
      <div className="page-scroll"><div className="page-inner">
        <PageHeader
          title="Strategies"
          subtitle="Every strategy is a Spec v2 document: instrument, entry trigger, filters, stop, target, sizing. Strategies are written on disk; this page only reads them."
          actions={<Link className="btn" to="/backtests">All backtests</Link>}
        />
        {error && <div className="review-error">{error}</div>}

        <Card>
          <div className="toolbar-row">
            <input type="search" placeholder="Search by name…" value={q} onChange={(e) => setQ(e.target.value)} />
            <select value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="">All statuses</option>
              {STATUSES.map((s) => <option key={s} value={s}>{s.replace('_', ' ')}</option>)}
            </select>
            <span className="inline-note">{rows.length} of {strategies.length}</span>
          </div>
          {loading ? <div className="review-card-empty">Loading…</div> : rows.length === 0 ? (
            <EmptyState title={strategies.length ? 'Nothing matches' : 'No strategies yet'}
              text={strategies.length ? 'Try another filter.' : 'Add a spec on disk and it will show up here.'} />
          ) : (
            <div className="table-wrap">
              <table className="data-table">
                <thead>
                  <tr><th>Name</th><th>Market</th><th>Status</th><th>Entry</th><th>Validation</th><th className="num">Runs</th><th>Last run</th><th /></tr>
                </thead>
                <tbody>
                  {rows.map(({ s, runs, latestIs, running, last }) => (
                    <tr key={s.id}>
                      <td><Link className="row-link" to={`/strategies/${s.id}`}>{s.name || 'Untitled'}</Link><div className="inline-note">{s.lineage?.parentId ? 'variant' : 'root'}</div></td>
                      <td>{s.instrument?.symbol} · {s.timeframes?.primary} · <span className={`review-dir ${s.direction}`}>{s.direction}</span></td>
                      <td><StatusChip status={s.status} /></td>
                      <td className="inline-note" title={describeExpr(s.entry?.trigger)}>{describeExpr(s.entry?.trigger)?.slice(0, 48)}</td>
                      <td>{latestIs ? <><StatusChip status={latestIs.metrics.verdict.status} kind="verdict" /> <span className="inline-note">{latestIs.summary?.trades} trades · PF {latestIs.metrics.profitFactor ?? '—'}</span></> : <span className="inline-note">not validated</span>}</td>
                      <td className="num">{runs.length}{running ? <span className="inline-note"> · {running} running</span> : ''}</td>
                      <td className="inline-note nowrap">{last ? <Link to={`/review/${last.id}`}>{fmtWhen(last.createdAt)}</Link> : '—'}</td>
                      <td className="actions">
                        <button className="btn btn-sm" disabled={busy === s.id} onClick={() => validate(s.id)}>Validate</button>
                        <button className="btn btn-sm btn-primary" disabled={busy === s.id} onClick={() => run(s.id)}>{busy === s.id ? '…' : 'Run backtest'}</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div></div>
      {toast && <div className="toast">{toast}</div>}
    </div>
  );
}
