import { useCallback, useContext, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Link, useParams } from 'react-router-dom';
import { createChart, CandlestickSeries } from 'lightweight-charts';
import {
  compileTeachingSession, fetchAgentRun, fetchTeachingSession, fetchTeachingSnapshot, labelTeachingEntry, pickTeachingStrategy,
} from '../api';
import { HeaderSlotContext } from '../headerSlot';
import { AgentRunCard } from '../components/AgentRuns';
import { formatEtClock } from '../chart/time';

const fmt = (v, d = 2) => (v == null || Number.isNaN(v) ? '—' : Number(v).toFixed(d));
const pct = (v) => (v == null ? '—' : `${Math.round(v * 100)}%`);
const LABELS = [['valid_skip', 'Valid skip'], ['missed', 'Missed'], ['rule_too_loose', 'Rule too loose']];

// Mini chart from a snapshot's bars (no data fetch — the snapshot is the record).
function SnapshotChart({ snapshot }) {
  const ref = useRef(null);
  useEffect(() => {
    if (!ref.current || !snapshot) return undefined;
    const chart = createChart(ref.current, {
      layout: { background: { color: '#0a0a0c' }, textColor: '#e8e8ea' }, width: ref.current.clientWidth, height: 220,
      timeScale: { timeVisible: true, secondsVisible: false }, grid: { vertLines: { color: '#17171b' }, horzLines: { color: '#17171b' } },
    });
    const series = chart.addSeries(CandlestickSeries, { upColor: '#3ecf6e', downColor: '#ef4444', borderVisible: false, wickUpColor: '#3ecf6e', wickDownColor: '#ef4444' });
    const bars = (snapshot.bars?.['1min'] || []).map((b) => ({ time: b.time, open: b.open, high: b.high, low: b.low, close: b.close }));
    series.setData(bars);
    const p = snapshot.position || snapshot.trade;
    if (p) {
      series.createPriceLine({ price: p.entryPrice, color: '#d1d4dc', lineWidth: 1, title: 'entry' });
      if (p.stop) series.createPriceLine({ price: p.stop, color: '#ef5350', lineWidth: 1, title: 'stop' });
      if (p.target) series.createPriceLine({ price: p.target, color: '#26a69a', lineWidth: 1, title: 'target' });
    }
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [snapshot]);
  return <div className="snapshot-chart" ref={ref} />;
}

export default function TeachPage() {
  const { sessionId } = useParams();
  const { leading: leadingSlot, main: headerSlot } = useContext(HeaderSlotContext);
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState(null);
  const [selected, setSelected] = useState(null);
  const [snapshot, setSnapshot] = useState(null);
  const [run, setRun] = useState(null);
  const [tab, setTab] = useState('trades');

  const load = useCallback(async () => {
    try {
      const d = await fetchTeachingSession(sessionId);
      setDetail(d);
      if (d.compileRunId) fetchAgentRun(d.compileRunId, false).then(setRun).catch(() => {});
    } catch (e) {
      setError(e.message);
    }
  }, [sessionId]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (!detail || !['compiling'].includes(detail.status)) return undefined;
    const t = setInterval(load, 4000);
    return () => clearInterval(t);
  }, [detail, load]);

  useEffect(() => {
    if (!selected) { setSnapshot(null); return; }
    fetchTeachingSnapshot(sessionId, selected).then(setSnapshot).catch(() => setSnapshot(null));
  }, [sessionId, selected]);

  if (error) return <div className="review-page"><div className="review-error">{error}</div></div>;
  if (!detail) return <div className="review-page"><div className="review-empty">Loading…</div></div>;

  const sim = detail.similarity;
  const hyps = detail.events.filter((e) => e.type === 'hypothesis_update');
  const skipped = detail.events.filter((e) => e.type === 'skipped_setup');
  const fpLabels = new Map(detail.events.filter((e) => e.type === 'fp_label').map((e) => [e.payload.entryTime, e.payload.label]));
  const candidates = sim ? [sim, ...(sim.refinements || [])] : [];

  return (
    <div className="page teach-page">
      {leadingSlot && createPortal((
        <div className="review-crumb">
          <Link className="icon-btn" to={`/chart/${encodeURIComponent(detail.symbol)}`} title="Back to the chart">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M15 18l-6-6 6-6" /></svg>
          </Link>
          <div className="hdr-symbol">
            <span className="symbol-avatar">{detail.symbol[0]}</span>
            <div className="review-crumb-text">
              <span className="review-crumb-name">Teaching session · {detail.dateFrom || ''}</span>
              <span className="review-crumb-sub">{detail.symbol} · {detail.status} · {detail.trades.length} trades</span>
            </div>
          </div>
        </div>
      ), leadingSlot)}
      {headerSlot && createPortal((
        <div className="chart-tools">
          <div className="toolbar-spacer" />
          <button className="btn btn-sm" onClick={async () => { await compileTeachingSession(sessionId); load(); }} disabled={detail.status === 'compiling'}>
            {detail.compiledStrategyId ? 'Compile again' : 'Compile'}
          </button>
          {detail.compiledStrategyId && <Link className="btn btn-sm btn-primary" to={`/strategies/${detail.compiledStrategyId}`}>Open strategy</Link>}
        </div>
      ), headerSlot)}
      <div className="teach-body">
        <div className="teach-main">
          <div className="modal-tabs">
            {[['trades', 'Trades'], ['questions', 'Questions'], ['hypothesis', 'Hypothesis'], ['similarity', 'Similarity'], ['compile', 'Compile run']].map(([id, label]) => (
              <button key={id} className={`modal-tab ${tab === id ? 'active' : ''}`} onClick={() => setTab(id)}>{label}</button>
            ))}
          </div>
          {tab === 'trades' && (
            <div className="teach-split">
              <table className="trades-table">
                <thead><tr><th>#</th><th>Dir</th><th>Entry (ET)</th><th>Entry</th><th>Stop</th><th>Target</th><th>Exit</th><th>Reason</th><th>PnL</th><th>Conf</th><th>Note</th></tr></thead>
                <tbody>
                  {detail.trades.map((t, i) => (
                    <tr key={t.id} className={selected === t.id ? 'active' : ''} onClick={() => setSelected(t.id)}>
                      <td>{i + 1}</td><td className={t.direction === 'long' ? 'pos' : 'neg'}>{t.direction}</td>
                      <td>{formatEtClock(t.entryTime)}</td><td>{fmt(t.entryPrice)}</td><td>{fmt(t.stopPrice)}</td><td>{fmt(t.targetPrice)}</td>
                      <td>{fmt(t.exitPrice)}</td><td>{t.exitReason || '—'}</td>
                      <td className={(t.pnlUsd || 0) >= 0 ? 'pos' : 'neg'}>{fmt(t.pnlUsd)}</td><td>{t.confidence ?? '—'}</td><td>{t.note || ''}</td>
                    </tr>
                  ))}
                  {skipped.map((e) => (
                    <tr key={e.id} className="muted" onClick={() => setSelected(e.payload.source === 'user' ? `mark-${e.id}` : null)}>
                      <td>K</td><td colSpan={2}>{formatEtClock(e.payload.time || e.ts / 1e9)}</td>
                      <td colSpan={8}>skipped setup ({e.payload.source}) {e.payload.reason || e.payload.ruleText || ''}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="snapshot-pane">
                {snapshot ? (
                  <>
                    <SnapshotChart snapshot={snapshot} />
                    <div className="snapshot-meta">
                      <div><b>Levels</b> OR {fmt(snapshot.levels?.openingRange?.low)}–{fmt(snapshot.levels?.openingRange?.high)} · VWAP {fmt(snapshot.levels?.vwap)} · POC {fmt(snapshot.levels?.profile?.poc)}</div>
                      <div><b>Regime</b> {Object.entries(snapshot.regime || {}).map(([k, v]) => `${k}=${v}`).join(' ') || '—'}</div>
                      <div><b>Book</b> {snapshot.book?.bids?.slice(0, 3).map((b) => `${b[0]}×${b[1]}`).join(' ')} | {snapshot.book?.asks?.slice(0, 3).map((a) => `${a[0]}×${a[1]}`).join(' ')}</div>
                      <details><summary>Feature vector ({Object.keys(snapshot.features || {}).length})</summary>
                        <div className="feature-grid">
                          {Object.entries(snapshot.features || {}).filter(([, v]) => v != null).map(([k, v]) => <span key={k}>{k}: <b>{typeof v === 'number' ? fmt(v, 3) : String(v)}</b></span>)}
                        </div>
                      </details>
                    </div>
                  </>
                ) : <div className="analysis-empty">Select a trade to see its snapshot.</div>}
              </div>
            </div>
          )}
          {tab === 'questions' && (
            <div className="teach-list">
              {detail.questions.length === 0 && <div className="analysis-empty">No questions were asked.</div>}
              {detail.questions.map((q) => (
                <div key={q.id} className="teach-q">
                  <div className="question-kind">{q.kind}{q.replayTs ? ` · ${formatEtClock(q.replayTs / 1e9)}` : ''}</div>
                  <div className="question-text">{q.question}</div>
                  <div className="teach-a">{q.answer ? `→ ${q.answer}` : <i>unanswered</i>}</div>
                </div>
              ))}
            </div>
          )}
          {tab === 'hypothesis' && (
            <div className="teach-list">
              {hyps.length === 0 && <div className="analysis-empty">No hypothesis updates yet.</div>}
              {hyps.slice().reverse().map((h) => (
                <div key={h.id} className="teach-q">
                  <div className="question-kind">v{h.payload.version} · {formatEtClock(h.ts / 1e9)}</div>
                  <div className="question-text">{h.payload.summary}</div>
                  <ul>
                    {(h.payload.rules || []).map((r) => (
                      <li key={r.id}><b>{r.id}</b> {r.text} <span className="muted">(conf {fmt(r.confidence, 2)}, supports {r.supports?.length || 0}, contradicts {r.contradicts?.length || 0})</span></li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          )}
          {tab === 'similarity' && (
            <div className="teach-list">
              {!sim && <div className="analysis-empty">Compile the session to get a similarity report.</div>}
              {candidates.map((c, i) => (
                <div key={c.strategyId || i} className={`teach-cand ${detail.compiledStrategyId === c.strategyId ? 'picked' : ''}`}>
                  <div className="teach-cand-head">
                    <b>{i === 0 ? 'Compiled' : `Refinement ${i}`}</b> <Link to={`/strategies/${c.strategyId}`}>{c.strategyId}</Link>
                    <span className="muted">{c.rationale || ''}</span>
                    <button className="btn btn-sm" onClick={async () => { await pickTeachingStrategy(sessionId, c.strategyId); load(); }} disabled={detail.compiledStrategyId === c.strategyId}>
                      {detail.compiledStrategyId === c.strategyId ? 'Picked' : 'Pick'}
                    </button>
                  </div>
                  <div className="analysis-stats-row">
                    <span>Recall <b>{pct(c.recall)}</b></span><span>Precision <b>{pct(c.precision)}</b></span>
                    <span>Matched <b>{c.matched}</b> / you {c.userEntries} / engine {c.engineEntries}</span>
                    <span>Exit Δ <b>{fmt(c.exitSimilarity?.medianExitTickDiff, 1)} ticks</b>, <b>{fmt(c.exitSimilarity?.medianRDiff, 2)} R</b></span>
                    <span>PnL you <b>{fmt(c.pnl?.user)}</b> / engine <b>{fmt(c.pnl?.engine)}</b></span>
                  </div>
                  {c.unmatchedEngine?.length > 0 && (
                    <div className="teach-unmatched">
                      <div className="settings-section-label">ENGINE ENTRIES YOU DID NOT TAKE — label them</div>
                      {c.unmatchedEngine.map((e) => (
                        <div key={e.entryTime} className="teach-unmatched-row">
                          <span>{formatEtClock(e.entryTime)} {e.direction} @ {fmt(e.entryPrice)} → {fmt(e.exitPrice)} ({e.exitReason}, {fmt(e.pnlUsd)})</span>
                          <span>
                            {LABELS.map(([v, l]) => (
                              <button key={v} className={`btn btn-sm ${fpLabels.get(e.entryTime) === v ? 'btn-primary' : ''}`}
                                onClick={async () => { await labelTeachingEntry(sessionId, e.entryTime, v); load(); }}>{l}</button>
                            ))}
                          </span>
                        </div>
                      ))}
                    </div>
                  )}
                  {c.unmatchedUser?.length > 0 && (
                    <div className="muted">Your entries the engine did not take: {c.unmatchedUser.map((u) => `${formatEtClock(u.entryTime)} ${u.direction}`).join(', ')}</div>
                  )}
                </div>
              ))}
            </div>
          )}
          {tab === 'compile' && (
            <div className="teach-list">
              {run ? <AgentRunCard run={run} onChanged={load} /> : <div className="analysis-empty">No compile run yet — press Compile.</div>}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
