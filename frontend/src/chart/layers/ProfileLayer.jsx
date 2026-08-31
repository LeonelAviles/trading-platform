import { useMemo, useRef } from 'react';
import { useCanvasLayer } from './useCanvasLayer';
import { valueArea } from '../orderflowMath';

// Volume-at-price histogram on the right edge with POC / VAH / VAL lines.
// `bins` is [[price, volume], ...] — the session profile or the visible
// range's profile depending on the settings.
export default function ProfileLayer({ chart, series, bins, tickSize, width = 110 }) {
  const canvasRef = useRef(null);
  const va = useMemo(() => valueArea(bins || []), [bins]);
  const maxVol = useMemo(() => Math.max(1, ...(bins || []).map((b) => b[1])), [bins]);

  const draw = (ctx, w, h) => {
    if (!bins?.length) return;
    const x0 = w - width;
    const y1 = series.priceToCoordinate(bins[0][0]);
    const y2 = series.priceToCoordinate(bins[0][0] + tickSize);
    const rowH = y1 != null && y2 != null ? Math.max(1, Math.abs(y1 - y2)) : 2;
    for (const [price, vol] of bins) {
      const y = series.priceToCoordinate(price);
      if (y == null || y < -rowH || y > h + rowH) continue;
      const len = (vol / maxVol) * (width - 8);
      const inVa = va.val != null && price >= va.val && price <= va.vah;
      ctx.fillStyle = price === va.poc ? 'rgba(240,180,41,0.85)' : inVa ? 'rgba(77,142,255,0.45)' : 'rgba(138,138,155,0.30)';
      ctx.fillRect(w - 4 - len, y - rowH / 2, len, Math.max(1, rowH - 0.5));
    }
    const line = (price, color, label) => {
      if (price == null) return;
      const y = series.priceToCoordinate(price);
      if (y == null) return;
      ctx.strokeStyle = color;
      ctx.setLineDash([4, 4]);
      ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(x0, y); ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = color;
      ctx.font = '10px ui-monospace, Menlo, monospace';
      ctx.textAlign = 'left';
      ctx.textBaseline = 'bottom';
      ctx.fillText(`${label} ${price.toFixed(2)}`, 6, y - 2);
    };
    line(va.poc, 'rgba(240,180,41,0.9)', 'POC');
    line(va.vah, 'rgba(77,142,255,0.9)', 'VAH');
    line(va.val, 'rgba(77,142,255,0.9)', 'VAL');
  };

  useCanvasLayer(canvasRef, chart, series, draw, [bins, va, maxVol, tickSize, width, chart, series]);
  return <canvas ref={canvasRef} className="layer-canvas" />;
}
