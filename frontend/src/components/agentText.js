const HEADING = /^#{1,6}\s+(.+)$/;
const BULLET = /^\s*(?:[-*+]\s+|•\s*)(.+)$/;
const ORDERED = /^\s*\d+[.)]\s+(.+)$/;
const TABLE_RULE = /^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$/;

function tableCells(line) {
  return line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map((cell) => cell.trim());
}

export function parseAgentText(text = '') {
  const lines = text.replace(/\r/g, '').split('\n');
  const blocks = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i].trim();
    if (!line) { i += 1; continue; }
    const heading = line.match(HEADING);
    if (heading) { blocks.push({ type: 'heading', text: heading[1] }); i += 1; continue; }
    if (/^```/.test(line)) {
      const code = [];
      i += 1;
      while (i < lines.length && !/^```/.test(lines[i].trim())) code.push(lines[i++]);
      if (i < lines.length) i += 1;
      blocks.push({ type: 'code', text: code.join('\n') });
      continue;
    }
    if (line.includes('|') && i + 1 < lines.length && TABLE_RULE.test(lines[i + 1])) {
      const rows = [tableCells(line)];
      i += 2;
      while (i < lines.length && lines[i].includes('|') && lines[i].trim()) rows.push(tableCells(lines[i++]));
      blocks.push({ type: 'table', rows });
      continue;
    }
    const bullet = line.match(BULLET);
    const ordered = line.match(ORDERED);
    if (bullet || ordered) {
      const type = bullet ? 'list' : 'ordered';
      const items = [];
      while (i < lines.length) {
        const match = lines[i].trim().match(type === 'list' ? BULLET : ORDERED);
        if (!match) break;
        items.push(match[1]); i += 1;
      }
      blocks.push({ type, items });
      continue;
    }
    if (line.startsWith('>')) {
      blocks.push({ type: 'quote', text: line.replace(/^>\s?/, '') }); i += 1; continue;
    }
    if (/^[-*_]{3,}$/.test(line)) { i += 1; continue; }
    const paragraph = [line];
    i += 1;
    while (i < lines.length && lines[i].trim()
      && !HEADING.test(lines[i].trim()) && !BULLET.test(lines[i].trim())
      && !ORDERED.test(lines[i].trim()) && !lines[i].trim().startsWith('>')
      && !/^```/.test(lines[i].trim())) {
      if (lines[i].includes('|') && i + 1 < lines.length && TABLE_RULE.test(lines[i + 1])) break;
      paragraph.push(lines[i].trim()); i += 1;
    }
    blocks.push({ type: 'paragraph', text: paragraph.join(' ') });
  }
  return blocks;
}
