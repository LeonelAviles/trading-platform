import { useEffect, useRef, useState } from 'react';
import ShapeElement from './ShapeElement';
import ColorPicker from '../components/ColorPicker';
import {
  pxToData, dataToPx, makePositionFromDrag, LINE_WIDTHS,
  DEFAULT_COLOR, DEFAULT_LINE_WIDTH, DEFAULT_FILL_COLOR,
  DEFAULT_PROFIT_COLOR, DEFAULT_LOSS_COLOR, DEFAULT_ENTRY_COLOR,
} from './geometry';

export default function DrawingOverlay({ chart, series, shapes, setShapes, activeTool, setActiveTool, selectedId, setSelectedId }) {
  const svgRef = useRef(null);
  const [draft, setDraft] = useState(null);
  const createRef = useRef(null); // { startPx } while dragging out a new shape

  // Clicking empty chart space (never reaches our shapes, since they sit on
  // top and stop propagation) deselects whatever's currently selected.
  useEffect(() => {
    if (!chart) return;
    const handler = () => setSelectedId(null);
    chart.subscribeClick(handler);
    return () => chart.unsubscribeClick(handler);
  }, [chart, setSelectedId]);

  useEffect(() => {
    function onKeyDown(e) {
      if (e.key !== 'Delete' && e.key !== 'Backspace' && e.key !== 'Escape') return;
      const tag = document.activeElement?.tagName;
      if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') return;
      if (e.key === 'Escape') { setSelectedId(null); return; }
      if (!selectedId) return;
      if (shapes.find((s) => s.id === selectedId)?.locked) return;
      setShapes((prev) => prev.filter((s) => s.id !== selectedId));
      setSelectedId(null);
    }
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [selectedId, shapes, setShapes, setSelectedId]);

  if (!chart || !series) return null;

  const toXY = (e) => {
    const rect = svgRef.current.getBoundingClientRect();
    return { x: e.clientX - rect.left, y: e.clientY - rect.top };
  };

  function makeShape(tool, a, b) {
    if (tool === 'line') {
      return { id: crypto.randomUUID(), type: 'line', x1: a.logical, p1: a.price, x2: b.logical, p2: b.price, color: DEFAULT_COLOR, lineWidth: DEFAULT_LINE_WIDTH };
    }
    if (tool === 'hline') {
      // Only the drag's starting point sets the price — it stays flat
      // regardless of where the pointer ends up vertically.
      return { id: crypto.randomUUID(), type: 'hline', x1: a.logical, x2: b.logical, price: a.price, color: DEFAULT_COLOR, lineWidth: DEFAULT_LINE_WIDTH };
    }
    if (tool === 'rect') {
      return {
        id: crypto.randomUUID(), type: 'rect',
        left: Math.min(a.logical, b.logical), right: Math.max(a.logical, b.logical),
        top: Math.max(a.price, b.price), bottom: Math.min(a.price, b.price),
        borderColor: DEFAULT_COLOR, fillColor: DEFAULT_FILL_COLOR, lineWidth: DEFAULT_LINE_WIDTH,
      };
    }
    return makePositionFromDrag(tool, a, b);
  }

  function buildDraft(tool, startPx, curPx) {
    const a = pxToData(chart, series, startPx.x, startPx.y);
    const b = pxToData(chart, series, curPx.x, curPx.y);
    if (a.logical == null || b.logical == null) return null;
    return { ...makeShape(tool, a, b), id: 'draft' };
  }

  function onRootPointerDown(e) {
    if (activeTool === 'cursor') return;
    createRef.current = { startPx: toXY(e) };
    e.currentTarget.setPointerCapture(e.pointerId);
  }
  function onRootPointerMove(e) {
    if (!createRef.current) return;
    setDraft(buildDraft(activeTool, createRef.current.startPx, toXY(e)));
  }
  function onRootPointerUp(e) {
    if (!createRef.current) return;
    const startPx = createRef.current.startPx;
    const endPx = toXY(e);
    createRef.current = null;

    const a = pxToData(chart, series, startPx.x, startPx.y);
    const b = pxToData(chart, series, endPx.x, endPx.y);
    const dragDist = Math.hypot(endPx.x - startPx.x, endPx.y - startPx.y);
    const tool = activeTool;
    setDraft(null);
    setActiveTool('cursor');

    if (a.logical == null || b.logical == null || a.price == null || b.price == null) return;
    if (dragDist < 4 && tool !== 'long' && tool !== 'short') return;

    setShapes((prev) => [...prev, makeShape(tool, a, b)]);
  }

  const selectedShape = shapes.find((s) => s.id === selectedId);
  const isPosition = selectedShape && (selectedShape.type === 'long' || selectedShape.type === 'short');
  const showStyleToolbar = selectedShape && (selectedShape.type === 'line' || selectedShape.type === 'hline' || selectedShape.type === 'rect' || isPosition);

  let toolbarPos = null;
  if (showStyleToolbar) {
    if (selectedShape.type === 'rect') {
      const topLeft = dataToPx(chart, series, selectedShape.left, selectedShape.top);
      if (topLeft.x != null) toolbarPos = topLeft;
    } else if (selectedShape.type === 'hline') {
      const p1 = dataToPx(chart, series, selectedShape.x1, selectedShape.price);
      const p2 = dataToPx(chart, series, selectedShape.x2, selectedShape.price);
      if (p1.x != null && p2.x != null) toolbarPos = { x: Math.min(p1.x, p2.x), y: p1.y };
    } else if (isPosition) {
      const topPrice = Math.max(selectedShape.entryPrice, selectedShape.targetPrice, selectedShape.stopPrice);
      const p = dataToPx(chart, series, selectedShape.entryLogical, topPrice);
      if (p.x != null) toolbarPos = p;
    } else {
      const p1 = dataToPx(chart, series, selectedShape.x1, selectedShape.p1);
      const p2 = dataToPx(chart, series, selectedShape.x2, selectedShape.p2);
      if (p1.x != null && p2.x != null) toolbarPos = { x: Math.min(p1.x, p2.x), y: Math.min(p1.y, p2.y) };
    }
  }

  function updateSelected(patch) {
    setShapes((prev) => prev.map((s) => (s.id === selectedId ? { ...s, ...patch } : s)));
  }

  return (
    <>
      <svg
        ref={svgRef}
        className="draw-svg"
        style={{
          position: 'absolute', inset: 0, width: '100%', height: '100%', zIndex: 2,
          pointerEvents: activeTool === 'cursor' ? 'none' : 'auto',
          cursor: activeTool === 'cursor' ? 'default' : 'crosshair',
        }}
        onPointerDown={onRootPointerDown}
        onPointerMove={onRootPointerMove}
        onPointerUp={onRootPointerUp}
      >
        {shapes.map((shape) => (
          <ShapeElement
            key={shape.id}
            shape={shape}
            chart={chart}
            series={series}
            selected={shape.id === selectedId}
            interactive={activeTool === 'cursor'}
            onSelect={(e) => { e.stopPropagation(); setSelectedId(shape.id); }}
            setShapes={setShapes}
          />
        ))}
        {draft && (
          <g opacity={0.7}>
            <ShapeElement shape={draft} chart={chart} series={series} selected={false} interactive={false} onSelect={() => {}} setShapes={() => {}} />
          </g>
        )}
      </svg>

      {showStyleToolbar && toolbarPos && (
        <div className="shape-toolbar" style={{ left: toolbarPos.x, top: Math.max(0, toolbarPos.y - 44) }}>
          {isPosition ? (
            <>
              <ColorPicker
                title="Entry line color"
                icon={<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M12 20h9M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z" /></svg>}
                value={selectedShape.entryColor || DEFAULT_ENTRY_COLOR}
                onChange={(v) => updateSelected({ entryColor: v })}
              />
              <ColorPicker
                title="Profit zone color"
                icon={<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M4 17l7-7 4 4 5-8M14 6h6v6" /></svg>}
                value={selectedShape.profitColor || DEFAULT_PROFIT_COLOR}
                onChange={(v) => updateSelected({ profitColor: v })}
              />
              <ColorPicker
                title="Loss zone color"
                icon={<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M4 7l7 7 4-4 5 8M14 18h6v-6" /></svg>}
                value={selectedShape.lossColor || DEFAULT_LOSS_COLOR}
                onChange={(v) => updateSelected({ lossColor: v })}
              />
            </>
          ) : (
            <>
              <ColorPicker
                title="Line color"
                icon={<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M12 20h9M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z" /></svg>}
                value={selectedShape.type === 'rect' ? (selectedShape.borderColor || selectedShape.color || DEFAULT_COLOR) : (selectedShape.color || DEFAULT_COLOR)}
                onChange={(v) => updateSelected(selectedShape.type === 'rect' ? { borderColor: v } : { color: v })}
              />
              {selectedShape.type === 'rect' && (
                <ColorPicker
                  title="Fill color"
                  icon={<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="m19 11-7-7-8.5 8.5a2 2 0 0 0 0 2.83L7 19l12-8Z" /><path d="M5 2 2 5M14.5 5.5 20 11M2 22s2-1 2-3-2-3-2-3" /></svg>}
                  value={selectedShape.fillColor || DEFAULT_FILL_COLOR}
                  onChange={(v) => updateSelected({ fillColor: v })}
                />
              )}
            </>
          )}
          <div className="toolbar-sep" />
          {LINE_WIDTHS.map((w) => (
            <button
              key={w}
              className={`width-btn ${selectedShape.lineWidth === w ? 'active' : ''}`}
              title={`${w}px`}
              onClick={() => updateSelected({ lineWidth: w })}
            >
              <svg viewBox="0 0 24 24"><line x1="3" y1="12" x2="21" y2="12" stroke="currentColor" strokeWidth={w * 1.5} /></svg>
            </button>
          ))}
          <span className="toolbar-label">{selectedShape.lineWidth || DEFAULT_LINE_WIDTH}px</span>
          <div className="toolbar-sep" />
          <button
            className={`width-btn ${selectedShape.locked ? 'active' : ''}`}
            title={selectedShape.locked ? 'Unlock' : 'Lock'}
            onClick={() => updateSelected({ locked: !selectedShape.locked })}
          >
            {selectedShape.locked ? (
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><rect x="4" y="11" width="16" height="9" rx="1.5" /><path d="M8 11V7a4 4 0 0 1 8 0v4" /></svg>
            ) : (
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><rect x="4" y="11" width="16" height="9" rx="1.5" /><path d="M8 11V7a4 4 0 0 1 7.5-2" /></svg>
            )}
          </button>
          <button
            className="width-btn" title="Delete" disabled={selectedShape.locked}
            onClick={() => { if (selectedShape.locked) return; setShapes((prev) => prev.filter((s) => s.id !== selectedId)); setSelectedId(null); }}
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13" /></svg>
          </button>
        </div>
      )}
    </>
  );
}
