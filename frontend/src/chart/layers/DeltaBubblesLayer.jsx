import { useMemo, useRef } from 'react';
import { timeToLogical } from '../../drawing/geometry';
import { useCanvasLayer, interpolatedX } from './useCanvasLayer';
import { aggregateBubbles, bubbleRadius, bubbleWindowForBarSeconds, percentile } from '../orderflowMath';

// Delta bubbles per the owner's definition (PLATFORM-SPEC.md Phase 5):
// (500 ms, price) aggregation, radius clamp(4, 3 + 2.2·√|Δ|, 26), green/red
// by sign, alpha by |Δ| / p95(|Δ| in view), min |Δ| filter, optional fade
// over 30 s of exchange time from the replay clock.
export default function DeltaBubblesLayer({
  chart, series, bars, intervalSeconds, trades, settings, clockTime, widenWithZoom = false,
}) {
  const canvasRef = useRef(null);
  const minDelta = settings.bubbleMinDelta;
  const fade = settings.bubbleFade;
  const fadeSeconds = settings.bubbleFadeSeconds || 30;

  const bubbles = useMemo(() => {
    if (!trades?.length || !bars.length) return [];
    const spacing = chart ? chart.timeScale().options().barSpacing : 8;
    const secondsPerPx = intervalSeconds / Math.max(spacing, 1);
    // ≥ ~6 px between bubble slots in the static view
    const windowNs = widenWithZoom ? Math.max(bubbleWindowForBarSeconds(secondsPerPx * 6), 500e6) : 500e6;
    const agg = aggregateBubbles(trades, { windowNs }).filter((b) => Math.abs(b.netDelta) >= minDelta);
    return agg.map((b) => ({
      ...b,
      logical: timeToLogical(bars, intervalSeconds, b.ts / 1e9),
      r: bubbleRadius(b.netDelta),
    }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [trades, bars, intervalSeconds, minDelta, widenWithZoom, chart]);

  const draw = (ctx, width, height) => {
    if (!bubbles.length) return;
    const timeScale = chart.timeScale();
    const visible = [];
    for (const b of bubbles) {
      const x = interpolatedX(timeScale, b.logical);
      if (x == null || x < -30 || x > width + 30) continue;
      const y = series.priceToCoordinate(b.price);
      if (y == null || y < -30 || y > height + 30) continue;
      visible.push({ b, x, y });
    }
    if (!visible.length) return;
    const p95 = percentile(visible.map((v) => Math.abs(v.b.netDelta)), 0.95) || 1;
    for (const { b, x, y } of visible) {
      let alpha = 0.25 + 0.7 * Math.min(1, Math.abs(b.netDelta) / p95);
      if (fade && clockTime != null) {
        const age = clockTime - b.tsEnd / 1e9;
        if (age > fadeSeconds) continue;
        if (age > 0) alpha *= 1 - age / fadeSeconds;
      }
      ctx.beginPath();
      ctx.arc(x, y, b.r, 0, Math.PI * 2);
      ctx.fillStyle = b.netDelta > 0 ? `rgba(52,211,153,${alpha.toFixed(3)})` : `rgba(244,87,111,${alpha.toFixed(3)})`;
      ctx.fill();
      if (b.r >= 10) {
        ctx.fillStyle = 'rgba(255,255,255,0.9)';
        ctx.font = `${Math.min(11, b.r)}px ui-monospace, Menlo, monospace`;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(String(Math.abs(b.netDelta)), x, y);
      }
    }
  };

  useCanvasLayer(canvasRef, chart, series, draw, [bubbles, fade, fadeSeconds, clockTime, chart, series]);
  return <canvas ref={canvasRef} className="layer-canvas" />;
}
