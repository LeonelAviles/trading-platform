import { useEffect, useState } from 'react';
import { Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { HeaderSlotContext } from './headerSlot';
import Sidebar from './components/Sidebar';
import DeskPage from './pages/DeskPage';
import StrategiesPage from './pages/StrategiesPage';
import StrategyPage from './pages/StrategyPage';
import BacktestsPage from './pages/BacktestsPage';
import CandlestickPage from './pages/CandlestickPage';
import ChartPage from './pages/ChartPage';
import TeachingPage from './pages/TeachingPage';
import TeachPage from './pages/TeachPage';
import ResearchPage from './pages/ResearchPage';
import KnowledgePage from './pages/KnowledgePage';
import SettingsPage from './pages/SettingsPage';

const CHART_ROUTES = ['/chart', '/review/'];

function readPref() {
  try { return localStorage.getItem('sidebar.collapsed') === '1'; } catch { return false; }
}

// App shell: persistent sidebar + a top bar pages portal their controls into
// (the chart pages fill it with their toolbars) + the routed page.
export default function App() {
  const { pathname } = useLocation();
  const onChart = CHART_ROUTES.some((p) => pathname.startsWith(p));
  const [pref, setPref] = useState(readPref);
  const collapsed = onChart || pref;
  const [leadingSlot, setLeadingSlot] = useState(null);
  const [slot, setSlot] = useState(null);
  const [trailingSlot, setTrailingSlot] = useState(null);

  useEffect(() => {
    try { localStorage.setItem('sidebar.collapsed', pref ? '1' : '0'); } catch { /* private mode */ }
  }, [pref]);

  return (
    <div className="app-shell">
      <Sidebar collapsed={collapsed} onToggle={() => setPref((p) => (onChart ? false : !p))} />
      <div className="app">
        <header className="app-header">
          <div className="hdr-leading" ref={setLeadingSlot} />
          <div className="hdr-slot" ref={setSlot} />
          <div className="hdr-trailing" ref={setTrailingSlot} />
        </header>
        <div className="app-body">
          <HeaderSlotContext.Provider value={{ leading: leadingSlot, main: slot, trailing: trailingSlot }}>
            <Routes>
              <Route path="/" element={<DeskPage />} />
              <Route path="/strategies" element={<StrategiesPage />} />
              <Route path="/strategies/:strategyId" element={<StrategyPage />} />
              <Route path="/backtests" element={<BacktestsPage />} />
              <Route path="/review" element={<Navigate to="/backtests" replace />} />
              <Route path="/review/:backtestId" element={<CandlestickPage />} />
              <Route path="/chart/:symbol" element={<ChartPage />} />
              <Route path="/chart" element={<Navigate to="/chart/ES1!" replace />} />
              <Route path="/teaching" element={<TeachingPage />} />
              <Route path="/teach/:sessionId" element={<TeachPage />} />
              <Route path="/research" element={<ResearchPage />} />
              <Route path="/knowledge" element={<KnowledgePage />} />
              <Route path="/settings" element={<SettingsPage />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </HeaderSlotContext.Provider>
        </div>
      </div>
    </div>
  );
}
