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
