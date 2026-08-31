import { useCallback, useContext, useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import {
  addResearchTopic, fetchPrimitiveRequests, fetchResearchQueue, fetchResearchSources, fetchResearchStatus,
  fetchUsage, putSettings, runResearch, searchKnowledge, setPrimitiveRequestStatus,
} from '../api';
import { HeaderSlotContext } from '../headerSlot';

const money = (v) => `$${Number(v || 0).toFixed(3)}`;

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

  const refresh = useCallback(async () => {
    const [qu, so, re, st, us] = await Promise.all([
      fetchResearchQueue().catch(() => []), fetchResearchSources().catch(() => []), fetchPrimitiveRequests().catch(() => []),
      fetchResearchStatus().catch(() => null), fetchUsage().catch(() => null),
    ]);
    setQueue(qu); setSources(so); setRequests(re); setStatus(st); setUsage(us);
    if (us?.prices && !prices) setPrices(us.prices);
  }, [prices]);
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
          <table className="trades-table"><thead><tr><th>Title</th><th>Domain</th><th>Tier</th><th>Credibility</th><th>Why</th></tr></thead>
            <tbody>{sources.map((s) => (
              <tr key={s.id}><td><a href={s.url} target="_blank" rel="noreferrer">{s.title || s.url}</a></td><td>{s.domain}</td><td>{s.tier ?? '—'}</td>
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
