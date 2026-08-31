import { useLayoutEffect, useMemo, useRef } from 'react';
import { timeToLogical } from '../drawing/geometry';
import { prepareCanvas } from './canvas';

// Bookmap-style liquidity ramp: empty/ordinary resting size stays in the
// blue field, then unusually large levels move through cyan/white/yellow to
// orange and red. Stops are interpolated once when data arrives, never in
// the per-frame draw loop.
const HEAT_STOPS = [
  [0.00, [4, 25, 34]],
  [0.18, [3, 70, 96]],
  [0.38, [0, 145, 211]],
  [0.56, [65, 214, 255]],
  [0.69, [235, 249, 244]],
  [0.78, [255, 241, 0]],
  [0.90, [255, 112, 0]],
  [1.00, [255, 38, 0]],
];
const MIN_VISIBLE_INTENSITY = 0.90;
const COLOR_GAMMA = 1.35;

function heatColor(size, maxSize) {
  const ratio = Math.min(1, size / maxSize);
  const intensity = Math.pow(ratio, COLOR_GAMMA);
  let upper = 1;
  while (upper < HEAT_STOPS.length - 1 && intensity > HEAT_STOPS[upper][0]) upper++;
  const [loAt, loRgb] = HEAT_STOPS[upper - 1];
  const [hiAt, hiRgb] = HEAT_STOPS[upper];
  const mix = (intensity - loAt) / (hiAt - loAt || 1);
  const rgb = loRgb.map((channel, i) => Math.round(channel + (hiRgb[i] - channel) * mix));
  const alpha = 0.28 + Math.pow(ratio, 0.55) * 0.68;
  return `rgba(${rgb[0]}, ${rgb[1]}, ${rgb[2]}, ${alpha.toFixed(3)})`;
}

// lightweight-charts accepts a Logical value in its public API, but the
// version used by this app collapses fractional logical indices to x=0.
// Heatmap buckets are usually 5 seconds wide while candles are 1 minute, so
// almost every bucket is fractional. Resolve the neighboring whole-bar
// coordinates and interpolate between them instead.
function logicalToInterpolatedCoordinate(timeScale, logical) {
  if (logical == null) return null;
  const lower = Math.floor(logical);
  const upper = Math.ceil(logical);
  const x0 = timeScale.logicalToCoordinate(lower);
  if (x0 == null || lower === upper) return x0;
  const x1 = timeScale.logicalToCoordinate(upper);
  if (x1 == null) return null;
  return x0 + (x1 - x0) * (logical - lower);
}

// Left as a currently-unused pure function: the seam for the descoped
// large-liquidity-level (orange band) feature. computeLargeLevels would
// scan `buckets` for levels whose size exceeds `threshold` and return the
// price ranges to highlight; nothing calls this yet.
// eslint-disable-next-line no-unused-vars
function computeLargeLevels(buckets, threshold) {
  return [];
}

// Canvas, not SVG: a bucket x price-level grid redrawn on every pan/zoom
// frame needs canvas fill-rect performance, not per-cell React elements.
export default function DomHeatmapLayer({ chart, series, heatmapData, bars, intervalSeconds, maxTime = null }) {
  const canvasRef = useRef(null);

  const tickSize = useMemo(() => {
    if (!heatmapData?.buckets?.length) return 0.25;
    const uniquePrices = new Set();
    for (const bucket of heatmapData.buckets) {
      for (const level of bucket.levels) uniquePrices.add(level.p);
    }
    const prices = [...uniquePrices].sort((a, b) => a - b);
    let smallest = Infinity;
    for (let i = 1; i < prices.length; i++) {
      const difference = prices[i] - prices[i - 1];
      if (difference > 1e-8 && difference < smallest) smallest = difference;
    }
    return Number.isFinite(smallest) ? smallest : 0.25;
  }, [heatmapData]);

  // Time interpolation, sorting and color scaling depend on fetched data,
  // not on the chart's pan/zoom transform. Keep them out of the redraw path,
  // which can run every animation frame while the user drags the chart.
  const preparedBuckets = useMemo(() => {
    if (!heatmapData?.buckets?.length || !bars.length) return [];
    // The backend materialises the 95th percentile of the *whole* live book.
    // Recomputing a percentile from the already-filtered hot subset would
    // filter twice and turn persistent lines into isolated dots.
    const maxSize = heatmapData.scaleMax || 1;

    // During replay the "now" edge is the replay clock: nothing to its right.
    const buckets = maxTime == null ? heatmapData.buckets : heatmapData.buckets.filter((b) => b.t < maxTime);
    return buckets.map((bucket) => ({
      l0: timeToLogical(bars, intervalSeconds, bucket.t),
      l1: timeToLogical(bars, intervalSeconds, bucket.t + heatmapData.bucketSeconds),
      levels: [...bucket.levels]
        .filter((level) => Math.pow(Math.min(1, level.s / maxSize), COLOR_GAMMA) >= MIN_VISIBLE_INTENSITY)
        .sort((a, b) => a.p - b.p)
        .map((level) => {
          return { price: level.p, fill: heatColor(level.s, maxSize) };
        }),
    }));
  }, [bars, heatmapData, intervalSeconds, maxTime]);

  useLayoutEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !chart || !series) return;
    const timeScale = chart.timeScale();
    const host = canvas.parentElement;
    let frame = null;
    let settleFrame = null;
    let dragging = false;

    const draw = () => {
      const { ctx, width: canvasWidth, height: canvasHeight } = prepareCanvas(canvas);
      if (!preparedBuckets.length) return;

      let rowHeight = 2;
      const sampleLevel = preparedBuckets.find((bucket) => bucket.levels.length)?.levels[0];
      if (sampleLevel) {
        const sampleY = series.priceToCoordinate(sampleLevel.price);
        const nextTickY = series.priceToCoordinate(sampleLevel.price + tickSize);
        if (sampleY != null && nextTickY != null) {
          rowHeight = Math.min(8, Math.max(1, Math.abs(sampleY - nextTickY)));
        }
      }
      for (const bucket of preparedBuckets) {
        if (bucket.l0 == null || bucket.l1 == null) continue;
        const x0 = logicalToInterpolatedCoordinate(timeScale, bucket.l0);
        const x1 = logicalToInterpolatedCoordinate(timeScale, bucket.l1);
        if (x0 == null || x1 == null) continue;
        // Whole-pixel, slightly overlapping bucket edges avoid the vertical
        // hairline gaps that made persistent zones look like a barcode.
        const left = Math.floor(Math.min(x0, x1));
        const right = Math.ceil(Math.max(x0, x1));
        const cellWidth = Math.max(1, right - left + 1);
        if (left > canvasWidth || left + cellWidth < 0) continue;

        const levels = bucket.levels;
        for (let i = 0; i < levels.length; i++) {
          const y = series.priceToCoordinate(levels[i].price);
          if (y == null) continue;
          if (y + rowHeight / 2 < 0 || y - rowHeight / 2 > canvasHeight) continue;

          ctx.fillStyle = levels[i].fill;
          ctx.fillRect(left, y - rowHeight / 2, cellWidth, rowHeight);
        }
      }
    };

    // lightweight-charts publishes its visible-range event before every
    // dependent pane/price autoscale has necessarily painted. Draw on the
    // next frame and once more on the following frame so the canvas always
    // uses the same final transform as the candles.
    const scheduleDraw = () => {
      if (frame != null) return;
      frame = requestAnimationFrame(() => {
        frame = null;
        draw();
        if (settleFrame != null) cancelAnimationFrame(settleFrame);
        settleFrame = requestAnimationFrame(() => {
          settleFrame = null;
          draw();
          if (dragging) scheduleDraw();
        });
      });
    };

    const onPointerDown = () => {
      dragging = true;
      scheduleDraw();
    };
    const onPointerUp = () => {
      dragging = false;
      scheduleDraw();
    };
    const resizeObserver = new ResizeObserver(scheduleDraw);
    resizeObserver.observe(canvas);
    timeScale.subscribeVisibleLogicalRangeChange(scheduleDraw);
    host?.addEventListener('wheel', scheduleDraw, { passive: true });
    host?.addEventListener('pointerdown', onPointerDown);
    window.addEventListener('pointerup', onPointerUp);
    scheduleDraw();

    return () => {
      resizeObserver.disconnect();
      timeScale.unsubscribeVisibleLogicalRangeChange(scheduleDraw);
      host?.removeEventListener('wheel', scheduleDraw);
      host?.removeEventListener('pointerdown', onPointerDown);
      window.removeEventListener('pointerup', onPointerUp);
      if (frame != null) cancelAnimationFrame(frame);
      if (settleFrame != null) cancelAnimationFrame(settleFrame);
    };
  }, [chart, preparedBuckets, series, tickSize]);

  return <canvas ref={canvasRef} className="dom-heatmap-canvas" />;
}
