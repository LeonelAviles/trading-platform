import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { fetchOHLCV, fetchRange, fetchFootprint, fetchTrades, fetchVolumeProfile } from '../api';
import ChartView from './ChartView';
import ReplayBar from './ReplayBar';
import RightDock from './RightDock';
import CvdPane from './CvdPane';
import DrawToolbar from '../components/DrawToolbar';
import SettingsModal from '../components/SettingsModal';
import { candlePoint, volumePoint } from './useChart';
import { useReplay } from './useReplay';
import { useReplaySelect } from './useReplaySelect';
import { useFootprintHistory } from './useFootprintHistory';
import { useLayerSettings, useLayerToggles } from './layerSettings';
import { aggregateFootprints } from './orderflowMath';
import { etDateString, formatEtClock } from './time';
import { DEFAULT_COLOR, intervalToSeconds, remapShapeToInterval } from '../drawing/geometry';
import { useDrawings } from '../hooks/useDrawings';
import { useChartSettings } from '../hooks/useChartSettings';

export const INTERVALS = [
  ['1min', '1m'], ['5min', '5m'], ['15min', '15m'], ['30min', '30m'], ['1h', '1h'], ['4h', '4h'], ['1D', 'D'],
];
// Streamed during replay so switching timeframe mid-session keeps working;
// '1min' must stay first (CvdPane and the footprint rollup feed off it).
const SESSION_TFS = ['1min', '5min', '15min', '30min', '1h', '4h', '1D'];
// Bars requested for the first paint. Aggregating every tick in the store
// takes seconds regardless of how few bars come back, while a window this
// size returns in well under a second — so the chart draws immediately and
// the full history is swapped in behind it.
const FIRST_PAINT_BARS = 1500;
const HISTORY_DAYS = 3;
const LAYER_BUTTONS = [
  ['ladder', 'DOM'], ['tape', 'T&S'], ['heatmap', 'Heat'], ['footprint', 'Footprint'], ['bubbles', 'Bubbles'],
  ['profile', 'Profile'], ['cvd', 'CVD'],
];

const GEAR = (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" /></svg>
);

// The one chart both pages share (PLATFORM-SPEC.md Phase 5): the continuous
// series end to end, tick replay over /ws/replay from any clicked candle, the
// order-flow layers (DOM ladder, T&S, heatmap, footprint, bubbles, profile,
// CVD), drawings, settings and the keyboard. The review page (/review/:id)
// adds the backtest's trades and docks on top of it; the teaching page
// (/chart/:symbol) adds the teaching session. Neither page owns any chart
// loading or replay plumbing of its own — everything that differs between
// them is passed in through the options or the render helpers.
//
// options:
//   symbol, interval, setInterval — the series; the page owns `interval`
//     because the review page sets it from the backtest job.
//   root — instrument root for the "decoding…" message.
//   beforeStart(unixSeconds, dateStr) — optional; may return extra `start`
//     params (teaching session id…) or `false` to abort the replay.
export function useOrderFlowChart({ symbol, interval, setInterval, root, beforeStart }) {
  const [status, setStatus] = useState('');
  const [settings, setSettings] = useChartSettings();
  const [layerSettings, setLayerSettings] = useLayerSettings();
  const [layers, toggleLayer] = useLayerToggles('chartLayers');
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [activeTool, setActiveTool] = useState('cursor');
  const [selectedId, setSelectedId] = useState(null);
  const [shapes, setShapes] = useDrawings(symbol);
  const [date, setDate] = useState('');
  const [selecting, setSelecting] = useState(false);
  const [bookLayer, setBookLayer] = useState(true);
  const [api, setApi] = useState(null);
  const { replay, tick, start, send, stop, subscribe } = useReplay();
  const replaying = replay.status !== 'idle' && replay.status !== 'closed';

  // History bars before the session day (context to the left of the replay).
  const historyRef = useRef({ key: null, bars: [] });
  const [historyVersion, setHistoryVersion] = useState(0);
  const [idleBars, setIdleBars] = useState([]);
  const [footprintHistory, setFootprintHistory] = useState({});
  const [staticTrades, setStaticTrades] = useState([]);
  const [visibleProfile, setVisibleProfile] = useState(null);
  const [view, setView] = useState(null);
  // What the shapes' logical-index fields are anchored to, so switching
  // timeframe re-anchors them through real time instead of leaving them on
  // unrelated bars. Only advances once a full load completes.
  const anchorRef = useRef({ bars: [], interval, symbol });

  const onReady = useCallback((a) => setApi(a), []);

  // Idle view: the full series in two phases — a recent window first so the
  // chart is usable straight away, then everything swapped in underneath the
  // same viewport so any past candle can be scrolled to and picked as a
  // replay start.
  useEffect(() => {
    if (!api || !symbol || replaying) return undefined;
    let cancelled = false;
    const anchor = anchorRef.current;
    setStatus('Loading…');
    const paint = (bars) => {
      setIdleBars(bars);
      api.candleSeries.setData(bars.map(candlePoint));
      api.volumeSeries.setData(bars.map(volumePoint));
    };
    const reanchor = (bars) => {
      if (anchor.symbol === symbol && anchor.interval !== interval && anchor.bars.length && bars.length) {
        const oldSeconds = intervalToSeconds(anchor.interval);
        const newSeconds = intervalToSeconds(interval);
        setShapes((prev) => prev.map((s) => remapShapeToInterval(s, anchor.bars, oldSeconds, bars, newSeconds)));
      }
      anchorRef.current = { bars, interval, symbol };
    };
    (async () => {
      try {
        const { start: s0, end } = await fetchRange(symbol);
        if (cancelled) return;
        const windowStart = end - FIRST_PAINT_BARS * intervalToSeconds(interval);
        const windowed = windowStart > s0;
        if (windowed) {
          const head = await fetchOHLCV(symbol, interval, windowStart);
          if (cancelled) return;
          if (head.length) {
            paint(head);
            api.chart.timeScale().fitContent();
            setStatus('Loading history…');
            api.forceUpdate();
          }
        }
        const bars = await fetchOHLCV(symbol, interval, s0);
        if (cancelled) return;
        const v = windowed ? api.chart.timeScale().getVisibleRange() : null;
        paint(bars);
        if (v) api.chart.timeScale().setVisibleRange(v);
        else api.chart.timeScale().fitContent();
        reanchor(bars);
        setStatus('');
        api.forceUpdate();
      } catch {
        if (cancelled) return;
        paint([]);
        setStatus('No data for this interval');
      }
    })();
    return () => { cancelled = true; };
  }, [api, symbol, interval, replaying, setShapes]);

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
    const all = historyRef.current.bars.concat(replay.bars?.[interval] || []);
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

  // Footprint history for the session day (1-minute; rolled up client-side).
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

  // Footprints for the whole visible chart, not just the replay session: the
  // hook fetches whatever days are scrolled into view (already at `interval`,
  // so no rollup), while the session's own day keeps coming over the socket
  // at 1min and is rolled up here — session data wins where they meet.
  const idleFootprints = useFootprintHistory(
    symbol, interval, !!layers.footprint && !!api && !!symbol, view,
    replaying && replay.dayStart ? replay.dayStart / 1e9 : null,
  );
  const { footprints, live } = useMemo(() => {
    const closed = { ...footprintHistory, ...replay.footprints };
    const agg = aggregateFootprints(closed, replay.footprint, stepSeconds);
    return { footprints: { ...idleFootprints, ...agg.footprints }, live: agg.live };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [idleFootprints, footprintHistory, replay.footprints, replay.footprint, stepSeconds, tick]);

  // Static bubbles / visible profile for the idle view.
  useEffect(() => {
    if (replaying || !symbol || !view || (!layers.bubbles && layerSettings.profileMode !== 'visible')) return undefined;
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

  // Replay starts from a candle the user clicked in select mode: the day is
  // derived from the candle's timestamp.
  const startReplayAt = async (unixSeconds) => {
    const d = etDateString(unixSeconds);
    setDate(d);
    setSelecting(false);
    setStatus('');
    let extra = {};
    if (beforeStart) {
      extra = await beforeStart(unixSeconds, d);
      if (extra === false) return;
    }
    start({
      symbol, fromTs: Math.round(unixSeconds) * 1e9, speed: 1, layers: { book: bookLayer, trades: true, bars: SESSION_TFS }, autoplay: false,
      ...(extra || {}),
    });
  };

  // Select mode: bold vertical line follows the cursor; clicking a candle
  // starts the replay from that candle onwards.
  useReplaySelect(api?.chart, selecting && !replaying, (logical) => {
    const bar = bars[Math.max(0, Math.min(bars.length - 1, logical))];
    if (bar) startReplayAt(bar.time);
  });

  const exitReplay = useCallback(() => { stop(); setSelecting(false); historyRef.current = { key: null, bars: [] }; }, [stop]);

  // A new symbol (the review page loads its job asynchronously) ends any
  // replay that was running against the old one.
  useEffect(() => { exitReplay(); }, [symbol, exitReplay]);

  // Keyboard: space play/pause, → step print, ⇧→ step bar.
  useEffect(() => {
    if (!replaying) return undefined;
    const onKey = (e) => {
      const tag = document.activeElement?.tagName;
      if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') return;
      if (e.code === 'Space') { e.preventDefault(); send({ type: replay.paused ? 'resume' : 'pause' }); }
      if (e.code === 'ArrowRight') { e.preventDefault(); send({ type: 'step', unit: e.shiftKey ? 'bar' : 'tick', n: 1 }); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [replaying, replay, send]);

  const pickPrice = (price) => {
    setShapes((prev) => [...prev, {
      id: crypto.randomUUID(), type: 'hline', x1: 0, x2: Math.max(bars.length, 1) + 200, price, color: DEFAULT_COLOR, lineWidth: 1,
    }]);
  };

  const clockTime = replaying && replay.clock != null ? replay.clock / 1e9 : null;
  const oneMin = replay.bars?.['1min'] || [];
  const rootLabel = root || replay.root || symbol.replace(/1!$/, '');
  const showIdleHint = !replaying && !selecting && bars.length > 0;

  // ---- render helpers ------------------------------------------------------

  // The header toolbar (portalled by the page): intervals, layers, status,
  // then the replay controls; `extra` lands after Replay.
  const renderToolbar = (extra = null) => (
    <div className="chart-tools">
      <div className="toolbar-sep-v" />
      <div className="interval-group">
        {INTERVALS.map(([value, label]) => (
          <button key={value} className={`interval-btn ${interval === value ? 'active' : ''}`} onClick={() => setInterval(value)}>{label}</button>
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
        <label className="session-book" title="Rebuild the L3 book (first use on a day decodes it into the replay cache, ~2 min)">
          <input type="checkbox" checked={bookLayer} onChange={(e) => setBookLayer(e.target.checked)} /> book
        </label>
        <button
          className={`replay-toggle ${selecting || replaying ? 'active' : ''}`}
          title="Tick replay: click a candle to start from there"
          onClick={() => { if (replaying) exitReplay(); else setSelecting((s) => !s); }}
          disabled={!symbol}
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7">
            <path d="M11 19a7 7 0 1 0-6.7-9" />
            <path d="M4 4v6h6" />
            <path d="M11 8.5v4l3 2" />
          </svg>
          Replay
        </button>
        {extra && <div className="toolbar-sep-v" />}
        {extra}
      </div>
    </div>
  );

  const settingsButton = (
    <button className="icon-btn" title="Chart settings" onClick={() => setSettingsOpen(true)}>{GEAR}</button>
  );

  const settingsModal = (
    <SettingsModal
      open={settingsOpen} settings={settings} onApply={setSettings} onClose={() => setSettingsOpen(false)}
      layerSettings={layerSettings} onApplyLayers={setLayerSettings}
    />
  );

  const drawToolbar = (
    <DrawToolbar activeTool={activeTool} setActiveTool={setActiveTool} onClear={() => { setShapes([]); setSelectedId(null); }} />
  );

  // The chart column: ChartView with the replay overlays, plus the CVD pane.
  //   trades/revealTime — overlay for the page's trades (review page).
  //   replayExtra — buttons appended to the replay bar (teaching page).
  //   dock — extra chips for the bottom-right dock.
  //   children — anything else drawn over the chart.
  const renderChart = ({ trades = [], revealTime = null, replayExtra = null, dock = null, children = null } = {}) => (
    <div className="chart-column">
      <ChartView
        symbol={symbol} interval={interval} bars={bars} settings={settings} onReady={onReady} onView={setView}
        layers={layers} layerSettings={layerSettings} tickSize={replay.tickSize}
        footprints={footprints} liveFootprint={live}
        bubbleTrades={replaying ? replay.trades : staticTrades} widenBubbles={!replaying}
        profileBins={profileBins} clockTime={clockTime}
        drawing={{ shapes, setShapes, activeTool, setActiveTool, selectedId, setSelectedId }}
        trades={trades} revealTime={revealTime}
      >
        {replay.status === 'preparing' || replay.status === 'connecting' ? (
          <div className="replay-preparing">
            <div className="replay-preparing-title">{replay.status === 'connecting' ? 'Connecting…' : `Decoding ${rootLabel} ${date} into the replay cache… ${replay.pct}%`}</div>
            <div className="budget-gauge"><div className="budget-fill" style={{ width: `${replay.pct}%` }} /></div>
            {replay.note && <div className="replay-preparing-note">{replay.note}</div>}
          </div>
        ) : null}
        {replay.status === 'error' && <div className="replay-preparing"><div className="replay-preparing-title">{replay.error}</div><button className="btn btn-sm" onClick={exitReplay}>Close</button></div>}
        {selecting && !replaying && (
          <div className="replay-bar">
            <span className="replay-hint">Click a candle to start replay from there</span>
            <button className="replay-btn" onClick={() => setSelecting(false)}>Cancel</button>
          </div>
        )}
        {replaying && replay.status === 'ready' && (
          <ReplayBar
            replay={replay}
            onPlayPause={() => send({ type: replay.paused ? 'resume' : 'pause' })}
            onSpeed={(v) => send({ type: 'speed', value: v })}
            onStep={(unit) => send({ type: 'step', unit, n: 1 })}
            onSeek={(unixS) => send({ type: 'seek', ts: Math.round(unixS) * 1e9 })}
            onExit={exitReplay}
            extra={replayExtra}
          />
        )}
        {replaying && replay.status === 'ready' && replay.error && <div className="replay-toast">{replay.error}</div>}
        {children}
        {(dock || showIdleHint) && (
          <div className="backtest-dock">
            {dock}
            {showIdleHint && (
              <span className="compare-chip">Press Replay, then click a candle · {formatEtClock(bars[bars.length - 1]?.time, { date: true })} ET is the latest bar</span>
            )}
          </div>
        )}
      </ChartView>
      {layers.cvd && replaying && <CvdPane bars={oneMin} onClose={() => toggleLayer('cvd')} />}
    </div>
  );

  const rightDock = (layers.ladder || layers.tape) ? (
    <RightDock replay={replay} layerSettings={layerSettings} onPickPrice={pickPrice} initialTab={layers.ladder ? 'dom' : 'tape'}
      onClose={() => { if (layers.ladder) toggleLayer('ladder'); if (layers.tape) toggleLayer('tape'); }} />
  ) : null;

  return {
    // state
    replay, tick, send, stop, subscribe, replaying, api, bars, clockTime, date, status, setStatus,
    shapes, setShapes, layers, toggleLayer, exitReplay, startReplayAt,
    // chrome
    renderToolbar, settingsButton, settingsModal, drawToolbar, renderChart, rightDock,
  };
}
