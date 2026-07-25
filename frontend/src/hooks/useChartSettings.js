import { useEffect, useState } from 'react';

export const DEFAULT_SETTINGS = {
  upColor: '#26a69a', downColor: '#ef5350',
  borderVisible: true, borderUpColor: '#26a69a', borderDownColor: '#ef5350',
  wickVisible: true, wickUpColor: '#26a69a', wickDownColor: '#ef5350',
  background: '#0e0f14',
  vertGridVisible: true, horzGridVisible: true, gridColor: '#1a1d29',
};

const KEY = 'chartSettings';

// Global chart appearance (candle colors, grid, background) — one shared
// preference across symbols, unlike drawings which are saved per symbol.
export function useChartSettings() {
  const [settings, setSettings] = useState(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(KEY));
      return saved ? { ...DEFAULT_SETTINGS, ...saved } : DEFAULT_SETTINGS;
    } catch {
      return DEFAULT_SETTINGS;
    }
  });

  useEffect(() => {
    localStorage.setItem(KEY, JSON.stringify(settings));
  }, [settings]);

  return [settings, setSettings];
}
