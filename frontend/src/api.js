const BASE = '/api';

export async function fetchSymbols() {
  const res = await fetch(`${BASE}/symbols`);
  const { symbols } = await res.json();
  return symbols;
}

export async function fetchOHLCV(symbol, interval) {
  const res = await fetch(`${BASE}/ohlcv?symbol=${encodeURIComponent(symbol)}&interval=${encodeURIComponent(interval)}`);
  if (!res.ok) throw new Error('No data');
  return res.json();
}
