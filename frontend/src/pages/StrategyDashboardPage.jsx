import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import {
  fetchStrategies, fetchBacktests, fetchBacktest, fetchBacktestAnalytics, fetchBacktestInsights,
  runBacktest, fetchEngineStatus,
} from '../api';

const TABS = ['Overview', 'Performance', 'Trades', 'Conditions', 'Charts', 'Settings', 'Logs'];

function fmtMoney(v) {
  if (v == null) return '—';
  const sign = v > 0 ? '+' : v < 0 ? '-' : '';
  return `${sign}$${Math.abs(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}
function fmtR(v) {
  if (v == null) return '—';
  return `${v > 0 ? '+' : ''}${v.toFixed(2)}R`;
}
function fmtPct(v) {
  return v == null ? '—' : `${v.toFixed(1)}%`;
}
function fmtTime(t) {
  return new Date(t * 1000).toLocaleString('en-US', {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', timeZone: 'UTC', hour12: false,
  });
}
const MONTH_NAMES = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

// Renders "**bold**" spans from the assistant's plain-text reply without a
// markdown lib or dangerouslySetInnerHTML — just a split on the delimiter.
function renderInlineMarkdown(text) {
  const parts = text.split(/\*\*(.+?)\*\*/g);
  return parts.map((part, i) => (i % 2 === 1 ? <b key={i}>{part}</b> : part));
}

export default function StrategyDashboardPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [strategy, setStrategy] = useState(null);
  const [job, setJob] = useState(null);
  const [analytics, setAnalytics] = useState(null);
  const [tab, setTab] = useState('Overview');
  const [error, setError] = useState('');
  const [insights, setInsights] = useState(null);
  const [insightsLoading, setInsightsLoading] = useState(false);
  const [engineStatus, setEngineStatus] = useState(null);
  const [running, setRunning] = useState(null); // in-flight job while polling, or null
  const pollRef = useRef(null);

  const load = useCallback(async () => {
    setError('');
    try {
      const [strategies, backtests] = await Promise.all([fetchStrategies(), fetchBacktests()]);
      const s = strategies.find((x) => x.id === id);
      setStrategy(s || null);
      const jobs = backtests
        .filter((b) => b.strategyId === id && b.status === 'done')
        .sort((a, b) => new Date(b.createdAt) - new Date(a.createdAt));
      const latest = jobs[0];
      setJob(latest || null);
      if (latest) {
        setAnalytics(await fetchBacktestAnalytics(latest.id));
      } else {
        setAnalytics(null);
      }
    } catch (e) {
      setError(e.message);
    }
  }, [id]);

  useEffect(() => {
    load();
    fetchEngineStatus().then(setEngineStatus).catch(() => setEngineStatus(null));
    return () => clearInterval(pollRef.current);
  }, [load]);

  function pollRunningJob(jobId) {
    clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      try {
        const j = await fetchBacktest(jobId);
        setRunning(j);
        if (j.status === 'done' || j.status === 'error') {
          clearInterval(pollRef.current);
          if (j.status === 'done') {
            setInsights(null);
            load();
          }
        }
      } catch {
        clearInterval(pollRef.current);
      }
    }, 2000);
  }

  async function handleRunBacktest() {
    if (!strategy) return;
    setError('');
    try {
      const j = await runBacktest(strategy.id);
      setRunning(j);
      if (j.status === 'done' || j.status === 'error') {
        if (j.status === 'done') { setInsights(null); load(); }
      } else {
        pollRunningJob(j.id);
      }
    } catch (e) {
      setError(e.message);
    }
  }

  async function handleGenerateInsights() {
    if (!job) return;
    setInsightsLoading(true);
    try {
      const reply = await fetchBacktestInsights(job.id);
      setInsights(reply);
    } catch (e) {
      setInsights({ content: e.message, error: true });
    } finally {
      setInsightsLoading(false);
    }
  }

  const tiles = useMemo(() => {
    if (!analytics) return [];
    return [
      { label: 'Net P&L', value: fmtMoney(analytics.netPnl), tone: analytics.netPnl >= 0 ? 'pos' : 'neg' },
      { label: 'Total Trades', value: analytics.trades },
      { label: 'Win Rate', value: fmtPct(analytics.winRate) },
      { label: 'Profit Factor', value: analytics.profitFactor == null ? '∞' : analytics.profitFactor.toFixed(2) },
      { label: 'Expectancy', value: fmtR(analytics.expectancyR), tone: analytics.expectancyR >= 0 ? 'pos' : 'neg' },
      { label: 'Max Drawdown', value: fmtMoney(analytics.maxDrawdown), tone: 'neg' },
      { label: 'Sharpe Ratio', value: analytics.sharpe.toFixed(2) },
      { label: 'SQN', value: analytics.sqn.toFixed(2) },
    ];
  }, [analytics]);

  if (!strategy && !error) {
    return <div className="page strategy-dash"><div className="dash-loading">Loading…</div></div>;
  }
  if (error || !strategy) {
    return (
      <div className="page strategy-dash">
        <div className="dash-loading">{error || 'Strategy not found.'} <button className="btn" onClick={() => navigate('/')}>Back to strategies</button></div>
      </div>
    );
  }

  return (
    <div className="page strategy-dash">
      <div className="dash-scroll">
        <div className="dash-breadcrumb">
          <span className="crumb-link" onClick={() => navigate('/')}>Strategies</span>
          <span className="crumb-sep">/</span>
          <span>{strategy.name}</span>
        </div>

        <div className="dash-head">
          <div>
            <div className="dash-title-row">
              <h1 className="page-title">{strategy.name}</h1>
              <span className="badge badge-active">Active</span>
            </div>
            <p className="page-sub">
              {strategy.direction === 'long' ? 'Long' : 'Short'} · {strategy.symbol} · {strategy.conditions.length} entry condition{strategy.conditions.length === 1 ? '' : 's'}
            </p>
            <div className="dash-tags">
              <span className="tag">{strategy.interval}</span>
              <span className="tag">{strategy.symbol}</span>
              {strategy.conditions.slice(0, 3).map((c, i) => <span className="tag" key={i}>{c.type.replace(/_/g, ' ')}</span>)}
            </div>
          </div>
          <div className="page-head-actions">
            <button className="btn" onClick={() => navigate('/', { state: {} })}>Edit Strategy</button>
            <button className="btn" onClick={() => navigate('/chart', { state: { strategyId: strategy.id, symbol: strategy.symbol } })}>Open in Chart</button>
            <button
              className="btn btn-primary" onClick={handleRunBacktest}
              disabled={(running && running.status !== 'done' && running.status !== 'error') || (engineStatus && !engineStatus.installed)}
              title={engineStatus && !engineStatus.installed ? 'Install nautilus_trader first' : 'Run through the NautilusTrader engine'}
            >
              {running && running.status !== 'done' && running.status !== 'error' ? 'Running…' : 'Run Backtest'}
            </button>
          </div>
        </div>

        {running && running.status !== 'done' && (
          <div className={`job-status job-${running.status}`}>
            <b>{running.strategyName}</b> — {running.status}
            {running.message ? ` · ${running.message}` : ''}
          </div>
        )}
        {engineStatus && !engineStatus.installed && (
          <div className="lean-warning">
            NautilusTrader not installed — real backtests unavailable.
            <br />Run <code>pip install nautilus_trader</code> (see SETUP-ENGINE.md).
          </div>
        )}

        <div className="dash-tabs">
          {TABS.map((t) => (
            <button key={t} className={`dash-tab ${tab === t ? 'active' : ''}`} onClick={() => setTab(t)}>{t}</button>
          ))}
        </div>

        {tab !== 'Overview' && (
          <div className="dash-placeholder">This view isn't built yet — see the Overview tab for available analytics.</div>
        )}

        {tab === 'Overview' && (
          !job || !analytics || analytics.trades === 0 ? (
            <div className="dash-placeholder">
              No completed backtest for this strategy yet.
              <button className="btn btn-primary" style={{ marginLeft: 10 }} onClick={() => navigate('/chart', { state: { strategyId: strategy.id, symbol: strategy.symbol } })}>Run a backtest</button>
            </div>
          ) : (
            <>
              <div className="stat-tiles">
                {tiles.map((t) => (
                  <div className="stat-tile" key={t.label}>
                    <div className="stat-tile-label">{t.label}</div>
                    <div className={`stat-tile-value ${t.tone || ''}`}>{t.value}</div>
                  </div>
                ))}
              </div>

              <div className="dash-grid-2">
                <section className="card">
                  <div className="card-head"><h2>Equity Curve (R)</h2><span className="card-hint">Cumulative R per closed trade</span></div>
                  <EquityCurveChart points={analytics.equityCurve} />
                </section>

                <section className="card">
                  <div className="card-head"><h2>Trade Distribution</h2><span className="card-hint">R-multiple buckets</span></div>
                  <DistributionChart buckets={analytics.distribution} />
                  <div className="dist-stats">
                    <span>Avg win <b className="pos">{fmtMoney(analytics.avgWin)}</b></span>
                    <span>Avg loss <b className="neg">{fmtMoney(analytics.avgLoss)}</b></span>
                    <span>Largest win <b className="pos">{fmtMoney(analytics.largestWin)}</b></span>
                    <span>Largest loss <b className="neg">{fmtMoney(analytics.largestLoss)}</b></span>
                    <span>Expectancy <b className={analytics.expectancyR >= 0 ? 'pos' : 'neg'}>{fmtR(analytics.expectancyR)}</b></span>
                  </div>
                </section>
              </div>

              <div className="dash-grid-2">
                <section className="card">
                  <div className="card-head"><h2>Exit Reason Breakdown</h2><span className="card-hint">Real exit-reason mix from the backtest</span></div>
                  <ExitReasonTable rows={analytics.exitReasons} total={analytics.trades} />
                </section>

                <section className="card">
                  <div className="card-head"><h2>Performance Over Time</h2><span className="card-hint">Monthly win rate</span></div>
                  <MonthlyTable rows={analytics.monthly} />
                </section>
              </div>

              <section className="card">
                <div className="card-head"><h2>Recent Trades</h2></div>
                <RecentTradesTable trades={analytics.recentTrades} />
              </section>

              <section className="card ai-insights-card">
                <div className="card-head">
                  <h2>AI Insights</h2>
                  <span className="toolbar-spacer" />
                  <button className="btn btn-primary" onClick={handleGenerateInsights} disabled={insightsLoading}>
                    {insightsLoading ? 'Analyzing…' : 'New Analysis'}
                  </button>
                </div>
                {!insights && !insightsLoading && (
                  <div className="dash-placeholder-inline">Ask the assistant to read this backtest's trades and summarize the edge.</div>
                )}
                {insightsLoading && <div className="dash-placeholder-inline">Reading trades and generating insights…</div>}
                {insights && !insightsLoading && (
                  <div className={`ai-insights-body ${insights.error ? 'error' : ''}`}>
                    {insights.content.split('\n').filter(Boolean).map((line, i) => (
                      <p key={i}>{renderInlineMarkdown(line.replace(/^[-*]\s+/, ''))}</p>
                    ))}
                  </div>
                )}
              </section>
            </>
          )
        )}
      </div>
    </div>
  );
}

function EquityCurveChart({ points }) {
  const w = 1000, h = 220, pad = 14;
  if (!points.length) return <div className="dash-placeholder-inline">No closed trades yet.</div>;
  const values = points.map((p) => p.cumR);
  const min = Math.min(0, ...values);
  const max = Math.max(0, ...values);
  const range = max - min || 1;
  const stepX = points.length > 1 ? (w - pad * 2) / (points.length - 1) : 0;
  const x = (i) => pad + i * stepX;
  const y = (v) => h - pad - ((v - min) / range) * (h - pad * 2);
  const zeroY = y(0);
  const linePath = values.map((v, i) => `${i === 0 ? 'M' : 'L'} ${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join(' ');
  const areaPath = `${linePath} L ${x(values.length - 1).toFixed(1)} ${zeroY.toFixed(1)} L ${x(0).toFixed(1)} ${zeroY.toFixed(1)} Z`;
  const last = values[values.length - 1];
  const tone = last >= 0 ? 'pos' : 'neg';

  return (
    <div className="equity-curve-wrap">
      <svg className="equity-curve-r" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none">
        <defs>
          <linearGradient id="eqFillPos" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--green-bright)" stopOpacity="0.22" />
            <stop offset="100%" stopColor="var(--green-bright)" stopOpacity="0" />
          </linearGradient>
          <linearGradient id="eqFillNeg" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--red)" stopOpacity="0" />
            <stop offset="100%" stopColor="var(--red)" stopOpacity="0.22" />
          </linearGradient>
        </defs>
        <line x1={pad} y1={zeroY} x2={w - pad} y2={zeroY} className="equity-zero" />
        <path d={areaPath} fill={tone === 'pos' ? 'url(#eqFillPos)' : 'url(#eqFillNeg)'} stroke="none" />
        <path d={linePath} className={`equity-line ${tone}`} fill="none" />
      </svg>
      <div className="equity-curve-foot">
        <span>{points.length} trades</span>
        <span className={tone}>{fmtR(last)} total</span>
      </div>
    </div>
  );
}

function DistributionChart({ buckets }) {
  if (!buckets.length) return <div className="dash-placeholder-inline">No R-multiple data yet.</div>;
  const max = Math.max(...buckets.map((b) => b.count), 1);
  return (
    <div className="dist-chart">
      {buckets.map((b) => (
        <div className="dist-bar-col" key={b.bucket} title={`${b.bucket >= 0 ? '+' : ''}${b.bucket}R to ${b.bucket >= 0 ? '+' : ''}${(b.bucket + 0.5).toFixed(1)}R: ${b.count} trade${b.count === 1 ? '' : 's'}`}>
          <div className="dist-bar-track">
            <div
              className={`dist-bar ${b.bucket >= 0 ? 'pos' : 'neg'}`}
              style={{ height: `${Math.max(4, (b.count / max) * 100)}%` }}
            />
          </div>
          <div className="dist-bar-label">{b.bucket > 0 ? '+' : ''}{b.bucket}</div>
        </div>
      ))}
    </div>
  );
}

function ExitReasonTable({ rows, total }) {
  if (!rows.length) return <div className="dash-placeholder-inline">No trades yet.</div>;
  return (
    <div className="context-rows">
      {rows.map((r) => (
        <div className="context-row" key={r.reason}>
          <span className="context-row-label">{r.reason.replace(/_/g, ' ')}</span>
          <div className="context-row-bar-track">
            <div className="context-row-bar" style={{ width: `${(r.count / total) * 100}%` }} />
          </div>
          <span className="context-row-count">{r.count}</span>
          <span className="context-row-pct muted">{((r.count / total) * 100).toFixed(0)}%</span>
        </div>
      ))}
    </div>
  );
}

function MonthlyTable({ rows }) {
  if (!rows.length) return <div className="dash-placeholder-inline">No monthly data yet.</div>;
  const years = [...new Set(rows.map((r) => r.year))];
  const byKey = new Map(rows.map((r) => [`${r.year}-${r.month}`, r]));
  return (
    <div className="monthly-table-wrap">
      <table className="monthly-table">
        <thead>
          <tr>
            <th>Year</th>
            {MONTH_NAMES.map((m) => <th key={m}>{m}</th>)}
          </tr>
        </thead>
        <tbody>
          {years.map((y) => (
            <tr key={y}>
              <td className="monthly-year">{y}</td>
              {MONTH_NAMES.map((_, i) => {
                const cell = byKey.get(`${y}-${i + 1}`);
                if (!cell) return <td key={i} className="monthly-cell empty">—</td>;
                const wr = cell.winRate;
                const intensity = Math.min(1, Math.abs(wr - 50) / 50);
                const tone = wr >= 50 ? 'pos' : 'neg';
                return (
                  <td key={i} className={`monthly-cell ${tone}`} style={{ opacity: 0.35 + intensity * 0.65 }} title={`${cell.trades} trades · ${wr}% win`}>
                    {wr.toFixed(0)}%
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function RecentTradesTable({ trades }) {
  if (!trades.length) return <div className="dash-placeholder-inline">No trades yet.</div>;
  return (
    <table className="trades-table">
      <thead>
        <tr><th>Time</th><th>Side</th><th>Entry</th><th>Exit</th><th>R</th><th>P&amp;L</th><th>Reason</th></tr>
      </thead>
      <tbody>
        {trades.map((t) => (
          <tr key={t.id}>
            <td>{fmtTime(t.exitTime)}</td>
            <td className={t.direction === 'long' ? 'pos' : 'neg'}>{t.direction === 'long' ? 'Long' : 'Short'}</td>
            <td>{t.entryPrice}</td>
            <td>{t.exitPrice}</td>
            <td className={t.r >= 0 ? 'pos' : 'neg'}>{fmtR(t.r)}</td>
            <td className={t.pnl >= 0 ? 'pos' : 'neg'}>{fmtMoney(t.pnl)}</td>
            <td className="muted">{t.reason}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
