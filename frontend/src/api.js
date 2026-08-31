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

export async function fetchDom(symbol, asOf) {
  const asOfParam = asOf != null ? `&as_of=${Math.round(asOf)}` : '';
  return json(await fetch(`${BASE}/dom?symbol=${encodeURIComponent(symbol)}${asOfParam}`));
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

// Create (no id) or update (with id) a Strategy Spec v2; v1 documents are converted.
export async function saveStrategy(spec) {
  return json(await fetch(`${BASE}/strategies`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(spec),
  }));
}

// -> { valid, errors[], requiredMode }
export async function validateStrategy(spec) {
  return json(await fetch(`${BASE}/strategies/validate`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(spec),
  }));
}

export async function fetchStrategyLineage(id) {
  return json(await fetch(`${BASE}/strategies/${id}/lineage`));
}

export async function patchStrategyRisk(id, risk) {
  return json(await fetch(`${BASE}/strategies/${id}/risk`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(risk),
  }));
}

export async function setStrategyStatus(id, status) {
  return json(await fetch(`${BASE}/strategies/${id}/status`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status }),
  }));
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

export async function deleteBacktest(id) {
  return json(await fetch(`${BASE}/backtests/${id}`, { method: 'DELETE' }));
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

// --- assistant chat ---

export async function fetchChatStatus() {
  return json(await fetch(`${BASE}/chat/status`));
}

// Streams the assistant reply over SSE. Calls handlers as events arrive:
//   onDelta(text)  — a piece of assistant text
//   onTool(name)   — a backend tool call is running
//   onError(message)
// Resolves when the stream ends.
export async function streamChat(messages, context, { onDelta, onTool, onError }) {
  const res = await fetch(`${BASE}/chat/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ messages, context }),
  });
  if (!res.ok || !res.body) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch { /* not json */ }
    throw new Error(detail);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    // SSE events are separated by a blank line; keep the trailing partial.
    const events = buf.split('\n\n');
    buf = events.pop();
    for (const raw of events) {
      const line = raw.split('\n').find((l) => l.startsWith('data: '));
      if (!line) continue;
      let event;
      try { event = JSON.parse(line.slice(6)); } catch { continue; }
      if (event.type === 'delta') onDelta?.(event.text);
      else if (event.type === 'tool') onTool?.(event.name);
      else if (event.type === 'error') onError?.(event.message);
    }
  }
}
