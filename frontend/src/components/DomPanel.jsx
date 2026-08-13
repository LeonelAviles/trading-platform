import { useEffect, useRef, useState } from 'react';
import { fetchDom } from '../api';
import { useResizable } from '../hooks/useResizable';

const MIN_WIDTH = 170;
const MAX_WIDTH = 420;

// Depth of Market panel — right-hand dock, drag-resizable from its left
// edge. Reconstructed from the MBO Add/Cancel/Fill event stream (see
// backend/data_store.py order_book_snapshot); an approximation, not an
// exchange-certified book, since this is synthetic data with no
// matching-engine consistency guarantee.
export default function DomPanel({ symbol, asOf, onClose }) {
  const [snap, setSnap] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);
  const panelRef = useRef(null);
  const { size: width, resizing, bind } = useResizable({
    key: 'domPanelWidth', defaultSize: 210, min: MIN_WIDTH, max: MAX_WIDTH,
  });
  const resizeHandlers = bind((e) => panelRef.current.getBoundingClientRect().right - e.clientX);

  const load = () => {
    if (!symbol) return;
    setLoading(true);
    setError(null);
    fetchDom(symbol, asOf)
      .then((s) => setSnap(s))
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    let cancelled = false;
    if (!symbol) return;
    setLoading(true);
    setError(null);
    fetchDom(symbol, asOf)
      .then((s) => { if (!cancelled) setSnap(s); })
      .catch((e) => { if (!cancelled) setError(e.message); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [symbol, asOf]);

  const bids = snap?.bids || [];
  const asks = snap?.asks || [];
  const maxSize = Math.max(1, ...bids.map((l) => l.size), ...asks.map((l) => l.size));

  return (
    <div ref={panelRef} className="dom-panel" style={{ width }}>
      <div className={`panel-resize left ${resizing ? 'active' : ''}`} {...resizeHandlers} />
      <div className="dom-panel-head">
        <h2>DOM</h2>
        <div className="dom-panel-head-actions">
          <button className="icon-btn" title="Refresh" onClick={load} disabled={loading}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7"><path d="M20 11a8 8 0 1 0-2.3 6.3" /><path d="M20 4v7h-7" /></svg>
          </button>
          <button className="icon-btn" title="Close" onClick={onClose}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M6 6l12 12M18 6L6 18" /></svg>
          </button>
        </div>
      </div>

      {error && <div className="dom-panel-empty"><p>{error}</p></div>}

      {!error && snap && (
        <div className="dom-ladder">
          <div className="dom-side">
            {asks.length === 0 && <div className="dom-empty-side">No resting asks in range</div>}
            {asks.slice().reverse().map((l) => (
              <div key={l.price} className="dom-row dom-ask">
                <div className="dom-row-bar" style={{ width: `${(l.size / maxSize) * 100}%` }} />
                <span className="dom-row-price">{l.price.toFixed(2)}</span>
                <span className="dom-row-size">{l.size}</span>
              </div>
            ))}
          </div>

          <div className="dom-mid">
            {snap.lastPrice != null ? snap.lastPrice.toFixed(2) : '—'}
          </div>

          <div className="dom-side">
            {bids.length === 0 && <div className="dom-empty-side">No resting bids in range</div>}
            {bids.map((l) => (
              <div key={l.price} className="dom-row dom-bid">
                <div className="dom-row-bar" style={{ width: `${(l.size / maxSize) * 100}%` }} />
                <span className="dom-row-price">{l.price.toFixed(2)}</span>
                <span className="dom-row-size">{l.size}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {snap && (
        <div className="dom-panel-foot">
          Approximate — reconstructed from the last {snap.windowMinutes} min of order-flow events.
        </div>
      )}
    </div>
  );
}
