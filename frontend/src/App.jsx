import { useEffect, useState } from 'react';
import { NavLink, Route, Routes } from 'react-router-dom';
import { fetchSymbols } from './api';
import { HeaderSlotContext } from './headerSlot';
import CandlestickPage from './pages/CandlestickPage';
import StrategyPage from './pages/StrategyPage';
import ChatPanel from './components/ChatPanel';

export default function App() {
  const [symbols, setSymbols] = useState([]);
  const [symbol, setSymbol] = useState('');
  const [chatOpen, setChatOpen] = useState(false);
  // Ref-callback into state so the slot node is available to child portals
  // once the header mounts.
  const [slot, setSlot] = useState(null);

  useEffect(() => {
    fetchSymbols().then((syms) => {
      setSymbols(syms);
      if (syms.length) setSymbol(syms[0]);
    });
  }, []);

  return (
    <div className="app">
      <header className="app-header">
        <div className="hdr-symbol">
          {symbol && <span className="symbol-avatar">{symbol[0]}</span>}
          <select className="symbol-select chevron" value={symbol} onChange={(e) => setSymbol(e.target.value)}>
            {symbols.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        </div>

        <div className="hdr-slot" ref={setSlot} />

        <button
          className={`chat-toggle ${chatOpen ? 'active' : ''}`}
          title="Assistant" onClick={() => setChatOpen((o) => !o)}
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
          </svg>
        </button>

        <nav className="app-nav">
          <NavLink to="/" end>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M3 3v18h18" /><path d="M7 14l3-4 3 3 4-6" />
            </svg>
            Chart
          </NavLink>
          <NavLink to="/strategy">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M4 6h16M4 12h16M4 18h10" />
            </svg>
            Strategies
          </NavLink>
        </nav>
      </header>
      <div className="app-body">
        <HeaderSlotContext.Provider value={slot}>
          <Routes>
            <Route path="/" element={<CandlestickPage symbol={symbol} />} />
            <Route path="/strategy" element={<StrategyPage symbol={symbol} />} />
          </Routes>
        </HeaderSlotContext.Provider>
        {chatOpen && <ChatPanel symbol={symbol} onClose={() => setChatOpen(false)} />}
      </div>
    </div>
  );
}
