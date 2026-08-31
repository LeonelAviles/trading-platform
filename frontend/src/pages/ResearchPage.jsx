import { useCallback, useContext, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import {
  addResearchSource, addResearchTopic, fetchPrimitiveRequests, fetchResearchQueue, fetchResearchSettings, fetchResearchSources,
  fetchResearchStatus, fetchUsage, putResearchSettings, putSettings, runResearch, searchKnowledge, setPrimitiveRequestStatus,
  tickAutorun, uploadResearchSource,
} from '../api';
import { HeaderSlotContext } from '../headerSlot';

const money = (v) => `$${Number(v || 0).toFixed(3)}`;
const when = (iso) => {
  if (!iso) return '—';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
};
const lines = (arr) => (arr || []).join('\n');

// "Hand it a book": a URL, pasted text or a PDF/text file goes through the
// same fetch → score → summarise path as the worker's own finds, tagged owner.
function AddSource({ onDone }) {
  const [url, setUrl] = useState('');
  const [text, setText] = useState('');
  const [title, setTitle] = useState('');
  const [topic, setTopic] = useState('');
  const [msg, setMsg] = useState('');
  const fileRef = useRef(null);

  async function submit() {
    setMsg('Sending…');
    try {
      const body = url.trim() ? { url: url.trim(), topic } : { text, title, topic };
      const job = await addResearchSource(body);
      setMsg(`Reading ${job.title || job.url} — check the jobs list below.`);
      setUrl(''); setText(''); setTitle('');
      onDone();
    } catch (e) {
      setMsg(e.message);
    }
  }

  async function onFile(ev) {
    const file = ev.target.files?.[0];
    ev.target.value = '';
    if (!file) return;
    setMsg(`Uploading ${file.name}…`);
    try {
      const job = await uploadResearchSource(file, { title: title || file.name, topic });
      setMsg(`Reading ${job.title || file.name} — check the jobs list below.`);
      onDone();
    } catch (e) {
      setMsg(e.message);
    }
  }

  return (
    <div className="research-add">
      <div className="chat-input-row">
        <input className="agent-answer-input" value={url} placeholder="https://… a paper, an article, a PDF link" onChange={(e) => setUrl(e.target.value)} />
        <input className="agent-answer-input research-topic" value={topic} placeholder="topic (optional)" onChange={(e) => setTopic(e.target.value)} />
      </div>
      <div className="chat-input-row">
        <input className="agent-answer-input" value={title} placeholder="title (for pasted text / uploads)" onChange={(e) => setTitle(e.target.value)} />
      </div>
      <textarea className="research-paste" rows={4} value={text} placeholder="…or paste text here (notes, an excerpt, a transcript)" onChange={(e) => setText(e.target.value)} />
      <div className="strategy-actions">
        <button className="btn btn-primary btn-sm" disabled={!url.trim() && text.trim().length < 200} onClick={submit}>Read this</button>
        <button className="btn btn-sm" onClick={() => fileRef.current?.click()}>Upload PDF / text…</button>
        <input ref={fileRef} type="file" accept=".pdf,.txt,.md,application/pdf,text/plain" hidden onChange={onFile} />
        {msg && <span className="muted">{msg}</span>}
      </div>
    </div>
  );
}

// The self-study switch: read the queue on a schedule, within the daily cap.
function SelfStudy({ settings, autorun, onSave, onRefresh }) {
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
        <span>Last read {when(autorun?.lastRunAt)}{autorun?.lastRunBy ? ` (${autorun.lastRunBy})` : ''}</span>
        <span>Next {autorun?.enabled ? when(autorun?.nextRunAt) : 'off'}</span>
        <span>{autorun?.queued ?? '—'} topic(s) queued</span>
        {autorun?.researchCapped && <span className="neg">daily research budget spent</span>}
        {autorun?.skipped && <span className="muted">skipped {when(autorun.skipped.at)}: {autorun.skipped.reason}</span>}
        <button className="btn btn-sm" onClick={() => tickAutorun().then((t) => { setMsg(t.ran ? 'Started.' : `Not started: ${t.reason}`); onRefresh(); })}>Read now</button>
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

// Which authors to trust: domain suffixes fixed to a tier before the model's
// own reading of the page counts.
function TrustedDomains({ settings, onSave }) {
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

// /research — queue, sources with tier/credibility, primitive requests,
// budget gauge and the editable LLM price table (PLATFORM-SPEC.md §4.8, §4.9).
export default function ResearchPage() {
  const { leading: leadingSlot } = useContext(HeaderSlotContext);
  const [queue, setQueue] = useState([]);
  const [sources, setSources] = useState([]);
  const [requests, setRequests] = useState([]);
  const [status, setStatus] = useState(null);
  const [usage, setUsage] = useState(null);
  const [topic, setTopic] = useState('');
  const [q, setQ] = useState('');
  const [hits, setHits] = useState([]);
  const [prices, setPrices] = useState(null);
  const [msg, setMsg] = useState('');
  const [jobs, setJobs] = useState([]);
  const [rsettings, setRsettings] = useState(null);

  const refresh = useCallback(async () => {
    const [qu, so, re, st, us, rs] = await Promise.all([
      fetchResearchQueue().catch(() => []), fetchResearchSources().catch(() => ({ sources: [], jobs: [] })), fetchPrimitiveRequests().catch(() => []),
      fetchResearchStatus().catch(() => null), fetchUsage().catch(() => null), fetchResearchSettings().catch(() => null),
    ]);
    setQueue(qu); setSources(so.sources || []); setJobs(so.jobs || []); setRequests(re); setStatus(st); setUsage(us);
    if (rs) setRsettings(rs);
    if (us?.prices && !prices) setPrices(us.prices);
  }, [prices]);

  async function saveResearch(changes) {
    setRsettings(await putResearchSettings(changes));
    refresh();
  }
  useEffect(() => { refresh(); const id = setInterval(refresh, 8000); return () => clearInterval(id); }, [refresh]);

  async function savePrices() {
    await putSettings({ 'llm.prices': prices });
    setMsg('Price table saved (estimates).');
    refresh();
  }

  const frac = usage?.monthFraction ?? 0;
  return (
    <div className="page strategy-page">
      {leadingSlot && createPortal(<div className="hdr-title">Research &amp; knowledge</div>, leadingSlot)}
      <div className="review-body strategy-body">
        <section className="review-card">
          <div className="review-card-name">Budget</div>
          {usage && (
            <>
              <div className="budget-gauge"><div className="budget-fill" style={{ width: `${Math.min(100, frac * 100)}%`, background: usage.capped ? '#ef5350' : '#26a69a' }} /></div>
              <div className="review-card-spec">
                <span>Month {money(usage.monthSpendUsd)} / ${usage.monthlyBudgetUsd} ({(frac * 100).toFixed(1)}%){usage.capped ? ' — CAPPED' : ''}</span>
                <span>Research today {money(usage.researchDaySpendUsd)} / ${usage.dailyResearchBudgetUsd}{usage.researchCapped ? ' — capped' : ''}</span>
                <span>Reasoning {usage.models?.reasoning} · fast {usage.models?.fast}</span>
                <span className="muted">Costs are estimates from the price table below.</span>
              </div>
              <table className="settings-table"><thead><tr><th>Purpose</th><th>Calls</th><th>In</th><th>Out</th><th>Cache read</th><th>Cost</th></tr></thead>
                <tbody>{Object.entries(usage.byPurpose || {}).map(([k, v]) => (
                  <tr key={k}><td>{k}</td><td>{v.calls}</td><td>{v.tokensIn}</td><td>{v.tokensOut}</td><td>{v.cacheRead}</td><td>{money(v.costUsd)}</td></tr>))}</tbody></table>
            </>
          )}
          {prices && (
            <>
              <div className="settings-section-label">PRICE TABLE ($ per million tokens — fill in from Anthropic&apos;s pricing page)</div>
              <table className="settings-table"><thead><tr><th>Model</th><th>In</th><th>Out</th><th>Cache read</th><th>Cache write</th></tr></thead>
                <tbody>{Object.entries(prices).map(([m, p]) => (
                  <tr key={m}><td>{m}{p.placeholder ? ' (placeholder)' : ''}</td>
                    {['in', 'out', 'cacheRead', 'cacheWrite'].map((k) => (
                      <td key={k}><input type="number" step="0.01" value={p[k] ?? ''} onChange={(e) => setPrices({ ...prices, [m]: { ...p, [k]: Number(e.target.value), placeholder: false } })} /></td>))}
                  </tr>))}</tbody></table>
              <button className="btn btn-sm" onClick={savePrices}>Save price table</button> {msg && <span className="muted">{msg}</span>}
            </>
          )}
        </section>

        <section className="review-card">
          <header className="review-card-head">
            <div><div className="review-card-name">Self-study</div>
              <div className="review-card-sub">Reads the queue on its own — seed topics, what the agent asks for during runs, and yours — within the daily research budget.</div></div>
          </header>
          <SelfStudy settings={rsettings} autorun={status?.autorun} onSave={saveResearch} onRefresh={refresh} />
        </section>

        <section className="review-card">
          <header className="review-card-head">
            <div><div className="review-card-name">Hand it a source</div>
              <div className="review-card-sub">A link, pasted text or a PDF. Scored and summarised like everything else; its facts are tagged <code>owner</code>.</div></div>
          </header>
          <AddSource onDone={refresh} />
          {jobs.length > 0 && (
            <table className="trades-table"><thead><tr><th>Source</th><th>Topic</th><th>Status</th><th>Result</th></tr></thead>
              <tbody>{jobs.map((j) => (
                <tr key={j.id}><td>{j.title || j.url}</td><td>{j.topic}</td><td>{j.status}</td>
                  <td className="muted">{j.error || (j.result ? (j.result.blocked ? 'blocked (tier 4)' : j.result.skipped || `tier ${j.result.tier} · ${j.result.facts} fact(s)`) : '')}</td></tr>))}</tbody></table>
          )}
        </section>

        <section className="review-card">
          <header className="review-card-head">
            <div><div className="review-card-name">Trusted domains</div>
              <div className="review-card-sub">Fixes a source&apos;s tier by where it comes from, before the model&apos;s reading of the page counts.</div></div>
          </header>
          <TrustedDomains settings={rsettings} onSave={saveResearch} />
        </section>

        <section className="review-card">
          <header className="review-card-head">
            <div><div className="review-card-name">Research queue</div>
              <div className="review-card-sub">Backend {status?.knowledge?.backend} · embedder {status?.knowledge?.embedder} · {status?.knowledge?.facts} facts · worker {status?.workerRunning ? 'running' : 'idle'}</div></div>
            <div className="strategy-actions">
              <button className="btn" onClick={() => runResearch(1).then(refresh)}>Research next topic</button>
              <button className="btn" onClick={() => runResearch(3).then(refresh)}>Next 3</button>
            </div>
          </header>
          <div className="chat-input-row">
            <input className="agent-answer-input" value={topic} placeholder="Add a topic…" onChange={(e) => setTopic(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter' && topic.trim()) { addResearchTopic(topic.trim()).then(() => { setTopic(''); refresh(); }); } }} />
            <button className="btn btn-sm" disabled={!topic.trim()} onClick={() => addResearchTopic(topic.trim()).then(() => { setTopic(''); refresh(); })}>Add</button>
          </div>
          <table className="trades-table"><thead><tr><th>Topic</th><th>Status</th><th>By</th></tr></thead>
            <tbody>{queue.map((t) => <tr key={t.id}><td>{t.topic}</td><td>{t.status}</td><td>{t.requestedBy}</td></tr>)}</tbody></table>
        </section>

        <section className="review-card">
          <div className="review-card-name">Sources</div>
          <table className="trades-table"><thead><tr><th>Title</th><th>Domain</th><th>By</th><th>Tier</th><th>Credibility</th><th>Why</th></tr></thead>
            <tbody>{sources.map((s) => (
              <tr key={s.id}><td>{s.url.startsWith('owner://') ? (s.title || 'pasted text') : <a href={s.url} target="_blank" rel="noreferrer">{s.title || s.url}</a>}</td>
                <td>{s.domain}</td><td>{s.scored?.providedBy === 'user' ? 'you' : 'worker'}</td>
                <td>{s.tier ?? '—'}{s.scored?.domainRule ? <span className="muted"> (rule)</span> : ''}</td>
                <td>{s.credibility != null ? s.credibility.toFixed(2) : '—'}</td><td className="muted">{s.scored?.reason}</td></tr>))}</tbody></table>
          {sources.length === 0 && <div className="review-card-empty">No sources yet — run the research worker.</div>}
        </section>

        <section className="review-card">
          <div className="review-card-name">Knowledge search</div>
          <div className="chat-input-row">
            <input className="agent-answer-input" value={q} placeholder="e.g. daily loss limit" onChange={(e) => setQ(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') searchKnowledge(q).then(setHits); }} />
            <button className="btn btn-sm" onClick={() => searchKnowledge(q).then(setHits)}>Search</button>
          </div>
          <ul className="strategy-sentences">{hits.map((h) => <li key={h.id}><b>[{h.credibility}]</b> {h.text} <span className="muted">— {h.source} ({h.kind})</span></li>)}</ul>
        </section>

        <section className="review-card">
          <div className="review-card-name">Primitive requests</div>
          {requests.length === 0 ? <div className="review-card-empty">None — the agent composes from the registry.</div> : (
            <table className="trades-table"><thead><tr><th>Name</th><th>Description</th><th>Status</th><th /></tr></thead>
              <tbody>{requests.map((r) => (
                <tr key={r.id}><td>{r.name}</td><td>{r.description}<pre className="muted">{r.pseudocode}</pre></td><td>{r.status}</td>
                  <td>{['implemented', 'rejected'].map((s) => <button key={s} className="btn btn-sm" onClick={() => setPrimitiveRequestStatus(r.id, s).then(refresh)}>{s}</button>)}</td></tr>))}</tbody></table>
          )}
        </section>
      </div>
    </div>
  );
}
