import { useRef, useState } from 'react';
import { useResizable } from '../hooks/useResizable';
import DomLadder from './layers/DomLadder';
import TimeAndSales from './layers/TimeAndSales';

// Right-hand dock: DOM ladder and Time & Sales tabs, drag-resizable.
export default function RightDock({ replay, layerSettings, onPickPrice, onClose, initialTab = 'dom' }) {
  const [tab, setTab] = useState(initialTab);
  const panelRef = useRef(null);
  const { size: width, resizing, bind } = useResizable({ key: 'rightDockWidth', defaultSize: 260, min: 200, max: 460 });
  const handlers = bind((e) => panelRef.current.getBoundingClientRect().right - e.clientX);
  return (
    <div ref={panelRef} className="dom-panel right-dock" style={{ width }}>
      <div className={`panel-resize left ${resizing ? 'active' : ''}`} {...handlers} />
      <div className="dom-panel-head">
        <div className="dock-tabs">
          <button className={`dock-tab ${tab === 'dom' ? 'active' : ''}`} onClick={() => setTab('dom')}>DOM</button>
          <button className={`dock-tab ${tab === 'tape' ? 'active' : ''}`} onClick={() => setTab('tape')}>T&amp;S</button>
        </div>
        <button className="icon-btn" title="Close" onClick={onClose}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M6 6l12 12M18 6L6 18" /></svg>
        </button>
      </div>
      {tab === 'dom' ? (
        <DomLadder
          book={replay.book} vap={replay.vap} lastTrade={replay.lastTrade} tickSize={replay.tickSize}
          depth={layerSettings.ladderDepth} approx={replay.bookMode !== 'l3'} onPickPrice={onPickPrice}
        />
      ) : (
        <TimeAndSales trades={replay.trades} large={layerSettings.tapeLargePrint} rows={layerSettings.tapeRows} />
      )}
    </div>
  );
}
