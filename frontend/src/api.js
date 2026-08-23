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

export async function fetchOHLCV(symbol, interval) {
  return json(await fetch(`${BASE}/ohlcv?symbol=${encodeURIComponent(symbol)}&interval=${encodeURIComponent(interval)}`));
}

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

// --- strategies ---

export async function fetchStrategies() {
  return json(await fetch(`${BASE}/strategies`));
}

export async function saveStrategy(strategy) {
  return json(await fetch(`${BASE}/strategies`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(strategy),
  }));
}

export async function deleteStrategy(id) {
  return json(await fetch(`${BASE}/strategies/${id}`, { method: 'DELETE' }));
}

// Idea -> deterministic strategy (entry conditions, stop, target) via the
// agent. Returns { strategy, explanation } for direction long/short, or
// { directionGroup, long, short, explanation } for direction "both".
export async function generateStrategy(name, symbol, direction, prompt, interval) {
  return json(await fetch(`${BASE}/strategies/generate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, symbol, direction, prompt, interval }),
  }));
}

// --- backtests ---

export async function fetchEngineStatus() {
  return json(await fetch(`${BASE}/engine/status`));
}

export async function fetchBacktests() {
  return json(await fetch(`${BASE}/backtests`));
}

export async function fetchBacktest(id) {
  return json(await fetch(`${BASE}/backtests/${id}`));
}

export async function deleteBacktest(id) {
  return json(await fetch(`${BASE}/backtests/${id}`, { method: 'DELETE' }));
}

export async function fetchBacktestAnalytics(id) {
  return json(await fetch(`${BASE}/backtests/${id}/analytics`));
}

export async function fetchBacktestInsights(id) {
  return json(await fetch(`${BASE}/backtests/${id}/insights`, { method: 'POST' }));
}

export async function runBacktest(strategyId) {
  return json(await fetch(`${BASE}/backtests`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ strategyId }),
  }));
}

export async function runDemoBacktest(symbol) {
  return json(await fetch(`${BASE}/backtests`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ demo: true, symbol }),
  }));
}

// --- assistant chat ---

export async function fetchChatStatus() {
  return json(await fetch(`${BASE}/chat/status`));
}

export async function sendChat(messages, context) {
  return json(await fetch(`${BASE}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ messages, context }),
  }));
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
