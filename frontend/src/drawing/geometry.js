import { useRef } from 'react';
import { parseColor, toRgbaString } from '../components/colorUtils';

export const COLOR_PALETTE = ['#5b9dd9', '#ef5350', '#26a69a', '#f0c419', '#ab47bc', '#ffffff'];
export const LINE_WIDTHS = [1, 2, 3];
export const DEFAULT_COLOR = COLOR_PALETTE[0];
export const DEFAULT_LINE_WIDTH = 2;
// A rectangle's fill defaults to its border color at low opacity, same look
// as before fill became independently editable.
export const DEFAULT_FILL_COLOR = toRgbaString({ ...parseColor(DEFAULT_COLOR), a: 0.15 });
// Position tool defaults, matching the fixed colors it used before its zone
// fills/entry line became independently editable.
export const DEFAULT_PROFIT_COLOR = 'rgba(38,166,154,0.25)';
export const DEFAULT_LOSS_COLOR = 'rgba(239,83,80,0.25)';
export const DEFAULT_ENTRY_COLOR = '#d1d4dc';

// All shapes are stored in (logical index, price) space rather than pixels
// or Time, so they stay anchored to the right bar/price as the chart is
// panned or zoomed, and a position box's right edge can extend into the
// empty space past the last bar (logical index keeps counting there; a
// Time string would not).
export function pxToData(chart, series, x, y) {
  return {
    logical: chart.timeScale().coordinateToLogical(x),
    price: series.coordinateToPrice(y),
  };
}

export function dataToPx(chart, series, logical, price) {
  if (logical == null || price == null) return { x: null, y: null };
  return {
    x: chart.timeScale().logicalToCoordinate(logical),
    y: series.priceToCoordinate(price),
  };
}

// A draggable handle/body: captures the shape's original field values and
// the pointer's starting data-space position on pointerdown, then on every
// move recomputes the delta from that fixed start and asks `apply` for the
// new absolute field values — never compounds deltas onto the live shape,
// which would drift.
export function useDragHandler(chart, series, shape, apply) {
  const gestureRef = useRef(null);

  const svgPoint = (e) => {
    const rect = e.currentTarget.ownerSVGElement.getBoundingClientRect();
    return { x: e.clientX - rect.left, y: e.clientY - rect.top };
  };

  const onPointerDown = (e) => {
    e.stopPropagation();
    e.currentTarget.setPointerCapture(e.pointerId);
    const p = svgPoint(e);
    gestureRef.current = { startData: pxToData(chart, series, p.x, p.y), snapshot: shape };
  };

  const onPointerMove = (e, setShapes) => {
    if (!gestureRef.current) return;
    const p = svgPoint(e);
    const cur = pxToData(chart, series, p.x, p.y);
    if (cur.logical == null || cur.price == null) return;
    const { startData, snapshot } = gestureRef.current;
    const updated = apply(snapshot, startData, cur);
    setShapes((prev) => prev.map((s) => (s.id === snapshot.id ? { ...s, ...updated } : s)));
  };

  const onPointerUp = () => {
    gestureRef.current = null;
  };

  return { onPointerDown, onPointerMove, onPointerUp };
}

const INTERVAL_SECONDS = {
  '1s': 1, '5s': 5, '15s': 15,
  '1min': 60, '5min': 300, '15min': 900, '30min': 1800,
  '1h': 3600, '4h': 14400, '1D': 86400,
};
export function intervalToSeconds(interval) {
  return INTERVAL_SECONDS[interval] || 60;
}

// Logical index is only meaningful relative to the bar array of whatever
// interval is currently loaded — switching timeframe reloads a completely
// different set of bars, so a shape's stored logical index would suddenly
// point at an unrelated point in time. These two convert between a bar
// array's logical-index space and real (Unix seconds) time, driven by the
// bars' actual timestamps rather than an assumed uniform spacing (bars can
// have gaps), so shapes can be remapped through time when the interval
// changes and land back on the same real point instead of drifting.
export function logicalToTime(bars, intervalSeconds, logical) {
  if (!bars.length || logical == null) return null;
  const last = bars.length - 1;
  if (logical <= 0) return bars[0].time + logical * intervalSeconds;
  if (logical >= last) return bars[last].time + (logical - last) * intervalSeconds;
  const i0 = Math.floor(logical);
  const frac = logical - i0;
  const t0 = bars[i0].time, t1 = bars[i0 + 1].time;
  return t0 + frac * (t1 - t0);
}

export function timeToLogical(bars, intervalSeconds, time) {
  if (!bars.length || time == null) return null;
  const last = bars.length - 1;
  if (time <= bars[0].time) return (time - bars[0].time) / intervalSeconds;
  if (time >= bars[last].time) return last + (time - bars[last].time) / intervalSeconds;
  let lo = 0, hi = last;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (bars[mid].time <= time) lo = mid; else hi = mid;
  }
  const t0 = bars[lo].time, t1 = bars[hi].time;
  return lo + (t1 > t0 ? (time - t0) / (t1 - t0) : 0);
}

// Re-anchors every logical-index field on a shape through real time, so it
// keeps sitting over the same candles/price after the timeframe changes.
export function remapShapeToInterval(shape, oldBars, oldIntervalSeconds, newBars, newIntervalSeconds) {
  const convert = (logical) => {
    if (logical == null) return logical;
    const time = logicalToTime(oldBars, oldIntervalSeconds, logical);
    const remapped = timeToLogical(newBars, newIntervalSeconds, time);
    if (remapped == null) return logical;
    // logicalToCoordinate() in this chart version only handles integer
    // logical indices (fractional input resolves to 0), and mouse-driven
    // logical values are always whole numbers already — so round rather
    // than leave the interpolated fractional value in place. Deliberately
    // not clamped to the new bar range: if the new timeframe's loaded data
    // genuinely doesn't cover the shape's original time (e.g. this app's
    // 1s/5s bars only cover a slice of what 1min/5min/1D cover), the shape
    // correctly ends up off-screen rather than being shoved onto whichever
    // shape happens to clamp to the same edge, which silently destroys
    // distinct shapes' relative spacing.
    return Math.round(remapped);
  };
  const next = { ...shape };
  for (const key of ['x1', 'x2', 'left', 'right', 'entryLogical', 'endLogical']) {
    if (next[key] != null) next[key] = convert(next[key]);
  }
  return next;
}

export function makePositionFromDrag(tool, entry, dragEnd) {
  const riskDist = Math.abs(entry.price - dragEnd.price) || entry.price * 0.005;
  const targetPrice = tool === 'long' ? entry.price + riskDist * 2 : entry.price - riskDist * 2;
  const stopPrice = tool === 'long' ? entry.price - riskDist : entry.price + riskDist;
  return {
    id: crypto.randomUUID(),
    type: tool,
    entryLogical: entry.logical,
    entryPrice: entry.price,
    stopPrice,
    targetPrice,
    endLogical: entry.logical + 30,
    profitColor: DEFAULT_PROFIT_COLOR,
    lossColor: DEFAULT_LOSS_COLOR,
    entryColor: DEFAULT_ENTRY_COLOR,
    lineWidth: 1,
  };
}
