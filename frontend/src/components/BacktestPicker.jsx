import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

function labelOf(b) {
  const detail = b.summary ? `${b.summary.trades} trades` : b.status;
  // Interval matters here: selecting a job switches the chart to its bars.
  const tf = b.interval ? ` · ${b.interval}` : '';
  return `${b.strategyName}${tf} · ${detail}`;
}

// Custom dropdown (a native <select> can't host per-row delete buttons):
// shows the selected backtest, opens a menu of all backtests each with a
// trash icon. Popover is portaled to <body> so it escapes the top bar.
export default function BacktestPicker({ backtests, value, onChange, onDelete }) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState(null);
  const btnRef = useRef(null);
  const popRef = useRef(null);

  const selected = backtests.find((b) => b.id === value);

  useEffect(() => {
    if (!open) return;
    function onDown(e) {
      if (btnRef.current?.contains(e.target)) return;
      if (popRef.current?.contains(e.target)) return;
      setOpen(false);
    }
    document.addEventListener('pointerdown', onDown);
    return () => document.removeEventListener('pointerdown', onDown);
  }, [open]);

  function toggle() {
    if (!open) {
      const r = btnRef.current.getBoundingClientRect();
      // Flip the menu upward when the trigger is near the bottom (e.g. the
      // dock in the chart's bottom corner).
      const openUp = window.innerHeight - r.bottom < 320;
      setPos({
        top: openUp ? undefined : r.bottom + 6,
        bottom: openUp ? window.innerHeight - r.top + 6 : undefined,
        right: window.innerWidth - r.right,
        width: Math.max(r.width, 260),
      });
    }
    setOpen((o) => !o);
  }

  return (
    <div className="backtest-picker">
      <span className="backtest-label">Backtest</span>
      <button ref={btnRef} className={`backtest-trigger ${open ? 'open' : ''}`} onClick={toggle} title="Select backtest">
        <span className="backtest-trigger-text">{selected ? labelOf(selected) : 'None'}</span>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2"><path d="M6 9l6 6 6-6" /></svg>
      </button>

      {open && pos && createPortal(
        <div ref={popRef} className="backtest-menu" style={{ position: 'fixed', top: pos.top, bottom: pos.bottom, right: pos.right, minWidth: pos.width }}>
          <div className={`backtest-item ${!value ? 'active' : ''}`}>
            <button className="backtest-item-label" onClick={() => { onChange(''); setOpen(false); }}>None</button>
          </div>
          {backtests.map((b) => (
            <div key={b.id} className={`backtest-item ${b.id === value ? 'active' : ''}`}>
              <button className="backtest-item-label" onClick={() => { onChange(b.id); setOpen(false); }}>{labelOf(b)}</button>
              <button
                className="backtest-item-del" title="Delete backtest"
                onClick={(e) => { e.stopPropagation(); onDelete(b.id); }}
              >
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7"><path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13" /></svg>
              </button>
            </div>
          ))}
          {backtests.length === 0 && <div className="backtest-empty">No backtests yet</div>}
        </div>,
        document.body,
      )}
    </div>
  );
}
