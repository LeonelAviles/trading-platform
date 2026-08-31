import { describe, expect, it } from 'vitest';
import { adjacency, clusterColor, neighbourhood, radius, showLabel, visibleSubgraph } from './graphMath';

const nodes = [
  { id: 'c:a', type: 'concept', facts: 5, cluster: 0 },
  { id: 'c:b', type: 'concept', facts: 1, cluster: 0 },
  { id: 'i:es', type: 'instrument', facts: 9 },
  { id: 's:1', type: 'source', facts: 3 },
  { id: 'g:x', type: 'strategy', facts: 1 },
];
const edges = [
  { source: 'c:a', target: 'c:b', weight: 2 },
  { source: 'c:a', target: 'i:es', weight: 4 },
  { source: 's:1', target: 'c:a', weight: 3 },
  { source: 'g:x', target: 'c:b', weight: 1 },
];

describe('graphMath', () => {
  it('adjacency and neighbourhood are symmetric', () => {
    const adj = adjacency(edges);
    expect(adj.get('c:a').get('i:es')).toBe(4);
    expect(adj.get('i:es').get('c:a')).toBe(4);
    expect([...neighbourhood(adj, 'c:b')].sort()).toEqual(['c:a', 'c:b', 'g:x']);
  });

  it('hides sources/strategies unless shown or expanded', () => {
    let v = visibleSubgraph(nodes, edges);
    expect(v.nodes.map((n) => n.id)).toEqual(['c:a', 'c:b', 'i:es']);
    expect(v.edges).toHaveLength(2);
    v = visibleSubgraph(nodes, edges, { showSources: true });
    expect(v.nodes).toHaveLength(5);
    v = visibleSubgraph(nodes, edges, { expanded: new Set(['c:a']) });
    expect(v.nodes.map((n) => n.id)).toEqual(['c:a', 'c:b', 'i:es', 's:1']);
    v = visibleSubgraph(nodes, edges, { hiddenTypes: new Set(['instrument']), minFacts: 2 });
    expect(v.nodes.map((n) => n.id)).toEqual(['c:a']);
  });

  it('colours, radii and labels', () => {
    expect(clusterColor(null)).toBe('#8a8f98');
    expect(clusterColor(0)).not.toBe(clusterColor(1));
    expect(radius(16, 'concept')).toBeGreaterThan(radius(1, 'concept'));
    expect(showLabel({ id: 'x', type: 'concept', facts: 1 }, { scale: 1 })).toBe(false);
    expect(showLabel({ id: 'x', type: 'concept', facts: 1 }, { scale: 1, hovered: 'x' })).toBe(true);
    expect(showLabel({ id: 'x', type: 'instrument', facts: 1 }, { scale: 1 })).toBe(true);
    expect(showLabel({ id: 'x', type: 'concept', facts: 3 }, { scale: 2 })).toBe(true);
  });
});
