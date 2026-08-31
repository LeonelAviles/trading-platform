// Pure helpers for the knowledge graph page (tested in graphMath.test.js).

export const PALETTE = ['#26a69a', '#ab47bc', '#ffca28', '#42a5f5', '#ef5350', '#66bb6a', '#ff7043', '#8d6e63', '#26c6da', '#d4e157', '#ec407a', '#7e57c2'];

export function clusterColor(cluster) {
  if (cluster == null) return '#8a8f98';
  return PALETTE[cluster % PALETTE.length];
}

export function radius(facts, type) {
  const base = type === 'source' || type === 'strategy' || type === 'teaching' ? 3 : 3.5;
  return base + Math.sqrt(Math.max(0, facts || 0)) * 1.6;
}

// Adjacency map id -> Map(otherId -> weight)
export function adjacency(edges) {
  const adj = new Map();
  for (const e of edges) {
    const a = typeof e.source === 'object' ? e.source.id : e.source;
    const b = typeof e.target === 'object' ? e.target.id : e.target;
    if (!adj.has(a)) adj.set(a, new Map());
    if (!adj.has(b)) adj.set(b, new Map());
    adj.get(a).set(b, e.weight);
    adj.get(b).set(a, e.weight);
  }
  return adj;
}

export function neighbourhood(adj, id) {
  const set = new Set([id]);
  for (const other of adj.get(id)?.keys() || []) set.add(other);
  return set;
}

// Which nodes/edges are drawn. Sources, strategies and sessions are hidden
// unless `showSources` or they were expanded from a concept (double-click).
// `query` highlights rather than filters; `hiddenTypes` removes whole types.
export function visibleSubgraph(nodes, edges, { showSources = false, expanded = new Set(), hiddenTypes = new Set(), minFacts = 1 } = {}) {
  const anchorTypes = new Set(['source', 'strategy', 'teaching']);
  const adj = adjacency(edges);
  const keep = new Set();
  for (const n of nodes) {
    if (hiddenTypes.has(n.type)) continue;
    if (anchorTypes.has(n.type)) {
      if (showSources) keep.add(n.id);
      continue;
    }
    if ((n.facts || 0) >= minFacts) keep.add(n.id);
  }
  for (const id of expanded) {
    for (const other of adj.get(id)?.keys() || []) {
      const n = nodes.find((x) => x.id === other);
      if (n && anchorTypes.has(n.type) && !hiddenTypes.has(n.type)) keep.add(other);
    }
  }
  const vn = nodes.filter((n) => keep.has(n.id));
  const ve = edges.filter((e) => {
    const a = typeof e.source === 'object' ? e.source.id : e.source;
    const b = typeof e.target === 'object' ? e.target.id : e.target;
    return keep.has(a) && keep.has(b);
  });
  return { nodes: vn, edges: ve };
}

export function matchesQuery(node, query) {
  if (!query) return false;
  const q = query.trim().toLowerCase();
  return q.length > 0 && (node.label || '').toLowerCase().includes(q);
}

// Label visibility: hubs and big nodes always, the rest only near the
// pointer or when zoomed in.
export function showLabel(node, { scale = 1, hovered = null, selected = null, focus = null }) {
  if (node.id === hovered || node.id === selected) return true;
  if (focus && focus.has(node.id)) return true;
  if (node.type === 'topic' || node.type === 'instrument') return true;
  const f = node.facts || 0;
  if (f >= 8) return true;
  if (scale >= 1.6 && f >= 3) return true;
  return scale >= 2.6;
}
