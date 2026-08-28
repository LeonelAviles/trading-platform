import { useEffect, useState } from 'react';
import { Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { fetchSymbols } from './api';
import { HeaderSlotContext } from './headerSlot';
import CandlestickPage from './pages/CandlestickPage';

export default function App() {
  const [symbols, setSymbols] = useState([]);
  const [symbol, setSymbol] = useState('');
  // Ref-callbacks into state so the slot nodes are available to child portals
  // once the header mounts.
  const [slot, setSlot] = useState(null);
  const [trailingSlot, setTrailingSlot] = useState(null);
  const location = useLocation();

  // Arriving with a symbol in the route state (e.g. a link to a specific
  // instrument): honour it over whatever the picker last had.
  useEffect(() => {
    if (location.state?.symbol) setSymbol(location.state.symbol);
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
        <div className="hdr-symbol">
          {symbol && <span className="symbol-avatar">{symbol[0]}</span>}
          <select className="symbol-select chevron" value={symbol} onChange={(e) => setSymbol(e.target.value)}>
            {symbols.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        </div>

        {/* Pages portal their route-specific controls into these slots. */}
        <div className="hdr-slot" ref={setSlot} />
        <div className="hdr-trailing" ref={setTrailingSlot} />
      </header>
      <div className="app-body">
        <HeaderSlotContext.Provider value={{ main: slot, trailing: trailingSlot }}>
          <Routes>
            <Route path="/" element={<CandlestickPage symbol={symbol} />} />
            {/* The chart is the whole app now; old deep links (/chart,
                /strategies, ...) would otherwise render an empty body. */}
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </HeaderSlotContext.Provider>
      </div>
    </div>
  );
}
