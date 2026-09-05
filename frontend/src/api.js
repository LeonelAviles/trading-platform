const BASE = '/api';

async function json(res) {
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch { /* not json */ }
    throw new Error(detail);
  }
  return res.json();
}

export async function fetchSymbols() {
  const { symbols } = await json(await fetch(`${BASE}/symbols`));
  return symbols;
}

// `start`/`end` (unix seconds) are pushed all the way down into the SQL that
// aggregates the ticks, so a bounded request is genuinely cheaper rather than
// just smaller — see the chart's two-phase load in CandlestickPage.
export async function fetchOHLCV(symbol, interval, start, end) {
  const bounds = (start != null ? `&start=${Math.floor(start)}` : '')
    + (end != null ? `&end=${Math.ceil(end)}` : '');
  return json(await fetch(`${BASE}/ohlcv?symbol=${encodeURIComponent(symbol)}&interval=${encodeURIComponent(interval)}${bounds}`));
}

// { start, end } unix seconds bounding the symbol's available data.
export async function fetchRange(symbol) {
  return json(await fetch(`${BASE}/range?symbol=${encodeURIComponent(symbol)}`));
}

export async function fetchCVD(symbol, interval) {
  return json(await fetch(`${BASE}/cvd?symbol=${encodeURIComponent(symbol)}&interval=${encodeURIComponent(interval)}`));
}

// Time-bucketed resting-book depth in [start, end] unix seconds — the
// order-flow heatmap overlay's data source.
export async function fetchDomHeatmap(symbol, start, end, {
  depth, minPrice, maxPrice, signal,
} = {}) {
  const depthParam = depth != null ? `&depth=${depth}` : '';
  const priceParams = minPrice != null && maxPrice != null
    ? `&min_price=${encodeURIComponent(minPrice)}&max_price=${encodeURIComponent(maxPrice)}`
    : '';
  return json(await fetch(
    `${BASE}/dom-heatmap?symbol=${encodeURIComponent(symbol)}&start=${Math.floor(start)}&end=${Math.ceil(end)}${depthParam}${priceParams}`,
    { signal },
  ));
}

// --- instruments / data coverage (Phase 1) ---

export async function fetchInstruments() {
  return json(await fetch(`${BASE}/instruments`));
}

export async function fetchDataCoverage() {
  return json(await fetch(`${BASE}/data/coverage`));
}

// Prints in [start, end) unix seconds.
export async function fetchTrades(symbol, start, end, { minSize, limit, signal } = {}) {
  const extra = (minSize != null ? `&min_size=${minSize}` : '') + (limit != null ? `&limit=${limit}` : '');
  return json(await fetch(
    `${BASE}/trades?symbol=${encodeURIComponent(symbol)}&start=${Math.floor(start)}&end=${Math.ceil(end)}${extra}`,
    { signal },
  ));
}

// Per-bar bid×ask volume ladders for the footprint layer.
export async function fetchFootprint(symbol, tf, start, end, { signal } = {}) {
  return json(await fetch(
    `${BASE}/footprint?symbol=${encodeURIComponent(symbol)}&tf=${encodeURIComponent(tf)}&start=${Math.floor(start)}&end=${Math.ceil(end)}`,
    { signal },
  ));
}

export async function fetchVolumeProfile(symbol, start, end, { bins, signal } = {}) {
  const b = bins != null ? `&bins=${bins}` : '';
  return json(await fetch(
    `${BASE}/volume-profile?symbol=${encodeURIComponent(symbol)}&start=${Math.floor(start)}&end=${Math.ceil(end)}${b}`,
    { signal },
  ));
}

// `date` is the New York session date, YYYY-MM-DD.
export async function fetchSessionLevels(symbol, date) {
  return json(await fetch(`${BASE}/session-levels?symbol=${encodeURIComponent(symbol)}&date=${date}`));
}

// --- strategies ---

export async function fetchStrategies() {
  return json(await fetch(`${BASE}/strategies`));
}

export async function fetchStrategy(id) {
  return json(await fetch(`${BASE}/strategies/${id}`));
}

export async function fetchStrategyLineage(id) {
  return json(await fetch(`${BASE}/strategies/${id}/lineage`));
}

export async function fetchSpecSchema() {
  return json(await fetch(`${BASE}/strategies/schema/spec`));
}

// --- backtests ---

export async function fetchBacktests() {
  return json(await fetch(`${BASE}/backtests`));
}

export async function fetchBacktest(id) {
  return json(await fetch(`${BASE}/backtests/${id}`));
}

// Kicks off an engine run for a strategy and returns the (initially
// 'queued') job — the review route polls it from there.
// options: { mode: 'bars'|'ticks'|'l3', windowKind: 'is'|'wf1'|'wf2'|'wf3'|'oos'|'full' }
export async function createBacktest(strategyId, options = {}) {
  return json(await fetch(`${BASE}/backtests`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ strategyId, ...options }),
  }));
}

// Queues IS + WF1–3 for a strategy (never OOS); returns the jobs.
export async function createValidation(strategyId, mode) {
  return json(await fetch(`${BASE}/backtests/validate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ strategyId, mode }),
  }));
}

export async function fetchBacktestAnalytics(id) {
  return json(await fetch(`${BASE}/backtests/${id}/analytics`));
}

// IS / WF / OOS (hidden until finalize) / Monte Carlo / DSR / verdict.
export async function fetchBacktestValidation(id) {
  return json(await fetch(`${BASE}/backtests/${id}/validation`));
}

// --- settings ---

export async function fetchSettings() { return json(await fetch(`${BASE}/settings`)); }
export async function putSettings(values) {
  return json(await fetch(`${BASE}/settings`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(values) }));
}

// Replay cache (PLATFORM-SPEC.md §4.11).
export async function fetchReplayCache() { return json(await fetch(`${BASE}/data/replay-cache`)); }
export async function warmReplayDay(root, date) {
  return json(await fetch(`${BASE}/data/replay-cache/warm`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ root, date }),
  }));
}
export function replaySocketUrl() {
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
  return `${proto}://${window.location.host}/ws/replay`;
}

// --- Phase 7: desk, packages, compare ---------------------------------------
export async function fetchDesk() { return json(await fetch(`${BASE}/desk`)); }
export function strategyPackageUrl(id) { return `${BASE}/strategies/${id}/package`; }
export async function compareStrategies(a, b, window = 'is') {
  return json(await fetch(`${BASE}/strategies/${a}/compare/${b}?window=${window}`));
}

