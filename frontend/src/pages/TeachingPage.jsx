import { useContext, useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { Link, useNavigate } from 'react-router-dom';
import { fetchTeachingSessions } from '../api';
import { HeaderSlotContext } from '../headerSlot';
import { Card, EmptyState, PageHeader, StatusChip } from '../components/ui';
import { fmtWhen } from '../format';

const SYMBOLS = ['ES1!', 'NQ1!'];

// /teaching — sessions so far and the one button that starts a new one.
export default function TeachingPage() {
  const { leading: leadingSlot } = useContext(HeaderSlotContext);
  const navigate = useNavigate();
  const [sessions, setSessions] = useState(null);
  const [symbol, setSymbol] = useState('ES1!');
  useEffect(() => {
    fetchTeachingSessions().then(setSessions).catch(() => setSessions([]));
  }, []);

  const start = <button className="btn btn-primary" onClick={() => navigate(`/chart/${encodeURIComponent(symbol)}?teaching=1`)}>Start a teaching session →</button>;
  return (
    <div className="page">
      {leadingSlot && createPortal(<div className="hdr-title">Teaching</div>, leadingSlot)}
      <div className="page-scroll"><div className="page-inner">
        <PageHeader
          title="Teaching"
          subtitle="Trade a replay the way you actually trade. The agent snapshots every fill, works out the rules you seem to follow, asks when it is unsure, and compiles a strategy you can backtest."
          actions={(
            <>
              <select value={symbol} onChange={(e) => setSymbol(e.target.value)}>{SYMBOLS.map((s) => <option key={s}>{s}</option>)}</select>
              {start}
            </>
          )}
        />
        <div className="steps">
          <div className="step"><div className="step-n">1</div><b>Replay and trade</b><span>Pick a session date, press Replay. Buy / Sell / Flat with the buttons or B / S / F; mark setups you skipped with K.</span></div>
          <div className="step"><div className="step-n">2</div><b>Answer its questions</b><span>The replay pauses when the agent wants to confirm a rule or spots a trade that contradicts one. Short answers are fine.</span></div>
          <div className="step"><div className="step-n">3</div><b>End and compile</b><span>End session → the agent compiles a Spec v2, backtests it over the same range and shows how closely it matches your trades.</span></div>
        </div>

        <Card title="Sessions" sub={sessions ? `${sessions.length} so far` : ''}>
          {sessions === null ? <div className="review-card-empty">Loading…</div> : sessions.length === 0 ? (
            <EmptyState title="No sessions yet" text="Start one — it takes a replay of any ingested day and about fifteen minutes of trading." action={start} />
          ) : (
            <div className="table-wrap">
              <table className="data-table">
                <thead><tr><th>Session</th><th>Market</th><th>Status</th><th className="num">Trades</th><th>Match</th><th>Compiled strategy</th><th /></tr></thead>
                <tbody>
                  {sessions.map((s) => (
                    <tr key={s.id}>
                      <td><Link className="row-link" to={`/teach/${s.id}`}>{s.dateFrom ? s.dateFrom.slice(0, 16).replace('T', ' ') : fmtWhen(s.createdAt)}</Link><div className="inline-note">started {fmtWhen(s.createdAt)}</div></td>
                      <td>{s.symbol}</td>
                      <td><StatusChip status={s.status} /></td>
                      <td className="num">{s.trades}</td>
                      <td className="inline-note">{s.similarity ? `precision ${Number(s.similarity.precision).toFixed(2)} · recall ${Number(s.similarity.recall).toFixed(2)}` : '—'}</td>
                      <td>{s.compiledStrategyId ? <Link to={`/strategies/${s.compiledStrategyId}`}>open →</Link> : <span className="inline-note">not compiled</span>}</td>
                      <td className="actions"><Link className="btn btn-sm" to={`/teach/${s.id}`}>Open</Link></td>
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
