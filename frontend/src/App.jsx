import { useEffect, useState } from 'react';
import { Route, Routes, useLocation, useNavigate } from 'react-router-dom';
import { fetchSymbols } from './api';
import { HeaderSlotContext } from './headerSlot';
import CandlestickPage from './pages/CandlestickPage';
import StrategyPage from './pages/StrategyPage';
import ChatPanel from './components/ChatPanel';

export default function App() {
  const [symbols, setSymbols] = useState([]);
  const [symbol, setSymbol] = useState('');
  const [chatOpen, setChatOpen] = useState(false);
  // Ref-callbacks into state so the slot nodes are available to child portals
  // once the header mounts.
  const [slot, setSlot] = useState(null);
  const [trailingSlot, setTrailingSlot] = useState(null);
  const location = useLocation();
  const navigate = useNavigate();
  const onChart = location.pathname.startsWith('/chart');

  useEffect(() => {
    fetchSymbols().then((syms) => {
      setSymbols(syms);
      if (syms.length) setSymbol(syms[0]);
    });
  }, []);

  return (
    <div className="app">
      <header className="app-header">
        <button className="home-btn" title={onChart ? 'Back to strategies' : 'Strategies'} onClick={() => navigate('/')}>
          <span className="home-mark">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M3 17l5-5 4 4 8-9" />
            </svg>
          </span>
        </button>

        {onChart && (
          <div className="hdr-symbol">
            {symbol && <span className="symbol-avatar">{symbol[0]}</span>}
            <select className="symbol-select chevron" value={symbol} onChange={(e) => setSymbol(e.target.value)}>
              {symbols.map((s) => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>
          </div>
        )}

        <div className="hdr-slot" ref={setSlot} />

        <button
          className={`chat-toggle ${chatOpen ? 'active' : ''}`}
          title="AI Assistant" onClick={() => setChatOpen((o) => !o)}
        >
          <svg className="chat-toggle-spark" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
            <path d="M9 2.5l1.4 3.7 3.7 1.4-3.7 1.4L9 12.7 7.6 9 3.9 7.6l3.7-1.4z" />
            <path d="M17.5 12l.8 2.1 2.1.8-2.1.8-.8 2.1-.8-2.1-2.1-.8 2.1-.8z" />
          </svg>
          AI Assist
        </button>

        <div className="hdr-trailing" ref={setTrailingSlot} />
      </header>
      <div className="app-body">
        <HeaderSlotContext.Provider value={{ main: slot, trailing: trailingSlot }}>
          <Routes>
            <Route path="/" element={<StrategyPage symbol={symbol} setSymbol={setSymbol} />} />
            <Route path="/chart" element={<CandlestickPage symbol={symbol} />} />
          </Routes>
        </HeaderSlotContext.Provider>
        {chatOpen && <ChatPanel symbol={symbol} onClose={() => setChatOpen(false)} />}
      </div>
    </div>
  );
}
