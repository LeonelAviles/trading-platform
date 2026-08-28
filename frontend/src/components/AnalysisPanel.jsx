import { useMemo, useRef, useState } from 'react';
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
];

// Bottom dock on the chart page: switches between the trade log, an equity
// curve, and cumulative volume delta — the surfaces you'd use while
// analyzing a backtest or a replay in progress. Collapsible, like the
// strategy panel — collapsed leaves just the tab bar visible.
export default function AnalysisPanel({ trades, cvd, open, onToggle }) {
  const [tab, setTab] = useState('trades');
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
          </div>

          {stats && tab !== 'cvd' && (
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
