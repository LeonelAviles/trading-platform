import { dataToPx, useDragHandler } from './geometry';

// Handles sit exactly on the body's edges/corners. At that exact coincident
// pixel, hit-testing can resolve to the larger body shape instead of the
// small handle on top of it regardless of paint order — so the body's
// interactive (pointer-events) hit-area is inset away from the edges once
// handles are showing, giving each handle exclusive territory.
const HANDLE_CLEARANCE = 8;

// Old shapes drawn before rectangles switched to an explicit left/right/
// top/bottom model (needed so each of the 8 handles can move one edge
// independently) still have x1/p1/x2/p2 in localStorage — fall back to
// deriving the box from those so they don't just vanish.
function geometry(shape) {
  if (shape.left != null) return shape;
  return {
    left: Math.min(shape.x1, shape.x2), right: Math.max(shape.x1, shape.x2),
    top: Math.max(shape.p1, shape.p2), bottom: Math.min(shape.p1, shape.p2),
  };
}

export default function RectShape({ shape, chart, series, selected, interactive, onSelect, setShapes }) {
  const { left, right, top, bottom } = geometry(shape);
  // Legacy shapes only had a single `color` used for both border and fill;
  // fall back to it so they keep rendering with their original look.
  const borderColor = shape.borderColor || shape.color || '#5b9dd9';
  const fillColor = shape.fillColor || `${borderColor}26`;
  const lineWidth = shape.lineWidth || 2;
  const canInteract = interactive && !shape.locked;

  const tl = dataToPx(chart, series, left, top);
  const tr = dataToPx(chart, series, right, top);
  const bl = dataToPx(chart, series, left, bottom);
  const br = dataToPx(chart, series, right, bottom);

  const bodyDrag = useDragHandler(chart, series, shape, (snap, start, cur) => {
    const g = geometry(snap);
    const dLogical = cur.logical - start.logical;
    const dPrice = cur.price - start.price;
    return { left: g.left + dLogical, right: g.right + dLogical, top: g.top + dPrice, bottom: g.bottom + dPrice };
  });
  const dragLeft = useDragHandler(chart, series, shape, (_snap, _start, cur) => ({ left: cur.logical }));
  const dragRight = useDragHandler(chart, series, shape, (_snap, _start, cur) => ({ right: cur.logical }));
  const dragTop = useDragHandler(chart, series, shape, (_snap, _start, cur) => ({ top: cur.price }));
  const dragBottom = useDragHandler(chart, series, shape, (_snap, _start, cur) => ({ bottom: cur.price }));
  const dragTL = useDragHandler(chart, series, shape, (_snap, _start, cur) => ({ left: cur.logical, top: cur.price }));
  const dragTR = useDragHandler(chart, series, shape, (_snap, _start, cur) => ({ right: cur.logical, top: cur.price }));
  const dragBL = useDragHandler(chart, series, shape, (_snap, _start, cur) => ({ left: cur.logical, bottom: cur.price }));
  const dragBR = useDragHandler(chart, series, shape, (_snap, _start, cur) => ({ right: cur.logical, bottom: cur.price }));

  if (tl.x == null || br.x == null) return null;

  const x = tl.x, y = tl.y;
  const w = br.x - tl.x, h = br.y - tl.y;
  const midX = (tl.x + tr.x) / 2, midY = (tl.y + bl.y) / 2;

  const clearance = selected && canInteract ? Math.min(HANDLE_CLEARANCE, w / 2, h / 2) : 0;
  const hitX = x + clearance, hitY = y + clearance;
  const hitW = Math.max(0, w - clearance * 2), hitH = Math.max(0, h - clearance * 2);

  // Handles use a fixed color scheme rather than the shape's own border
  // color, so they stay visible even when that color is dark/near the
  // chart background (e.g. black borders).
  const handle = (cx, cy, cursor, drag) => (
    <circle cx={cx} cy={cy} r={5} fill="#ffffff" stroke="#5b9dd9" pointerEvents="all" strokeWidth={1.5} style={{ cursor }}
      onPointerDown={drag.onPointerDown} onPointerMove={(e) => drag.onPointerMove(e, setShapes)} onPointerUp={drag.onPointerUp} />
  );

  return (
    <g>
      <rect x={x} y={y} width={w} height={h} fill={fillColor} stroke={borderColor} strokeWidth={selected ? lineWidth + 1 : lineWidth} pointerEvents="none" />
      {interactive && (
        <rect x={hitX} y={hitY} width={hitW} height={hitH} fill="transparent" pointerEvents="all" style={{ cursor: shape.locked ? 'default' : 'move' }}
          onPointerDown={(e) => { onSelect(e); if (canInteract) bodyDrag.onPointerDown(e); }}
          onPointerMove={(e) => { if (canInteract) bodyDrag.onPointerMove(e, setShapes); }}
          onPointerUp={bodyDrag.onPointerUp}
        />
      )}
      {selected && canInteract && (
        <>
          {handle(tl.x, tl.y, 'nwse-resize', dragTL)}
          {handle(tr.x, tr.y, 'nesw-resize', dragTR)}
          {handle(bl.x, bl.y, 'nesw-resize', dragBL)}
          {handle(br.x, br.y, 'nwse-resize', dragBR)}
          {handle(midX, tl.y, 'ns-resize', dragTop)}
          {handle(midX, bl.y, 'ns-resize', dragBottom)}
          {handle(tl.x, midY, 'ew-resize', dragLeft)}
          {handle(tr.x, midY, 'ew-resize', dragRight)}
        </>
      )}
    </g>
  );
}
