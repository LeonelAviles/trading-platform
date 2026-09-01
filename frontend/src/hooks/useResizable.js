import { useEffect, useState } from 'react';

// Drag-to-resize for a docked panel: persists the size in localStorage,
// clamps to [min, max] (max may be a function for viewport-relative caps),
// and suppresses text-selection/cursor flicker while dragging — same
// mechanics as the docks' resize handles, generalized for reuse.
export function useResizable({ key, defaultSize, min, max, cursor = 'col-resize' }) {
  const [size, setSize] = useState(() => {
    const v = Number(localStorage.getItem(key));
    return v >= min ? v : defaultSize;
  });
  const [resizing, setResizing] = useState(false);

  useEffect(() => { localStorage.setItem(key, String(size)); }, [key, size]);

  useEffect(() => {
    if (!resizing) return;
    const prevSelect = document.body.style.userSelect;
    const prevCursor = document.body.style.cursor;
    document.body.style.userSelect = 'none';
    document.body.style.cursor = cursor;
    return () => {
      document.body.style.userSelect = prevSelect;
      document.body.style.cursor = prevCursor;
    };
  }, [resizing, cursor]);

  function clamp(v) {
    const hi = typeof max === 'function' ? max() : max;
    return Math.min(Math.max(v, min), hi);
  }

  // `computeSize(pointerEvent)` maps the drag position to a raw size — each
  // panel knows its own geometry (measuring from its left/right/top edge).
  function bind(computeSize) {
    return {
      onPointerDown: (e) => {
        e.preventDefault();
        e.currentTarget.setPointerCapture(e.pointerId);
        setResizing(true);
      },
      onPointerMove: (e) => {
        if (!resizing) return;
        setSize(clamp(computeSize(e)));
      },
      onPointerUp: (e) => {
        setResizing(false);
        try { e.currentTarget.releasePointerCapture(e.pointerId); } catch { /* already released */ }
      },
    };
  }

  return { size, setSize, resizing, bind };
}
