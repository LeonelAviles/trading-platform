// Shared with backend/strategy_spec.py — keep condition/stop/target types in sync.
// Order-flow conditions are evaluated in backend/condition_engine.py against
// bars_1m.delta; they never fire for symbols with no MBO side data.

export const CONDITION_DEFS = {
  price_above: { label: 'Price above level', params: [{ name: 'value', label: 'Level', def: 100 }] },
  price_below: { label: 'Price below level', params: [{ name: 'value', label: 'Level', def: 100 }] },
  sma_cross_above: { label: 'SMA cross above (fast > slow)', params: [{ name: 'fast', label: 'Fast', def: 9 }, { name: 'slow', label: 'Slow', def: 21 }] },
  sma_cross_below: { label: 'SMA cross below (fast < slow)', params: [{ name: 'fast', label: 'Fast', def: 9 }, { name: 'slow', label: 'Slow', def: 21 }] },
  rsi_above: { label: 'RSI above', params: [{ name: 'period', label: 'Period', def: 14 }, { name: 'value', label: 'Value', def: 70 }] },
  rsi_below: { label: 'RSI below', params: [{ name: 'period', label: 'Period', def: 14 }, { name: 'value', label: 'Value', def: 30 }] },
  breaks_high: { label: 'Breaks N-bar high', params: [{ name: 'lookback', label: 'Bars', def: 20 }] },
  breaks_low: { label: 'Breaks N-bar low', params: [{ name: 'lookback', label: 'Bars', def: 20 }] },
  consecutive: { label: 'N consecutive candles', params: [{ name: 'count', label: 'Count', def: 3 }, { name: 'color', label: 'Color', def: 'green', options: ['green', 'red'] }] },
  // Order flow, from the Databento MBO ticks (aggressive buys minus sells).
  // delta_above/below take a unitless ratio, not a contract count — see
  // condition_engine.Indicators.rel_delta.
  delta_above: { label: 'Buy delta above', params: [{ name: 'lookback', label: 'Bars', def: 20 }, { name: 'value', label: 'Ratio', def: 1 }] },
  delta_below: { label: 'Sell delta below', params: [{ name: 'lookback', label: 'Bars', def: 20 }, { name: 'value', label: 'Ratio', def: -1 }] },
  cvd_rising: { label: 'CVD rising', params: [{ name: 'lookback', label: 'Bars', def: 20 }] },
  cvd_falling: { label: 'CVD falling', params: [{ name: 'lookback', label: 'Bars', def: 20 }] },
  rel_volume_above: { label: 'Relative volume above', params: [{ name: 'lookback', label: 'Bars', def: 20 }, { name: 'value', label: 'x avg', def: 1.5 }] },
};

// Mirrors backend/strategy_spec.INTERVALS. A strategy runs on exactly one of
// these; '' means the trader left the choice to the agent.
export const INTERVALS = [
  ['1min', '1 minute'], ['5min', '5 minutes'], ['15min', '15 minutes'], ['30min', '30 minutes'],
  ['1h', '1 hour'], ['4h', '4 hours'], ['1D', 'Daily'],
];

export function describeInterval(interval) {
  const found = INTERVALS.find(([v]) => v === interval);
  return found ? found[1] : interval || '—';
}

export const STOP_TYPES = [
  { value: 'percent', label: '% from entry', params: [{ name: 'value', label: '%', def: 0.5 }] },
  { value: 'fixed_points', label: 'Fixed points', params: [{ name: 'value', label: 'Points', def: 1 }] },
  { value: 'atr', label: 'ATR multiple', params: [{ name: 'value', label: '(unused)', def: 1, hidden: true }, { name: 'period', label: 'ATR period', def: 14 }, { name: 'mult', label: 'Multiplier', def: 1.5 }] },
];

export const TARGET_TYPES = [
  { value: 'rr', label: 'R multiple', params: [{ name: 'value', label: 'R', def: 2 }] },
  { value: 'percent', label: '% from entry', params: [{ name: 'value', label: '%', def: 1 }] },
  { value: 'fixed_points', label: 'Fixed points', params: [{ name: 'value', label: 'Points', def: 2 }] },
];

export function describeCondition(cond) {
  switch (cond.type) {
    case 'price_above': return `Price above ${cond.value}`;
    case 'price_below': return `Price below ${cond.value}`;
    case 'sma_cross_above': return `SMA cross above (${cond.fast} > ${cond.slow})`;
    case 'sma_cross_below': return `SMA cross below (${cond.fast} < ${cond.slow})`;
    case 'rsi_above': return `RSI(${cond.period}) above ${cond.value}`;
    case 'rsi_below': return `RSI(${cond.period}) below ${cond.value}`;
    case 'breaks_high': return `Breaks ${cond.lookback}-bar high`;
    case 'breaks_low': return `Breaks ${cond.lookback}-bar low`;
    case 'consecutive': return `${cond.count} consecutive ${cond.color} candles`;
    case 'delta_above': return `Buy delta over ${cond.lookback} bars above ${cond.value}× avg`;
    case 'delta_below': return `Sell delta over ${cond.lookback} bars below ${cond.value}× avg`;
    case 'cvd_rising': return `CVD rising over ${cond.lookback} bars`;
    case 'cvd_falling': return `CVD falling over ${cond.lookback} bars`;
    case 'rel_volume_above': return `Volume above ${cond.value}× the ${cond.lookback}-bar average`;
    default: return cond.type;
  }
}

export function describeSizing(sizing) {
  if (!sizing) return '—';
  if (sizing.type === 'fixed_qty') return `${sizing.value} contracts`;
  return `${sizing.value}% of equity`;
}

export function describeStop(stop) {
  if (!stop) return '—';
  if (stop.type === 'percent') return `${stop.value}% from entry`;
  if (stop.type === 'fixed_points') return `${stop.value} pts`;
  if (stop.type === 'atr') return `${stop.mult}× ATR(${stop.period})`;
  return stop.type;
}

export function describeTarget(target) {
  if (!target) return '—';
  if (target.type === 'rr') return `${target.value}R`;
  if (target.type === 'percent') return `${target.value}% from entry`;
  if (target.type === 'fixed_points') return `${target.value} pts`;
  return target.type;
}
