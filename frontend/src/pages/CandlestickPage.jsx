import { useCallback, useEffect, useRef, useState } from 'react';
import { createChart, CandlestickSeries, HistogramSeries, CrosshairMode } from 'lightweight-charts';
import { fetchOHLCV } from '../api';
import DrawToolbar from '../components/DrawToolbar';
import DrawingOverlay from '../drawing/DrawingOverlay';
import SettingsModal from '../components/SettingsModal';
import { intervalToSeconds, remapShapeToInterval } from '../drawing/geometry';
import { useDrawings } from '../hooks/useDrawings';
import { useChartSettings } from '../hooks/useChartSettings';

export default function CandlestickPage({ symbol }) {
  const [interval, setInterval_] = useState('1min');
  const [status, setStatus] = useState('');
  const [activeTool, setActiveTool] = useState('cursor');
  const [selectedId, setSelectedId] = useState(null);
  const [shapes, setShapes] = useDrawings(symbol);
  const [settings, setSettings] = useChartSettings();
  const [settingsOpen, setSettingsOpen] = useState(false);

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

  return (
    <div className="page">
      <div className="page-toolbar">
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
        <span className="status">{status}</span>
        <button className="icon-btn" title="Chart settings" onClick={() => setSettingsOpen(true)}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
            <circle cx="12" cy="12" r="3" />
            <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
          </svg>
        </button>
      </div>
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
          />
        </div>
      </div>
    </div>
  );
}
