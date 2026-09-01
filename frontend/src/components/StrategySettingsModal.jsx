import { useEffect, useState } from 'react';

const FIELDS = [
  ['accountSize', 'Account size ($)', 1000],
  ['riskPerTradePct', 'Risk per trade (%)', 0.05],
  ['maxContracts', 'Max contracts', 1],
  ['dailyLossLimitPct', 'Daily loss limit (%)', 0.1],
  ['weeklyLossLimitPct', 'Weekly loss limit (%)', 0.1],
  ['maxTradesPerDay', 'Max trades per day', 1],
  ['stopAfterConsecutiveLosses', 'Stop after consecutive losses', 1],
];
const CRITERIA = [
  ['minTradesInSample', 'Min trades in-sample', 1],
  ['minTradesOutOfSample', 'Min trades out-of-sample', 1],
  ['minProfitFactor', 'Min profit factor', 0.05],
  ['minExpectancyR', 'Min expectancy (R)', 0.01],
  ['maxDrawdownPct', 'Max drawdown (%)', 0.5],
  ['minWalkForwardWindowsPositive', 'Min positive walk-forward windows', 1],
  ['minOosProfitFactor', 'Min OOS profit factor', 0.05],
  ['maxMonteCarloDrawdown95Pct', 'Max Monte Carlo DD p95 (%)', 0.5],
  ['minDeflatedSharpeProb', 'Min deflated Sharpe prob (blank = report only)', 0.01],
];

// Strategy Settings modal — the risk profile (PLATFORM-SPEC.md §4.6).
export default function StrategySettingsModal({ open, risk, onApply, onClose }) {
  const [draft, setDraft] = useState(risk || {});
  useEffect(() => {
    if (!open) return;
    setDraft(JSON.parse(JSON.stringify(risk || {})));
  }, [open, risk]);
  if (!open) return null;

  const num = (v) => (v === '' || v == null ? null : Number(v));
  const setField = (k, v) => setDraft((d) => ({ ...d, [k]: num(v) }));
  const setCrit = (k, v) => setDraft((d) => ({ ...d, passCriteria: { ...(d.passCriteria || {}), [k]: num(v) } }));

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>Strategy settings — risk profile</h2>
          <button className="modal-close" onClick={onClose}>&times;</button>
        </div>
        <div className="modal-body">
          <div className="modal-content">
            <div className="settings-section-label">PROPOSED BY {String(draft.proposedBy || 'default').toUpperCase()}</div>
            {draft.rationale && <p className="muted">{draft.rationale}</p>}
            <table className="settings-table">
              <thead><tr><th>Field</th><th>Current</th></tr></thead>
              <tbody>
                {FIELDS.map(([k, label, step]) => (
                  <tr key={k}>
                    <td>{label}</td>
                    <td><input type="number" step={step} value={draft[k] ?? ''} onChange={(e) => setField(k, e.target.value)} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="settings-section-label">PASS CRITERIA</div>
            <table className="settings-table">
              <thead><tr><th>Criterion</th><th>Current</th></tr></thead>
              <tbody>
                {CRITERIA.map(([k, label, step]) => (
                  <tr key={k}>
                    <td>{label}</td>
                    <td><input type="number" step={step} value={draft.passCriteria?.[k] ?? ''} onChange={(e) => setCrit(k, e.target.value)} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
        <div className="modal-footer">
          <button className="btn" onClick={onClose}>Cancel</button>
          <button className="btn btn-primary" onClick={() => onApply({ ...draft, proposedBy: 'user' })}>Apply</button>
        </div>
      </div>
    </div>
  );
}
