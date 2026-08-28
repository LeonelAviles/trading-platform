// Read-only labels for a strategy's stop/target, used on the review chooser.
// Mirrors the stop/target types in backend/strategy_spec.py — keep in sync.

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
