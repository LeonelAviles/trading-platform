import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { saveStrategy } from '../api';
import { templateSpec } from '../spec/template';

const SYMBOLS = ['ES1!', 'NQ1!'];

// "New strategy": a draft from the opening-range-breakout template, then the
// spec editor.
export default function NewStrategyModal({ open, onClose }) {
  const navigate = useNavigate();
  const [symbol, setSymbol] = useState('ES1!');
  const [name, setName] = useState('');
  const [direction, setDirection] = useState('both');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  if (!open) return null;

  function close() { setError(''); onClose(); }

  async function create() {
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
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>New strategy</h2>
          <button className="modal-close" onClick={close} aria-label="Close">×</button>
        </div>
        <div className="modal-body stack">
          <div className="form-col">
            <div className="form-grid">
              <label>Name<input type="text" autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="My breakout" onKeyDown={(e) => { if (e.key === 'Enter') create(); }} /></label>
              <label>Symbol<select value={symbol} onChange={(e) => setSymbol(e.target.value)}>{SYMBOLS.map((s) => <option key={s}>{s}</option>)}</select></label>
              <label>Direction<select value={direction} onChange={(e) => setDirection(e.target.value)}><option value="long">long</option><option value="short">short</option><option value="both">both</option></select></label>
            </div>
            <div className="inline-note">Creates a draft from the opening-range-breakout template and opens the spec editor, where the rules are edited directly with schema validation.</div>
            {error && <div className="review-error">{error}</div>}
          </div>
        </div>
        <div className="modal-foot">
          <button className="btn" onClick={close}>Cancel</button>
          <div className="toolbar-spacer" />
          <button className="btn btn-primary" disabled={busy} onClick={create}>{busy ? 'Creating…' : 'Create and open the editor'}</button>
        </div>
      </div>
    </div>
  );
}
