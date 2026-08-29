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

// --- strategies ---

export async function fetchStrategies() {
  return json(await fetch(`${BASE}/strategies`));
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
// 'preparing') job — the review route polls it from there.
export async function createBacktest(strategyId) {
  return json(await fetch(`${BASE}/backtests`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ strategyId }),
  }));
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
