import { useState } from 'react';
import { formatEtClock } from '../time';

// Last N prints, newest first; side colours; large prints highlighted;
// hovering pauses the list so a print can be read.
export default function TimeAndSales({ trades, large = 50, rows = 300 }) {
  const [hover, setHover] = useState(false);
  const [frozen, setFrozen] = useState(null);
  const list = (hover && frozen) || trades.slice(-rows).reverse();
  return (
    <div
      className="tape"
      onMouseEnter={() => { setFrozen(trades.slice(-rows).reverse()); setHover(true); }}
      onMouseLeave={() => { setHover(false); setFrozen(null); }}
    >
      <div className="tape-head"><span>Time</span><span>Price</span><span>Size</span></div>
      <div className="tape-body">
        {list.map((t, i) => (
          <div key={`${t.ts}-${i}`} className={`tape-row ${t.side === 'B' ? 'buy' : 'sell'} ${t.size >= large ? 'large' : ''}`}>
            <span>{formatEtClock(t.ts / 1e9)}</span>
            <span>{t.price.toFixed(2)}</span>
            <span>{t.size}</span>
          </div>
        ))}
        {!list.length && <div className="dom-panel-empty"><p>No prints yet.</p></div>}
      </div>
      {hover && <div className="tape-paused">paused</div>}
    </div>
  );
}
