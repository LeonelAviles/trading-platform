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


// --- agent runs (Phase 4) ---

export async function fetchAgentRuns() {
  return json(await fetch(`${BASE}/agent/runs`));
}

export async function fetchAgentRun(id) {
  return json(await fetch(`${BASE}/agent/runs/${id}`));
}

// { kind: 'generate', prompt, symbol?, direction?, name?, interval?, risk? }
export async function startAgentRun(body) {
  return json(await fetch(`${BASE}/agent/runs`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  }));
}

export async function answerAgentRun(id, text) {
  return json(await fetch(`${BASE}/agent/runs/${id}/answer`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text }),
  }));
}

export async function cancelAgentRun(id) {
  return json(await fetch(`${BASE}/agent/runs/${id}/cancel`, { method: 'POST' }));
}

// Live event feed for one run. Calls onEvent(event) for each message; returns a close() fn.
export function subscribeAgentRun(id, onEvent) {
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
  const ws = new WebSocket(`${proto}://${window.location.host}/ws/agent/${id}`);
  ws.onmessage = (m) => { try { onEvent(JSON.parse(m.data)); } catch { /* ignore */ } };
  return () => ws.close();
}

// --- research / knowledge / usage ---

export async function fetchResearchQueue() { return json(await fetch(`${BASE}/research/queue`)); }
export async function addResearchTopic(topic) {
  return json(await fetch(`${BASE}/research/queue`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ topic }) }));
}
export async function runResearch(maxTopics = 1) {
  return json(await fetch(`${BASE}/research/run`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ maxTopics }) }));
}
export async function fetchResearchStatus() { return json(await fetch(`${BASE}/research/status`)); }
export async function fetchResearchSources() { return json(await fetch(`${BASE}/research/sources`)); }
export async function fetchPrimitiveRequests() { return json(await fetch(`${BASE}/research/primitive-requests`)); }
export async function setPrimitiveRequestStatus(id, status) {
  return json(await fetch(`${BASE}/research/primitive-requests/${id}/status`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status }) }));
}
export async function searchKnowledge(q) { return json(await fetch(`${BASE}/knowledge/search?q=${encodeURIComponent(q)}`)); }
export async function fetchUsage() { return json(await fetch(`${BASE}/usage`)); }
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

// Teaching mode (PLATFORM-SPEC.md Phase 6).
export async function fetchTeachingSessions() { return json(await fetch(`${BASE}/teaching/sessions`)); }
export async function createTeachingSession(symbol, dateFrom) {
  return json(await fetch(`${BASE}/teaching/sessions`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ symbol, dateFrom }),
  }));
}
export async function fetchTeachingSession(id) { return json(await fetch(`${BASE}/teaching/sessions/${id}`)); }
export async function endTeachingSession(id, notes) {
  return json(await fetch(`${BASE}/teaching/sessions/${id}/end`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ notes }),
  }));
}
export async function compileTeachingSession(id) {
  return json(await fetch(`${BASE}/teaching/sessions/${id}/compile`, { method: 'POST' }));
}
export async function labelTeachingEntry(id, entryTime, label, reason) {
  return json(await fetch(`${BASE}/teaching/sessions/${id}/labels`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ entryTime, label, reason }),
  }));
}
export async function pickTeachingStrategy(id, strategyId) {
  return json(await fetch(`${BASE}/teaching/sessions/${id}/pick`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ strategyId }),
  }));
}
export async function fetchTeachingSnapshot(id, key) { return json(await fetch(`${BASE}/teaching/sessions/${id}/snapshots/${key}`)); }

// --- Phase 7: desk, packages, compare ---------------------------------------
export async function fetchDesk() { return json(await fetch(`${BASE}/desk`)); }
export function strategyPackageUrl(id) { return `${BASE}/strategies/${id}/package`; }
export async function importStrategyPackage(file, { keepId = true } = {}) {
  return json(await fetch(`${BASE}/strategies/import?keepId=${keepId}`, {
    method: 'POST', headers: { 'Content-Type': 'application/zip' }, body: file,
  }));
}
export async function forwardTestStrategy(id) {
  return json(await fetch(`${BASE}/strategies/${id}/forward-test`, { method: 'POST' }));
}
export async function compareStrategies(a, b, window = 'is') {
  return json(await fetch(`${BASE}/strategies/${a}/compare/${b}?window=${window}`));
}

// --- Research: owner sources, self-study, trusted domains -------------------
export async function fetchResearchSettings() { return json(await fetch(`${BASE}/research/settings`)); }
export async function putResearchSettings(values) {
  return json(await fetch(`${BASE}/research/settings`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(values),
  }));
}
export async function fetchAutorun() { return json(await fetch(`${BASE}/research/autorun`)); }
export async function tickAutorun() { return json(await fetch(`${BASE}/research/autorun/tick`, { method: 'POST' })); }
export async function addResearchSource(body) {
  return json(await fetch(`${BASE}/research/sources`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  }));
}
export async function uploadResearchSource(file, { title, topic } = {}) {
  const params = new URLSearchParams({ filename: file.name });
  if (title) params.set('title', title);
  if (topic) params.set('topic', topic);
  return json(await fetch(`${BASE}/research/sources/upload?${params}`, {
    method: 'POST', headers: { 'Content-Type': file.type || 'application/octet-stream' }, body: file,
  }));
}

// --- Knowledge graph --------------------------------------------------------
export async function fetchKnowledgeGraph({ minCredibility = 0, kinds = [], tiers = [], sources = true } = {}) {
  const p = new URLSearchParams({ min_credibility: String(minCredibility), sources: String(sources) });
  if (kinds.length) p.set('kinds', kinds.join(','));
  if (tiers.length) p.set('tiers', tiers.join(','));
  return json(await fetch(`${BASE}/knowledge/graph?${p}`));
}
export async function fetchKnowledgeNode(id) { return json(await fetch(`${BASE}/knowledge/graph/node/${encodeURIComponent(id)}`)); }
export async function fetchKnowledgeFacts(ids) { return json(await fetch(`${BASE}/knowledge/facts?ids=${encodeURIComponent(ids.join(','))}`)); }
