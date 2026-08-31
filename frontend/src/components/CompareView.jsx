import { useEffect, useState } from 'react';
import { compareStrategies } from '../api';

const LABELS = {
  trades: 'Trades', winRate: 'Win rate %', profitFactor: 'Profit factor', expectancyR: 'Expectancy (R)',
  maxDrawdown: 'Max drawdown', sharpe: 'Sharpe', sqn: 'SQN', netPnl: 'Net PnL',
};

function fmt(v) {
  if (v == null) return '—';
  if (typeof v === 'number') return Number.isInteger(v) ? v.toLocaleString() : v.toFixed(2);
  return String(v);
}

// "Compare two nodes" (Phase 7): the agent's compare_backtests output for the
// latest finished in-sample run of each strategy, side by side.
export default function CompareView({ a, b, window = 'is', onClose }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  useEffect(() => {
    let live = true;
    setData(null);
    setError('');
    compareStrategies(a, b, window).then((d) => live && setData(d)).catch((e) => live && setError(e.message));
    return () => { live = false; };
  }, [a, b, window]);

  const cmp = data?.comparison;
  const side = cmp?.metrics || {};
  return (
    <div className="compare-view">
      <div className="compare-head">
        <div className="review-card-name">Compare ({window.toUpperCase()})</div>
        {onClose && <button className="btn btn-sm" onClick={onClose}>Close</button>}
      </div>
      {error && <div className="review-error">{error}</div>}
      {!data && !error && <div className="muted">Comparing…</div>}
      {cmp && (
        <>
          <table className="compare-table">
            <thead>
              <tr>
                <th />
                <th className={cmp.winner === 'a' ? 'winner' : ''}>{data.a.name || data.a.strategyId}</th>
                <th className={cmp.winner === 'b' ? 'winner' : ''}>{data.b.name || data.b.strategyId}</th>
              </tr>
            </thead>
            <tbody>
              {Object.keys(side).map((m) => (
                <tr key={m}>
                  <td className="muted">{LABELS[m] || m}</td>
                  <td>{fmt(side[m].a)}</td>
                  <td>{fmt(side[m].b)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="compare-verdict">{cmp.verdict}</div>
          {cmp.winRateZScore != null && <div className="muted">Win-rate z = {fmt(cmp.winRateZScore)}{cmp.winRateSignificant ? ' (significant at 95%)' : ''}</div>}
          {(cmp.warnings || []).map((w) => <div key={w} className="compare-warning">⚠ {w}</div>)}
        </>
      )}
    </div>
  );
}
