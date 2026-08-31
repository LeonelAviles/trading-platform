import { useEffect, useState } from 'react';
import { tickAutorun } from '../api';
import { fmtWhen } from '../format';
const lines = (arr) => (arr || []).join('\n');

// The self-study switch: read the queue on a schedule, within the daily cap.
export function SelfStudy({ settings, autorun, onSave, onRefresh }) {
  const [draft, setDraft] = useState(settings);
  const [msg, setMsg] = useState('');
  useEffect(() => { setDraft(settings); }, [settings]);
  if (!draft) return null;
  const r = autorun?.lastResult;
  return (
    <div className="research-selfstudy">
      <label className="research-switch">
        <input type="checkbox" checked={!!draft.autoRun} onChange={(e) => onSave({ autoRun: e.target.checked })} />
        <b>Self-study {draft.autoRun ? 'on' : 'off'}</b>
        <span className="muted">— every</span>
        <input type="number" min="1" step="1" value={draft.intervalHours} onChange={(e) => setDraft({ ...draft, intervalHours: Number(e.target.value) })} onBlur={() => onSave({ intervalHours: draft.intervalHours })} />
        <span className="muted">hours, read</span>
        <input type="number" min="1" max="10" step="1" value={draft.topicsPerRun} onChange={(e) => setDraft({ ...draft, topicsPerRun: Number(e.target.value) })} onBlur={() => onSave({ topicsPerRun: draft.topicsPerRun })} />
        <span className="muted">topic(s), stop at the daily research budget.</span>
      </label>
      <div className="review-card-spec">
        <span>Last read {fmtWhen(autorun?.lastRunAt)}{autorun?.lastRunBy ? ` (${autorun.lastRunBy})` : ''}</span>
        <span>Next {autorun?.enabled ? fmtWhen(autorun?.nextRunAt) : 'off'}</span>
        <span>{autorun?.queued ?? '—'} topic(s) queued</span>
        {autorun?.researchCapped && <span className="neg">daily research budget spent</span>}
        {autorun?.skipped && <span className="muted">skipped {fmtWhen(autorun.skipped.at)}: {autorun.skipped.reason}</span>}
        <button className="btn btn-sm" onClick={() => tickAutorun().then((t) => { setMsg(t.ran ? 'Started.' : `Not started: ${t.reason}`); onRefresh?.(); })}>Read now</button>
        {msg && <span className="muted">{msg}</span>}
      </div>
      {r && (
        <div className="muted research-last">
          Last result: {r.topics?.length || 0} topic(s) — {r.sources} source(s), {r.facts} fact(s){r.errors?.length ? ` · ${r.errors.length} error(s): ${r.errors[0]}` : ''}
        </div>
      )}
    </div>
  );
}

// Which sources to trust: domain suffixes pinned to a tier before the model's
// own reading of the page counts.
export function TrustedDomains({ settings, onSave }) {
  const [t1, setT1] = useState('');
  const [t2, setT2] = useState('');
  const [blocked, setBlocked] = useState('');
  const [msg, setMsg] = useState('');
  useEffect(() => {
    if (!settings) return;
    setT1(lines(settings.trustedDomains?.tier1));
    setT2(lines(settings.trustedDomains?.tier2));
    setBlocked(lines(settings.trustedDomains?.blocked));
  }, [settings]);
  if (!settings) return null;
  return (
    <div className="research-domains">
      <div className="research-domains-grid">
        <label>Tier 1 — papers, exchanges, regulators<textarea rows={6} value={t1} onChange={(e) => setT1(e.target.value)} /></label>
        <label>Tier 2 — established practitioners<textarea rows={6} value={t2} onChange={(e) => setT2(e.target.value)} /></label>
        <label>Blocked — never enters the knowledge base<textarea rows={6} value={blocked} onChange={(e) => setBlocked(e.target.value)} /></label>
      </div>
      <div className="strategy-actions">
        <button className="btn btn-sm" onClick={() => onSave({ trustedDomains: { tier1: t1, tier2: t2, blocked } }).then(() => setMsg('Saved.'))}>Save trusted domains</button>
        <span className="muted">One domain per line; subdomains match. {msg}</span>
      </div>
    </div>
  );
}
