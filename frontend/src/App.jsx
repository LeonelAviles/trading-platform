import { useEffect, useState } from 'react';
import { Route, Routes, useLocation, useNavigate } from 'react-router-dom';
import { fetchSymbols } from './api';
import { HeaderSlotContext } from './headerSlot';
import { ChatContext } from './chatContext';
import CandlestickPage from './pages/CandlestickPage';
import StrategyPage from './pages/StrategyPage';
import StrategyDashboardPage from './pages/StrategyDashboardPage';
import ChatPanel from './components/ChatPanel';

export default function App() {
  const [symbols, setSymbols] = useState([]);
  const [symbol, setSymbol] = useState('');
  const [chatOpen, setChatOpen] = useState(false);
  const [chatContext, setChatContext] = useState({});
  // Ref-callbacks into state so the slot nodes are available to child portals
  // once the header mounts.
  const [slot, setSlot] = useState(null);
  const [trailingSlot, setTrailingSlot] = useState(null);
  const location = useLocation();
  const navigate = useNavigate();
  const onChart = location.pathname.startsWith('/chart');

  // Arriving from a run (Run backtest -> chart): honour the symbol the job
  // belongs to and pop the assistant open, so the trader lands on the trades
  // with somewhere to talk about them. Keyed on location.key so running a
  // second backtest re-opens a panel that was closed in between.
  useEffect(() => {
    const st = location.state;
    if (!st) return;
    if (st.symbol) setSymbol(st.symbol);
    if (st.openChat) setChatOpen(true);
  }, [location.key, location.state]);

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
            <svg viewBox="0 0 24 24" fill="none">
              <g stroke="currentColor" strokeWidth="1.6" strokeLinecap="round">
                <line x1="5" y1="3" x2="5" y2="9" />
                <line x1="5" y1="16" x2="5" y2="21" />
                <line x1="12" y1="2" x2="12" y2="7" />
                <line x1="12" y1="17" x2="12" y2="22" />
                <line x1="19" y1="6" x2="19" y2="11" />
                <line x1="19" y1="17" x2="19" y2="20" />
              </g>
              <g fill="currentColor">
                <rect x="3.2" y="9" width="3.6" height="7" rx="1" />
                <rect x="10.2" y="7" width="3.6" height="10" rx="1" />
                <rect x="17.2" y="11" width="3.6" height="6" rx="1" />
              </g>
            </svg>
          </span>
          <span className="brand-name">Stratify</span>
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
          <ChatContext.Provider value={{ chatContext, setChatContext }}>
            <Routes>
              <Route path="/" element={<StrategyPage symbol={symbol} setSymbol={setSymbol} />} />
              <Route path="/strategy/:id" element={<StrategyDashboardPage />} />
              <Route path="/chart" element={<CandlestickPage symbol={symbol} />} />
            </Routes>
            {chatOpen && <ChatPanel symbol={symbol} onClose={() => setChatOpen(false)} />}
          </ChatContext.Provider>
        </HeaderSlotContext.Provider>
      </div>
    </div>
  );
}
