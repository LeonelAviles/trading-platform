import { useEffect, useState } from 'react';

export const DEFAULT_SETTINGS = {
  upColor: '#3ecf6e', downColor: '#ef4444',
  borderVisible: true, borderUpColor: '#3ecf6e', borderDownColor: '#ef4444',
  wickVisible: true, wickUpColor: '#3ecf6e', wickDownColor: '#ef4444',
  background: '#0a0a0c',
  vertGridVisible: true, horzGridVisible: true, gridColor: '#17171b',
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
