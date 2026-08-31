import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { answerAgentRun, cancelAgentRun, fetchAgentRuns, startAgentRun, subscribeAgentRun } from '../api';

const STATUS_LABEL = {
  queued: 'Queued', running: 'Running', paused_for_user: 'Waiting for you', done: 'Done', error: 'Error',
  cancelled: 'Cancelled', budget_exhausted: 'Budget exhausted',
};

function fmtWhen(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

// Live view of one run: event feed over the WebSocket, question form when paused.
export function AgentRunCard({ run, onChanged }) {
  const [events, setEvents] = useState([]);
  const [status, setStatus] = useState(run.status);
  const [question, setQuestion] = useState(run.question);
  const [answer, setAnswer] = useState('');
  const [open, setOpen] = useState(run.status === 'running' || run.status === 'paused_for_user');
  const feedRef = useRef(null);

  useEffect(() => {
    setStatus(run.status);
    setQuestion(run.question);
  }, [run.status, run.question]);

  useEffect(() => {
    if (!open || ['done', 'error', 'cancelled', 'budget_exhausted'].includes(status) && events.length) return undefined;
    const close = subscribeAgentRun(run.id, (ev) => {
      if (ev.type === 'status') {
        setStatus(ev.status);
        if (ev.status !== 'paused_for_user') setQuestion(null);
        return;
      }
      if (ev.type === 'question') setQuestion(ev.question);
      setEvents((list) => (list.some((e) => e.seq === ev.seq) ? list : [...list, ev]));
    });
    return close;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run.id, open]);

  useEffect(() => {
    if (feedRef.current) feedRef.current.scrollTop = feedRef.current.scrollHeight;
  }, [events]);

  async function submitAnswer(text) {
    const t = (text ?? answer).trim();
    if (!t) return;
    setAnswer('');
    await answerAgentRun(run.id, t);
    setQuestion(null);
    setStatus('queued');
    onChanged?.();
  }

  const p = run.progress || {};
  return (
    <li className={`agent-run status-${status}`}>
      <div className="agent-run-head" onClick={() => setOpen((o) => !o)}>
        <span className={`review-chip agent-status ${status}`}>{STATUS_LABEL[status] || status}</span>
        <span className="agent-run-prompt" title={run.input?.prompt}>{run.input?.prompt || run.kind}</span>
        <span className="muted">{run.kind} · {fmtWhen(run.createdAt)} · ${(run.costUsd || 0).toFixed(3)}</span>
        {p.phase && <span className="review-chip">{p.phase}</span>}
        {p.changesUsed != null && <span className="review-chip">{p.changesUsed}/{p.changeBudget} changes</span>}
        {p.championId && <Link className="review-chip" to={`/strategies/${p.championId}`} onClick={(e) => e.stopPropagation()}>champion</Link>}
        {['queued', 'running', 'paused_for_user'].includes(status) && (
          <button className="btn btn-sm" onClick={(e) => { e.stopPropagation(); cancelAgentRun(run.id).then(onChanged); }}>Cancel</button>
        )}
      </div>
      {open && (
        <div className="agent-run-body">
          {question && (
            <div className="agent-question">
              <div className="agent-question-text">{question.text}</div>
              {question.options?.length > 0 && (
                <div className="agent-question-options">
                  {question.options.map((o) => <button key={o} className="btn btn-sm" onClick={() => submitAnswer(o)}>{o}</button>)}
                </div>
              )}
              <div className="chat-input-row">
                <input className="agent-answer-input" value={answer} placeholder="Your answer…" onChange={(e) => setAnswer(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter') submitAnswer(); }} />
                <button className="btn btn-primary btn-sm" onClick={() => submitAnswer()}>Answer</button>
              </div>
            </div>
          )}
          <div className="agent-feed" ref={feedRef}>
            {events.length === 0 && <div className="muted">No events yet.</div>}
            {events.map((ev) => (
              <div key={ev.seq} className={`agent-ev agent-ev-${ev.type}`}>
                {ev.type === 'tool' && <span><b>{ev.name}</b> {ev.input}</span>}
                {ev.type === 'tool_result' && <span className="muted">↳ {ev.result}</span>}
                {ev.type === 'text' && <span>{ev.text}</span>}
                {ev.type === 'question' && <span><b>Question:</b> {ev.question?.text}</span>}
                {ev.type === 'answer' && <span><b>You:</b> {ev.text}</span>}
                {['started', 'done', 'error', 'cancelled', 'budget_exhausted'].includes(ev.type) && <span><b>{ev.type}</b> {ev.message || ''}</span>}
              </div>
            ))}
          </div>
          {run.report?.text && status === 'done' && <pre className="agent-report">{run.report.text}</pre>}
        </div>
      )}
    </li>
  );
}

// Section: start a run from a prompt (only the prompt is required) and list runs.
export default function AgentRuns() {
  const [runs, setRuns] = useState([]);
  const [prompt, setPrompt] = useState('');
  const [symbol, setSymbol] = useState('ES1!');
  const [direction, setDirection] = useState('');
  const [name, setName] = useState('');
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState('');

  const refresh = useCallback(() => fetchAgentRuns().then(setRuns).catch(() => {}), []);
  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 5000);
    return () => clearInterval(id);
  }, [refresh]);

  async function start() {
    if (!prompt.trim()) return;
    setStarting(true);
    setError('');
    try {
      await startAgentRun({ kind: 'generate', prompt: prompt.trim(), symbol: symbol || undefined, direction: direction || undefined, name: name || undefined });
      setPrompt('');
      refresh();
    } catch (e) {
      setError(e.message);
    } finally {
      setStarting(false);
    }
  }

  return (
    <section className="review-card agent-runs">
      <header className="review-card-head">
        <div>
          <div className="review-card-name">Agent runs</div>
          <div className="review-card-sub">Prompt → variants → in-sample & walk-forward → ≤5 single-variable experiments → one out-of-sample look → verdict</div>
        </div>
      </header>
      <div className="agent-start">
        <textarea className="spec-editor" rows={3} placeholder="Describe the strategy idea. Ambiguities (breakout vs retest, direction, stop/target) become variants."
          value={prompt} onChange={(e) => setPrompt(e.target.value)} />
        <div className="strategy-actions">
          <input className="agent-answer-input" style={{ width: 90 }} value={symbol} onChange={(e) => setSymbol(e.target.value)} placeholder="ES1!" title="symbol" />
          <select value={direction} onChange={(e) => setDirection(e.target.value)} title="direction (blank = agent decides)">
            <option value="">direction: agent decides</option><option value="long">long</option><option value="short">short</option><option value="both">both</option>
          </select>
          <input className="agent-answer-input" style={{ width: 180 }} value={name} onChange={(e) => setName(e.target.value)} placeholder="name (optional)" />
          <button className="btn btn-primary" disabled={starting || !prompt.trim()} onClick={start}>{starting ? 'Starting…' : 'Start run'}</button>
        </div>
        {error && <div className="review-error">{error}</div>}
      </div>
      {runs.length === 0 ? <div className="review-card-empty">No runs yet.</div> : (
        <ul className="agent-run-list">
          {runs.map((r) => <AgentRunCard key={r.id} run={r} onChanged={refresh} />)}
        </ul>
      )}
    </section>
  );
}
