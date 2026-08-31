import { Link } from 'react-router-dom';

// One lineage tree (GET /api/strategies/:id/lineage). The champion carries a
// star; when `selected`/`onSelect` are given each node has a checkbox so two
// nodes can be picked for the compare view.
function LineageNode({ node, currentId, champion, selected, onSelect }) {
  const v = node.verdict;
  const isSel = selected?.includes(node.id);
  return (
    <li className="lineage-node">
      <div className={`lineage-row ${node.id === currentId ? 'current' : ''} ${node.id === champion ? 'champion' : ''}`}>
        {onSelect && (
          <input
            type="checkbox"
            checked={!!isSel}
            disabled={!v || (!isSel && selected.length >= 2)}
            title={v ? 'Select to compare' : 'No finished in-sample run to compare'}
            onChange={() => onSelect(node.id)}
          />
        )}
        {node.id === champion && <span className="lineage-star" title="Champion of this lineage">★</span>}
        <Link to={`/strategies/${node.id}`}>{node.name}</Link>
        {node.changedVariable && <span className="review-chip">{node.changedVariable}</span>}
        <span className={`review-chip status-${node.status}`}>{node.status}</span>
        {v && <span className={`review-chip verdict ${v.status}`} title={(v.failures || []).join('\n')}>{v.status}</span>}
        {v?.expectancyR != null && <span className="muted">exp {v.expectancyR}R · PF {v.profitFactor ?? '—'}</span>}
      </div>
      {node.rationale && <div className="lineage-rationale muted">{node.rationale}</div>}
      {node.children?.length > 0 && (
        <ul className="lineage-children">
          {node.children.map((c) => (
            <LineageNode key={c.id} node={c} currentId={currentId} champion={champion} selected={selected} onSelect={onSelect} />
          ))}
        </ul>
      )}
    </li>
  );
}

export default function LineageTree({ lineage, currentId, selected, onSelect }) {
  if (!lineage?.tree) return <div className="review-card-empty">No lineage yet.</div>;
  return (
    <ul className="lineage-tree">
      <LineageNode node={lineage.tree} currentId={currentId} champion={lineage.champion} selected={selected} onSelect={onSelect} />
    </ul>
  );
}
