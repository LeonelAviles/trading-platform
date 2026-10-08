import { useContext, useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { fetchBacktest, fetchCVD } from '../api';
import { HeaderSlotContext } from '../headerSlot';
import { useResearchSelection } from '../researchSelection';
import AnalysisPanel from '../components/AnalysisPanel';
import ChartAgentPanel from '../components/ChartAgentPanel';
import { useOrderFlowChart } from '../chart/useOrderFlowChart';

function shortDuration(seconds) {
  if (seconds == null) return 'estimating…';
  if (seconds < 60) return `${Math.max(1, Math.round(seconds))}s left`;
  return `about ${Math.round(seconds / 60)}m left`;
}

// A chart is never standalone: this page *is* the review of one backtest,
// named by the route. The strategy and the symbol both come from that job, so
// there is nothing to pick here and no way to end up staring at bars that
// aren't attached to a strategy. The chart itself — tick replay, order-flow
// layers, docks — is `useOrderFlowChart`; this file adds the backtest on top: its trades drawn on the bars (and
// revealed as the replay clock passes them) and the analysis dock.
export default function CandlestickPage() {
  const { leading: leadingSlot, main: headerSlot, trailing: trailingSlot } = useContext(HeaderSlotContext);
  const { backtestId } = useParams();
  const { select } = useResearchSelection();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  let storedThread = null;
  try { storedThread = localStorage.getItem('stratos.agent.thread'); } catch { /* private mode */ }
  const threadId = searchParams.get('thread') || storedThread;
  const [chatOpen, setChatOpen] = useState(() => searchParams.get('chat') === '1');

  // The job under review, loaded from the route param. Everything else on the
  // page hangs off it — including the symbol, which is why the header has no
  // symbol picker to wander away with.
  const [selectedJob, setSelectedJob] = useState(null);
  const symbol = selectedJob?.symbol || '';
  const [interval, setInterval_] = useState('1min');
  const [backtestTrades, setBacktestTrades] = useState([]);
  const [cvdData, setCvdData] = useState([]);
  const [followRun, setFollowRun] = useState(true);
  const runActive = ['queued', 'running'].includes(selectedJob?.status);
  const progress = selectedJob?.progress;
  const engineTime = runActive && followRun ? progress?.currentTime : null;

  // The bottom analysis dock persists its open/closed state.
  const [analysisPanelOpen, setAnalysisPanelOpen] = useState(
    () => localStorage.getItem('analysisPanelOpen') !== 'false',
  );
  useEffect(() => { localStorage.setItem('analysisPanelOpen', String(analysisPanelOpen)); }, [analysisPanelOpen]);

  const chart = useOrderFlowChart({ symbol, interval, setInterval: setInterval_, displayUntil: engineTime });
  const { clockTime } = chart;

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
        select({ strategyId: job.strategyId, runId: job.id, name: job.strategyName });
        setBacktestTrades(job.trades || []);
        // Draw the trades on the bars that produced them: a 15-minute
        // strategy's entries are meaningless against a 1-minute chart. Jobs
        // recorded before interval was stored have none — leave those alone.
        if (job.interval) setInterval_(job.interval);
        if (['queued', 'preparing', 'running'].includes(job.status)) {
          timer = setTimeout(load, 2000);
        }
      } catch {
        if (!cancelled) navigate('/backtests', { replace: true });
      }
    }
    load();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [backtestId, navigate, select]);

  const runClockTime = clockTime ?? engineTime;

  // Follow mode advances the viewport one completed engine session at a time.
  // It uses the worker's actual progress rather than starting a second replay
  // process, so the trades shown are exactly those produced by this run.
  useEffect(() => {
    if (!runActive || !followRun || !progress?.currentTime || !chart.api || !chart.bars.length) return;
    chart.api.chart.timeScale().setVisibleRange({
      from: progress.currentTime - (2 * 3600),
      to: progress.currentTime + (10 * 60),
    });
  }, [runActive, followRun, progress?.currentTime, chart.api, chart.bars.length]);

  // During replay or live follow, reveal engine trades only as the clock passes
  // their entries so the chart behaves like a running review.
  const visibleTrades = runClockTime == null ? backtestTrades : backtestTrades.filter((t) => t.entryTime <= runClockTime);
  const visibleCvd = runClockTime == null ? cvdData : cvdData.filter((p) => p.time <= runClockTime);
  const pendingJob = selectedJob && selectedJob.status !== 'done' ? selectedJob : null;

  return (
    <div className="page review-split">
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
              <span className="review-crumb-name" title={selectedJob?.strategyName}>{selectedJob?.strategyName || 'Loading…'}</span>
              <span className="review-crumb-sub">{symbol}{selectedJob?.interval ? ` · ${selectedJob.interval}` : ''}</span>
            </div>
          </div>
        </div>
      ), leadingSlot)}
      {headerSlot && createPortal(chart.renderToolbar(), headerSlot)}
      {trailingSlot && createPortal((
        <div className="chart-header-actions">
          {threadId && <button className={`btn btn-ghost chart-agent-toggle ${chatOpen ? 'active' : ''}`} onClick={() => setChatOpen((value) => !value)}>
            <span className="agent-mini-mark">S</span> Stratos
          </button>}
          {chart.settingsButton}
        </div>
      ), trailingSlot)}
      {chart.settingsModal}
      <div className="review-split-main">
        <div className="page-body">
          {chart.drawToolbar}
          {chart.renderChart({
            trades: backtestTrades,
            revealTime: runClockTime,
            // Floating status for the run under review, bottom-right of the
            // chart. There's no backtest picker — switching runs means going
            // back to the list, so the URL always names what's on screen.
            dock: (
              <>
                {runActive && (
                  <div className="backtest-live-progress">
                    <div className="backtest-live-top">
                      <span className="live-dot" />
                      <strong>{pendingJob.status === 'queued' ? 'Waiting for engine' : `Session ${progress?.sessionsCompleted ?? 0} of ${progress?.sessionsTotal ?? '—'}`}</strong>
                      <span>{Math.round(progress?.percent ?? 0)}%</span>
                    </div>
                    <div className="backtest-live-track"><i style={{ width: `${progress?.percent ?? 0}%` }} /></div>
                    <div className="backtest-live-meta">
                      <span>{progress?.currentDate || 'Preparing data'}</span>
                      <span className="backtest-live-trades">{progress?.tradeCount ?? 0} trade{progress?.tradeCount === 1 ? '' : 's'}</span>
                      <span>{shortDuration(progress?.etaSeconds)}</span>
                      <button type="button" className={followRun ? 'active' : ''} onClick={() => setFollowRun((v) => !v)}>
                        {followRun ? 'Following chart' : 'Follow chart'}
                      </button>
                    </div>
                  </div>
                )}
                {pendingJob && !runActive && (
                  <span className={`compare-chip backtest-status-chip ${pendingJob.status === 'error' ? 'chip-error' : 'chip-live'}`}
                    title={pendingJob.status === 'error' ? pendingJob.message : pendingJob.strategyName}>
                    {pendingJob.status === 'error'
                      ? `Backtest failed${pendingJob.message ? ` · ${pendingJob.message}` : ''}`
                      : `Running the engine on ${pendingJob.strategyName}…`}
                  </span>
                )}
              </>
            ),
          })}
          {chart.rightDock}
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
      <ChartAgentPanel threadId={threadId} backtestId={backtestId} open={chatOpen} onClose={() => setChatOpen(false)} />
    </div>
  );
}
