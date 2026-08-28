// Read-only labels for a strategy overlaid on the chart. Mirrors the
// condition/stop/target types in backend/strategy_spec.py — keep in sync.
// Order-flow conditions are evaluated in backend/condition_engine.py against
// bars_1m.delta; they never fire for symbols with no MBO side data.

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
