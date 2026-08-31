import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { saveStrategy } from '../api';
import { AgentPromptForm } from './AgentRuns';
import { templateSpec } from '../spec/template';

const SYMBOLS = ['ES1!', 'NQ1!'];

const OPTIONS = [
  { id: 'agent', step: 'Recommended', title: 'Describe it', text: 'Write the idea in plain English. The agent builds variants, backtests them and reports a verdict — asking you when something is ambiguous.',
    icon: <svg viewBox="0 0 24 24"><path d="M4 5h16v11H8l-4 4z" /><path d="M8 9h8M8 12h5" /></svg> },
  { id: 'teach', step: 'Show, don’t tell', title: 'Teach it on the chart', text: 'Replay a session tick by tick and trade it yourself. The agent watches, asks questions and compiles a strategy from your trades.',
    icon: <svg viewBox="0 0 24 24"><path d="M3 17l5-6 4 4 4-7 5 3" /></svg> },
  { id: 'manual', step: 'Full control', title: 'Write the spec', text: 'Start from a template and edit the rules directly in the spec editor with schema validation.',
    icon: <svg viewBox="0 0 24 24"><path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z" /><path d="M14 3v6h6M8 13h8M8 17h5" /></svg> },
];

// "New strategy": three clear ways in, one dialog.
export default function NewStrategyModal({ open, onClose, onStarted }) {
  const navigate = useNavigate();
  const [mode, setMode] = useState(null);
  const [symbol, setSymbol] = useState('ES1!');
  const [name, setName] = useState('');
  const [direction, setDirection] = useState('both');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  if (!open) return null;

  function close() { setMode(null); setError(''); onClose(); }

  async function createManual() {
    setBusy(true);
    setError('');
    try {
      const saved = await saveStrategy(templateSpec({ name: name.trim() || 'New strategy', symbol, direction }));
      close();
      navigate(`/strategies/${saved.id}?tab=spec&edit=1`);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-backdrop" onClick={close}>
      <div className="modal wide" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>{mode ? OPTIONS.find((o) => o.id === mode).title : 'New strategy'}</h2>
          <button className="modal-close" onClick={close} aria-label="Close">×</button>
        </div>
        <div className="modal-body stack">
          {!mode && (
            <>
              <div className="inline-note">How do you want to start?</div>
              <div className="option-grid">
                {OPTIONS.map((o) => (
                  <button key={o.id} className="option-card" onClick={() => setMode(o.id)}>
                    <span className="option-icon">{o.icon}</span>
                    <span className="option-step">{o.step}</span>
                    <b>{o.title}</b>
                    <span>{o.text}</span>
                  </button>
                ))}
              </div>
            </>
          )}
          {mode === 'agent' && (
            <AgentPromptForm autoFocus onStarted={(run) => { close(); onStarted?.(run); }} />
          )}
          {mode === 'teach' && (
            <div className="form-col">
              <div className="inline-note">You will land on the chart with Teaching switched on. Pick a session date and press Replay, then trade with the buttons or hotkeys (B / S / F). End the session to compile the strategy.</div>
              <div className="form-grid">
                <label>Symbol<select value={symbol} onChange={(e) => setSymbol(e.target.value)}>{SYMBOLS.map((s) => <option key={s}>{s}</option>)}</select></label>
              </div>
            </div>
          )}
          {mode === 'manual' && (
            <div className="form-col">
              <div className="form-grid">
                <label>Name<input type="text" autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="My breakout" /></label>
                <label>Symbol<select value={symbol} onChange={(e) => setSymbol(e.target.value)}>{SYMBOLS.map((s) => <option key={s}>{s}</option>)}</select></label>
                <label>Direction<select value={direction} onChange={(e) => setDirection(e.target.value)}><option value="long">long</option><option value="short">short</option><option value="both">both</option></select></label>
              </div>
              <div className="inline-note">Creates a draft from the opening-range-breakout template and opens the spec editor.</div>
              {error && <div className="review-error">{error}</div>}
            </div>
          )}
        </div>
        <div className="modal-foot">
          {mode && <button className="btn" onClick={() => setMode(null)}>← Back</button>}
          <div className="toolbar-spacer" />
          {mode === 'teach' && <button className="btn btn-primary" onClick={() => { close(); navigate(`/chart/${encodeURIComponent(symbol)}?teaching=1`); }}>Open the chart with Teaching on →</button>}
          {mode === 'manual' && <button className="btn btn-primary" disabled={busy} onClick={createManual}>{busy ? 'Creating…' : 'Create and open the editor'}</button>}
          {!mode && <button className="btn" onClick={close}>Cancel</button>}
        </div>
      </div>
    </div>
  );
}
