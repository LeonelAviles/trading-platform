import { useMemo, useRef } from 'react';
import { useCanvasLayer } from './useCanvasLayer';
import { footprintImbalances, stackedRuns, footprintPoc } from '../orderflowMath';

// Above this bar spacing the footprint renders bid × ask cells with numbers;
// below it, compact per-level heat cells so the *whole* chart stays a
// footprint chart at any zoom. The gap between bars keeps adjacent clusters
// visually separate (and absorbs slight number overflow), which is what lets
// the text threshold sit this low.
const TEXT_BAR_PX = 34;
// Target pixel height for one rendered row. When a single tick is thinner
// than this, adjacent ticks are merged into one row (ATAS-style scale
// grouping) so the numbers stay readable at any vertical scale instead of
// requiring a handful of bars zoomed to full width.
const TEXT_ROW_PX = 9;
const HEAT_ROW_PX = 2;

// Footprint chart (PLATFORM-SPEC.md Phase 5). This layer *replaces* the
// candles (ChartView blanks the candle series while it's on): bid × ask cells
// per bar per price row with same-level imbalance highlight, stacked imbalance
// outline, POC band and delta/volume under each bar when zoomed in; compact
// delta-heat cells when zoomed out. Bars with no footprint data (not fetched
// yet, or none recorded) fall back to a hand-drawn candle so the chart never
// goes blank.
export default function FootprintLayer({
  chart, series, bars, footprints, liveFootprint, tickSize, settings, chartSettings,
}) {
  const canvasRef = useRef(null);
  const ratio = settings.footprintRatio;
  const minVol = settings.footprintMinVolume;
  const stackedMin = settings.stackedMin;

  // Merge history + session footprints + the live bar into time -> levels.
  const byTime = useMemo(() => {
    const m = { ...footprints };
    if (liveFootprint?.time != null) m[liveFootprint.time] = liveFootprint.levels;
    return m;
  }, [footprints, liveFootprint]);

  // Per-bar analysis is grouped by the current row size, which depends on
  // zoom — so it's computed lazily per visible bar and cached until the data,
  // grouping, or thresholds change (a zoom gesture only re-analyses the ~30
  // bars on screen, not the whole history).
  const cacheRef = useRef({ byTime: null, rowTicks: 0, ratio: 0, minVol: 0, stackedMin: 0, map: new Map() });

  const draw = (ctx, width, height) => {
    if (!bars.length) return;
    const timeScale = chart.timeScale();
    const spacing = timeScale.options().barSpacing;
    const range = timeScale.getVisibleLogicalRange();
    if (!range) return;
    const from = Math.max(0, Math.floor(range.from));
    const to = Math.min(bars.length - 1, Math.ceil(range.to));
    const yTick = (p) => series.priceToCoordinate(p);
    const sampleY = yTick(bars[to].close);
    const nextY = yTick(bars[to].close + tickSize);
    const tickH = sampleY != null && nextY != null ? Math.abs(sampleY - nextY) : 0;
    if (!(tickH > 0)) return;

    const textMode = spacing >= TEXT_BAR_PX;
    const rowTicks = Math.max(1, Math.ceil((textMode ? TEXT_ROW_PX : HEAT_ROW_PX) / tickH));
    const rowH = tickH * rowTicks;
    const fontPx = Math.max(7, Math.min(12, rowH - 2, spacing / 5));
    ctx.font = `${fontPx}px "SF Mono", ui-monospace, Menlo, monospace`;
    ctx.textBaseline = 'middle';
    // Breathing room between bars: ~18% of the slot in text mode (clamped),
    // a hairline in heat mode.
    const gap = textMode
      ? Math.min(14, Math.max(2, spacing * 0.18))
      : Math.min(6, Math.max(1, spacing * 0.12));
    const bodyW = Math.max(1, spacing - gap);
    const half = bodyW / 2;

    let cache = cacheRef.current;
    if (cache.byTime !== byTime || cache.rowTicks !== rowTicks
      || cache.ratio !== ratio || cache.minVol !== minVol || cache.stackedMin !== stackedMin) {
      cache = cacheRef.current = { byTime, rowTicks, ratio, minVol, stackedMin, map: new Map() };
    }
    const analyse = (time) => {
      let fp = cache.map.get(time);
      if (fp !== undefined) return fp;
      const raw = byTime[time];
      if (!raw?.length) { cache.map.set(time, null); return null; }
      let rows = raw;
      if (rowTicks > 1) {
        const m = new Map();
        for (const l of raw) {
          const bucket = Math.floor(Math.round(l.price / tickSize) / rowTicks);
          const cur = m.get(bucket);
          if (cur) { cur.bid += l.bid; cur.ask += l.ask; }
          else m.set(bucket, { price: (bucket * rowTicks + (rowTicks - 1) / 2) * tickSize, bid: l.bid, ask: l.ask });
        }
        rows = [...m.values()];
      }
      const { buy, sell, sorted } = footprintImbalances(rows, { ratio, minVolume: minVol });
      const prices = sorted.map((l) => l.price);
      fp = {
        levels: sorted, buy, sell,
        stackedBuy: stackedRuns(prices, buy, stackedMin), stackedSell: stackedRuns(prices, sell, stackedMin),
        poc: footprintPoc(sorted),
        volume: sorted.reduce((s, l) => s + l.bid + l.ask, 0),
        delta: sorted.reduce((s, l) => s + l.ask - l.bid, 0),
        maxLevelVol: sorted.reduce((s, l) => Math.max(s, l.bid + l.ask), 0),
      };
      cache.map.set(time, fp);
      return fp;
    };

    // Candle fallback for bars without footprint data.
    const upColor = chartSettings?.upColor || '#3ecf6e';
    const downColor = chartSettings?.downColor || '#ef4444';
    const drawCandle = (bar, x) => {
      const yO = yTick(bar.open); const yC = yTick(bar.close);
      const yH = yTick(bar.high); const yL = yTick(bar.low);
      if (yO == null || yC == null || yH == null || yL == null) return;
      const color = bar.close >= bar.open ? upColor : downColor;
      ctx.strokeStyle = color;
      ctx.beginPath(); ctx.moveTo(x, yH); ctx.lineTo(x, yL); ctx.stroke();
      ctx.fillStyle = color;
      ctx.fillRect(x - bodyW / 2, Math.min(yO, yC), bodyW, Math.max(1, Math.abs(yC - yO)));
    };

    for (let i = from; i <= to; i++) {
      const bar = bars[i];
      const x = timeScale.timeToCoordinate(bar.time);
      if (x == null) continue;
      const fp = analyse(bar.time);
      if (!fp) { drawCandle(bar, x); continue; }
      const left = x - half;
      const cellW = half;
      const cellH = Math.max(1, rowH);

      if (!textMode) {
        // Compact mode: one heat cell per row — hue from the row's delta,
        // intensity from its share of the bar's busiest row.
        for (const l of fp.levels) {
          const y = yTick(l.price);
          if (y == null || y < -cellH || y > height + cellH) continue;
          const vol = l.bid + l.ask;
          const frac = fp.maxLevelVol ? vol / fp.maxLevelVol : 0;
          const d = l.ask - l.bid;
          const a = 0.16 + 0.64 * frac;
          ctx.fillStyle = d > 0 ? `rgba(52,211,153,${a})` : d < 0 ? `rgba(244,87,111,${a})` : `rgba(185,185,198,${a * 0.7})`;
          ctx.fillRect(left, y - cellH / 2, bodyW, cellH);
        }
        if (fp.poc != null) {
          const y = yTick(fp.poc);
          if (y != null) {
            ctx.fillStyle = 'rgba(240,180,41,0.55)';
            ctx.fillRect(left, y - Math.max(1, cellH * 0.25) / 2, bodyW, Math.max(1, cellH * 0.25));
          }
        }
        continue;
      }

      // POC band
      if (fp.poc != null) {
        const y = yTick(fp.poc);
        if (y != null) {
          ctx.fillStyle = 'rgba(240,180,41,0.18)';
          ctx.fillRect(left, y - rowH / 2, bodyW, rowH);
        }
      }
      for (const l of fp.levels) {
        const y = yTick(l.price);
        if (y == null || y < -rowH || y > height + rowH) continue;
        const top = y - rowH / 2;
        const isBuy = fp.buy.has(l.price);
        const isSell = fp.sell.has(l.price);
        ctx.fillStyle = 'rgba(10,10,12,0.55)';
        ctx.fillRect(left, top, bodyW, rowH);
        if (isSell) { ctx.fillStyle = 'rgba(244,87,111,0.35)'; ctx.fillRect(left, top, cellW, rowH); }
        if (isBuy) { ctx.fillStyle = 'rgba(52,211,153,0.35)'; ctx.fillRect(left + cellW, top, cellW, rowH); }
        ctx.textAlign = 'right';
        ctx.fillStyle = isSell ? '#ff9db0' : '#b9b9c6';
        ctx.fillText(String(l.bid), x - 3, y);
        ctx.textAlign = 'left';
        ctx.fillStyle = isBuy ? '#8ff0c6' : '#b9b9c6';
        ctx.fillText(String(l.ask), x + 3, y);
        ctx.strokeStyle = 'rgba(255,255,255,0.08)';
        ctx.beginPath(); ctx.moveTo(x, top); ctx.lineTo(x, top + rowH); ctx.stroke();
      }
      // stacked imbalance outlines
      const outline = (runs, color, side) => {
        for (const run of runs) {
          const y0 = yTick(run[run.length - 1]);
          const y1 = yTick(run[0]);
          if (y0 == null || y1 == null) continue;
          ctx.strokeStyle = color;
          ctx.lineWidth = 1.5;
          const ox = side === 'buy' ? x : left;
          ctx.strokeRect(ox + 0.5, y0 - rowH / 2 + 0.5, cellW - 1, (y1 - y0) + rowH - 1);
          ctx.lineWidth = 1;
        }
      };
      outline(fp.stackedBuy, 'rgba(110,231,183,0.95)', 'buy');
      outline(fp.stackedSell, 'rgba(255,120,140,0.95)', 'sell');
      // delta & volume under the bar
      ctx.textAlign = 'center';
      ctx.fillStyle = fp.delta >= 0 ? '#6ee7b7' : '#ff8aa0';
      ctx.fillText(`${fp.delta >= 0 ? '+' : ''}${fp.delta}`, x, height - 30);
      ctx.fillStyle = '#8a8a9b';
      ctx.fillText(String(fp.volume), x, height - 17);
    }
  };

  useCanvasLayer(canvasRef, chart, series, draw, [bars, byTime, tickSize, chart, series, chartSettings, ratio, minVol, stackedMin]);
  return <canvas ref={canvasRef} className="layer-canvas" />;
}
