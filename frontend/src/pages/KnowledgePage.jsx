import { useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import { Link } from 'react-router-dom';
import { addResearchTopic, fetchKnowledgeFacts, fetchKnowledgeGraph } from '../api';
import { HeaderSlotContext } from '../headerSlot';
import ForceGraph from '../components/ForceGraph';
import { adjacency, clusterColor, neighbourhood, visibleSubgraph } from '../knowledge/graphMath';

const KINDS = ['claim', 'hypothesis', 'fact', 'experiment', 'finding', 'note', 'teaching'];
const TYPES = [['concept', 'Concepts'], ['regime', 'Regimes'], ['instrument', 'Instruments'], ['topic', 'Topics'], ['source', 'Sources'], ['strategy', 'Strategies'], ['teaching', 'Sessions']];

function Facts({ facts }) {
  if (!facts?.length) return <div className="review-card-empty">No facts.</div>;
  return (
    <ul className="kg-facts">
      {facts.map((f) => (
        <li key={f.id}>
          <div className="kg-fact-head">
            <span className={`review-chip kg-kind-${f.kind}`}>{f.kind}</span>
            <b>{f.credibility.toFixed(2)}</b>
            {f.evidenceType && <span className="muted">{f.evidenceType}</span>}
            {f.sourceUrl ? <a href={f.sourceUrl} target="_blank" rel="noreferrer" className="muted">{f.source}</a> : <span className="muted">{f.source}</span>}
            {(f.tags || []).includes('owner') && <span className="review-chip">owner</span>}
          </div>
          <div>{f.text}</div>
        </li>
      ))}
    </ul>
  );
}

// /knowledge — what the agent knows, as an interactive graph (PLATFORM-SPEC.md
// §4.8 knowledge graph; built from the local fact store, complete in both
// backends). Concepts are nodes, co-occurrence on a fact is an edge.
export default function KnowledgePage() {
  const { leading: leadingSlot } = useContext(HeaderSlotContext);
  const [graph, setGraph] = useState(null);
  const [error, setError] = useState('');
  const [minCred, setMinCred] = useState(0);
  const [kinds, setKinds] = useState([]);
  const [tiers, setTiers] = useState([]);
  const [hiddenTypes, setHiddenTypes] = useState(new Set());
  const [showSources, setShowSources] = useState(false);
  const [minFacts, setMinFacts] = useState(1);
  const [expanded, setExpanded] = useState(new Set());
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState(null);
  const [edgeSel, setEdgeSel] = useState(null);
  const [facts, setFacts] = useState([]);
  const [focusCluster, setFocusCluster] = useState(null);
  const [hovered, setHovered] = useState(null);
  const [msg, setMsg] = useState('');

  const load = useCallback(async () => {
    try {
      setGraph(await fetchKnowledgeGraph({ minCredibility: minCred, kinds, tiers, sources: true }));
      setError('');
    } catch (e) {
      setError(e.message);
    }
  }, [minCred, kinds, tiers]);
  useEffect(() => { load(); }, [load]);

  const visible = useMemo(() => (graph ? visibleSubgraph(graph.nodes, graph.edges, { showSources, expanded, hiddenTypes, minFacts }) : { nodes: [], edges: [] }),
    [graph, showSources, expanded, hiddenTypes, minFacts]);
  const adj = useMemo(() => adjacency(visible.edges), [visible.edges]);
  const focus = useMemo(() => {
    if (focusCluster == null || !graph) return null;
    return new Set(graph.nodes.filter((n) => n.cluster === focusCluster).map((n) => n.id));
  }, [focusCluster, graph]);

  useEffect(() => {
    if (!selected) { setFacts([]); return; }
    const n = graph?.nodes.find((x) => x.id === selected);
    if (n?.factIds?.length) fetchKnowledgeFacts(n.factIds.slice(0, 60)).then(setFacts).catch(() => setFacts([]));
    else setFacts([]);
  }, [selected, graph]);

  function onSelect(node) {
    setEdgeSel(null);
    setSelected(node ? node.id : null);
  }
  function onExpand(node) {
    setExpanded((s) => { const n = new Set(s); if (n.has(node.id)) n.delete(node.id); else n.add(node.id); return n; });
  }
  async function showEdge(otherId) {
    const e = visible.edges.find((x) => (x.source === selected && x.target === otherId) || (x.target === selected && x.source === otherId));
    if (!e) return;
    setEdgeSel(otherId);
    setFacts(await fetchKnowledgeFacts(e.factIds).catch(() => []));
  }
  async function queue(gap) {
    try {
      await addResearchTopic(gap.suggest);
      setMsg(`Queued: ${gap.suggest}`);
    } catch (e) {
      setMsg(e.message);
    }
  }
  function toggle(list, setList, v) { setList(list.includes(v) ? list.filter((x) => x !== v) : [...list, v]); }
  function toggleType(t) { setHiddenTypes((s) => { const n = new Set(s); if (n.has(t)) n.delete(t); else n.add(t); return n; }); }

  const selNode = graph?.nodes.find((n) => n.id === selected);
  const neighbours = selNode ? [...neighbourhood(adj, selected)].filter((id) => id !== selected).map((id) => ({ id, w: adj.get(selected)?.get(id) || 0, n: graph.nodes.find((x) => x.id === id) })).filter((x) => x.n).sort((a, b) => b.w - a.w) : [];
  const st = graph?.stats;

  return (
    <div className="page kg-page">
      {leadingSlot && createPortal(<div className="hdr-title"><Link to="/research" className="muted">Research</Link> / Knowledge graph</div>, leadingSlot)}
      <div className="kg-layout">
        <aside className="kg-side kg-filters">
          <div className="review-card-name">Filters</div>
          <label>Min credibility <b>{minCred.toFixed(2)}</b><input type="range" min="0" max="1" step="0.05" value={minCred} onChange={(e) => setMinCred(Number(e.target.value))} /></label>
          <label>Min facts per node <b>{minFacts}</b><input type="range" min="1" max="10" step="1" value={minFacts} onChange={(e) => setMinFacts(Number(e.target.value))} /></label>
          <div className="kg-group"><div className="muted">Kinds (none = all)</div>{KINDS.map((k) => <label key={k} className="kg-check"><input type="checkbox" checked={kinds.includes(k)} onChange={() => toggle(kinds, setKinds, k)} />{k}</label>)}</div>
          <div className="kg-group"><div className="muted">Source tier (none = all)</div>{[1, 2, 3].map((t) => <label key={t} className="kg-check"><input type="checkbox" checked={tiers.includes(t)} onChange={() => toggle(tiers, setTiers, t)} />tier {t}</label>)}</div>
          <div className="kg-group"><div className="muted">Node types</div>{TYPES.map(([t, label]) => <label key={t} className="kg-check"><input type="checkbox" checked={!hiddenTypes.has(t)} onChange={() => toggleType(t)} />{label}</label>)}</div>
          <label className="kg-check"><input type="checkbox" checked={showSources} onChange={(e) => setShowSources(e.target.checked)} />Show sources / strategies (or double-click a concept)</label>
          <input className="agent-answer-input" value={query} placeholder="Find a concept…" onChange={(e) => setQuery(e.target.value)} />
          <div className="kg-legend">
            <span><i className="kg-dot" /> concept (colour = cluster)</span>
            <span><i className="kg-dot kg-dot-topic" /> topic / instrument</span>
            <span><i className="kg-sq" /> source (gold ring = yours)</span>
            <span><i className="kg-dia" /> strategy / session</span>
          </div>
          {st && <div className="muted kg-stats">{st.facts} facts · {visible.nodes.length}/{st.nodes} nodes · {visible.edges.length}/{st.edges} edges · {st.clusters} clusters · density {st.density}</div>}
          {error && <div className="review-error">{error}</div>}
        </aside>

        <main className="kg-main">
          {graph ? (
            <ForceGraph nodes={visible.nodes} edges={visible.edges} selected={selected} focus={focus} query={query} onSelect={onSelect} onExpand={onExpand} onHover={setHovered} height={640} />
          ) : <div className="review-empty">{error || 'Loading…'}</div>}
          <div className="kg-hint muted">{hovered ? `${hovered.label} — ${hovered.facts} fact(s), credibility ${hovered.credibility}${hovered.definition ? ` — ${hovered.definition}` : ''}` : 'Drag to pan · wheel to zoom · drag a node · click to inspect · double-click a concept to show its sources'}</div>
          {selNode && (
            <section className="review-card kg-detail">
              <header className="review-card-head">
                <div>
                  <div className="review-card-name">{selNode.label} <span className="review-chip">{selNode.type}</span>{selNode.cluster != null && <span className="review-chip" style={{ borderColor: clusterColor(selNode.cluster) }}>cluster {selNode.cluster + 1}</span>}</div>
                  <div className="review-card-sub">{selNode.facts} fact(s) · avg credibility {selNode.credibility}{selNode.definition ? ` · ${selNode.definition}` : ''}{selNode.url ? <> · <a href={selNode.url} target="_blank" rel="noreferrer">open source</a></> : ''}{selNode.type === 'strategy' ? <> · <Link to={`/strategies/${selNode.id.slice(2)}`}>open strategy</Link></> : ''}</div>
                </div>
                <div className="strategy-actions">
                  <button className="btn btn-sm" onClick={() => onExpand(selNode)}>{expanded.has(selNode.id) ? 'Hide sources' : 'Show sources'}</button>
                  <button className="btn btn-sm" onClick={() => onSelect(null)}>Close</button>
                </div>
              </header>
              <div className="kg-neighbours">
                <span className="muted">Linked:</span>
                {neighbours.slice(0, 24).map(({ id, w, n }) => (
                  <button key={id} className={`review-chip kg-neigh ${edgeSel === id ? 'active' : ''}`} style={{ borderColor: clusterColor(n.cluster) }} title={`${w} shared fact(s) — click to see them`} onClick={() => showEdge(id)}>{n.label.slice(0, 30)} · {w}</button>
                ))}
                {edgeSel && <button className="btn btn-sm" onClick={() => { setEdgeSel(null); setSelected(selected); fetchKnowledgeFacts(selNode.factIds.slice(0, 60)).then(setFacts); }}>all facts</button>}
              </div>
              <Facts facts={facts} />
            </section>
          )}
        </main>

        <aside className="kg-side kg-analytics">
          <div className="review-card-name">Main topics <span className="muted">(clusters)</span></div>
          <ol className="kg-clusters">
            {(graph?.clusters || []).slice(0, 10).map((c) => (
              <li key={c.id} className={focusCluster === c.id ? 'active' : ''} onClick={() => setFocusCluster(focusCluster === c.id ? null : c.id)}>
                <i className="kg-dot" style={{ background: clusterColor(c.id) }} />
                <div><b>{c.name}</b><div className="muted">{c.size} concepts · {c.facts} facts · cred {c.avgCredibility}</div></div>
              </li>
            ))}
          </ol>
          <div className="review-card-name">Most central concepts</div>
          <div className="kg-chips">
            {(graph?.central || []).map((c) => <button key={c.id} className="review-chip kg-neigh" style={{ borderColor: clusterColor(c.cluster) }} title={`weighted degree ${c.weightedDegree}, bridges ${c.bridges} clusters`} onClick={() => setSelected(c.id)}>{c.label}</button>)}
          </div>
          <div className="review-card-name">Hubs</div>
          <div className="kg-chips">
            {(graph?.hubs || []).map((h) => <button key={h.id} className="review-chip kg-neigh" onClick={() => setSelected(h.id)}>{h.label.split(':')[0].slice(0, 30)} · {h.facts}</button>)}
          </div>
          <div className="review-card-name">Content gaps <span className="muted">(what to read next)</span></div>
          <ul className="kg-gaps">
            {(graph?.gaps || []).map((g, i) => (
              <li key={i}>
                <div><span className="review-chip">{g.kind.replace('_', ' ')}</span> <b>{g.label}</b></div>
                <div className="muted">{g.why}</div>
                <div className="strategy-actions">
                  {g.node && <button className="btn btn-sm" onClick={() => setSelected(g.node)}>show</button>}
                  {g.cluster != null && <button className="btn btn-sm" onClick={() => setFocusCluster(g.cluster)}>focus</button>}
                  {g.kind !== 'unread_topic' && <button className="btn btn-sm" onClick={() => queue(g)}>queue topic</button>}
                </div>
              </li>
            ))}
          </ul>
          {msg && <div className="muted">{msg}</div>}
        </aside>
      </div>
    </div>
  );
}
