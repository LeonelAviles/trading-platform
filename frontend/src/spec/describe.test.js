import { describe, expect, it } from 'vitest';
import { describeExpr, describeSpec, describeStop, describeTarget } from './describe';
import { parseSpec, validateSpec } from './validate';

const ORB = {
  schemaVersion: 2, name: 'ORB', instrument: { root: 'ES', symbol: 'ES1!' },
  timeframes: { primary: '1min', context: ['15min'] }, direction: 'both',
  session: { entryWindow: { start: '09:45', end: '11:30' }, noTradeWindows: [], flattenAt: '15:58' },
  entry: {
    trigger: { op: 'and', args: [
      { op: 'gt', args: [{ field: 'close' }, { ind: 'opening_range_high', params: { minutes: 15 } }] },
      { op: 'gt', args: [{ ind: 'rel_volume', params: { n: 20 } }, 1.5] },
      { op: 'gt', args: [{ ind: 'ema', params: { period: 9, tf: '15min' } }, { ind: 'ema', params: { period: 21, tf: '15min' } }] },
    ] },
    orderType: 'market', timeoutBars: 1,
  },
  filters: [],
  exit: { stop: { type: 'structure', structure: 'or_low', bufferTicks: 2 }, target: { type: 'rr', value: 2 } },
  sizing: { type: 'fixed_risk', value: 0.5, maxContracts: 5 },
  constraints: { maxTradesPerDay: 1, cooldownBars: 0, stopAfterConsecutiveLosses: 1 },
  execution: { mode: 'ticks' },
};

describe('describe', () => {
  it('renders the ORB trigger as a sentence', () => {
    expect(describeExpr(ORB.entry.trigger)).toBe(
      'close > opening range high(15) AND relative volume(20) > 1.5 AND EMA(9) on 15min > EMA(21) on 15min',
    );
  });
  it('renders stops, targets and the full spec', () => {
    expect(describeStop(ORB.exit.stop)).toBe('or low − 2 ticks');
    expect(describeTarget(ORB.exit.target)).toBe('2R');
    const lines = describeSpec(ORB);
    expect(lines[1]).toMatch(/^Enter long or short \(mirrored\) at market when close > opening range high/);
    expect(lines).toContain('Stop or low − 2 ticks, target 2R');
    expect(lines.some((l) => l.startsWith('Entries 09:45–11:30 ET'))).toBe(true);
  });
  it('renders stateful operators', () => {
    expect(describeExpr({ op: 'retest', args: [{ ind: 'opening_range_high', params: { minutes: 15 } }, 4, 20] }))
      .toBe('retest of opening range high(15) (±4 ticks, within 20 bars)');
    expect(describeExpr({ op: 'cross_above', args: [{ ind: 'ema', params: { period: 9 } }, { ind: 'ema', params: { period: 21 } }] }))
      .toBe('EMA(9) crosses above EMA(21)');
  });
});

describe('validate', () => {
  it('accepts the ORB example', () => {
    expect(validateSpec(ORB)).toEqual({ valid: true, errors: [] });
  });
  it('reports unknown fields, enums, primitives and operators', () => {
    const { errors } = validateSpec({ ...ORB, bogus: 1, direction: 'sideways',
      entry: { trigger: { op: 'xor', args: [{ ind: 'magic' }, 1] } } });
    expect(errors.some((e) => e.includes("unknown field 'bogus'"))).toBe(true);
    expect(errors.some((e) => e.startsWith('direction:'))).toBe(true);
    expect(errors.some((e) => e.includes("unknown primitive 'magic'"))).toBe(true);
    expect(errors.some((e) => e.includes("unknown operator 'xor'"))).toBe(true);
  });
  it('parses JSON text', () => {
    expect(parseSpec('{"a":1}').spec).toEqual({ a: 1 });
    expect(parseSpec('{oops').error).toMatch(/^JSON:/);
  });
});
