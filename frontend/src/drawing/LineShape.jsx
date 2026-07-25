import { dataToPx, useDragHandler } from './geometry';

// See RectShape for why this exists: handle circles sit exactly at the
// line's endpoints, so once they're showing, the invisible hit-line used to
// drag the whole line is pulled in from both ends to give each endpoint
// handle exclusive territory instead of losing the hit-test to the body.
const HANDLE_CLEARANCE = 8;

export default function LineShape({ shape, chart, series, selected, interactive, onSelect, setShapes }) {
  const p1 = dataToPx(chart, series, shape.x1, shape.p1);
  const p2 = dataToPx(chart, series, shape.x2, shape.p2);
  const color = shape.color || '#5b9dd9';
  const lineWidth = shape.lineWidth || 1.5;
  const canInteract = interactive && !shape.locked;

  const bodyDrag = useDragHandler(chart, series, shape, (snap, start, cur) => ({
    x1: snap.x1 + (cur.logical - start.logical),
    p1: snap.p1 + (cur.price - start.price),
    x2: snap.x2 + (cur.logical - start.logical),
    p2: snap.p2 + (cur.price - start.price),
  }));
  const h1Drag = useDragHandler(chart, series, shape, (_snap, _start, cur) => ({ x1: cur.logical, p1: cur.price }));
  const h2Drag = useDragHandler(chart, series, shape, (_snap, _start, cur) => ({ x2: cur.logical, p2: cur.price }));

  if (p1.x == null || p2.x == null) return null;

  const dx = p2.x - p1.x, dy = p2.y - p1.y;
  const len = Math.hypot(dx, dy) || 1;
  const clearance = selected && canInteract ? Math.min(HANDLE_CLEARANCE, len / 2) : 0;
  const ux = dx / len, uy = dy / len;
  const hitX1 = p1.x + ux * clearance, hitY1 = p1.y + uy * clearance;
  const hitX2 = p2.x - ux * clearance, hitY2 = p2.y - uy * clearance;

  return (
    <g>
      <line x1={p1.x} y1={p1.y} x2={p2.x} y2={p2.y} stroke={color} strokeWidth={selected ? lineWidth + 0.5 : lineWidth} pointerEvents="none" />
      {interactive && (
        <line
          x1={hitX1} y1={hitY1} x2={hitX2} y2={hitY2}
          stroke="transparent" strokeWidth={10} style={{ cursor: shape.locked ? 'default' : 'move' }} pointerEvents="stroke"
          onPointerDown={(e) => { onSelect(e); if (canInteract) bodyDrag.onPointerDown(e); }}
          onPointerMove={(e) => { if (canInteract) bodyDrag.onPointerMove(e, setShapes); }}
          onPointerUp={bodyDrag.onPointerUp}
        />
      )}
      {selected && canInteract && (
        <>
          <circle cx={p1.x} cy={p1.y} r={5} fill="#0e0f14" stroke={color} pointerEvents="all" strokeWidth={1.5} style={{ cursor: 'crosshair' }}
            onPointerDown={h1Drag.onPointerDown} onPointerMove={(e) => h1Drag.onPointerMove(e, setShapes)} onPointerUp={h1Drag.onPointerUp} />
          <circle cx={p2.x} cy={p2.y} r={5} fill="#0e0f14" stroke={color} pointerEvents="all" strokeWidth={1.5} style={{ cursor: 'crosshair' }}
            onPointerDown={h2Drag.onPointerDown} onPointerMove={(e) => h2Drag.onPointerMove(e, setShapes)} onPointerUp={h2Drag.onPointerUp} />
        </>
      )}
    </g>
  );
}
