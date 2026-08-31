import { useState } from 'react';
import { Navigate, Route, Routes } from 'react-router-dom';
import { HeaderSlotContext } from './headerSlot';
import CandlestickPage from './pages/CandlestickPage';
import ReviewPicker from './pages/ReviewPicker';
import StrategyPage from './pages/StrategyPage';
import ResearchPage from './pages/ResearchPage';
import ChartPage from './pages/ChartPage';

// Two kinds of chart: /review/:backtestId is bound to the backtest it names
// (no symbol picker there), and /chart/:symbol is the free chart with tick
// replay and teaching mode (PLATFORM-SPEC.md Phase 5/6).
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
            <Route path="/research" element={<ResearchPage />} />
            <Route path="/chart/:symbol" element={<ChartPage />} />
            <Route path="/chart" element={<Navigate to="/chart/ES1!" replace />} />
            {/* Everything else lands on the chooser. */}
            <Route path="*" element={<Navigate to="/review" replace />} />
          </Routes>
        </HeaderSlotContext.Provider>
      </div>
    </div>
  );
}
