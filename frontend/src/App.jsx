import { lazy, Suspense, useEffect, useState } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { HeaderSlotContext } from "./headerSlot";
import Sidebar from "./components/Sidebar";
import ResearchSelection from "./components/ResearchSelection";
import { researchHref, useResearchSelection } from "./researchSelection";
import { Link } from "react-router-dom";

const DeskPage = lazy(() => import("./pages/DeskPage"));
const StrategiesPage = lazy(() => import("./pages/StrategiesPage"));
const StrategyPage = lazy(() => import("./pages/StrategyPage"));
const BacktestsPage = lazy(() => import("./pages/BacktestsPage"));
const CandlestickPage = lazy(() => import("./pages/CandlestickPage"));
const SettingsPage = lazy(() => import("./pages/SettingsPage"));
const AgentPage = lazy(() => import("./pages/AgentPage"));

const CHART_ROUTES = ["/review/"];

function readPref() {
  try {
    return localStorage.getItem("sidebar.collapsed") === "1";
  } catch {
    return false;
  }
}

// App shell: persistent sidebar + a top bar pages portal their controls into
// (the review chart fills it with its toolbar) + the routed page.
function AppShell() {
  const { selection } = useResearchSelection();
  const { pathname } = useLocation();
  const onChart = CHART_ROUTES.some((p) => pathname.startsWith(p));
  const showRouteBar = true;
  const [pref, setPref] = useState(readPref);
  const collapsed = onChart || pref;
  const [leadingSlot, setLeadingSlot] = useState(null);
  const [slot, setSlot] = useState(null);
  const [trailingSlot, setTrailingSlot] = useState(null);

  useEffect(() => {
    try {
      localStorage.setItem("sidebar.collapsed", pref ? "1" : "0");
    } catch {
      /* private mode */
    }
  }, [pref]);

  return (
    <div className="app-shell">
      <Sidebar
        collapsed={collapsed}
        onToggle={() => setPref((p) => (onChart ? false : !p))}
      />
      <div className="app">
        {showRouteBar && (
          <header className="app-header">
            <div className="hdr-leading" ref={setLeadingSlot} />
            <div className="hdr-slot" ref={setSlot} />
            <div className="hdr-trailing" ref={setTrailingSlot} />
            {!onChart && (
              <div className="terminal-top-scope">ES · Historical research</div>
            )}
          </header>
        )}
        {pathname === "/agent" && selection.strategyId && (
          <div className="research-context-strip">
            <span>
              Selected: {selection.name || selection.strategyId}
              {selection.runId ? ` · Run ${selection.runId}` : ""}
            </span>
            <Link to={researchHref("/", selection)}>Back to desk</Link>
          </div>
        )}
        <div className="app-body">
          <HeaderSlotContext.Provider
            value={{ leading: leadingSlot, main: slot, trailing: trailingSlot }}
          >
            <Suspense
              fallback={
                <div className="route-loading">
                  <span />
                  <span />
                  <span />
                </div>
              }
            >
              <Routes>
                <Route path="/" element={<DeskPage />} />
                <Route path="/strategies" element={<StrategiesPage />} />
                <Route
                  path="/strategies/:strategyId"
                  element={<StrategyPage />}
                />
                <Route path="/backtests" element={<BacktestsPage />} />
                <Route path="/agent" element={<AgentPage />} />
                <Route
                  path="/review"
                  element={<Navigate to="/backtests" replace />}
                />
                <Route
                  path="/review/:backtestId"
                  element={<CandlestickPage />}
                />
                <Route path="/settings" element={<SettingsPage />} />
                <Route path="*" element={<Navigate to="/" replace />} />
              </Routes>
            </Suspense>
          </HeaderSlotContext.Provider>
        </div>
      </div>
    </div>
  );
}

export default function App() {
  return (
    <ResearchSelection>
      <AppShell />
    </ResearchSelection>
  );
}
