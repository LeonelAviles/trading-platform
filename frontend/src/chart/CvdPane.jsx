import { useLayoutEffect, useRef } from 'react';
import { prepareCanvas } from '../orderflow/canvas';

// Cumulative volume delta over the session's 1-minute bars (bar.cvd from the
// replay), live-updated. Small canvas pane under the chart.
export default function CvdPane({ bars, height = 110, onClose }) {
  const ref = useRef(null);
  useLayoutEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    const { ctx, width, height: h } = prepareCanvas(canvas);
    const pts = bars.filter((b) => b.cvd != null);
    if (pts.length < 2) return;
    let min = Infinity, max = -Infinity;
    for (const b of pts) { if (b.cvd < min) min = b.cvd; if (b.cvd > max) max = b.cvd; }
    if (max === min) { max += 1; min -= 1; }
    const x = (i) => 8 + (i / (pts.length - 1)) * (width - 16);
    const y = (v) => 8 + (1 - (v - min) / (max - min)) * (h - 16);
    if (min < 0 && max > 0) {
      ctx.strokeStyle = 'rgba(255,255,255,0.12)';
      ctx.setLineDash([3, 3]);
      ctx.beginPath(); ctx.moveTo(0, y(0)); ctx.lineTo(width, y(0)); ctx.stroke();
      ctx.setLineDash([]);
    }
    ctx.strokeStyle = '#35d6f0';
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    pts.forEach((b, i) => { if (i === 0) ctx.moveTo(x(i), y(b.cvd)); else ctx.lineTo(x(i), y(b.cvd)); });
    ctx.stroke();
    ctx.fillStyle = '#8a8a9b';
    ctx.font = '10px ui-monospace, Menlo, monospace';
    ctx.textAlign = 'right';
    ctx.fillText(String(max), width - 4, 12);
    ctx.fillText(String(min), width - 4, h - 4);
    ctx.textAlign = 'left';
    ctx.fillText(`CVD ${pts[pts.length - 1].cvd}`, 6, 12);
  });
  return (
    <div className="cvd-pane" style={{ height }}>
      <canvas ref={ref} className="cvd-canvas" />
      {onClose && <button className="icon-btn cvd-close" title="Hide CVD" onClick={onClose}>×</button>}
    </div>
  );
}
