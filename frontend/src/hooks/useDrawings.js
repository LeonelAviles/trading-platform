import { useEffect, useState } from 'react';

function storageKey(symbol) {
  return `drawings:${symbol}`;
}

function load(symbol) {
  try {
    const raw = localStorage.getItem(storageKey(symbol));
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

// Drawings are keyed by symbol and persisted to localStorage, so switching
// symbols or reloading the page doesn't lose a chart's annotations.
export function useDrawings(symbol) {
  const [shapes, setShapes] = useState(() => load(symbol));

  useEffect(() => {
    setShapes(load(symbol));
  }, [symbol]);

  useEffect(() => {
    if (!symbol) return;
    localStorage.setItem(storageKey(symbol), JSON.stringify(shapes));
  }, [symbol, shapes]);

  return [shapes, setShapes];
}
