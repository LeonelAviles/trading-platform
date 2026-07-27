import { useCallback, useContext, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { createChart, CandlestickSeries, HistogramSeries, CrosshairMode, LineStyle } from 'lightweight-charts';
import { fetchOHLCV, fetchBacktests, fetchBacktest, deleteBacktest } from '../api';
import { HeaderSlotContext } from '../headerSlot';
import DrawToolbar from '../components/DrawToolbar';
import DrawingOverlay from '../drawing/DrawingOverlay';
import SettingsModal from '../components/SettingsModal';
import ReplayControls from '../components/ReplayControls';
import BacktestPicker from '../components/BacktestPicker';
import { intervalToSeconds, remapShapeToInterval } from '../drawing/geometry';
import { useDrawings } from '../hooks/useDrawings';
import { useChartSettings } from '../hooks/useChartSettings';

function volumePoint(b) {
  return {
    time: b.time,
    value: b.volume,
    color: b.close >= b.open ? 'rgba(38,166,154,0.5)' : 'rgba(239,83,80,0.5)',
  };
}

export default function CandlestickPage({ symbol }) {
  const headerSlot = useContext(HeaderSlotContext);
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

  // Backtest overlay
  const [backtests, setBacktests] = useState([]);
  const [selectedBacktestId, setSelectedBacktestId] = useState('');
  const [backtestTrades, setBacktestTrades] = useState([]);

  const chartAreaRef = useRef(null);
  const chartDivRef = useRef(null);
  const chartRef = useRef(null);
  const candleSeriesRef = useRef(null);
  const volumeSeriesRef = useRef(null);
  // Tracks the bars/interval a symbol's shapes are currently anchored to, so
  // that switching timeframe can re-anchor them through real time instead of
  // leaving their logical-index fields pointing at unrelated bars.
  const barsRef = useRef([]);
  const intervalRef = useRef(interval);
  const symbolRef = useRef(symbol);

  const [, setTick] = useState(0);
  const forceUpdate = useCallback(() => setTick((n) => n + 1), []);

  useEffect(() => {
    const chart = createChart(chartDivRef.current, {
      layout: { background: { color: settings.background }, textColor: '#d1d4dc' },
      grid: {
        vertLines: { visible: settings.vertGridVisible, color: settings.gridColor },
        horzLines: { visible: settings.horzGridVisible, color: settings.gridColor },
      },
      timeScale: { timeVisible: true, secondsVisible: true, borderColor: '#2a2e3d', rightOffset: 35 },
      rightPriceScale: { borderColor: '#2a2e3d' },
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

  useEffect(() => {
    if (!symbol) return;
    let cancelled = false;
    setStatus('Loading…');
    fetchOHLCV(symbol, interval)
      .then((bars) => {
        if (cancelled) return;
        candleSeriesRef.current.setData(bars.map((b) => ({ time: b.time, open: b.open, high: b.high, low: b.low, close: b.close })));
        volumeSeriesRef.current.setData(
          bars.map((b) => ({
            time: b.time,
            value: b.volume,
            color: b.close >= b.open ? 'rgba(38,166,154,0.5)' : 'rgba(239,83,80,0.5)',
          }))
        );

        if (symbolRef.current === symbol && intervalRef.current !== interval && barsRef.current.length && bars.length) {
          const oldBars = barsRef.current, oldSeconds = intervalToSeconds(intervalRef.current);
          const newSeconds = intervalToSeconds(interval);
          setShapes((prev) => prev.map((s) => remapShapeToInterval(s, oldBars, oldSeconds, bars, newSeconds)));
        }
        barsRef.current = bars;
        intervalRef.current = interval;
        symbolRef.current = symbol;
        chartRef.current.timeScale().fitContent();
        setStatus(`${bars.length} bars`);
        forceUpdate();
      })
      .catch(() => {
        if (!cancelled) {
          candleSeriesRef.current.setData([]);
          volumeSeriesRef.current.setData([]);
          setStatus('No data for this interval');
        }
      });
    return () => { cancelled = true; };
  }, [symbol, interval, forceUpdate]);

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
        vertLine: { color: '#4c8dff', width: 1, style: LineStyle.Solid, labelBackgroundColor: '#4c8dff' },
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
          vertLine: { color: '#787b86', width: 1, style: LineStyle.Dashed, labelBackgroundColor: '#2a2e3d', visible: true, labelVisible: true },
          horzLine: { color: '#787b86', width: 1, style: LineStyle.Dashed, labelBackgroundColor: '#2a2e3d', visible: true, labelVisible: true },
        },
      });
    };
  }, [replay?.phase]);

  // Backtest list + selected job's trades.
  const refreshBacktests = useCallback(() => {
    fetchBacktests().then(setBacktests).catch(() => setBacktests([]));
  }, []);
  useEffect(() => { refreshBacktests(); }, [refreshBacktests]);

  const handleDeleteBacktest = useCallback(async (id) => {
    await deleteBacktest(id).catch(() => {});
    setSelectedBacktestId((cur) => (cur === id ? '' : cur));
    refreshBacktests();
  }, [refreshBacktests]);

  useEffect(() => {
    if (!selectedBacktestId) { setBacktestTrades([]); return; }
    let cancelled = false;
    fetchBacktest(selectedBacktestId)
      .then((job) => { if (!cancelled) setBacktestTrades(job.trades || []); })
      .catch(() => { if (!cancelled) setBacktestTrades([]); });
    return () => { cancelled = true; };
  }, [selectedBacktestId]);

  // Backtests belong to a symbol; changing symbol clears the selection.
  useEffect(() => { setSelectedBacktestId(''); }, [symbol]);

  const bars = barsRef.current;
  const intervalSeconds = intervalToSeconds(interval);
  const revealTime = replay?.phase === 'active' ? (bars[replay.idx]?.time ?? null) : null;
  const visibleTrades = revealTime == null
    ? backtestTrades
    : backtestTrades.filter((t) => t.entryTime <= revealTime);
  const symbolBacktests = backtests.filter((b) => b.symbol === symbol && b.status === 'done');

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
        <span className="status">{status}</span>

        <div className="toolbar-spacer" />

        {selectedBacktestId && backtestTrades.length > 0 && (
          <span className="compare-chip">
            Engine {visibleTrades.length}{revealTime != null ? `/${backtestTrades.length}` : ''} · You {myTrades.length} · Matched {matched}
          </span>
        )}
        <BacktestPicker
          backtests={symbolBacktests}
          value={selectedBacktestId}
          onChange={setSelectedBacktestId}
          onDelete={handleDeleteBacktest}
        />
        <button className="icon-btn" title="Refresh backtests" onClick={refreshBacktests}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7"><path d="M20 11a8 8 0 1 0-2.3 6.3" /><path d="M20 4v7h-7" /></svg>
        </button>
        <div className="toolbar-sep-v" />
        <button className="icon-btn" title="Chart settings" onClick={() => setSettingsOpen(true)}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
            <circle cx="12" cy="12" r="3" />
            <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
          </svg>
        </button>
        </div>
      ), headerSlot)}
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
        </div>
      </div>
    </div>
  );
}
