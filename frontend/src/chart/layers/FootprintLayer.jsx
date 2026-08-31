import { useMemo, useRef } from 'react';
import { useCanvasLayer } from './useCanvasLayer';
import { footprintImbalances, stackedRuns, footprintPoc } from '../orderflowMath';

const MIN_BAR_PX = 56;

// Bid × ask cells per bar per price level, drawn over the candles
// (PLATFORM-SPEC.md Phase 5): diagonal imbalance highlight, stacked
// imbalance outline, POC band, delta/volume under each bar.
export default function FootprintLayer({
  chart, series, bars, footprints, liveFootprint, tickSize, settings, onTooNarrow,
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

  const analysed = useMemo(() => {
    const out = {};
    for (const [t, levels] of Object.entries(byTime)) {
      if (!levels?.length) continue;
      const { buy, sell, sorted } = footprintImbalances(levels, { ratio, minVolume: minVol });
      const prices = sorted.map((l) => l.price);
      out[t] = {
        levels: sorted, buy, sell,
        stackedBuy: stackedRuns(prices, buy, stackedMin), stackedSell: stackedRuns(prices, sell, stackedMin),
        poc: footprintPoc(sorted),
        volume: sorted.reduce((s, l) => s + l.bid + l.ask, 0),
        delta: sorted.reduce((s, l) => s + l.ask - l.bid, 0),
      };
    }
    return out;
  }, [byTime, ratio, minVol, stackedMin]);

  const draw = (ctx, width, height) => {
    if (!bars.length) return;
    const timeScale = chart.timeScale();
    const spacing = timeScale.options().barSpacing;
    if (spacing < MIN_BAR_PX) { onTooNarrow?.(true); return; }
    onTooNarrow?.(false);
    const range = timeScale.getVisibleLogicalRange();
    if (!range) return;
    const from = Math.max(0, Math.floor(range.from));
    const to = Math.min(bars.length - 1, Math.ceil(range.to));
    const yTick = (p) => series.priceToCoordinate(p);
    const sampleY = yTick(bars[to].close);
    const nextY = yTick(bars[to].close + tickSize);
    const rowH = sampleY != null && nextY != null ? Math.abs(sampleY - nextY) : 0;
    if (rowH < 3) return;
    const fontPx = Math.max(8, Math.min(12, rowH - 2));
    ctx.font = `${fontPx}px "SF Mono", ui-monospace, Menlo, monospace`;
    ctx.textBaseline = 'middle';
    const half = spacing / 2 - 1;
    for (let i = from; i <= to; i++) {
      const bar = bars[i];
      const fp = analysed[bar.time];
      if (!fp) continue;
      const x = timeScale.timeToCoordinate(bar.time);
      if (x == null) continue;
      const left = x - half;
      const cellW = half;
      // POC band
      if (fp.poc != null) {
        const y = yTick(fp.poc);
        if (y != null) {
          ctx.fillStyle = 'rgba(240,180,41,0.18)';
          ctx.fillRect(left, y - rowH / 2, spacing - 2, rowH);
        }
      }
      for (const l of fp.levels) {
        const y = yTick(l.price);
        if (y == null || y < -rowH || y > height + rowH) continue;
        const top = y - rowH / 2;
        const isBuy = fp.buy.has(l.price);
        const isSell = fp.sell.has(l.price);
        ctx.fillStyle = 'rgba(10,10,12,0.55)';
        ctx.fillRect(left, top, spacing - 2, rowH);
        if (isSell) { ctx.fillStyle = 'rgba(244,87,111,0.35)'; ctx.fillRect(left, top, cellW, rowH); }
        if (isBuy) { ctx.fillStyle = 'rgba(52,211,153,0.35)'; ctx.fillRect(left + cellW, top, cellW, rowH); }
        if (rowH >= 9) {
          ctx.textAlign = 'right';
          ctx.fillStyle = isSell ? '#ff9db0' : '#b9b9c6';
          ctx.fillText(String(l.bid), x - 3, y);
          ctx.textAlign = 'left';
          ctx.fillStyle = isBuy ? '#8ff0c6' : '#b9b9c6';
          ctx.fillText(String(l.ask), x + 3, y);
        }
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

  useCanvasLayer(canvasRef, chart, series, draw, [bars, analysed, tickSize, chart, series]);
  return <canvas ref={canvasRef} className="layer-canvas" />;
}
