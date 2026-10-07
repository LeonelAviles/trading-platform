import { Fragment } from 'react';
import { parseAgentText } from './agentText';

function Inline({ text }) {
  const parts = String(text).split(/(\*\*[^*]+\*\*|__[^_]+__|`[^`]+`|\[[^\]]+\]\(https?:\/\/[^)]+\))/g);
  return parts.map((part, index) => {
    if (/^(\*\*|__)/.test(part)) return <strong key={index}>{part.slice(2, -2)}</strong>;
    if (part.startsWith('`') && part.endsWith('`')) return <code key={index}>{part.slice(1, -1)}</code>;
    const link = part.match(/^\[([^\]]+)\]\((https?:\/\/[^)]+)\)$/);
    if (link) return <a key={index} href={link[2]} target="_blank" rel="noreferrer">{link[1]}</a>;
    return <Fragment key={index}>{part.replace(/\*\*|__/g, '')}</Fragment>;
  });
}

export default function AgentMessageBody({ text }) {
  return (
    <div className="agent-message-body agent-rich-body">
      {parseAgentText(text).map((block, index) => {
        if (block.type === 'heading') return <h3 key={index}><Inline text={block.text} /></h3>;
        if (block.type === 'list' || block.type === 'ordered') {
          const Tag = block.type === 'ordered' ? 'ol' : 'ul';
          return <Tag key={index}>{block.items.map((item, itemIndex) => <li key={itemIndex}><Inline text={item} /></li>)}</Tag>;
        }
        if (block.type === 'quote') return <blockquote key={index}><Inline text={block.text} /></blockquote>;
        if (block.type === 'code') return <pre key={index}><code>{block.text}</code></pre>;
        if (block.type === 'table') return (
          <div className="agent-table-wrap" key={index}><table><thead><tr>{block.rows[0].map((cell, j) => <th key={j}><Inline text={cell} /></th>)}</tr></thead>
            <tbody>{block.rows.slice(1).map((row, j) => <tr key={j}>{row.map((cell, k) => <td key={k}><Inline text={cell} /></td>)}</tr>)}</tbody></table></div>
        );
        return <p key={index}><Inline text={block.text} /></p>;
      })}
    </div>
  );
}
