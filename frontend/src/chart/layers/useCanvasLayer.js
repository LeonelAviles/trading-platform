import { useLayoutEffect, useRef } from 'react';
import { prepareCanvas } from '../../orderflow/canvas';

// Shared redraw plumbing for canvas layers: draw on the next frame (and once
// more on the following one, since lightweight-charts publishes its range
// event before dependent scales have painted), on resize, on wheel and while
// dragging. `draw(ctx, width, height)` is called with a CSS-pixel context.
export function useCanvasLayer(canvasRef, chart, series, draw, deps) {
  const drawRef = useRef(draw);
  drawRef.current = draw;
  useLayoutEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !chart || !series) return undefined;
    const timeScale = chart.timeScale();
    const host = canvas.parentElement;
    let frame = null;
    let settle = null;
    let dragging = false;
    const paint = () => {
      const { ctx, width, height } = prepareCanvas(canvas);
      drawRef.current(ctx, width, height);
    };
    const schedule = () => {
      if (frame != null) return;
      frame = requestAnimationFrame(() => {
        frame = null;
        paint();
        if (settle != null) cancelAnimationFrame(settle);
        settle = requestAnimationFrame(() => {
          settle = null;
          paint();
          if (dragging) schedule();
        });
      });
    };
    const onDown = () => { dragging = true; schedule(); };
    const onUp = () => { dragging = false; schedule(); };
    const ro = new ResizeObserver(schedule);
    ro.observe(canvas);
    timeScale.subscribeVisibleLogicalRangeChange(schedule);
    host?.addEventListener('wheel', schedule, { passive: true });
    host?.addEventListener('pointerdown', onDown);
    window.addEventListener('pointerup', onUp);
    schedule();
    return () => {
      ro.disconnect();
      timeScale.unsubscribeVisibleLogicalRangeChange(schedule);
      host?.removeEventListener('wheel', schedule);
      host?.removeEventListener('pointerdown', onDown);
      window.removeEventListener('pointerup', onUp);
      if (frame != null) cancelAnimationFrame(frame);
      if (settle != null) cancelAnimationFrame(settle);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [canvasRef, chart, series, ...deps]);
}

export function interpolatedX(timeScale, logical) {
  if (logical == null) return null;
  const lower = Math.floor(logical);
  const upper = Math.ceil(logical);
  const x0 = timeScale.logicalToCoordinate(lower);
  if (x0 == null || lower === upper) return x0;
  const x1 = timeScale.logicalToCoordinate(upper);
  if (x1 == null) return null;
  return x0 + (x1 - x0) * (logical - lower);
}
