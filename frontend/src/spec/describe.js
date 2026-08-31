// Plain-English rendering of a Strategy Spec v2 (PLATFORM-SPEC.md §5 Phase 3 task 7).
// Pure functions — unit-tested with vitest.

const OPS = {
  gt: '>', gte: '≥', lt: '<', lte: '≤', eq: '=',
};

function fmtNum(v) {
  if (typeof v !== 'number') return String(v);
  return Number.isInteger(v) ? String(v) : String(Math.round(v * 10000) / 10000);
}

function fmtParams(params = {}) {
  const entries = Object.entries(params).filter(([k]) => k !== 'tf');
  if (!entries.length) return '';
  return `(${entries.map(([k, v]) => (typeof v === 'number' ? fmtNum(v) : `${k}=${v}`)).join(', ')})`;
}

const NAMES = {
  opening_range_high: 'opening range high', opening_range_low: 'opening range low',
  initial_balance_high: 'initial balance high', initial_balance_low: 'initial balance low',
  session_high: 'session high', session_low: 'session low', prior_day_high: 'prior day high',
  prior_day_low: 'prior day low', prior_day_close: 'prior day close', bar_delta: 'bar delta',
  cvd_session: 'session CVD', cvd_window: 'CVD', cvd_slope: 'CVD slope', rel_volume: 'relative volume',
  rel_delta: 'relative delta', delta_divergence: 'delta divergence', footprint_imbalance: 'footprint imbalances',
  stacked_imbalances: 'stacked imbalances', poc_migration: 'POC migration', large_print: 'large print',
  time_of_day: 'minutes since open', minutes_to_close: 'minutes to close', bars_since_open: 'bars since open',
  bollinger_upper: 'Bollinger upper', bollinger_lower: 'Bollinger lower', swing_high: 'swing high', swing_low: 'swing low',
  candle_pattern: 'candle', volume_at_price: 'volume at price', profile_shape: 'profile shape',
  large_resting_size_near: 'resting size near', resting_size_at: 'resting size at', book_imbalance: 'book imbalance',
};

export function describeLeaf(node) {
  if (typeof node === 'number') return fmtNum(node);
  if (typeof node === 'boolean') return node ? 'always' : 'never';
  if (!node || typeof node !== 'object') return String(node);
  if (node.field) return node.tf ? `${node.field} (${node.tf})` : node.field;
  if (node.ind) {
    const name = NAMES[node.ind] || node.ind.toUpperCase().replace(/_/g, ' ').toLowerCase();
    const base = /^(sma|ema|rsi|atr|adx|vwap|poc|vah|val)$/.test(node.ind) ? node.ind.toUpperCase() : name;
    const tf = node.tf || node.params?.tf;
    return `${base}${fmtParams(node.params)}${tf ? ` on ${tf}` : ''}`;
  }
  return describeExpr(node);
}

export function describeExpr(node) {
  if (node == null) return '';
  if (typeof node !== 'object' || node.field || node.ind) return describeLeaf(node);
  const { op, args = [] } = node;
  const a = args.map(describeLeaf);
  switch (op) {
    case 'and': return a.join(' AND ');
    case 'or': return `(${a.join(' OR ')})`;
    case 'not': return `NOT (${a[0]})`;
    case 'between': return `${a[0]} between ${a[1]} and ${a[2]}`;
    case 'cross_above': return `${a[0]} crosses above ${a[1]}`;
    case 'cross_below': return `${a[0]} crosses below ${a[1]}`;
    case 'rising': return `${a[0]} rising for ${a[1]} bars`;
    case 'falling': return `${a[0]} falling for ${a[1]} bars`;
    case 'within_ticks': return `${a[0]} within ${a[1]} ticks of ${a[1] !== undefined ? a[1] : ''}`.replace(`within ${a[1]} ticks of ${a[1]}`, `within ${a[2]} ticks of ${a[1]}`);
    case 'touched': return `${a[0]} touched (±${a[1]} ticks) within the last ${a[2]} bars`;
    case 'held_above': return `held above ${a[0]} for ${a[1]} bars`;
    case 'held_below': return `held below ${a[0]} for ${a[1]} bars`;
    case 'bars_since': return `bars since (${a[0]})`;
    case 'retest': return `retest of ${a[0]} (±${a[1]} ticks, within ${a[2]} bars)`;
    default:
      if (OPS[op]) return `${a[0]} ${OPS[op]} ${a[1]}`;
      return `${op}(${a.join(', ')})`;
  }
}

export function describeStop(stop) {
  if (!stop) return '—';
  switch (stop.type) {
    case 'ticks': return `${fmtNum(stop.value)} ticks`;
    case 'points': return `${fmtNum(stop.value)} pts`;
    case 'percent': return `${fmtNum(stop.value)}%`;
    case 'atr': return `${fmtNum(stop.value)}× ATR(${stop.period ?? 14})`;
    case 'structure': return `${(stop.structure || '').replace(/_/g, ' ')}${stop.bufferTicks ? ` − ${stop.bufferTicks} ticks` : ''}`;
    default: return stop.type;
  }
}

export function describeTarget(target) {
  if (!target) return '—';
  switch (target.type) {
    case 'rr': return `${fmtNum(target.value)}R`;
    case 'ticks': return `${fmtNum(target.value)} ticks`;
    case 'points': return `${fmtNum(target.value)} pts`;
    case 'level': return (target.level || '').replace(/_/g, ' ');
    default: return target.type;
  }
}

export function describeSizing(sizing) {
  if (!sizing) return '—';
  if (sizing.type === 'fixed_contracts') return `${sizing.value} contract${sizing.value === 1 ? '' : 's'}`;
  if (sizing.type === 'fixed_risk') return `${fmtNum(sizing.value)}% risk per trade (max ${sizing.maxContracts})`;
  if (sizing.type === 'vol_scaled') return `${fmtNum(sizing.value)}% risk, ATR-scaled (max ${sizing.maxContracts})`;
  return sizing.type;
}

// Full sentences for the strategy page.
export function describeSpec(spec) {
  if (!spec) return [];
  const dir = spec.direction === 'both' ? 'long or short (mirrored)' : spec.direction;
  const entry = spec.entry || {};
  const order = entry.orderType === 'limit'
    ? `at a limit ${entry.limitOffsetTicks ? `${entry.limitOffsetTicks} ticks from the trigger price` : 'at the trigger price'}`
    : entry.orderType === 'stop' ? `on a stop ${entry.stopOffsetTicks ?? 1} tick(s) beyond the trigger` : 'at market';
  const lines = [];
  lines.push(`${spec.instrument?.symbol || ''} · ${spec.timeframes?.primary || '1min'} bars${spec.timeframes?.context?.length ? ` with ${spec.timeframes.context.join(', ')} context` : ''} · ${spec.execution?.mode || 'ticks'} mode`);
  if (entry.sequence?.length) {
    entry.sequence.forEach((s, i) => lines.push(`Setup step ${i + 1}: ${describeExpr(s.when)} (within ${s.withinBars} bars)`));
  }
  lines.push(`Enter ${dir} ${order} when ${describeExpr(entry.trigger)}`);
  (spec.filters || []).forEach((f) => lines.push(`Only if ${describeExpr(f)}`));
  const ex = spec.exit || {};
  lines.push(`Stop ${describeStop(ex.stop)}, target ${describeTarget(ex.target)}`);
  if (ex.trailing) lines.push(`Trail ${ex.trailing.type === 'atr' ? `${fmtNum(ex.trailing.value)}× ATR` : `${fmtNum(ex.trailing.value)} ticks`} after ${fmtNum(ex.trailing.activateAtR)}R`);
  if (ex.breakeven) lines.push(`Move stop to breakeven (+${ex.breakeven.offsetTicks} ticks) at ${fmtNum(ex.breakeven.atR)}R`);
  if (ex.timeStop) lines.push(`Time stop after ${ex.timeStop.bars} bars`);
  (ex.scaleOut || []).forEach((so) => lines.push(`Scale out ${Math.round(so.fraction * 100)}% at ${fmtNum(so.atR)}R`));
  const s = spec.session || {};
  lines.push(`Entries ${s.entryWindow?.start}–${s.entryWindow?.end} ET${s.noTradeWindows?.length ? `, not ${s.noTradeWindows.map((w) => `${w.start}–${w.end}`).join(', ')}` : ''}; flat at ${s.flattenAt} ET`);
  lines.push(`Size: ${describeSizing(spec.sizing)}`);
  const c = spec.constraints || {};
  lines.push(`Max ${c.maxTradesPerDay ?? '—'} trades/day, cooldown ${c.cooldownBars ?? 0} bars, stop after ${c.stopAfterConsecutiveLosses ?? '—'} losses`);
  return lines;
}
