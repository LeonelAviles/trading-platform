import { useEffect, useState } from 'react';
import { Route, Routes } from 'react-router-dom';
import { fetchSymbols } from './api';
import CandlestickPage from './pages/CandlestickPage';

export default function App() {
  const [symbols, setSymbols] = useState([]);
  const [symbol, setSymbol] = useState('');

  useEffect(() => {
    fetchSymbols().then((syms) => {
      setSymbols(syms);
      if (syms.length) setSymbol(syms[0]);
    });
  }, []);

  return (
    <div className="app">
      <div id="topbar">
        {symbol && <span className="symbol-avatar">{symbol[0]}</span>}
        <select className="symbol-select" value={symbol} onChange={(e) => setSymbol(e.target.value)}>
          {symbols.map((s) => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>
      </div>
      <Routes>
        <Route path="/" element={<CandlestickPage symbol={symbol} />} />
      </Routes>
    </div>
  );
}
