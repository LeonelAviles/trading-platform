import { useCallback, useContext, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Link } from 'react-router-dom';
import {
  addResearchSource, addResearchTopic, fetchPrimitiveRequests, fetchResearchQueue, fetchResearchSources,
  fetchResearchStatus, fetchUsage, runResearch, searchKnowledge, setPrimitiveRequestStatus, uploadResearchSource,
} from '../api';
import { HeaderSlotContext } from '../headerSlot';
import { Card, PageHeader, StatusChip, Tabs } from '../components/ui';
import { fmtWhen } from '../format';

const money = (v) => `$${Number(v || 0).toFixed(3)}`;

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

// /research — what the agent reads: the queue, the sources, the knowledge.
export default function ResearchPage() {
  const { leading: leadingSlot } = useContext(HeaderSlotContext);
  const [tab, setTab] = useState('queue');
  const [queue, setQueue] = useState([]);
  const [sources, setSources] = useState([]);
  const [jobs, setJobs] = useState([]);
  const [requests, setRequests] = useState([]);
  const [status, setStatus] = useState(null);
  const [usage, setUsage] = useState(null);
  const [topic, setTopic] = useState('');
  const [q, setQ] = useState('');
  const [hits, setHits] = useState([]);
  const [msg, setMsg] = useState('');

  const refresh = useCallback(async () => {
    const [qu, so, re, st, us] = await Promise.all([
      fetchResearchQueue().catch(() => []), fetchResearchSources().catch(() => ({ sources: [], jobs: [] })), fetchPrimitiveRequests().catch(() => []),
      fetchResearchStatus().catch(() => null), fetchUsage().catch(() => null),
    ]);
    setQueue(qu); setSources(so.sources || []); setJobs(so.jobs || []); setRequests(re); setStatus(st); setUsage(us);
  }, []);
  useEffect(() => { refresh(); const id = setInterval(refresh, 8000); return () => clearInterval(id); }, [refresh]);
  useEffect(() => { if (!msg) return undefined; const t = setTimeout(() => setMsg(''), 4000); return () => clearTimeout(t); }, [msg]);

  async function addTopic() {
    if (!topic.trim()) return;
    await addResearchTopic(topic.trim());
    setTopic('');
    setMsg('Topic queued.');
    refresh();
  }
  async function readNext(n) {
    const r = await runResearch(n);
    setMsg(r.started ? `Reading the next ${n} topic(s)…` : `Not started: ${r.reason}`);
    refresh();
  }

  const auto = status?.autorun;
  const queued = queue.filter((t) => t.status === 'queued').length;
  const done = queue.filter((t) => t.status === 'done').length;

  return (
    <div className="page">
      {leadingSlot && createPortal(<div className="hdr-title">Research</div>, leadingSlot)}
      <div className="page-scroll"><div className="page-inner">
        <PageHeader
          title="Research"
          subtitle="The agent reads topics from a queue, scores each source by tier and credibility, and keeps what it learned as facts the strategy runs can cite. Hand it your own sources here."
          actions={(
            <>
              <Link className="btn" to="/knowledge">Knowledge graph</Link>
              <button className="btn" onClick={() => readNext(3)} disabled={status?.workerRunning}>Read next 3</button>
              <button className="btn btn-primary" onClick={() => readNext(1)} disabled={status?.workerRunning}>{status?.workerRunning ? 'Reading…' : 'Read next topic'}</button>
            </>
          )}
        >
          {usage && (
            <div className="budget-strip" style={{ marginTop: 8 }}>
              <span>{status?.knowledge?.facts ?? '—'} facts · backend {status?.knowledge?.backend}</span>
              <span>Research today {money(usage.researchDaySpendUsd)} / ${usage.dailyResearchBudgetUsd}<span className="bar"><i className={usage.researchCapped ? 'capped' : ''} style={{ width: `${Math.min(100, usage.dailyResearchBudgetUsd ? (usage.researchDaySpendUsd / usage.dailyResearchBudgetUsd) * 100 : 0)}%` }} /></span></span>
              <span>Self-study {auto?.enabled ? `on · next ${fmtWhen(auto.nextRunAt)}` : 'off'} · <Link to="/settings">settings</Link></span>
            </div>
          )}
        </PageHeader>
        {msg && <div className="toast">{msg}</div>}
        <Tabs value={tab} onChange={setTab} tabs={[
          { id: 'queue', label: 'Queue', count: queued }, { id: 'sources', label: 'Sources', count: sources.length },
          { id: 'knowledge', label: 'Knowledge' }, { id: 'requests', label: 'Primitive requests', count: requests.filter((r) => r.status === 'open').length || null },
        ]} />

        {tab === 'queue' && (
          <Card title="Topics" sub={`${done} read · ${queued} queued · seed topics first, then yours, then what the agent asked for during runs`}>
            <div className="toolbar-row">
              <input type="text" value={topic} placeholder="Add a topic, e.g. walk-forward validation for intraday futures" onChange={(e) => setTopic(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') addTopic(); }} style={{ flex: 1 }} />
              <button className="btn btn-primary" disabled={!topic.trim()} onClick={addTopic}>Add topic</button>
            </div>
            <table className="data-table"><thead><tr><th>Topic</th><th>Status</th><th>Requested by</th><th>Added</th></tr></thead>
              <tbody>{queue.map((t) => <tr key={t.id}><td>{t.topic}</td><td><StatusChip status={t.status} /></td><td className="inline-note">{t.requestedBy}</td><td className="inline-note">{fmtWhen(t.createdAt)}</td></tr>)}</tbody></table>
          </Card>
        )}

        {tab === 'sources' && (
          <>
            <Card title="Hand it a source" sub="A link, pasted text or a PDF. Scored and summarised like everything else; its facts are tagged owner.">
              <AddSource onDone={refresh} />
              {jobs.length > 0 && (
                <table className="data-table"><thead><tr><th>Source</th><th>Topic</th><th>Status</th><th>Result</th></tr></thead>
                  <tbody>{jobs.map((j) => (
                    <tr key={j.id}><td>{j.title || j.url}</td><td className="inline-note">{j.topic}</td><td><StatusChip status={j.status} /></td>
                      <td className="inline-note">{j.error || (j.result ? (j.result.blocked ? `blocked — ${j.result.reason || `tier ${j.result.tier}`}` : j.result.skipped || `tier ${j.result.tier} · ${j.result.facts} fact(s)`) : '')}</td></tr>))}</tbody></table>
              )}
            </Card>
            <Card title="Sources" sub="Tier 1 papers / exchanges / regulators · 2 established practitioners · 3 blogs and forums · 4 marketing. Only tiers 1-2 feed the knowledge base. Pin tiers by domain in Settings.">
              {sources.length === 0 ? <div className="review-card-empty">No sources yet — read a topic or hand it one.</div> : (
                <div className="table-wrap"><table className="data-table"><thead><tr><th>Title</th><th>Domain</th><th>By</th><th className="num">Tier</th><th className="num">Credibility</th><th>Why</th></tr></thead>
                  <tbody>{sources.map((s) => (
                    <tr key={s.id}><td>{s.url.startsWith('owner://') ? (s.title || 'pasted text') : <a href={s.url} target="_blank" rel="noreferrer">{(s.title || s.url).slice(0, 80)}</a>}</td>
                      <td className="inline-note">{s.domain}</td><td className="inline-note">{s.scored?.providedBy === 'user' ? 'you' : 'worker'}</td>
                      <td className="num">{s.tier ?? '—'}{s.scored?.domainRule ? <span className="inline-note"> (rule)</span> : ''}</td>
                      <td className="num">{s.credibility != null ? s.credibility.toFixed(2) : '—'}</td><td className="inline-note">{s.scored?.reason}</td></tr>))}</tbody></table></div>
              )}
            </Card>
          </>
        )}

        {tab === 'knowledge' && (
          <Card title="Knowledge search" sub="What the agent would retrieve for a query, with credibility." actions={<Link className="btn btn-sm" to="/knowledge">Open the graph →</Link>}>
            <div className="toolbar-row">
              <input type="text" value={q} placeholder="e.g. daily loss limit" onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') searchKnowledge(q).then(setHits); }} style={{ flex: 1 }} />
              <button className="btn btn-primary" onClick={() => searchKnowledge(q).then(setHits)}>Search</button>
            </div>
            {hits.length === 0 ? <div className="inline-note">No results yet.</div> : (
              <ul className="kg-facts">{hits.map((h) => <li key={h.id}><div className="kg-fact-head"><span className="chip">{h.kind}</span><b>{h.credibility}</b><span className="muted">{h.source}</span></div><div>{h.text}</div></li>)}</ul>
            )}
          </Card>
        )}

        {tab === 'requests' && (
          <Card title="Primitive requests" sub="Indicators the agent asked for that the registry does not have. Mark them implemented or rejected.">
            {requests.length === 0 ? <div className="review-card-empty">None — the agent composes from the registry.</div> : (
              <table className="data-table"><thead><tr><th>Name</th><th>Description</th><th>Status</th><th /></tr></thead>
                <tbody>{requests.map((r) => (
                  <tr key={r.id}><td>{r.name}</td><td>{r.description}<pre className="muted">{r.pseudocode}</pre></td><td><StatusChip status={r.status} /></td>
                    <td className="actions">{['implemented', 'rejected'].map((st) => <button key={st} className="btn btn-sm" onClick={() => setPrimitiveRequestStatus(r.id, st).then(refresh)}>{st}</button>)}</td></tr>))}</tbody></table>
            )}
          </Card>
        )}
      </div></div>
    </div>
  );
}
