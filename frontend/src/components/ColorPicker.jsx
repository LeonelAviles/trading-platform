import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { parseColor, toRgbaString, rgbToHex, generatePalette } from './colorUtils';

const PALETTE = generatePalette();

const POPOVER_WIDTH = 216;

export default function ColorPicker({ value, onChange, disabled, icon, title }) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState(null);
  const wrapRef = useRef(null);
  const popoverRef = useRef(null);
  const btnRef = useRef(null);
  const nativeRef = useRef(null);
  const { r, g, b, a } = parseColor(value);

  useEffect(() => {
    if (!open) return;
    // The popover is portaled to document.body (see below), so it's no
    // longer a DOM descendant of wrapRef — check both.
    function onDocPointerDown(e) {
      if (wrapRef.current?.contains(e.target)) return;
      if (popoverRef.current?.contains(e.target)) return;
      setOpen(false);
    }
    // Escape should just close this popover, not bubble up to whatever
    // global Escape handler the host page has (e.g. deselecting a shape).
    function onDocKeyDown(e) {
      if (e.key !== 'Escape') return;
      e.stopPropagation();
      setOpen(false);
    }
    document.addEventListener('pointerdown', onDocPointerDown);
    document.addEventListener('keydown', onDocKeyDown, true);
    return () => {
      document.removeEventListener('pointerdown', onDocPointerDown);
      document.removeEventListener('keydown', onDocKeyDown, true);
    };
  }, [open]);

  function toggleOpen() {
    if (disabled) return;
    if (!open) {
      const rect = btnRef.current.getBoundingClientRect();
      const left = Math.min(rect.left, window.innerWidth - POPOVER_WIDTH - 8);
      setPos({ top: rect.bottom + 6, left });
    }
    setOpen((o) => !o);
  }

  function pickHex(hex) {
    const rgb = parseColor(hex);
    onChange(toRgbaString({ ...rgb, a }));
  }
  function setOpacityPct(pct) {
    onChange(toRgbaString({ r, g, b, a: pct / 100 }));
  }

  return (
    <div className="color-picker" ref={wrapRef}>
      {icon ? (
        <button
          ref={btnRef}
          type="button" className="color-trigger-btn" disabled={disabled} title={title}
          onClick={toggleOpen}
        >
          <span className="color-trigger-icon">{icon}</span>
          <span
            className="color-trigger-bar"
            style={{
              // Layer the actual (possibly transparent) color over a
              // checkerboard so opacity is visible, like a real swatch.
              backgroundImage: `linear-gradient(${value}, ${value}), linear-gradient(45deg, rgba(0,0,0,0.35) 25%, transparent 25%, transparent 75%, rgba(0,0,0,0.35) 75%), linear-gradient(45deg, rgba(0,0,0,0.35) 25%, transparent 25%, transparent 75%, rgba(0,0,0,0.35) 75%)`,
              backgroundSize: '100% 100%, 4px 4px, 4px 4px',
              backgroundPosition: '0 0, 0 0, 2px 2px',
            }}
          />
        </button>
      ) : (
        <button
          ref={btnRef}
          type="button" className="color-swatch-btn" disabled={disabled}
          style={{ background: value }}
          onClick={toggleOpen}
        />
      )}
      {open && pos && createPortal(
        <div ref={popoverRef} className="color-popover" style={{ position: 'fixed', top: pos.top, left: pos.left }}>
          <div className="color-grid">
            {PALETTE.map((row, i) => (
              <div className="color-row" key={i}>
                {row.map((c) => {
                  const cur = parseColor(c);
                  const selected = cur.r === r && cur.g === g && cur.b === b;
                  return (
                    <button
                      key={c} type="button" title={c}
                      className={`color-cell ${selected ? 'selected' : ''}`}
                      style={{ background: c }}
                      onClick={() => pickHex(c)}
                    />
                  );
                })}
              </div>
            ))}
          </div>
          <div className="color-custom-row">
            <button type="button" className="color-plus" title="Custom color" onClick={() => nativeRef.current.click()}>+</button>
            <input
              ref={nativeRef} type="color" style={{ display: 'none' }}
              value={rgbToHex(r, g, b)}
              onChange={(e) => pickHex(e.target.value)}
            />
          </div>
          <div className="opacity-row">
            <span>Opacity</span>
            <input type="range" min="0" max="100" value={Math.round(a * 100)} onChange={(e) => setOpacityPct(Number(e.target.value))} />
            <span className="opacity-value">{Math.round(a * 100)}%</span>
          </div>
        </div>,
        document.body
      )}
    </div>
  );
}
