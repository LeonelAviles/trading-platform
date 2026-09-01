import { useCallback, useContext, useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom';
import { fetchBacktest, deleteBacktest, fetchCVD } from '../api';
import { HeaderSlotContext } from '../headerSlot';
import AnalysisPanel from '../components/AnalysisPanel';
import ChatPanel from '../components/ChatPanel';
import { useOrderFlowChart } from '../chart/useOrderFlowChart';
import { intervalToSeconds } from '../drawing/geometry';

// A chart is never standalone: this page *is* the review of one backtest,
// named by the route. The strategy and the symbol both come from that job, so
// there is nothing to pick here and no way to end up staring at bars that
// aren't attached to a strategy. The chart itself — tick replay, order-flow
// layers, docks — is `useOrderFlowChart`, the same chart the teaching page
// uses; this file adds the backtest on top: its trades drawn on the bars (and
// revealed as the replay clock passes them), the analysis dock and Stratos.
export default function CandlestickPage() {
  const { leading: leadingSlot, main: headerSlot, trailing: trailingSlot } = useContext(HeaderSlotContext);
  const { backtestId } = useParams();
  const navigate = useNavigate();
  const location = useLocation();

  // The job under review, loaded from the route param. Everything else on the
  // page hangs off it — including the symbol, which is why the header has no
  // symbol picker to wander away with.
  const [selectedJob, setSelectedJob] = useState(null);
  const symbol = selectedJob?.symbol || '';
  const [interval, setInterval_] = useState('1min');
  const [backtestTrades, setBacktestTrades] = useState([]);
  const [cvdData, setCvdData] = useState([]);

  // Bottom analysis dock and right assistant dock both persist their
  // open/closed state. The assistant additionally opens itself when arriving
  // straight off "Run backtest", so there's somewhere to talk about the run
  // the moment it lands.
  const [analysisPanelOpen, setAnalysisPanelOpen] = useState(
    () => localStorage.getItem('analysisPanelOpen') !== 'false',
  );
  const [chatOpen, setChatOpen] = useState(
    () => Boolean(location.state?.openChat) || localStorage.getItem('chatOpen') === 'true',
  );
  useEffect(() => { localStorage.setItem('analysisPanelOpen', String(analysisPanelOpen)); }, [analysisPanelOpen]);
  useEffect(() => { localStorage.setItem('chatOpen', String(chatOpen)); }, [chatOpen]);

  const chart = useOrderFlowChart({ symbol, interval, setInterval: setInterval_ });
  const { bars, clockTime, shapes } = chart;

  // CVD for the analysis panel's CVD tab — fetched independently so a failure
  // here (e.g. a symbol with no MBO side data) never affects the candles.
  useEffect(() => {
    if (!symbol) return undefined;
    let cancelled = false;
    fetchCVD(symbol, interval)
      .then((points) => { if (!cancelled) setCvdData(points); })
      .catch(() => { if (!cancelled) setCvdData([]); });
    return () => { cancelled = true; };
  }, [symbol, interval]);

  // Deleting the run under review deletes the reason for this chart to exist,
  // so it goes back to the list rather than leaving empty bars behind.
  const handleDeleteBacktest = useCallback(async () => {
    await deleteBacktest(backtestId).catch(() => {});
    navigate('/backtests', { replace: true });
  }, [backtestId, navigate]);

  // The reviewed job's trades and live status. Arriving straight off a Run
  // backtest means the job is still running, so poll until it settles and draw
  // its trades the moment they exist. A job id that doesn't resolve is not a
  // chart we're allowed to show — bounce to the list.
  useEffect(() => {
    let cancelled = false;
    let timer = null;
    async function load() {
      try {
        const job = await fetchBacktest(backtestId);
        if (cancelled) return;
        setSelectedJob(job);
        setBacktestTrades(job.trades || []);
        // Draw the trades on the bars that produced them: a 15-minute
        // strategy's entries are meaningless against a 1-minute chart. Jobs
        // recorded before interval was stored have none — leave those alone.
        if (job.interval) setInterval_(job.interval);
        if (job.status === 'preparing' || job.status === 'running') {
          timer = setTimeout(load, 2000);
        }
      } catch {
        if (!cancelled) navigate('/backtests', { replace: true });
      }
    }
    load();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [backtestId, navigate]);

  // During replay the engine's trades are revealed as the clock passes their
  // entries, so you see the setup before you see what the engine did with it.
  const intervalSeconds = intervalToSeconds(interval);
  const visibleTrades = clockTime == null ? backtestTrades : backtestTrades.filter((t) => t.entryTime <= clockTime);
  const visibleCvd = clockTime == null ? cvdData : cvdData.filter((p) => p.time <= clockTime);
  const pendingJob = selectedJob && selectedJob.status !== 'done' ? selectedJob : null;

  // "Does the engine trade like me": my manual position shapes vs the visible
  // engine trades, matched by direction and entry within 3 bars.
  const myTrades = shapes.filter((s) => s.type === 'long' || s.type === 'short');
  const matched = visibleTrades.filter((t) =>
    myTrades.some((m) => {
      if (m.type !== t.direction) return false;
      const bar = bars[Math.round(m.entryLogical)];
      return bar && Math.abs(bar.time - t.entryTime) <= 3 * intervalSeconds;
    })
  ).length;

  return (
    <div className="page">
      {leadingSlot && createPortal((
        <div className="review-crumb">
          <Link className="icon-btn" to="/backtests" title="Back to backtests">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M15 18l-6-6 6-6" />
            </svg>
          </Link>
          <div className="hdr-symbol">
            {symbol && <span className="symbol-avatar">{symbol[0]}</span>}
            <div className="review-crumb-text">
              <span className="review-crumb-name">{selectedJob?.strategyName || 'Loading…'}</span>
              <span className="review-crumb-sub">{symbol}{selectedJob?.interval ? ` · ${selectedJob.interval}` : ''}</span>
            </div>
          </div>
        </div>
      ), leadingSlot)}
      {headerSlot && createPortal(chart.renderToolbar(
        <button className={`chat-toggle ${chatOpen ? 'active' : ''}`} title="Stratos" onClick={() => setChatOpen((o) => !o)}>
          <svg className="chat-toggle-spark" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
            <path d="M9 2.5l1.4 3.7 3.7 1.4-3.7 1.4L9 12.7 7.6 9 3.9 7.6l3.7-1.4z" />
            <path d="M17.5 12l.8 2.1 2.1.8-2.1.8-.8 2.1-.8-2.1-2.1-.8 2.1-.8z" />
          </svg>
          Ask Stratos
        </button>
      ), headerSlot)}
      {trailingSlot && createPortal(chart.settingsButton, trailingSlot)}
      {chart.settingsModal}
      <div className="page-body">
        {chart.drawToolbar}
        {chart.renderChart({
          trades: backtestTrades,
          revealTime: clockTime,
          // Floating status for the run under review, bottom-right of the
          // chart. There's no backtest picker — switching runs means going
          // back to the list, so the URL always names what's on screen.
          dock: (
            <>
              {pendingJob && (
                <span className={`compare-chip ${pendingJob.status === 'error' ? 'chip-error' : 'chip-live'}`}>
                  {pendingJob.status === 'error'
                    ? `Backtest failed${pendingJob.message ? ` · ${pendingJob.message}` : ''}`
                    : `Running the engine on ${pendingJob.strategyName}…`}
                </span>
              )}
              {backtestTrades.length > 0 && (
                <span className="compare-chip">
                  Engine {visibleTrades.length}{clockTime != null ? `/${backtestTrades.length}` : ''} · You {myTrades.length} · Matched {matched}
                </span>
              )}
              <Link className="btn btn-ghost" to="/backtests">All backtests</Link>
              <button className="icon-btn" title="Delete this run" onClick={handleDeleteBacktest}>
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7"><path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13" /></svg>
              </button>
            </>
          ),
        })}
        {chart.rightDock}
        {chatOpen && (
          <ChatPanel
            symbol={symbol}
            interval={interval}
            backtestId={backtestId}
            strategyName={selectedJob?.strategyName}
            backtestStatus={selectedJob?.status}
          />
        )}
      </div>
      <AnalysisPanel
        trades={visibleTrades}
        cvd={visibleCvd}
        open={analysisPanelOpen}
        onToggle={() => setAnalysisPanelOpen((o) => !o)}
        backtestId={backtestId}
        jobStatus={selectedJob?.status}
      />
    </div>
  );
}
