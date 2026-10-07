import { describe, expect, it } from 'vitest';
import { parseAgentText } from './agentText';

describe('parseAgentText', () => {
  it('turns markdown syntax into readable blocks', () => {
    const blocks = parseAgentText('## Result\n\n**Clear answer.**\n\n- First\n- Second');
    expect(blocks).toEqual([
      { type: 'heading', text: 'Result' },
      { type: 'paragraph', text: '**Clear answer.**' },
      { type: 'list', items: ['First', 'Second'] },
    ]);
  });

  it('parses tables without exposing separator rows', () => {
    const blocks = parseAgentText('| Side | Rate |\n|---|---|\n| Up | 58% |');
    expect(blocks).toEqual([{ type: 'table', rows: [['Side', 'Rate'], ['Up', '58%']] }]);
  });
});
