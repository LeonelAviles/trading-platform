import { dataToPx, useDragHandler, DEFAULT_PROFIT_COLOR, DEFAULT_LOSS_COLOR, DEFAULT_ENTRY_COLOR } from './geometry';

// See RectShape for why this exists: the TP/SL/width handles sit exactly on
// the edges of the translate-hit-rect below, so once they're showing, that
// hit-rect is inset away from those edges to give each handle exclusive
// territory (the left edge is left alone since the entry handle there does
// the same translate action as the body anyway, so there's no conflict).
const HANDLE_CLEARANCE = 8;

export default function PositionShape({ shape, chart, series, selected, interactive, onSelect, setShapes }) {
  const entryPx = dataToPx(chart, series, shape.entryLogical, shape.entryPrice);
  const endPx = dataToPx(chart, series, shape.endLogical, shape.entryPrice);
  const stopPx = dataToPx(chart, series, shape.entryLogical, shape.stopPrice);
  const targetPx = dataToPx(chart, series, shape.entryLogical, shape.targetPrice);
  const profitColor = shape.profitColor || DEFAULT_PROFIT_COLOR;
  const lossColor = shape.lossColor || DEFAULT_LOSS_COLOR;
  const entryColor = shape.entryColor || DEFAULT_ENTRY_COLOR;
  const lineWidth = shape.lineWidth || 1;
  const canInteract = interactive && !shape.locked;

  const entryDrag = useDragHandler(chart, series, shape, (snap, start, cur) => {
    const dLogical = cur.logical - start.logical;
    const dPrice = cur.price - start.price;
    return {
      entryLogical: snap.entryLogical + dLogical,
      entryPrice: snap.entryPrice + dPrice,
      stopPrice: snap.stopPrice + dPrice,
      targetPrice: snap.targetPrice + dPrice,
      endLogical: snap.endLogical + dLogical,
    };
  });
  const tpDrag = useDragHandler(chart, series, shape, (_snap, _start, cur) => ({ targetPrice: cur.price }));
  const slDrag = useDragHandler(chart, series, shape, (_snap, _start, cur) => ({ stopPrice: cur.price }));
  const widthDrag = useDragHandler(chart, series, shape, (_snap, _start, cur) => ({ endLogical: cur.logical }));

  if (entryPx.x == null || endPx.x == null || stopPx.y == null || targetPx.y == null) return null;

  const x1 = entryPx.x, x2 = endPx.x;
  const yEntry = entryPx.y, yStop = stopPx.y, yTarget = targetPx.y;

  const riskPct = (Math.abs(shape.entryPrice - shape.stopPrice) / shape.entryPrice) * 100;
  const rewardPct = (Math.abs(shape.targetPrice - shape.entryPrice) / shape.entryPrice) * 100;
  const rr = (rewardPct / riskPct).toFixed(2);

  const bandTop = Math.min(yTarget, yStop), bandBottom = Math.max(yTarget, yStop);
  const bandLeft = Math.min(x1, x2), bandRight = Math.max(x1, x2);
  const vClearance = selected && canInteract ? Math.min(HANDLE_CLEARANCE, (bandBottom - bandTop) / 2) : 0;
  const hClearance = selected && canInteract ? Math.min(HANDLE_CLEARANCE, (bandRight - bandLeft) / 2) : 0;

  return (
    <g>
      <rect x={bandLeft} y={Math.min(yEntry, yTarget)} width={bandRight - bandLeft} height={Math.abs(yEntry - yTarget)} fill={profitColor} pointerEvents="none" />
      <rect x={bandLeft} y={Math.min(yEntry, yStop)} width={bandRight - bandLeft} height={Math.abs(yEntry - yStop)} fill={lossColor} pointerEvents="none" />

      <line x1={x1} y1={yEntry} x2={x2} y2={yEntry} stroke={entryColor} strokeWidth={lineWidth} strokeDasharray="4 3" pointerEvents="none" />

      {interactive && (
        <rect
          x={bandLeft} y={bandTop + vClearance}
          width={Math.max(0, bandRight - bandLeft - hClearance)} height={Math.max(0, bandBottom - bandTop - vClearance * 2)}
          fill="transparent" pointerEvents="all" style={{ cursor: shape.locked ? 'default' : 'move' }}
          onPointerDown={(e) => { onSelect(e); if (canInteract) entryDrag.onPointerDown(e); }}
          onPointerMove={(e) => { if (canInteract) entryDrag.onPointerMove(e, setShapes); }}
          onPointerUp={entryDrag.onPointerUp}
        />
      )}

      <text x={x2 + 4} y={yTarget + 4} fontSize="11" fontFamily="monospace" fill={profitColor}>
        {`TP ${shape.targetPrice.toFixed(2)}  (+${rewardPct.toFixed(2)}%)`}
      </text>
      <text x={x2 + 4} y={yStop + 4} fontSize="11" fontFamily="monospace" fill={lossColor}>
        {`SL ${shape.stopPrice.toFixed(2)}  (-${riskPct.toFixed(2)}%)`}
      </text>
      <text x={x2 + 4} y={yEntry + 4} fontSize="11" fontFamily="monospace" fill={entryColor}>
        {`Entry ${shape.entryPrice.toFixed(2)}   R:R 1:${rr}`}
      </text>

      {selected && canInteract && (
        <>
          {/* Handles use a fixed color scheme rather than the shape's own
              profit/loss/entry colors, so they stay visible even when one
              of those is dark or transparent (see RectShape for the same
              reasoning). */}
          <circle cx={x1} cy={yEntry} r={5} fill="#ffffff" stroke="#5b9dd9" pointerEvents="all" strokeWidth={1.5} style={{ cursor: 'move' }}
            onPointerDown={entryDrag.onPointerDown} onPointerMove={(e) => entryDrag.onPointerMove(e, setShapes)} onPointerUp={entryDrag.onPointerUp} />
          <circle cx={x1} cy={yTarget} r={5} fill="#ffffff" stroke="#5b9dd9" pointerEvents="all" strokeWidth={1.5} style={{ cursor: 'ns-resize' }}
            onPointerDown={tpDrag.onPointerDown} onPointerMove={(e) => tpDrag.onPointerMove(e, setShapes)} onPointerUp={tpDrag.onPointerUp} />
          <circle cx={x1} cy={yStop} r={5} fill="#ffffff" stroke="#5b9dd9" pointerEvents="all" strokeWidth={1.5} style={{ cursor: 'ns-resize' }}
            onPointerDown={slDrag.onPointerDown} onPointerMove={(e) => slDrag.onPointerMove(e, setShapes)} onPointerUp={slDrag.onPointerUp} />
          <circle cx={x2} cy={yEntry} r={5} fill="#ffffff" stroke="#5b9dd9" pointerEvents="all" strokeWidth={1.5} style={{ cursor: 'ew-resize' }}
            onPointerDown={widthDrag.onPointerDown} onPointerMove={(e) => widthDrag.onPointerMove(e, setShapes)} onPointerUp={widthDrag.onPointerUp} />
        </>
      )}
    </g>
  );
}
