import { useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Link, useNavigate, useParams } from 'react-router-dom';
import {
  fetchDataCoverage, fetchInstruments, fetchOHLCV, fetchRange, fetchFootprint, fetchTrades, fetchVolumeProfile,
} from '../api';
import { HeaderSlotContext } from '../headerSlot';
import DrawToolbar from '../components/DrawToolbar';
import SettingsModal from '../components/SettingsModal';
import ChartView from '../chart/ChartView';
import ReplayBar from '../chart/ReplayBar';
import RightDock from '../chart/RightDock';
import CvdPane from '../chart/CvdPane';
import { candlePoint, volumePoint } from '../chart/useChart';
import { useReplay } from '../chart/useReplay';
import { useLayerSettings, useLayerToggles } from '../chart/layerSettings';
import { TeachingDefaults, QuestionDock, FillPrompt } from '../chart/TeachingPanel';
import { loadTeachingDefaults } from '../chart/teachingDefaults';
import { createTeachingSession, endTeachingSession } from '../api';
import { timeToLogical } from '../drawing/geometry';
import { DEFAULT_PROFIT_COLOR, DEFAULT_LOSS_COLOR, DEFAULT_ENTRY_COLOR } from '../drawing/geometry';
import { aggregateFootprints } from '../chart/orderflowMath';
import { etToUnix, formatEtClock } from '../chart/time';
import { intervalToSeconds } from '../drawing/geometry';
import { DEFAULT_COLOR } from '../drawing/geometry';
import { useDrawings } from '../hooks/useDrawings';
import { useChartSettings } from '../hooks/useChartSettings';

const INTERVALS = [['1min', '1m'], ['5min', '5m'], ['15min', '15m']];
const SESSION_TFS = ['1min', '5min', '15min'];
const IDLE_BARS = 1500;
const HISTORY_DAYS = 3;
const LAYER_BUTTONS = [
  ['ladder', 'DOM'], ['tape', 'T&S'], ['heatmap', 'Heat'], ['footprint', 'Footprint'], ['bubbles', 'Bubbles'],
  ['profile', 'Profile'], ['cvd', 'CVD'],
];

// Free chart route (PLATFORM-SPEC.md Phase 5): pick a session, replay it
// tick by tick over /ws/replay with the order-flow layers live. Teaching
// mode (Phase 6) hangs off this page.
export default function ChartPage() {
  const { leading: leadingSlot, main: headerSlot, trailing: trailingSlot } = useContext(HeaderSlotContext);
  const { symbol: symbolParam } = useParams();
  const navigate = useNavigate();
  const symbol = symbolParam || 'ES1!';

  const [instruments, setInstruments] = useState(null);
  const [coverage, setCoverage] = useState(null);
  const [interval, setInterval_] = useState('1min');
  const [status, setStatus] = useState('');
  const [settings, setSettings] = useChartSettings();
  const [layerSettings, setLayerSettings] = useLayerSettings();
  const [layers, toggleLayer] = useLayerToggles('chartLayers');
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [activeTool, setActiveTool] = useState('cursor');
  const [selectedId, setSelectedId] = useState(null);
  const [shapes, setShapes] = useDrawings(symbol);
  const [date, setDate] = useState('');
  const [time, setTime] = useState('09:30');
  const [bookLayer, setBookLayer] = useState(true);
  const [api, setApi] = useState(null);
  const { replay, tick, start, send, stop, subscribe } = useReplay();
  // Teaching mode (Phase 6): a teaching_sessions row is created on toggle and
  // sent with start; fills/questions come back over the same socket.
  const [teaching, setTeaching] = useState(false);
  const [teachingSessionId, setTeachingSessionId] = useState(null);
  const [teachingDefaults, setTeachingDefaults] = useState(() => loadTeachingDefaults('ES'));
  const [defaultsOpen, setDefaultsOpen] = useState(false);
  const [question, setQuestion] = useState(null);
  const [fillPrompt, setFillPrompt] = useState(null);
  const [ending, setEnding] = useState(false);
  const positionShapeRef = useRef(null);
  const replaying = replay.status !== 'idle' && replay.status !== 'closed';

  // History bars before the session day (context to the left of the replay).
  const historyRef = useRef({ key: null, bars: [] });
  const [historyVersion, setHistoryVersion] = useState(0);
  const [idleBars, setIdleBars] = useState([]);
  const [footprintHistory, setFootprintHistory] = useState({});
  const [staticTrades, setStaticTrades] = useState([]);
  const [visibleProfile, setVisibleProfile] = useState(null);
  const [view, setView] = useState(null);

  useEffect(() => {
    fetchInstruments().then(setInstruments).catch(() => {});
    fetchDataCoverage().then((c) => {
      setCoverage(c);
    }).catch(() => {});
  }, []);

  const root = useMemo(() => {
    if (!instruments) return symbol.replace(/1!$/, '');
    const r = Object.values(instruments.roots).find((x) => x.continuous === symbol || new RegExp(x.outrightRegex).test(symbol));
    return r?.root || symbol.replace(/1!$/, '');
  }, [instruments, symbol]);
  const dates = useMemo(() => coverage?.roots?.[root]?.dates || [], [coverage, root]);
  useEffect(() => { if (!date && dates.length) setDate(dates[dates.length - 1]); }, [dates, date]);
  const cachedDays = useMemo(() => new Set((coverage?.replayCache || []).map((d) => `${d.root}:${d.date}`)), [coverage]);

  useEffect(() => { setTeachingDefaults(loadTeachingDefaults(root)); }, [root]);
  const onReady = useCallback((a) => setApi(a), []);

  // Idle view: the tail of the series so the page is never blank.
  useEffect(() => {
    if (!api || replaying) return undefined;
    let cancelled = false;
    setStatus('Loading…');
    (async () => {
      try {
        const { start: s0, end } = await fetchRange(symbol);
        if (cancelled) return;
        const bars = await fetchOHLCV(symbol, interval, Math.max(s0, end - IDLE_BARS * intervalToSeconds(interval)));
        if (cancelled) return;
        setIdleBars(bars);
        api.candleSeries.setData(bars.map(candlePoint));
        api.volumeSeries.setData(bars.map(volumePoint));
        api.chart.timeScale().fitContent();
        setStatus('');
        api.forceUpdate();
      } catch {
        if (!cancelled) setStatus('No data');
      }
    })();
    return () => { cancelled = true; };
  }, [api, symbol, interval, replaying]);

  // Session bars for the chart: history + the replay's bars for `interval`.
  const sessionBars = replay.bars?.[interval] || [];
  const bars = useMemo(() => {
    if (!replaying) return idleBars;
    const h = historyRef.current.bars;
    return h.length ? h.concat(sessionBars) : sessionBars;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [replaying, idleBars, sessionBars, sessionBars.length, historyVersion, tick]);

  // Paint on ready / interval change; update incrementally on bar messages.
  const paintAll = useCallback(() => {
    if (!api) return;
    const h = historyRef.current.bars;
    const all = h.concat(replay.bars?.[interval] || []);
    api.candleSeries.setData(all.map(candlePoint));
    api.volumeSeries.setData(all.map(volumePoint));
    api.chart.timeScale().scrollToRealTime();
    api.forceUpdate();
  }, [api, replay, interval]);

  useEffect(() => {
    if (!api || replay.status !== 'ready') return undefined;
    let cancelled = false;
    const dayStart = replay.dayStart / 1e9;
    const key = `${replay.symbol}:${interval}:${dayStart}`;
    if (historyRef.current.key !== key) {
      historyRef.current = { key, bars: [] };
      fetchOHLCV(symbol, interval, dayStart - HISTORY_DAYS * 86400, dayStart - 1).then((h) => {
        if (cancelled) return;
        historyRef.current = { key, bars: h.filter((b) => b.time < dayStart) };
        setHistoryVersion((n) => n + 1);
        paintAll();
      }).catch(() => {});
    }
    paintAll();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, replay.status, replay.symbol, replay.dayStart, interval]);

  useEffect(() => {
    if (!api) return undefined;
    return subscribe((m) => {
      if (m.type === 'bar' && m.tf === interval) {
        api.candleSeries.update(candlePoint(m.bar));
        api.volumeSeries.update(volumePoint(m.bar));
      } else if (m.type === 'ready') {
        paintAll();
      }
    });
  }, [api, subscribe, interval, paintAll]);

  // Footprint history for the day (1-minute; rolled up client-side).
  useEffect(() => {
    if (!layers.footprint || replay.status !== 'ready' || !replay.dayStart) return undefined;
    let cancelled = false;
    const from = replay.dayStart / 1e9;
    const to = Math.ceil(replay.clock / 1e9) + 1;
    fetchFootprint(replay.symbol, '1min', from, to).then((fp) => {
      if (cancelled) return;
      const m = {};
      for (const b of fp.bars || []) m[b.time] = b.levels;
      setFootprintHistory(m);
    }).catch(() => {});
    return () => { cancelled = true; };
    // Refetch when a new session starts (dayStart/symbol change), not on every clock tick.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [layers.footprint, replay.status, replay.symbol, replay.dayStart]);

  const stepSeconds = intervalToSeconds(interval);
  const { footprints, live } = useMemo(() => {
    const closed = { ...footprintHistory, ...replay.footprints };
    return aggregateFootprints(closed, replay.footprint, stepSeconds);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [footprintHistory, replay.footprints, replay.footprint, stepSeconds, tick]);

  // Static bubbles / visible profile for the idle view.
  useEffect(() => {
    if (replaying || !view || (!layers.bubbles && layerSettings.profileMode !== 'visible')) return undefined;
    const span = view.to - view.from;
    if (span <= 0 || span > 6 * 3600) { setStaticTrades([]); return undefined; }
    let cancelled = false;
    const t = setTimeout(() => {
      if (layers.bubbles) {
        fetchTrades(symbol, view.from, view.to, { limit: 60000 }).then((tr) => { if (!cancelled) setStaticTrades(tr); }).catch(() => {});
      }
      if (layers.profile && layerSettings.profileMode === 'visible') {
        fetchVolumeProfile(symbol, view.from, view.to).then((p) => {
          if (!cancelled) setVisibleProfile((p.bins || []).map((b) => [b.price, b.volume]));
        }).catch(() => {});
      }
    }, 300);
    return () => { cancelled = true; clearTimeout(t); };
  }, [replaying, view, layers.bubbles, layers.profile, layerSettings.profileMode, symbol]);

  const profileBins = useMemo(() => {
    if (!layers.profile) return null;
    if (layerSettings.profileMode === 'visible' && !replaying) return visibleProfile;
    return [...replay.vap.entries()].map(([p, v]) => [p, v]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [layers.profile, layerSettings.profileMode, replaying, visibleProfile, replay.vap, tick]);

  const startSession = async (preset) => {
    if (!date) return;
    let d = date, t = time;
    if (preset === 'rth') { t = '09:30'; setTime(t); }
    if (preset === 'latest') { d = dates[dates.length - 1]; t = '15:00'; setDate(d); setTime(t); }
    const fromTs = etToUnix(d, t.length === 5 ? `${t}:00` : t) * 1e9;
    setStatus('');
    let tsid = teachingSessionId;
    if (teaching && !tsid) {
      try {
        const sess = await createTeachingSession(symbol, d);
        tsid = sess.id;
        setTeachingSessionId(tsid);
      } catch (e) {
        setStatus(`teaching session failed: ${e.message}`);
        return;
      }
    }
    start({ symbol, fromTs, speed: 1, layers: { book: bookLayer, trades: true, bars: SESSION_TFS }, autoplay: false,
      teachingSessionId: teaching ? tsid : undefined,
      teaching: teaching ? { stopTicks: teachingDefaults.stopTicks, targetTicks: teachingDefaults.targetTicks, pauseOnQuestion: teachingDefaults.pauseOnQuestion } : undefined });
  };

  const endTeaching = async () => {
    if (!teachingSessionId) return;
    setEnding(true);
    try {
      await endTeachingSession(teachingSessionId);
      stop();
      navigate(`/teach/${teachingSessionId}`);
    } catch (e) {
      setStatus(`end failed: ${e.message}`);
    } finally {
      setEnding(false);
    }
  };

  const placeOrder = useCallback((side) => {
    if (!replaying || !teaching) return;
    send({ type: 'order', side, contracts: teachingDefaults.contracts, stopTicks: teachingDefaults.stopTicks, targetTicks: teachingDefaults.targetTicks });
  }, [replaying, teaching, send, teachingDefaults]);

  // Teaching messages: fills draw a clock-snapped position shape and open the
  // note prompt; questions open the dock (the server already paused).
  useEffect(() => {
    return subscribe((m) => {
      if (!teaching) return;
      if (m.type === 'question') setQuestion({ id: m.id, kind: m.kind, text: m.text, tradeId: m.tradeId });
      if (m.type === 'fill' && m.position) {
        const pos = m.position;
        if (teachingDefaults.askNotes) setFillPrompt({ id: pos.id, direction: pos.direction, contracts: pos.contracts, entryPrice: pos.entryPrice });
        const allBars = historyRef.current.bars.concat(replay.bars?.[interval] || []);
        const logical = timeToLogical(allBars, intervalToSeconds(interval), pos.entryTime) ?? allBars.length - 1;
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
  }, [subscribe, teaching, teachingDefaults.askNotes, interval, replay, setShapes]);

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

  const exitReplay = () => { stop(); historyRef.current = { key: null, bars: [] }; };

  // Keyboard: space play/pause, → step print, ⇧→ step bar.
  useEffect(() => {
    if (!replaying) return undefined;
    const onKey = (e) => {
      const tag = document.activeElement?.tagName;
      if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') return;
      if (e.code === 'Space') { e.preventDefault(); send({ type: replay.paused ? 'resume' : 'pause' }); }
      if (e.code === 'ArrowRight') { e.preventDefault(); send({ type: 'step', unit: e.shiftKey ? 'bar' : 'tick', n: 1 }); }
      if (!teaching) return;
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
  }, [replaying, replay, send, teaching, placeOrder]);

  const pickPrice = (price) => {
    setShapes((prev) => [...prev, {
      id: crypto.randomUUID(), type: 'hline', x1: 0, x2: Math.max(bars.length, 1) + 200, price, color: DEFAULT_COLOR, lineWidth: 1,
    }]);
  };

  const clockTime = replaying && replay.clock != null ? replay.clock / 1e9 : null;
  const continuous = instruments ? Object.values(instruments.roots).map((r) => r.continuous) : [symbol];
  const oneMin = replay.bars?.['1min'] || [];
  const isCached = cachedDays.has(`${root}:${date}`);

  return (
    <div className="page">
      {leadingSlot && createPortal((
        <div className="review-crumb">
          <Link className="icon-btn" to="/review" title="Strategy reviews">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M15 18l-6-6 6-6" /></svg>
          </Link>
          <div className="hdr-symbol">
            <span className="symbol-avatar">{symbol[0]}</span>
            <select className="symbol-select" value={symbol} onChange={(e) => { exitReplay(); navigate(`/chart/${encodeURIComponent(e.target.value)}`); }}>
              {continuous.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          </div>
        </div>
      ), leadingSlot)}
      {headerSlot && createPortal((
        <div className="chart-tools">
          <div className="toolbar-sep-v" />
          <div className="interval-group">
            {INTERVALS.map(([value, label]) => (
              <button key={value} className={`interval-btn ${interval === value ? 'active' : ''}`} onClick={() => setInterval_(value)}>{label}</button>
            ))}
          </div>
          <div className="toolbar-sep-v" />
          <div className="layer-group">
            {LAYER_BUTTONS.map(([key, label]) => (
              <button key={key} className={`replay-toggle ${layers[key] ? 'active' : ''}`} onClick={() => toggleLayer(key)}>{label}</button>
            ))}
          </div>
          <span className="status">{status}</span>
          <div className="toolbar-spacer" />
          <div className="session-picker">
            <select value={date} onChange={(e) => setDate(e.target.value)} title="Session date">
              {dates.map((d) => <option key={d} value={d}>{d}{cachedDays.has(`${root}:${d}`) ? ' ●' : ''}</option>)}
            </select>
            <input type="time" value={time} onChange={(e) => setTime(e.target.value)} title="Start time (ET)" />
            <label className="session-book" title="Rebuild the L3 book (needs the day in the replay cache; first use decodes it, ~2 min)">
              <input type="checkbox" checked={bookLayer} onChange={(e) => setBookLayer(e.target.checked)} /> book{isCached ? ' (cached)' : ''}
            </label>
            <button className="btn btn-sm" onClick={() => startSession('rth')}>RTH open</button>
            <button className="btn btn-sm" onClick={() => startSession('latest')}>Latest</button>
            <button className="btn btn-sm btn-primary" onClick={() => startSession()}>Replay</button>
            <div className="toolbar-sep-v" />
            <button className={`replay-toggle ${teaching ? 'active' : ''}`} title="Teaching mode: trade the replay, the agent learns your rules"
              onClick={() => { if (replaying) return; setTeaching((t) => !t); setTeachingSessionId(null); }} disabled={replaying}>Teaching</button>
            {teaching && <button className="btn btn-sm" onClick={() => setDefaultsOpen((o) => !o)}>Defaults</button>}
            {teaching && teachingSessionId && replaying && <button className="btn btn-sm btn-primary" onClick={endTeaching} disabled={ending}>End session</button>}
          </div>
        </div>
      ), headerSlot)}
      {trailingSlot && createPortal((
        <button className="icon-btn" title="Chart settings" onClick={() => setSettingsOpen(true)}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" /></svg>
        </button>
      ), trailingSlot)}
      <SettingsModal
        open={settingsOpen} settings={settings} onApply={setSettings} onClose={() => setSettingsOpen(false)}
        layerSettings={layerSettings} onApplyLayers={setLayerSettings}
      />
      <div className="page-body">
        <DrawToolbar activeTool={activeTool} setActiveTool={setActiveTool} onClear={() => { setShapes([]); setSelectedId(null); }} />
        <div className="chart-column">
          <ChartView
            symbol={symbol} interval={interval} bars={bars} settings={settings} onReady={onReady} onView={setView}
            layers={layers} layerSettings={layerSettings} tickSize={replay.tickSize}
            footprints={footprints} liveFootprint={live}
            bubbleTrades={replaying ? replay.trades : staticTrades} widenBubbles={!replaying}
            profileBins={profileBins} clockTime={clockTime}
            drawing={{ shapes, setShapes, activeTool, setActiveTool, selectedId, setSelectedId }}
          >
            {replay.status === 'preparing' || replay.status === 'connecting' ? (
              <div className="replay-preparing">
                <div className="replay-preparing-title">{replay.status === 'connecting' ? 'Connecting…' : `Decoding ${root} ${date} into the replay cache… ${replay.pct}%`}</div>
                <div className="budget-gauge"><div className="budget-fill" style={{ width: `${replay.pct}%` }} /></div>
                {replay.note && <div className="replay-preparing-note">{replay.note}</div>}
              </div>
            ) : null}
            {replay.status === 'error' && <div className="replay-preparing"><div className="replay-preparing-title">{replay.error}</div><button className="btn btn-sm" onClick={exitReplay}>Close</button></div>}
            {replaying && replay.status === 'ready' && (
              <ReplayBar
                replay={replay}
                onPlayPause={() => send({ type: replay.paused ? 'resume' : 'pause' })}
                onSpeed={(v) => send({ type: 'speed', value: v })}
                onStep={(unit) => send({ type: 'step', unit, n: 1 })}
                onSeek={(unixS) => send({ type: 'seek', ts: Math.round(unixS) * 1e9 })}
                onExit={exitReplay}
                extra={teaching ? (
                  <span className="replay-teaching-btns">
                    <button className="btn btn-sm btn-long" onClick={() => placeOrder('buy')} title="Buy market (B)">Long</button>
                    <button className="btn btn-sm btn-short" onClick={() => placeOrder('sell')} title="Sell market (S)">Short</button>
                    <button className="btn btn-sm" onClick={() => send({ type: 'flatten' })} title="Flatten (F)">Flat</button>
                    <button className="btn btn-sm" onClick={() => send({ type: 'mark', kind: 'skipped_setup', payload: { reason: window.prompt('Skipped setup — why?') ?? '' } })} title="Mark skipped setup (K)">Skip</button>
                  </span>
                ) : null}
              />
            )}
            {teaching && (
              <QuestionDock
                question={question}
                onAnswer={(id, text, label) => { send({ type: 'answer', questionId: id, text, label }); setQuestion(null); }}
                onDismiss={() => { setQuestion(null); send({ type: 'resume' }); }}
              />
            )}
            {teaching && (
              <FillPrompt
                fill={fillPrompt}
                onSubmit={(id, confidence, note) => { send({ type: 'annotate', tradeId: id, confidence, note }); setFillPrompt(null); }}
                onDismiss={() => setFillPrompt(null)}
              />
            )}
            {defaultsOpen && teaching && (
              <TeachingDefaults root={root} value={teachingDefaults} onChange={setTeachingDefaults} onClose={() => setDefaultsOpen(false)} />
            )}
            {replaying && replay.status === 'ready' && replay.error && <div className="replay-toast">{replay.error}</div>}
            {replaying && replay.position && (
              <div className="backtest-dock">
                <span className={`compare-chip ${replay.position.unrealizedPnl >= 0 ? 'chip-live' : 'chip-error'}`}>
                  {replay.position.direction} {replay.position.contracts} @ {replay.position.entryPrice} · {replay.position.unrealizedPnl >= 0 ? '+' : ''}{replay.position.unrealizedPnl}
                </span>
              </div>
            )}
            {!replaying && (
              <div className="backtest-dock">
                <span className="compare-chip">Pick a session and press Replay · {formatEtClock(bars[bars.length - 1]?.time, { date: true })} ET is the latest bar</span>
              </div>
            )}
          </ChartView>
          {layers.cvd && replaying && <CvdPane bars={oneMin} onClose={() => toggleLayer('cvd')} />}
        </div>
        {(layers.ladder || layers.tape) && (
          <RightDock replay={replay} layerSettings={layerSettings} onPickPrice={pickPrice} initialTab={layers.ladder ? 'dom' : 'tape'}
            onClose={() => { if (layers.ladder) toggleLayer('ladder'); if (layers.tape) toggleLayer('tape'); }} />
        )}
      </div>
    </div>
  );
}
