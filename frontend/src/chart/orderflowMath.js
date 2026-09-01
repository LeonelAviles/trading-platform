// Pure order-flow arithmetic shared by the canvas layers (PLATFORM-SPEC.md §5 Phase 5).
// Kept free of React and the chart so vitest covers it directly.

// Delta bubbles — the owner's definition: aggregate prints by (500 ms window,
// same price) into one bubble whose net delta = buy volume − sell volume.
export const BUBBLE_WINDOW_NS = 500_000_000;

export function aggregateBubbles(trades, { windowNs = BUBBLE_WINDOW_NS } = {}) {
  const out = [];
  const open = new Map(); // key price -> bubble in the current window
  let windowStart = null;
  for (const t of trades) {
    const ts = typeof t.ts === 'bigint' ? Number(t.ts) : t.ts;
    if (windowStart == null || ts - windowStart >= windowNs) {
      windowStart = ts - (ts % windowNs);
      open.clear();
    }
    const signed = t.side === 'B' ? t.size : -t.size;
    let b = open.get(t.price);
    if (!b) {
      b = { ts: windowStart, tsEnd: ts, price: t.price, netDelta: 0, volume: 0, prints: 0 };
      open.set(t.price, b);
      out.push(b);
    }
    b.netDelta += signed;
    b.volume += t.size;
    b.prints += 1;
    b.tsEnd = ts;
  }
  return out;
}

export function bubbleRadius(netDelta) {
  const r = 3 + 2.2 * Math.sqrt(Math.abs(netDelta));
  return Math.max(4, Math.min(26, r));
}

export function percentile(values, p) {
  if (!values.length) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const idx = Math.min(sorted.length - 1, Math.max(0, Math.floor(p * (sorted.length - 1))));
  return sorted[idx];
}

// Widen the aggregation window as the zoom coarsens (static view): keep
// roughly ≤ 6 bubbles per bar width so the layer stays legible.
export function bubbleWindowForBarSeconds(barSeconds) {
  if (barSeconds <= 60) return BUBBLE_WINDOW_NS;
  const perBar = 6;
  return Math.max(BUBBLE_WINDOW_NS, Math.round((barSeconds / perBar) * 1e9));
}

// Footprint imbalance: horizontal comparison — bid vs ask traded at the
// *same* price level. Ask ≥ ratio × bid marks a buy imbalance, bid ≥ ratio ×
// ask a sell imbalance, each gated by minVolume so tiny prints don't qualify.
export function footprintImbalances(levels, { ratio = 3.0, minVolume = 5 } = {}) {
  const sorted = [...levels].sort((a, b) => a.price - b.price);
  const buy = new Set();
  const sell = new Set();
  for (const l of sorted) {
    if (l.ask >= minVolume && l.ask >= ratio * l.bid) buy.add(l.price);
    if (l.bid >= minVolume && l.bid >= ratio * l.ask) sell.add(l.price);
  }
  return { buy, sell, sorted };
}

// Stacked imbalances: runs of ≥ `min` consecutive imbalanced levels.
export function stackedRuns(sortedPrices, marked, min = 3) {
  const runs = [];
  let run = [];
  for (const p of sortedPrices) {
    if (marked.has(p)) run.push(p);
    else {
      if (run.length >= min) runs.push(run);
      run = [];
    }
  }
  if (run.length >= min) runs.push(run);
  return runs;
}

export function footprintPoc(levels) {
  let best = null;
  for (const l of levels) {
    const v = l.bid + l.ask;
    if (!best || v > best.v) best = { price: l.price, v };
  }
  return best?.price ?? null;
}

// Volume profile: value area holding `fraction` of volume expanding from the
// POC toward the larger neighbour (same rule as backend data_store._value_area).
export function valueArea(bins, fraction = 0.7) {
  if (!bins.length) return { poc: null, vah: null, val: null };
  const sorted = [...bins].sort((a, b) => a[0] - b[0]);
  const total = sorted.reduce((s, b) => s + b[1], 0);
  if (total <= 0) return { poc: null, vah: null, val: null };
  let i = 0;
  for (let k = 1; k < sorted.length; k++) if (sorted[k][1] > sorted[i][1]) i = k;
  let lo = i, hi = i, acc = sorted[i][1];
  while (acc < fraction * total && (lo > 0 || hi < sorted.length - 1)) {
    const up = hi < sorted.length - 1 ? sorted[hi + 1][1] : -1;
    const dn = lo > 0 ? sorted[lo - 1][1] : -1;
    if (up >= dn) { hi++; acc += up; } else { lo--; acc += dn; }
  }
  return { poc: sorted[i][0], vah: sorted[hi][0], val: sorted[lo][0] };
}

// Build a bid×ask footprint per bar from a list of trades (used for bars the
// session replayed before the footprint history was fetched).
export function footprintFromTrades(trades, barSeconds) {
  const bars = new Map();
  for (const t of trades) {
    const sec = Math.floor((typeof t.ts === 'bigint' ? Number(t.ts) : t.ts) / 1e9);
    const time = sec - (sec % barSeconds);
    let bar = bars.get(time);
    if (!bar) { bar = new Map(); bars.set(time, bar); }
    let lvl = bar.get(t.price);
    if (!lvl) { lvl = { price: t.price, bid: 0, ask: 0 }; bar.set(t.price, lvl); }
    if (t.side === 'B') lvl.ask += t.size; else lvl.bid += t.size;
  }
  const out = {};
  for (const [time, m] of bars) out[time] = [...m.values()].sort((a, b) => a.price - b.price);
  return out;
}

// Roll 1-minute footprints (closed map time -> levels, plus the live 1-minute
// bar) up to a coarser bar size: sums bid/ask per price across the minutes
// that fall in each bar. Returns { footprints, live }.
export function aggregateFootprints(closed1m, live1m, stepSeconds) {
  if (stepSeconds <= 60) return { footprints: closed1m, live: live1m };
  const out = {};
  const add = (time, levels) => {
    const bar = time - (time % stepSeconds);
    let m = out[bar];
    if (!m) { m = out[bar] = new Map(); }
    for (const l of levels) {
      const cur = m.get(l.price);
      if (cur) { cur.bid += l.bid; cur.ask += l.ask; } else m.set(l.price, { price: l.price, bid: l.bid, ask: l.ask });
    }
  };
  for (const [t, levels] of Object.entries(closed1m)) add(Number(t), levels);
  let liveTime = null;
  if (live1m?.time != null) { add(live1m.time, live1m.levels || []); liveTime = live1m.time - (live1m.time % stepSeconds); }
  const footprints = {};
  let live = null;
  for (const [t, m] of Object.entries(out)) {
    const levels = [...m.values()].sort((a, b) => a.price - b.price);
    if (Number(t) === liveTime) live = { time: liveTime, levels };
    else footprints[t] = levels;
  }
  return { footprints, live };
}
