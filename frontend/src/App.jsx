import { useState } from 'react';
import { Navigate, Route, Routes } from 'react-router-dom';
import { HeaderSlotContext } from './headerSlot';
import CandlestickPage from './pages/CandlestickPage';
import ReviewPicker from './pages/ReviewPicker';
import StrategyPage from './pages/StrategyPage';

// The app is a strategy-review tool: a chart only ever exists inside
// /review/:backtestId, so what's on screen always names the strategy it
// belongs to. There is no free-roaming chart and no symbol picker — the
// symbol comes from the backtest under review.
export default function App() {
  // Ref-callbacks into state so the slot nodes are available to child portals
  // once the header mounts.
  const [leadingSlot, setLeadingSlot] = useState(null);
  const [slot, setSlot] = useState(null);
  const [trailingSlot, setTrailingSlot] = useState(null);

  return (
    <div className="app">
      <header className="app-header">
        {/* Pages portal their route-specific controls into these slots. */}
        <div className="hdr-leading" ref={setLeadingSlot} />
        <div className="hdr-slot" ref={setSlot} />
        <div className="hdr-trailing" ref={setTrailingSlot} />
      </header>
      <div className="app-body">
        <HeaderSlotContext.Provider value={{ leading: leadingSlot, main: slot, trailing: trailingSlot }}>
          <Routes>
            <Route path="/review" element={<ReviewPicker />} />
            <Route path="/review/:backtestId" element={<CandlestickPage />} />
            <Route path="/strategies/:strategyId" element={<StrategyPage />} />
            {/* Everything else — including old deep links to a bare chart —
                lands on the chooser rather than an unattached chart. */}
            <Route path="*" element={<Navigate to="/review" replace />} />
          </Routes>
        </HeaderSlotContext.Provider>
      </div>
    </div>
  );
}
