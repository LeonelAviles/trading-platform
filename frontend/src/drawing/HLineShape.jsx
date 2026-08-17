import { dataToPx, useDragHandler } from './geometry';

// Same hit-testing rationale as LineShape/RectShape: handles sit exactly at
// the endpoints, so the invisible hit-line is pulled in from both ends once
// they're showing, giving each handle exclusive territory.
const HANDLE_CLEARANCE = 8;

// A horizontal line/ray: unlike LineShape, both endpoints always share one
// `price` field instead of independent p1/p2, so there's no way to tilt it.
// The endpoint handles can only extend/shrink it (drag changes x only); only
// dragging the body itself can move it to a different price.
export default function HLineShape({ shape, chart, series, selected, interactive, onSelect, setShapes }) {
  const p1 = dataToPx(chart, series, shape.x1, shape.price);
  const p2 = dataToPx(chart, series, shape.x2, shape.price);
  const color = shape.color || '#5b9dd9';
  const lineWidth = shape.lineWidth || 1.5;
  const canInteract = interactive && !shape.locked;

  const bodyDrag = useDragHandler(chart, series, shape, (snap, start, cur) => ({
    x1: snap.x1 + (cur.logical - start.logical),
    x2: snap.x2 + (cur.logical - start.logical),
    price: snap.price + (cur.price - start.price),
  }));
  const h1Drag = useDragHandler(chart, series, shape, (_snap, _start, cur) => ({ x1: cur.logical }));
  const h2Drag = useDragHandler(chart, series, shape, (_snap, _start, cur) => ({ x2: cur.logical }));

  if (p1.x == null || p2.x == null) return null;

  const len = Math.abs(p2.x - p1.x) || 1;
  const clearance = selected && canInteract ? Math.min(HANDLE_CLEARANCE, len / 2) : 0;
  const dir = p2.x >= p1.x ? 1 : -1;
  const hitX1 = p1.x + dir * clearance, hitX2 = p2.x - dir * clearance;

  return (
    <g>
      <line x1={p1.x} y1={p1.y} x2={p2.x} y2={p2.y} stroke={color} strokeWidth={selected ? lineWidth + 0.5 : lineWidth} pointerEvents="none" />
      {interactive && (
        <line
          x1={hitX1} y1={p1.y} x2={hitX2} y2={p2.y}
          stroke="transparent" strokeWidth={10} style={{ cursor: shape.locked ? 'default' : 'ns-resize' }} pointerEvents="stroke"
          onPointerDown={(e) => { onSelect(e); if (canInteract) bodyDrag.onPointerDown(e); }}
          onPointerMove={(e) => { if (canInteract) bodyDrag.onPointerMove(e, setShapes); }}
          onPointerUp={bodyDrag.onPointerUp}
        />
      )}
      {selected && canInteract && (
        <>
          <circle cx={p1.x} cy={p1.y} r={5} fill="#0a0a0c" stroke={color} pointerEvents="all" strokeWidth={1.5} style={{ cursor: 'ew-resize' }}
            onPointerDown={h1Drag.onPointerDown} onPointerMove={(e) => h1Drag.onPointerMove(e, setShapes)} onPointerUp={h1Drag.onPointerUp} />
          <circle cx={p2.x} cy={p2.y} r={5} fill="#0a0a0c" stroke={color} pointerEvents="all" strokeWidth={1.5} style={{ cursor: 'ew-resize' }}
            onPointerDown={h2Drag.onPointerDown} onPointerMove={(e) => h2Drag.onPointerMove(e, setShapes)} onPointerUp={h2Drag.onPointerUp} />
        </>
      )}
    </g>
  );
}
