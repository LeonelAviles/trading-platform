// A starter spec the manual path saves and opens in the editor.
export function templateSpec({ name, symbol, direction }) {
  const root = symbol.replace(/[^A-Z]/g, '').slice(0, 2) || 'ES';
  return {
    schemaVersion: 2, name, description: 'Starter: opening-range breakout. Edit the trigger, stop and target in the Spec tab.',
    instrument: { root, symbol }, timeframes: { primary: '1min', context: [] }, direction,
    session: { entryWindow: { start: '09:45', end: '11:30' }, flattenAt: '15:58' },
    entry: { trigger: { op: 'gt', args: [{ field: 'close' }, { ind: 'opening_range_high', params: { minutes: 15 } }] }, orderType: 'market', timeoutBars: 1 },
    filters: [],
    exit: { stop: { type: 'structure', structure: 'or_low', bufferTicks: 2 }, target: { type: 'rr', value: 2.0 }, trailing: null, breakeven: null, timeStop: null, scaleOut: [] },
    sizing: { type: 'fixed_risk', value: 0.5, maxContracts: 5 },
    constraints: { maxTradesPerDay: 1, cooldownBars: 0, stopAfterConsecutiveLosses: 1, maxConcurrentPositions: 1 },
    execution: { mode: 'ticks' },
  };
}

