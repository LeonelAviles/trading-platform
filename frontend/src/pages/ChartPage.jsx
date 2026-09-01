import { useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { fetchInstruments, createTeachingSession, endTeachingSession } from '../api';
import { HeaderSlotContext } from '../headerSlot';
import { useOrderFlowChart } from '../chart/useOrderFlowChart';
import { TeachingDefaults, QuestionDock, FillPrompt } from '../chart/TeachingPanel';
import { loadTeachingDefaults } from '../chart/teachingDefaults';
import {
  timeToLogical, intervalToSeconds, DEFAULT_PROFIT_COLOR, DEFAULT_LOSS_COLOR, DEFAULT_ENTRY_COLOR,
} from '../drawing/geometry';

// Teaching chart (PLATFORM-SPEC.md Phases 5–6). Charts are only ever on
// screen for one of two reasons: reviewing a backtest's trades (/review/:id)
// or a teaching session — this page is the latter, so teaching mode is always
// on and there is no way in from the navigation; only Teaching / the Desk's
// "Start a teaching session" lead here. The chart itself — tick replay,
// order-flow layers, docks — is `useOrderFlowChart`, shared with the review
// page; this file is only the teaching session on top of it: the
// teaching_sessions row created on replay start, orders and marks over the
// socket, the agent's questions and the fill prompt.
export default function ChartPage() {
  const { leading: leadingSlot, main: headerSlot, trailing: trailingSlot } = useContext(HeaderSlotContext);
  const { symbol: symbolParam } = useParams();
  const navigate = useNavigate();
  const symbol = symbolParam || 'ES1!';

  const [instruments, setInstruments] = useState(null);
  const [interval, setInterval_] = useState('1min');
  const [teachingSessionId, setTeachingSessionId] = useState(null);
  const [teachingDefaults, setTeachingDefaults] = useState(() => loadTeachingDefaults('ES'));
  const [defaultsOpen, setDefaultsOpen] = useState(false);
  const [question, setQuestion] = useState(null);
  const [fillPrompt, setFillPrompt] = useState(null);
  const [ending, setEnding] = useState(false);
  const positionShapeRef = useRef(null);

  useEffect(() => {
    fetchInstruments().then(setInstruments).catch(() => {});
  }, []);

  const root = useMemo(() => {
    if (!instruments) return symbol.replace(/1!$/, '');
    const r = Object.values(instruments.roots).find((x) => x.continuous === symbol || new RegExp(x.outrightRegex).test(symbol));
    return r?.root || symbol.replace(/1!$/, '');
  }, [instruments, symbol]);

  useEffect(() => { setTeachingDefaults(loadTeachingDefaults(root)); }, [root]);

  // Every replay here is a teaching session: the row is created before the
  // socket opens and its id travels with `start`.
  const beforeStart = async (unixSeconds, dateStr) => {
    let tsid = teachingSessionId;
    if (!tsid) {
      try {
        const sess = await createTeachingSession(symbol, dateStr);
        tsid = sess.id;
        setTeachingSessionId(tsid);
      } catch (e) {
        chart.setStatus(`teaching session failed: ${e.message}`);
        return false;
      }
    }
    return {
      teachingSessionId: tsid,
      teaching: { stopTicks: teachingDefaults.stopTicks, targetTicks: teachingDefaults.targetTicks, pauseOnQuestion: teachingDefaults.pauseOnQuestion },
    };
  };

  const chart = useOrderFlowChart({ symbol, interval, setInterval: setInterval_, root, beforeStart });
  const { replay, replaying, send, stop, subscribe, bars, shapes, setShapes } = chart;

  const endTeaching = async () => {
    if (!teachingSessionId) return;
    setEnding(true);
    try {
      await endTeachingSession(teachingSessionId);
      stop();
      navigate(`/teach/${teachingSessionId}`);
    } catch (e) {
      chart.setStatus(`end failed: ${e.message}`);
    } finally {
      setEnding(false);
    }
  };

  const placeOrder = useCallback((side) => {
    if (!replaying) return;
    send({ type: 'order', side, contracts: teachingDefaults.contracts, stopTicks: teachingDefaults.stopTicks, targetTicks: teachingDefaults.targetTicks });
  }, [replaying, send, teachingDefaults]);

  // Teaching messages: fills draw a clock-snapped position shape and open the
  // note prompt; questions open the dock (the server already paused).
  useEffect(() => {
    return subscribe((m) => {
      if (m.type === 'question') setQuestion({ id: m.id, kind: m.kind, text: m.text, tradeId: m.tradeId });
      if (m.type === 'fill' && m.position) {
        const pos = m.position;
        if (teachingDefaults.askNotes) setFillPrompt({ id: pos.id, direction: pos.direction, contracts: pos.contracts, entryPrice: pos.entryPrice });
        const logical = timeToLogical(bars, intervalToSeconds(interval), pos.entryTime) ?? bars.length - 1;
        const shape = {
          id: `pos-${pos.id}`, type: pos.direction, entryLogical: Math.round(logical), entryPrice: pos.entryPrice,
          stopPrice: pos.stop ?? pos.entryPrice, targetPrice: pos.target ?? pos.entryPrice, endLogical: Math.round(logical) + 30,
          profitColor: DEFAULT_PROFIT_COLOR, lossColor: DEFAULT_LOSS_COLOR, entryColor: DEFAULT_ENTRY_COLOR, lineWidth: 1, teaching: true,
        };
        positionShapeRef.current = { id: shape.id, stop: shape.stopPrice, target: shape.targetPrice, posId: pos.id };
        setShapes((prev) => [...prev.filter((s) => !s.teaching), shape]);
      }
      if (m.type === 'fill' && m.trade) {
        positionShapeRef.current = null;
        setShapes((prev) => prev.map((s) => (s.teaching ? { ...s, locked: true, teaching: false, closed: true } : s)));
      }
    });
  }, [subscribe, teachingDefaults.askNotes, interval, bars, setShapes]);

  // Dragging the open position's stop/target modifies the simulated order.
  useEffect(() => {
    const ref = positionShapeRef.current;
    if (!ref) return;
    const shape = shapes.find((s) => s.id === ref.id);
    if (!shape) return;
    if (shape.stopPrice !== ref.stop || shape.targetPrice !== ref.target) {
      ref.stop = shape.stopPrice;
      ref.target = shape.targetPrice;
      send({ type: 'modify', stopPrice: shape.stopPrice, targetPrice: shape.targetPrice });
    }
  }, [shapes, send]);

  // Teaching hotkeys: B buy, S sell, F flatten, K skipped setup, N note.
  // (Space / → for the replay itself live in the shared chart.)
  useEffect(() => {
    if (!replaying) return undefined;
    const onKey = (e) => {
      const tag = document.activeElement?.tagName;
      if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') return;
      if (e.key === 'b' || e.key === 'B') placeOrder('buy');
      if (e.key === 's' || e.key === 'S') placeOrder('sell');
      if (e.key === 'f' || e.key === 'F') send({ type: 'flatten' });
      if (e.key === 'k' || e.key === 'K') {
        const reason = window.prompt('Skipped setup — why did you pass?') ?? '';
        send({ type: 'mark', kind: 'skipped_setup', payload: { reason } });
      }
      if (e.key === 'n' || e.key === 'N') {
        const note = window.prompt('Note at the replay clock');
        if (note) send({ type: 'mark', kind: 'annotation', payload: { note } });
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [replaying, send, placeOrder]);

  const continuous = instruments ? Object.values(instruments.roots).map((r) => r.continuous) : [symbol];

  return (
    <div className="page">
      {leadingSlot && createPortal((
        <div className="review-crumb">
          <Link className="icon-btn" to="/teaching" title="Teaching sessions">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M15 18l-6-6 6-6" /></svg>
          </Link>
          <div className="hdr-symbol">
            <span className="symbol-avatar">{symbol[0]}</span>
            <select className="symbol-select" value={symbol} onChange={(e) => { chart.exitReplay(); navigate(`/chart/${encodeURIComponent(e.target.value)}`); }}>
              {continuous.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          </div>
        </div>
      ), leadingSlot)}
      {headerSlot && createPortal(chart.renderToolbar(
        <>
          <button className="btn btn-sm" onClick={() => setDefaultsOpen((o) => !o)}>Defaults</button>
          {teachingSessionId && replaying && <button className="btn btn-sm btn-primary" onClick={endTeaching} disabled={ending}>End session</button>}
        </>
      ), headerSlot)}
      {trailingSlot && createPortal(chart.settingsButton, trailingSlot)}
      {chart.settingsModal}
      <div className="page-body">
        {chart.drawToolbar}
        {chart.renderChart({
          replayExtra: (
            <span className="replay-teaching-btns">
              <button className="btn btn-sm btn-long" onClick={() => placeOrder('buy')} title="Buy market (B)">Long</button>
              <button className="btn btn-sm btn-short" onClick={() => placeOrder('sell')} title="Sell market (S)">Short</button>
              <button className="btn btn-sm" onClick={() => send({ type: 'flatten' })} title="Flatten (F)">Flat</button>
              <button className="btn btn-sm" onClick={() => send({ type: 'mark', kind: 'skipped_setup', payload: { reason: window.prompt('Skipped setup — why?') ?? '' } })} title="Mark skipped setup (K)">Skip</button>
            </span>
          ),
          dock: replaying && replay.position ? (
            <span className={`compare-chip ${replay.position.unrealizedPnl >= 0 ? 'chip-live' : 'chip-error'}`}>
              {replay.position.direction} {replay.position.contracts} @ {replay.position.entryPrice} · {replay.position.unrealizedPnl >= 0 ? '+' : ''}{replay.position.unrealizedPnl}
            </span>
          ) : null,
          children: (
            <>
              <QuestionDock
                question={question}
                onAnswer={(id, text, label) => { send({ type: 'answer', questionId: id, text, label }); setQuestion(null); }}
                onDismiss={() => { setQuestion(null); send({ type: 'resume' }); }}
              />
              <FillPrompt
                fill={fillPrompt}
                onSubmit={(id, confidence, note) => { send({ type: 'annotate', tradeId: id, confidence, note }); setFillPrompt(null); }}
                onDismiss={() => setFillPrompt(null)}
              />
              {defaultsOpen && (
                <TeachingDefaults root={root} value={teachingDefaults} onChange={setTeachingDefaults} onClose={() => setDefaultsOpen(false)} />
              )}
            </>
          ),
        })}
        {chart.rightDock}
      </div>
    </div>
  );
}
