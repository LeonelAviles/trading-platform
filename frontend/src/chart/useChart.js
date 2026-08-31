import { useCallback, useEffect, useRef, useState } from 'react';
import { createChart, CandlestickSeries, HistogramSeries, CrosshairMode } from 'lightweight-charts';

// Owns one lightweight-charts instance (candles + volume histogram) inside
// `innerRef`, sized to `areaRef`. Extracted from CandlestickPage so the
// review page and the free chart page share exactly one chart setup.
//
// Returns { api, tick, hoverTime }: `api` is null until the chart exists, then
// { chart, candleSeries, volumeSeries, forceUpdate }. `tick` increments on
// every pan/zoom/resize/drag frame — overlays that compute pixel positions
// re-render off it. `hoverTime` is the crosshair bar time (null when idle).
export function useChart(areaRef, innerRef, settings, { transparent = false } = {}) {
  const [api, setApi] = useState(null);
  const [tick, setTick] = useState(0);
  const [hoverTime, setHoverTime] = useState(null);
  const forceUpdate = useCallback(() => setTick((n) => n + 1), []);
  const settingsRef = useRef(settings);
  settingsRef.current = settings;

  useEffect(() => {
    const s = settingsRef.current;
    const chart = createChart(innerRef.current, {
      layout: { background: { color: transparent ? 'rgba(0,0,0,0)' : s.background }, textColor: '#e8e8ea' },
      grid: {
        vertLines: { visible: s.vertGridVisible, color: s.gridColor },
        horzLines: { visible: s.horzGridVisible, color: s.gridColor },
      },
      timeScale: { timeVisible: true, secondsVisible: true, borderColor: '#2c2a33', rightOffset: 35 },
      rightPriceScale: { borderColor: '#2c2a33' },
      crosshair: { mode: CrosshairMode.Normal },
    });
    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: s.upColor, downColor: s.downColor,
      borderVisible: s.borderVisible, borderUpColor: s.borderUpColor, borderDownColor: s.borderDownColor,
      wickVisible: s.wickVisible, wickUpColor: s.wickUpColor, wickDownColor: s.wickDownColor,
    });
    const volumeSeries = chart.addSeries(HistogramSeries, { priceFormat: { type: 'volume' }, priceScaleId: 'volume' });
    chart.priceScale('volume').applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });

    const resize = () => {
      if (!areaRef.current) return;
      chart.applyOptions({ width: areaRef.current.clientWidth, height: areaRef.current.clientHeight });
      forceUpdate();
    };
    const ro = new ResizeObserver(resize);
    ro.observe(areaRef.current);
    resize();
    chart.timeScale().subscribeVisibleLogicalRangeChange(forceUpdate);
    const onCrosshairMove = (param) => setHoverTime(param.time ?? null);
    chart.subscribeCrosshairMove(onCrosshairMove);

    // Price-axis drags rescale without any subscribable event; re-render
    // every frame while a pointer is down inside the area to catch it.
    let raf = null;
    const loop = () => { forceUpdate(); raf = requestAnimationFrame(loop); };
    const onPointerDown = () => { if (raf == null) loop(); };
    const onPointerUp = () => { if (raf != null) { cancelAnimationFrame(raf); raf = null; } };
    const area = areaRef.current;
    area.addEventListener('pointerdown', onPointerDown);
    window.addEventListener('pointerup', onPointerUp);

    setApi({ chart, candleSeries, volumeSeries, forceUpdate });
    return () => {
      ro.disconnect();
      chart.unsubscribeCrosshairMove(onCrosshairMove);
      chart.remove();
      area.removeEventListener('pointerdown', onPointerDown);
      window.removeEventListener('pointerup', onPointerUp);
      if (raf != null) cancelAnimationFrame(raf);
      setApi(null);
    };
    // Initial look only; later settings changes are applied live below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [forceUpdate]);

  useEffect(() => {
    if (!api) return;
    api.candleSeries.applyOptions({
      upColor: settings.upColor, downColor: settings.downColor,
      borderVisible: settings.borderVisible, borderUpColor: settings.borderUpColor, borderDownColor: settings.borderDownColor,
      wickVisible: settings.wickVisible, wickUpColor: settings.wickUpColor, wickDownColor: settings.wickDownColor,
    });
    api.chart.applyOptions({
      layout: { background: { color: transparent ? 'rgba(0,0,0,0)' : settings.background } },
      grid: {
        vertLines: { visible: settings.vertGridVisible, color: settings.gridColor },
        horzLines: { visible: settings.horzGridVisible, color: settings.gridColor },
      },
    });
  }, [api, settings, transparent]);

  return { api, tick, hoverTime };
}

export function candlePoint(b) {
  return { time: b.time, open: b.open, high: b.high, low: b.low, close: b.close };
}

export function volumePoint(b) {
  return {
    time: b.time,
    value: b.volume,
    color: b.close >= b.open ? 'rgba(62,207,110,0.5)' : 'rgba(239,68,68,0.5)',
  };
}

export function formatVol(v) {
  if (v >= 1e6) return `${(v / 1e6).toFixed(2)}M`;
  if (v >= 1e3) return `${(v / 1e3).toFixed(2)}K`;
  return `${Math.round(v)}`;
}
