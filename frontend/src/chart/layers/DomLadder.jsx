import { useEffect, useMemo, useRef, useState } from 'react';

// DOM ladder from replay `book` messages: centred on the last print, bid /
// ask sizes, a per-level session traded-volume column, a flash on prints.
// Clicking a price hands it back (the page turns it into a horizontal-line
// drawing — never an order).
export default function DomLadder({ book, vap, lastTrade, tickSize, depth = 20, approx, onPickPrice }) {
  const [flash, setFlash] = useState(null);
  const flashTimer = useRef(null);
  useEffect(() => {
    if (!lastTrade) return undefined;
    setFlash({ price: lastTrade.price, side: lastTrade.side, key: lastTrade.ts });
    clearTimeout(flashTimer.current);
    flashTimer.current = setTimeout(() => setFlash(null), 220);
    return () => clearTimeout(flashTimer.current);
  }, [lastTrade]);

  const bids = useMemo(() => new Map((book?.bids || []).map(([p, s]) => [p, s])), [book]);
  const asks = useMemo(() => new Map((book?.asks || []).map(([p, s]) => [p, s])), [book]);
  const centre = lastTrade?.price ?? (book?.bids?.[0]?.[0] ?? null);
  if (centre == null) return <div className="dom-panel-empty"><p>Waiting for the book…</p></div>;
  const decimals = tickSize >= 1 ? 0 : tickSize >= 0.1 ? 1 : 2;
  const rows = [];
  for (let i = depth; i >= -depth; i--) {
    const price = Number((centre + i * tickSize).toFixed(4));
    rows.push(price);
  }
  const maxSize = Math.max(1, ...bids.values(), ...asks.values());
  const maxVap = Math.max(1, ...rows.map((p) => vap?.get(p) || 0));

  return (
    <div className="ladder">
      <div className="ladder-head">
        <span>Bid</span><span>Price</span><span>Ask</span><span>Vol</span>
      </div>
      <div className="ladder-body">
        {rows.map((price) => {
          const b = bids.get(price);
          const a = asks.get(price);
          const v = vap?.get(price) || 0;
          const isLast = price === centre;
          const fl = flash && flash.price === price ? (flash.side === 'B' ? 'flash-buy' : 'flash-sell') : '';
          return (
            <div key={price} className={`ladder-row ${isLast ? 'last' : ''} ${fl}`} onClick={() => onPickPrice?.(price)}>
              <span className="ladder-bid">
                {b != null && <i style={{ width: `${(b / maxSize) * 100}%` }} />}
                <b>{b ?? ''}</b>
              </span>
              <span className="ladder-price">{price.toFixed(decimals)}</span>
              <span className="ladder-ask">
                {a != null && <i style={{ width: `${(a / maxSize) * 100}%` }} />}
                <b>{a ?? ''}</b>
              </span>
              <span className="ladder-vol">
                {v > 0 && <i style={{ width: `${(v / maxVap) * 100}%` }} />}
                <b>{v || ''}</b>
              </span>
            </div>
          );
        })}
      </div>
      <div className="dom-panel-foot">
        {approx ? 'Book approximate — 60-second checkpoints above 25× or without the day cached.' : 'Tick-exact L3 book rebuilt from MBO.'}
      </div>
    </div>
  );
}
