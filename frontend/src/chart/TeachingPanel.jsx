import { useEffect, useState } from 'react';

// Teaching-mode UI pieces on the chart page (PLATFORM-SPEC.md Phase 6.1):
// the defaults popover, the question dock and the post-fill note prompt.

import { saveTeachingDefaults } from './teachingDefaults';

export function TeachingDefaults({ root, value, onChange, onClose }) {
  const set = (patch) => {
    const next = { ...value, ...patch };
    saveTeachingDefaults(root, next);
    onChange(next);
  };
  return (
    <div className="teaching-popover" onClick={(e) => e.stopPropagation()}>
      <div className="teaching-popover-title">Teaching defaults · {root}</div>
      <label>Stop (ticks) <input type="number" min={1} value={value.stopTicks} onChange={(e) => set({ stopTicks: Number(e.target.value) })} /></label>
      <label>Target (ticks) <input type="number" min={1} value={value.targetTicks} onChange={(e) => set({ targetTicks: Number(e.target.value) })} /></label>
      <label>Contracts <input type="number" min={1} value={value.contracts} onChange={(e) => set({ contracts: Number(e.target.value) })} /></label>
      <label className="row"><input type="checkbox" checked={value.pauseOnQuestion} onChange={(e) => set({ pauseOnQuestion: e.target.checked })} /> Questions pause the replay</label>
      <label className="row"><input type="checkbox" checked={value.askNotes} onChange={(e) => set({ askNotes: e.target.checked })} /> Ask confidence / note after fills</label>
      <div className="teaching-popover-keys">B buy · S sell · F flatten · K mark skipped setup · N note · Space play/pause</div>
      <button className="btn btn-sm" onClick={onClose}>Done</button>
    </div>
  );
}

export function QuestionDock({ question, onAnswer, onDismiss }) {
  const [text, setText] = useState('');
  const [label, setLabel] = useState(null);
  useEffect(() => { setText(''); setLabel(null); }, [question?.id]);
  if (!question) return null;
  const skipped = question.kind === 'skipped_setup';
  return (
    <div className="question-dock">
      <div className="question-kind">{{ first: 'First trade', confirm: 'Confirm', contradiction: 'Contradiction', skipped_setup: 'Skipped setup' }[question.kind] || question.kind}</div>
      <div className="question-text">{question.text}</div>
      {skipped && (
        <div className="question-labels">
          {[['valid_skip', 'Deliberate skip'], ['missed', 'I missed it'], ['rule_too_loose', 'Not a setup']].map(([v, l]) => (
            <button key={v} className={`btn btn-sm ${label === v ? 'btn-primary' : ''}`} onClick={() => setLabel(v)}>{l}</button>
          ))}
        </div>
      )}
      <form onSubmit={(e) => { e.preventDefault(); onAnswer(question.id, text, label); }}>
        <input type="text" value={text} placeholder="Your answer…" onChange={(e) => setText(e.target.value)} autoFocus />
        <button className="btn btn-sm btn-primary" type="submit" disabled={!text.trim() && !label}>Answer &amp; resume</button>
        <button className="btn btn-sm" type="button" onClick={onDismiss}>Later</button>
      </form>
    </div>
  );
}

export function FillPrompt({ fill, onSubmit, onDismiss }) {
  const [confidence, setConfidence] = useState(null);
  const [note, setNote] = useState('');
  useEffect(() => { setConfidence(null); setNote(''); }, [fill?.id]);
  if (!fill) return null;
  return (
    <div className="fill-prompt">
      <div className="fill-prompt-title">Filled {fill.direction} {fill.contracts} @ {fill.entryPrice} — confidence?</div>
      <div className="fill-prompt-conf">
        {[1, 2, 3, 4, 5].map((n) => <button key={n} className={`btn btn-sm ${confidence === n ? 'btn-primary' : ''}`} onClick={() => setConfidence(n)}>{n}</button>)}
      </div>
      <form onSubmit={(e) => { e.preventDefault(); onSubmit(fill.id, confidence, note); }}>
        <input type="text" value={note} placeholder="Note (optional)" onChange={(e) => setNote(e.target.value)} />
        <button className="btn btn-sm btn-primary" type="submit">Save</button>
        <button className="btn btn-sm" type="button" onClick={onDismiss}>Skip</button>
      </form>
    </div>
  );
}
