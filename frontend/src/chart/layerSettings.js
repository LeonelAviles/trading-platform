import { useEffect, useState } from 'react';

// Thresholds for the order-flow layers (PLATFORM-SPEC.md Phase 5, task 4/5).
// One shared preference like chart settings, persisted in localStorage.
export const DEFAULT_LAYER_SETTINGS = {
  footprintRatio: 3.0,
  footprintMinVolume: 5,
  stackedMin: 3,
  bubbleMinDelta: 15,
  bubbleFade: false,
  bubbleFadeSeconds: 30,
  tapeLargePrint: 50,
  tapeRows: 300,
  ladderDepth: 20,
  profileMode: 'session',   // 'session' | 'visible'
  profileWidth: 110,
};

const KEY = 'layerSettings';

export function useLayerSettings() {
  const [settings, setSettings] = useState(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(KEY));
      return saved ? { ...DEFAULT_LAYER_SETTINGS, ...saved } : DEFAULT_LAYER_SETTINGS;
    } catch {
      return DEFAULT_LAYER_SETTINGS;
    }
  });
  useEffect(() => {
    try { localStorage.setItem(KEY, JSON.stringify(settings)); } catch { /* quota */ }
  }, [settings]);
  return [settings, setSettings];
}

// Which layers are switched on — per page, persisted separately.
export const DEFAULT_LAYERS = {
  heatmap: false, footprint: false, bubbles: false, profile: false, ladder: true, tape: false, cvd: true,
};

export function useLayerToggles(key = 'chartLayers') {
  const [layers, setLayers] = useState(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(key));
      return saved ? { ...DEFAULT_LAYERS, ...saved } : DEFAULT_LAYERS;
    } catch {
      return DEFAULT_LAYERS;
    }
  });
  useEffect(() => {
    try { localStorage.setItem(key, JSON.stringify(layers)); } catch { /* quota */ }
  }, [key, layers]);
  const toggle = (name) => setLayers((l) => ({ ...l, [name]: !l[name] }));
  return [layers, toggle, setLayers];
}
