import { useEffect, useMemo, useRef, useState } from 'react';
import { fetchBacktestAnalytics, fetchBacktestValidation } from '../api';
import { useResizable } from '../hooks/useResizable';

const MIN_HEIGHT = 120;
const maxHeight = () => Math.max(MIN_HEIGHT, Math.round(window.innerHeight * 0.7));

function fmtTime(t) {
  if (t == null) return '—';
  return new Date(t * 1000).toLocaleTimeString('en-US', {
    hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit', timeZone: 'UTC',
  });
}

function fmtMoney(v) {
  return `${v >= 0 ? '+' : ''}${v.toFixed(2)}`;
}

const TABS = [
  { id: 'trades', label: 'Trades' },
  { id: 'performance', label: 'Performance' },
  { id: 'cvd', label: 'CVD' },
  { id: 'validation', label: 'Validation' },
  { id: 'montecarlo', label: 'Monte Carlo' },
  { id: 'regimes', label: 'Regimes' },
];

const fmtNum = (v, d = 2) => (v == null || Number.isNaN(v) ? '—' : Number(v).toFixed(d));
const fmtPct = (v, d = 1) => (v == null ? '—' : `${Number(v).toFixed(d)}%`);

// Metrics table for one validation window (IS / WF / OOS).
function MetricsRow({ label, m, hidden }) {
  if (hidden) {
    return (
      <tr><td>{label}</td><td colSpan={6} className="muted">hidden until finalize</td></tr>
    );
  }
  if (!m) return <tr><td>{label}</td><td colSpan={6} className="muted">not run</td></tr>;
  return (
    <tr>
      <td>{label}</td>
      <td>{m.trades}</td>
      <td className={m.netPnl >= 0 ? 'pos' : 'neg'}>{fmtMoney(m.netPnl || 0)}</td>
      <td>{fmtNum(m.profitFactor)}</td>
      <td>{fmtNum(m.expectancyR, 3)}</td>
      <td>{fmtPct(m.maxDrawdownPct)}</td>
      <td>{fmtNum(m.sharpe)}</td>
    </tr>
  );
}

function ValidationTab({ report, loading }) {
  if (loading) return <div className="analysis-empty">Loading validation…</div>;
  if (!report || !report.inSample) {
    return <div className="analysis-empty">No in-sample run yet — queue a validation (IS + walk-forward) for this strategy.</div>;
  }
  const v = report.verdict;
  return (
    <div className="validation-wrap">
      {v && (
        <div className={`verdict-banner ${v.status}`}>
          <b>{v.status === 'pass' ? 'PASS — candidate for forward test' : v.status === 'fail' ? 'FAIL' : 'UNTESTABLE'}</b>
          {v.failures?.length > 0 && <ul>{v.failures.map((f) => <li key={f}>{f}</li>)}</ul>}
        </div>
      )}
      <table className="trades-table">
        <thead><tr><th>Window</th><th>Trades</th><th>Net P&amp;L</th><th>PF</th><th>Exp. R</th><th>Max DD</th><th>Sharpe</th></tr></thead>
        <tbody>
          <MetricsRow label={`In-sample ${report.windows?.is ? `${report.windows.is.dateFrom} → ${report.windows.is.dateTo}` : ''}`} m={report.inSample} />
          {(report.walkForward || []).map((w) => (
            <MetricsRow key={w.window} label={`${w.window.toUpperCase()}`} m={w} />
          ))}
          <MetricsRow label="Out-of-sample" m={report.outOfSample} hidden={report.oosHidden} />
        </tbody>
      </table>
      {report.deflatedSharpe && (
        <div className="analysis-stats-row">
          <span>Deflated Sharpe <b>{fmtNum(report.deflatedSharpe.dsr, 3)}</b></span>
          <span>Annualised SR <b>{fmtNum(report.deflatedSharpe.sharpeAnnualized)}</b></span>
          <span>Trials <b>{report.deflatedSharpe.trials}</b></span>
          <span>Sessions <b>{report.deflatedSharpe.observations}</b></span>
        </div>
      )}
    </div>
  );
}

function MonteCarloTab({ report, loading }) {
  const mc = report?.monteCarlo;
  if (loading) return <div className="analysis-empty">Loading Monte Carlo…</div>;
  if (!mc) return <div className="analysis-empty">Monte Carlo needs a finished in-sample run.</div>;
  const b = mc.bootstrap; const k = mc.skip;
  const row = (label, o, money = true) => (
    <tr key={label}><td>{label}</td>
      {['p5', 'p50', 'p95'].map((q) => <td key={q}>{o ? (money ? fmtMoney(o[q]) : fmtPct(o[q])) : '—'}</td>)}
    </tr>
  );
  return (
    <div className="validation-wrap">
      <table className="trades-table">
        <thead><tr><th>Bootstrap ({b.runs} reshuffles of {b.trades} trades)</th><th>5th</th><th>50th</th><th>95th</th></tr></thead>
        <tbody>
          {row('Max drawdown ($)', b.maxDrawdown)}
          {b.maxDrawdownPct && row('Max drawdown (%)', b.maxDrawdownPct, false)}
          {row('Final equity ($)', b.finalEquity)}
        </tbody>
      </table>
      <table className="trades-table">
        <thead><tr><th>Skip test (drop {Math.round(k.dropFraction * 100)}% of trades, {k.runs} runs)</th><th>5th</th><th>50th</th><th>95th</th></tr></thead>
        <tbody>
          {row('Final equity ($)', k.finalEquity)}
          {row('Max drawdown ($)', k.maxDrawdown)}
        </tbody>
      </table>
      <div className="analysis-stats-row">
        <span>P(loss) bootstrap <b>{fmtPct(b.probLoss * 100)}</b></span>
        <span>P(loss) skip test <b>{fmtPct(k.probLoss * 100)}</b></span>
        <span>Baseline <b>{fmtMoney(k.baseline)}</b></span>
      </div>
    </div>
  );
}

function RegimesTab({ analytics, loading }) {
  if (loading) return <div className="analysis-empty">Loading regimes…</div>;
  const by = analytics?.byRegime || {};
  const tags = Object.keys(by);
  if (!tags.length) return <div className="analysis-empty">No regime tags on these trades yet.</div>;
  return (
    <div className="validation-wrap">
      <table className="trades-table">
        <thead><tr><th>Regime</th><th>Trades</th><th>Net P&amp;L</th><th>Win rate</th><th>PF</th><th>Exp. R</th></tr></thead>
        <tbody>
          {tags.map((t) => (
            <tr key={t}>
              <td>{t}</td><td>{by[t].trades}</td>
              <td className={by[t].netPnl >= 0 ? 'pos' : 'neg'}>{fmtMoney(by[t].netPnl)}</td>
              <td>{fmtPct(by[t].winRate)}</td><td>{fmtNum(by[t].profitFactor)}</td><td>{fmtNum(by[t].expectancyR, 3)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {analytics?.byHour?.length > 0 && (
        <table className="trades-table">
          <thead><tr><th>Entry hour (ET)</th><th>Trades</th><th>Net P&amp;L</th><th>Win rate</th><th>PF</th></tr></thead>
          <tbody>
            {analytics.byHour.map((h) => (
              <tr key={h.hourEt}><td>{h.hourEt}:00</td><td>{h.trades}</td>
                <td className={h.netPnl >= 0 ? 'pos' : 'neg'}>{fmtMoney(h.netPnl)}</td><td>{fmtPct(h.winRate)}</td><td>{fmtNum(h.profitFactor)}</td></tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

// Bottom dock on the chart page: switches between the trade log, an equity
// curve, and cumulative volume delta — the surfaces you'd use while
// analyzing a backtest or a replay in progress. Collapsible, like the
// strategy panel — collapsed leaves just the tab bar visible.
export default function AnalysisPanel({ trades, cvd, open, onToggle, backtestId, jobStatus }) {
  const [tab, setTab] = useState('trades');
  // Validation report + full analytics are fetched lazily, once per finished job.
  const [report, setReport] = useState(null);
  const [analytics, setAnalytics] = useState(null);
  const [loadingExtra, setLoadingExtra] = useState(false);
  useEffect(() => {
    setReport(null); setAnalytics(null);
  }, [backtestId]);
  useEffect(() => {
    const needs = ['validation', 'montecarlo', 'regimes'].includes(tab);
    if (!needs || !backtestId || jobStatus !== 'done' || report || loadingExtra) return;
    let cancelled = false;
    setLoadingExtra(true);
    Promise.all([fetchBacktestValidation(backtestId).catch(() => null), fetchBacktestAnalytics(backtestId).catch(() => null)])
      .then(([r, a]) => { if (!cancelled) { setReport(r || {}); setAnalytics(a); } })
      .finally(() => { if (!cancelled) setLoadingExtra(false); });
    return () => { cancelled = true; };
  }, [tab, backtestId, jobStatus, report, loadingExtra]);
  const panelRef = useRef(null);
  const { size: height, resizing, bind } = useResizable({
    key: 'analysisPanelHeight', defaultSize: 230, min: MIN_HEIGHT, max: maxHeight, cursor: 'row-resize',
  });
  const resizeHandlers = bind((e) => panelRef.current.getBoundingClientRect().bottom - e.clientY);

  const closedTrades = useMemo(
    () => trades.filter((t) => t.exitTime != null).slice().sort((a, b) => a.exitTime - b.exitTime),
    [trades],
  );

  const stats = useMemo(() => {
    if (!closedTrades.length) return null;
    const wins = closedTrades.filter((t) => t.pnl > 0);
    const grossWin = wins.reduce((s, t) => s + t.pnl, 0);
    const grossLoss = closedTrades.filter((t) => t.pnl < 0).reduce((s, t) => s + t.pnl, 0);
    return {
      trades: closedTrades.length,
      winRate: Math.round((wins.length / closedTrades.length) * 1000) / 10,
      totalPnl: Math.round(closedTrades.reduce((s, t) => s + t.pnl, 0) * 100) / 100,
      profitFactor: grossLoss !== 0 ? Math.abs(grossWin / grossLoss) : (grossWin > 0 ? Infinity : 0),
    };
  }, [closedTrades]);

  const equityPoints = useMemo(() => {
    let cum = 0;
    return closedTrades.map((t) => { cum += t.pnl; return cum; });
  }, [closedTrades]);

  return (
    <div
      ref={panelRef}
      className={`analysis-panel ${open ? '' : 'collapsed'}`}
      style={open ? { height } : undefined}
    >
      {open && (
        <div className={`panel-resize top ${resizing ? 'active' : ''}`} {...resizeHandlers} />
      )}
      <div className="analysis-tabs">
        {TABS.map((t) => (
          <button
            key={t.id}
            className={`analysis-tab ${tab === t.id ? 'active' : ''}`}
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </button>
        ))}
        <div className="toolbar-spacer" />
        <button
          className="analysis-panel-toggle"
          onClick={onToggle}
          title={open ? 'Collapse panel' : 'Expand panel'}
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            {open ? <path d="M6 9l6 6 6-6" /> : <path d="M18 15l-6-6-6 6" />}
          </svg>
        </button>
      </div>

      {open && (
        <>
          <div className="analysis-body">
            {tab === 'trades' && (
              closedTrades.length === 0 ? (
                <div className="analysis-empty">No trades yet — run a backtest and select it below the chart.</div>
              ) : (
                <table className="trades-table">
                  <thead>
                    <tr><th>Time</th><th>Side</th><th>Entry</th><th>Exit</th><th>P&amp;L</th><th>Reason</th></tr>
                  </thead>
                  <tbody>
                    {closedTrades.map((t) => (
                      <tr key={t.id}>
                        <td>{fmtTime(t.entryTime)}</td>
                        <td className={t.direction === 'long' ? 'pos' : 'neg'}>{t.direction === 'long' ? 'Long' : 'Short'}</td>
                        <td>{t.entryPrice}</td>
                        <td>{t.exitPrice}</td>
                        <td className={t.pnl >= 0 ? 'pos' : 'neg'}>{fmtMoney(t.pnl)}</td>
                        <td className="muted">{t.reason}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )
            )}

            {tab === 'performance' && (
              equityPoints.length === 0 ? (
                <div className="analysis-empty">No closed trades to chart yet.</div>
              ) : (
                <LineChart points={equityPoints} />
              )
            )}

            {tab === 'cvd' && (
              !cvd || cvd.length === 0 ? (
                <div className="analysis-empty">No CVD data for this range yet.</div>
              ) : (
                <LineChart points={cvd.map((p) => p.cvd)} />
              )
            )}

            {tab === 'validation' && <ValidationTab report={report} loading={loadingExtra} />}
            {tab === 'montecarlo' && <MonteCarloTab report={report} loading={loadingExtra} />}
            {tab === 'regimes' && <RegimesTab analytics={analytics} loading={loadingExtra} />}
          </div>

          {stats && ['trades', 'performance'].includes(tab) && (
            <div className="analysis-stats-row">
              <span>Net P&amp;L <b className={stats.totalPnl >= 0 ? 'pos' : 'neg'}>{fmtMoney(stats.totalPnl)}</b></span>
              <span>Win rate <b>{stats.winRate}%</b></span>
              <span>Profit factor <b>{stats.profitFactor === Infinity ? '∞' : stats.profitFactor.toFixed(2)}</b></span>
              <span>Trades <b>{stats.trades}</b></span>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function LineChart({ points }) {
  const w = 1000, h = 160, pad = 10;
  const min = Math.min(0, ...points);
  const max = Math.max(0, ...points);
  const range = max - min || 1;
  const stepX = points.length > 1 ? (w - pad * 2) / (points.length - 1) : 0;
  const x = (i) => pad + i * stepX;
  const y = (v) => h - pad - ((v - min) / range) * (h - pad * 2);
  const path = points.map((v, i) => `${i === 0 ? 'M' : 'L'} ${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join(' ');
  const last = points[points.length - 1];

  return (
    <div className="equity-curve-wrap">
      <svg className="equity-curve" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none">
        <line x1={pad} y1={y(0)} x2={w - pad} y2={y(0)} className="equity-zero" />
        <path d={path} className={`equity-line ${last >= 0 ? 'pos' : 'neg'}`} fill="none" />
      </svg>
    </div>
  );
}
