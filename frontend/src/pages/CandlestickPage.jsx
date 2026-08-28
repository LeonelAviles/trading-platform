import { useCallback, useContext, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom';
import { createChart, CandlestickSeries, HistogramSeries, CrosshairMode, LineStyle } from 'lightweight-charts';
import { fetchOHLCV, fetchRange, fetchBacktest, deleteBacktest, fetchCVD } from '../api';
import { HeaderSlotContext } from '../headerSlot';
import DrawToolbar from '../components/DrawToolbar';
import DrawingOverlay from '../drawing/DrawingOverlay';
import SettingsModal from '../components/SettingsModal';
import ReplayControls from '../components/ReplayControls';
import DomPanel from '../components/DomPanel';
import AnalysisPanel from '../components/AnalysisPanel';
import ChatPanel from '../components/ChatPanel';
import { intervalToSeconds, remapShapeToInterval } from '../drawing/geometry';
import { useDrawings } from '../hooks/useDrawings';
import { useChartSettings } from '../hooks/useChartSettings';

// Bars requested for the first paint. Aggregating every tick in the store
// takes ~16s regardless of how few bars come back, while a window this size
// returns in well under a second — so the chart draws immediately and the
// full history is swapped in behind it (see the loader below).
const FIRST_PAINT_BARS = 1500;

function candlePoint(b) {
  return { time: b.time, open: b.open, high: b.high, low: b.low, close: b.close };
}

function volumePoint(b) {
  return {
    time: b.time,
    value: b.volume,
    color: b.close >= b.open ? 'rgba(62,207,110,0.5)' : 'rgba(239,68,68,0.5)',
  };
}

function formatVol(v) {
  if (v >= 1e6) return `${(v / 1e6).toFixed(2)}M`;
  if (v >= 1e3) return `${(v / 1e3).toFixed(2)}K`;
  return `${Math.round(v)}`;
}

// A chart is never standalone: this page *is* the review of one backtest,
// named by the route. The strategy and the symbol both come from that job, so
// there is nothing to pick here and no way to end up staring at bars that
// aren't attached to a strategy.
export default function CandlestickPage() {
  const { leading: leadingSlot, main: headerSlot, trailing: trailingSlot } = useContext(HeaderSlotContext);
  const { backtestId } = useParams();
  const navigate = useNavigate();
  const location = useLocation();

  // The job under review, loaded from the route param. Everything else on the
  // page hangs off it — including the symbol, which is why the header has no
  // symbol picker to wander away with.
  const [selectedJob, setSelectedJob] = useState(null);
  const symbol = selectedJob?.symbol || '';

  const [interval, setInterval_] = useState('1min');
  const [status, setStatus] = useState('');
  const [activeTool, setActiveTool] = useState('cursor');
  const [selectedId, setSelectedId] = useState(null);
  const [shapes, setShapes] = useDrawings(symbol);
  const [settings, setSettings] = useChartSettings();
  const [settingsOpen, setSettingsOpen] = useState(false);

  // Replay: null | {phase:'select'} | {phase:'active', idx, playing, speed}
  const [replay, setReplay] = useState(null);
  const prevReplayIdxRef = useRef(null);

  // Index (into barsRef.current) of the bar under the crosshair, for the
  // OHLCV legend; null means "not hovering" — legend falls back to the last bar.
  const [hoverIdx, setHoverIdx] = useState(null);

  const [backtestTrades, setBacktestTrades] = useState([]);
  const [cvdData, setCvdData] = useState([]);

  // Right DOM dock, bottom analysis dock, right assistant dock — all persist
  // their open/closed state. The assistant additionally opens itself when
  // arriving straight off "Run backtest" (see ReviewPicker), so there's
  // somewhere to talk about the run the moment it lands.
  const [domOpen, setDomOpen] = useState(() => localStorage.getItem('domOpen') === 'true');
  const [analysisPanelOpen, setAnalysisPanelOpen] = useState(
    () => localStorage.getItem('analysisPanelOpen') !== 'false',
  );
  const [chatOpen, setChatOpen] = useState(
    () => Boolean(location.state?.openChat) || localStorage.getItem('chatOpen') === 'true',
  );
  useEffect(() => { localStorage.setItem('domOpen', String(domOpen)); }, [domOpen]);
  useEffect(() => { localStorage.setItem('analysisPanelOpen', String(analysisPanelOpen)); }, [analysisPanelOpen]);
  useEffect(() => { localStorage.setItem('chatOpen', String(chatOpen)); }, [chatOpen]);

  const chartAreaRef = useRef(null);
  const chartDivRef = useRef(null);
  const chartRef = useRef(null);
  const candleSeriesRef = useRef(null);
  const volumeSeriesRef = useRef(null);
  // Tracks the bars/interval a symbol's shapes are currently anchored to, so
  // that switching timeframe can re-anchor them through real time instead of
  // leaving their logical-index fields pointing at unrelated bars.
  const barsRef = useRef([]);
  // What the current shapes' logical-index fields are anchored to. Tracked
  // separately from barsRef because the loader below paints a partial window
  // first: barsRef is "what the chart is showing right now", while this is
  // "the dataset the shapes were last remapped against", which only advances
  // once a full load completes.
  const anchorRef = useRef({ bars: [], interval, symbol });

  const [, setTick] = useState(0);
  const forceUpdate = useCallback(() => setTick((n) => n + 1), []);

  useEffect(() => {
    const chart = createChart(chartDivRef.current, {
      layout: { background: { color: settings.background }, textColor: '#e8e8ea' },
      grid: {
        vertLines: { visible: settings.vertGridVisible, color: settings.gridColor },
        horzLines: { visible: settings.horzGridVisible, color: settings.gridColor },
      },
      timeScale: { timeVisible: true, secondsVisible: true, borderColor: '#2c2a33', rightOffset: 35 },
      rightPriceScale: { borderColor: '#2c2a33' },
      crosshair: { mode: CrosshairMode.Normal },
    });
    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: settings.upColor, downColor: settings.downColor,
      borderVisible: settings.borderVisible, borderUpColor: settings.borderUpColor, borderDownColor: settings.borderDownColor,
      wickVisible: settings.wickVisible, wickUpColor: settings.wickUpColor, wickDownColor: settings.wickDownColor,
    });
    const volumeSeries = chart.addSeries(HistogramSeries, {
      priceFormat: { type: 'volume' },
      priceScaleId: 'volume',
    });
    chart.priceScale('volume').applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });

    chartRef.current = chart;
    candleSeriesRef.current = candleSeries;
    volumeSeriesRef.current = volumeSeries;

    const resize = () => {
      // A ResizeObserver callback can still fire once as the element is
      // being torn down on route change, when the ref is already null.
      if (!chartAreaRef.current) return;
      chart.applyOptions({ width: chartAreaRef.current.clientWidth, height: chartAreaRef.current.clientHeight });
      forceUpdate();
    };
    const ro = new ResizeObserver(resize);
    ro.observe(chartAreaRef.current);
    resize();

    chart.timeScale().subscribeVisibleLogicalRangeChange(forceUpdate);

    const onCrosshairMove = (param) => {
      if (!param.time) { setHoverIdx(null); return; }
      const idx = barsRef.current.findIndex((b) => b.time === param.time);
      setHoverIdx(idx >= 0 ? idx : null);
    };
    chart.subscribeCrosshairMove(onCrosshairMove);

    // Dragging the right-side price axis rescales the price scale, but
    // lightweight-charts has no subscribable event for that (only for the
    // time scale, above) — so without this, our SVG overlay's shapes never
    // get told to recompute their pixel position and are left stranded at
    // their old spot. Re-render every frame for the duration of any drag
    // inside the chart area to catch that (and any other internal rescale)
    // regardless of which specific interaction caused it.
    let raf = null;
    const loop = () => { forceUpdate(); raf = requestAnimationFrame(loop); };
    const onPointerDown = () => { if (raf == null) loop(); };
    const onPointerUp = () => { if (raf != null) { cancelAnimationFrame(raf); raf = null; } };
    const area = chartAreaRef.current;
    area.addEventListener('pointerdown', onPointerDown);
    window.addEventListener('pointerup', onPointerUp);

    return () => {
      ro.disconnect();
      chart.unsubscribeCrosshairMove(onCrosshairMove);
      chart.remove();
      area.removeEventListener('pointerdown', onPointerDown);
      window.removeEventListener('pointerup', onPointerUp);
      if (raf != null) cancelAnimationFrame(raf);
    };
    // `settings` intentionally omitted: this only sets the *initial* look on
    // mount. Later changes are applied live by the effect below instead of
    // recreating the whole chart.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [forceUpdate]);

  useEffect(() => {
    if (!chartRef.current || !candleSeriesRef.current) return;
    candleSeriesRef.current.applyOptions({
      upColor: settings.upColor, downColor: settings.downColor,
      borderVisible: settings.borderVisible, borderUpColor: settings.borderUpColor, borderDownColor: settings.borderDownColor,
      wickVisible: settings.wickVisible, wickUpColor: settings.wickUpColor, wickDownColor: settings.wickDownColor,
    });
    chartRef.current.applyOptions({
      layout: { background: { color: settings.background } },
      grid: {
        vertLines: { visible: settings.vertGridVisible, color: settings.gridColor },
        horzLines: { visible: settings.horzGridVisible, color: settings.gridColor },
      },
    });
  }, [settings]);

  // Two-phase load: a recent window first so the chart is usable straight
  // away, then the full history swapped in underneath the same view. Asking
  // for everything up front meant staring at an empty chart for ~30s.
  useEffect(() => {
    if (!symbol) return;
    let cancelled = false;
    // Read the shape anchors once, before either phase moves them.
    const anchor = anchorRef.current;
    setStatus('Loading…');

    const paint = (bars) => {
      candleSeriesRef.current.setData(bars.map(candlePoint));
      volumeSeriesRef.current.setData(bars.map(volumePoint));
      barsRef.current = bars;
    };

    // Re-anchor drawings through real time when the timeframe changed. Runs
    // only against the complete dataset — remapping onto a partial window
    // would strand every shape that falls outside it.
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
        const { start, end } = await fetchRange(symbol);
        if (cancelled) return;

        // Phase 1 — the tail of the series, if that's meaningfully less than
        // all of it.
        const windowStart = end - FIRST_PAINT_BARS * intervalToSeconds(interval);
        const windowed = windowStart > start;
        if (windowed) {
          const head = await fetchOHLCV(symbol, interval, windowStart);
          if (cancelled) return;
          if (head.length) {
            paint(head);
            chartRef.current.timeScale().fitContent();
            setStatus('Loading history…');
            forceUpdate();
          }
        }

        // Phase 2 — everything. Restoring the visible *time* range keeps the
        // viewport where the user left it; phase 1's bars are a suffix of
        // these and carry identical timestamps, so the same range still
        // frames the same candles.
        const bars = await fetchOHLCV(symbol, interval);
        if (cancelled) return;
        const view = windowed ? chartRef.current.timeScale().getVisibleRange() : null;
        paint(bars);
        if (view) chartRef.current.timeScale().setVisibleRange(view);
        else chartRef.current.timeScale().fitContent();
        reanchor(bars);
        setStatus('');
        forceUpdate();
      } catch {
        if (cancelled) return;
        candleSeriesRef.current.setData([]);
        volumeSeriesRef.current.setData([]);
        setStatus('No data for this interval');
      }
    })();

    return () => { cancelled = true; };
  }, [symbol, interval, forceUpdate, setShapes]);

  // CVD (cumulative volume delta) for the analysis panel's CVD tab — fetched
  // independently so a failure here (e.g. a symbol with no MBO side data)
  // never affects the candles.
  useEffect(() => {
    if (!symbol) return;
    let cancelled = false;
    fetchCVD(symbol, interval)
      .then((points) => { if (!cancelled) setCvdData(points); })
      .catch(() => { if (!cancelled) setCvdData([]); });
    return () => { cancelled = true; };
  }, [symbol, interval]);

  // Any data reload (new symbol/interval) invalidates an in-progress replay.
  useEffect(() => {
    setReplay(null);
    prevReplayIdxRef.current = null;
  }, [symbol, interval]);

  // Feed the chart during replay: full slice when (re)entering or jumping,
  // a single update() per bar while advancing.
  useEffect(() => {
    const bars = barsRef.current;
    if (!candleSeriesRef.current || !bars.length) return;
    if (replay?.phase === 'active') {
      const idx = replay.idx;
      if (prevReplayIdxRef.current != null && idx === prevReplayIdxRef.current + 1) {
        const b = bars[idx];
        candleSeriesRef.current.update({ time: b.time, open: b.open, high: b.high, low: b.low, close: b.close });
        volumeSeriesRef.current.update(volumePoint(b));
      } else {
        const slice = bars.slice(0, idx + 1);
        candleSeriesRef.current.setData(slice.map((b) => ({ time: b.time, open: b.open, high: b.high, low: b.low, close: b.close })));
        volumeSeriesRef.current.setData(slice.map(volumePoint));
      }
      prevReplayIdxRef.current = idx;
      forceUpdate();
    } else if (prevReplayIdxRef.current != null) {
      // Just exited replay — restore the full dataset.
      prevReplayIdxRef.current = null;
      candleSeriesRef.current.setData(bars.map((b) => ({ time: b.time, open: b.open, high: b.high, low: b.low, close: b.close })));
      volumeSeriesRef.current.setData(bars.map(volumePoint));
      forceUpdate();
    }
  }, [replay, forceUpdate]);

  // Playback timer.
  useEffect(() => {
    if (replay?.phase !== 'active' || !replay.playing) return;
    const timer = setInterval(() => {
      setReplay((r) => {
        if (!r || r.phase !== 'active') return r;
        if (r.idx >= barsRef.current.length - 1) return { ...r, playing: false };
        return { ...r, idx: r.idx + 1 };
      });
    }, replay.speed);
    return () => clearInterval(timer);
  }, [replay?.phase, replay?.playing, replay?.speed]);

  // While selecting a replay start point, the next chart click picks the bar,
  // and the crosshair becomes a bold blue bar-snapping "select" line (like
  // TradingView), with the horizontal line hidden and a blue time-axis label.
  useEffect(() => {
    if (replay?.phase !== 'select' || !chartRef.current) return;
    const chart = chartRef.current;
    chart.applyOptions({
      crosshair: {
        mode: CrosshairMode.Magnet,
        vertLine: { color: 'rgba(0, 0, 0, 0.65)', width: 1, style: LineStyle.Solid, labelBackgroundColor: 'rgba(0, 0, 0, 0.65)' },
        horzLine: { visible: false, labelVisible: false },
      },
    });
    const handler = (param) => {
      if (!param.point) return;
      const logical = chart.timeScale().coordinateToLogical(param.point.x);
      if (logical == null) return;
      const idx = Math.max(1, Math.min(barsRef.current.length - 1, Math.round(logical)));
      setReplay({ phase: 'active', idx, playing: false, speed: 1000 });
    };
    chart.subscribeClick(handler);
    return () => {
      chart.unsubscribeClick(handler);
      // Restore the normal (neutral, dashed) crosshair on leaving select mode.
      chart.applyOptions({
        crosshair: {
          mode: CrosshairMode.Normal,
          vertLine: { color: '#605f68', width: 1, style: LineStyle.Dashed, labelBackgroundColor: '#2c2a33', visible: true, labelVisible: true },
          horzLine: { color: '#605f68', width: 1, style: LineStyle.Dashed, labelBackgroundColor: '#2c2a33', visible: true, labelVisible: true },
        },
      });
    };
  }, [replay?.phase]);

  // Deleting the run under review deletes the reason for this chart to exist,
  // so it goes back to the chooser rather than leaving empty bars behind.
  const handleDeleteBacktest = useCallback(async () => {
    await deleteBacktest(backtestId).catch(() => {});
    navigate('/review', { replace: true });
  }, [backtestId, navigate]);

  // The reviewed job's trades and live status. Arriving straight off a Run
  // backtest means the job is still running, so poll until it settles and draw
  // its trades the moment they exist. A job id that doesn't resolve is not a
  // chart we're allowed to show — bounce to the chooser.
  useEffect(() => {
    let cancelled = false;
    let timer = null;
    async function load() {
      try {
        const job = await fetchBacktest(backtestId);
        if (cancelled) return;
        setSelectedJob(job);
        setBacktestTrades(job.trades || []);
        // Draw the trades on the bars that produced them: a 15-minute
        // strategy's entries are meaningless against a 1-minute chart. Jobs
        // recorded before interval was stored have none — leave those alone.
        if (job.interval) setInterval_(job.interval);
        if (job.status === 'preparing' || job.status === 'running') {
          timer = setTimeout(load, 2000);
        }
      } catch {
        if (!cancelled) navigate('/review', { replace: true });
      }
    }
    load();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [backtestId, navigate]);

  const bars = barsRef.current;
  const legendIdx = hoverIdx != null && hoverIdx < bars.length ? hoverIdx : bars.length - 1;
  const legendBar = legendIdx >= 0 ? bars[legendIdx] : null;
  const legendPrev = legendIdx > 0 ? bars[legendIdx - 1] : null;
  const legendBase = legendPrev ? legendPrev.close : legendBar?.open;
  const legendChange = legendBar ? legendBar.close - legendBase : 0;
  const legendChangePct = legendBase ? (legendChange / legendBase) * 100 : 0;
  const legendSign = legendChange >= 0 ? 'pos' : 'neg';
  const intervalSeconds = intervalToSeconds(interval);
  const revealTime = replay?.phase === 'active' ? (bars[replay.idx]?.time ?? null) : null;
  const visibleTrades = revealTime == null
    ? backtestTrades
    : backtestTrades.filter((t) => t.entryTime <= revealTime);
  const visibleCvd = revealTime == null ? cvdData : cvdData.filter((p) => p.time <= revealTime);
  const pendingJob = selectedJob && selectedJob.status !== 'done' ? selectedJob : null;

  // "Does the engine trade like me": my manual position shapes vs the visible
  // engine trades, matched by direction and entry within 3 bars.
  const myTrades = shapes.filter((s) => s.type === 'long' || s.type === 'short');
  const matched = visibleTrades.filter((t) =>
    myTrades.some((m) => {
      if (m.type !== t.direction) return false;
      const bar = bars[Math.round(m.entryLogical)];
      return bar && Math.abs(bar.time - t.entryTime) <= 3 * intervalSeconds;
    })
  ).length;

  return (
    <div className="page">
      {leadingSlot && createPortal((
        <div className="review-crumb">
          <Link className="icon-btn" to="/review" title="Back to strategy reviews">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M15 18l-6-6 6-6" />
            </svg>
          </Link>
          <div className="hdr-symbol">
            {symbol && <span className="symbol-avatar">{symbol[0]}</span>}
            <div className="review-crumb-text">
              <span className="review-crumb-name">{selectedJob?.strategyName || 'Loading…'}</span>
              <span className="review-crumb-sub">{symbol}{selectedJob?.interval ? ` · ${selectedJob.interval}` : ''}</span>
            </div>
          </div>
        </div>
      ), leadingSlot)}
      {headerSlot && createPortal((
        <div className="chart-tools">
        <div className="toolbar-sep-v" />
        <div className="interval-group">
          {[
            ['1min', '1m'], ['5min', '5m'], ['15min', '15m'], ['30min', '30m'], ['1h', '1h'], ['4h', '4h'], ['1D', 'D'],
          ].map(([value, label]) => (
            <button
              key={value}
              className={`interval-btn ${interval === value ? 'active' : ''}`}
              onClick={() => setInterval_(value)}
            >
              {label}
            </button>
          ))}
        </div>
        <div className="toolbar-sep-v" />
        <button
          className={`replay-toggle ${replay ? 'active' : ''}`}
          title="Bar replay"
          onClick={() => setReplay(replay ? null : { phase: 'select' })}
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7">
            <path d="M11 19a7 7 0 1 0-6.7-9" />
            <path d="M4 4v6h6" />
            <path d="M11 8.5v4l3 2" />
          </svg>
          Replay
        </button>
        <div className="toolbar-sep-v" />
        <button
          className={`replay-toggle ${domOpen ? 'active' : ''}`}
          title="Depth of Market"
          onClick={() => setDomOpen((o) => !o)}
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7">
            <path d="M4 6h16M4 12h16M4 18h10" />
          </svg>
          DOM
        </button>
        <span className="status">{status}</span>
        <div className="toolbar-spacer" />
        <button
          className={`chat-toggle ${chatOpen ? 'active' : ''}`}
          title="Stratos" onClick={() => setChatOpen((o) => !o)}
        >
          <svg className="chat-toggle-spark" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
            <path d="M9 2.5l1.4 3.7 3.7 1.4-3.7 1.4L9 12.7 7.6 9 3.9 7.6l3.7-1.4z" />
            <path d="M17.5 12l.8 2.1 2.1.8-2.1.8-.8 2.1-.8-2.1-2.1-.8 2.1-.8z" />
          </svg>
          Ask Stratos
        </button>
        </div>
      ), headerSlot)}
      {trailingSlot && createPortal((
        <button className="icon-btn" title="Chart settings" onClick={() => setSettingsOpen(true)}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
            <circle cx="12" cy="12" r="3" />
            <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
          </svg>
        </button>
      ), trailingSlot)}
      <SettingsModal
        open={settingsOpen}
        settings={settings}
        onApply={setSettings}
        onClose={() => setSettingsOpen(false)}
      />
      <div className="page-body">
        <DrawToolbar
          activeTool={activeTool}
          setActiveTool={setActiveTool}
          onClear={() => { setShapes([]); setSelectedId(null); }}
        />
        <div className="chart-area" ref={chartAreaRef}>
          <div className="chart-inner" ref={chartDivRef} />
          {legendBar && (
            <div className="chart-legend">
              <div className="legend-top">
                <span className="legend-symbol">{symbol}</span>
                <span className="legend-price">{legendBar.close.toFixed(2)}</span>
                <span className={`legend-change ${legendSign}`}>
                  {legendChange >= 0 ? '+' : ''}{legendChange.toFixed(2)} ({legendChange >= 0 ? '+' : ''}{legendChangePct.toFixed(2)}%)
                </span>
              </div>
              <div className="legend-ohlc">
                <span>O <b className={legendSign}>{legendBar.open.toFixed(2)}</b></span>
                <span>H <b className={legendSign}>{legendBar.high.toFixed(2)}</b></span>
                <span>L <b className={legendSign}>{legendBar.low.toFixed(2)}</b></span>
                <span>C <b className={legendSign}>{legendBar.close.toFixed(2)}</b></span>
                {legendBar.volume != null && <span>Vol <b>{formatVol(legendBar.volume)}</b></span>}
              </div>
            </div>
          )}
          <DrawingOverlay
            chart={chartRef.current}
            series={candleSeriesRef.current}
            shapes={shapes}
            setShapes={setShapes}
            activeTool={activeTool}
            setActiveTool={setActiveTool}
            selectedId={selectedId}
            setSelectedId={setSelectedId}
            trades={backtestTrades}
            revealTime={revealTime}
            bars={bars}
            intervalSeconds={intervalSeconds}
          />
          {replay && (
            <ReplayControls
              replay={replay}
              total={bars.length}
              onPlayPause={() => setReplay((r) => ({ ...r, playing: !r.playing }))}
              onStep={() => setReplay((r) => (r.idx < bars.length - 1 ? { ...r, idx: r.idx + 1 } : r))}
              onSpeed={(speed) => setReplay((r) => ({ ...r, speed }))}
              onExit={() => setReplay(null)}
            />
          )}

          {/* Floating status for the run under review, bottom-right of the
              chart. There's no backtest picker any more — switching runs means
              going back to the chooser, so the URL always names what's on
              screen. */}
          <div className="backtest-dock">
            {pendingJob && (
              <span className={`compare-chip ${pendingJob.status === 'error' ? 'chip-error' : 'chip-live'}`}>
                {pendingJob.status === 'error'
                  ? `Backtest failed${pendingJob.message ? ` · ${pendingJob.message}` : ''}`
                  : `Running the engine on ${pendingJob.strategyName}…`}
              </span>
            )}
            {backtestTrades.length > 0 && (
              <span className="compare-chip">
                Engine {visibleTrades.length}{revealTime != null ? `/${backtestTrades.length}` : ''} · You {myTrades.length} · Matched {matched}
              </span>
            )}
            <Link className="btn btn-ghost" to="/review">Other reviews</Link>
            <button className="icon-btn" title="Delete this run" onClick={handleDeleteBacktest}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7"><path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13" /></svg>
            </button>
          </div>
        </div>
        {domOpen && (
          <DomPanel symbol={symbol} asOf={revealTime} onClose={() => setDomOpen(false)} />
        )}
        {chatOpen && (
          <ChatPanel
            symbol={symbol}
            interval={interval}
            backtestId={backtestId}
            strategyName={selectedJob?.strategyName}
            backtestStatus={selectedJob?.status}
          />
        )}
      </div>
      <AnalysisPanel
        trades={visibleTrades}
        cvd={visibleCvd}
        open={analysisPanelOpen}
        onToggle={() => setAnalysisPanelOpen((o) => !o)}
      />
    </div>
  );
}
