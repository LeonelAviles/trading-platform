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
