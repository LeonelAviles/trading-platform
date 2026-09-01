import { useEffect, useRef, useState } from 'react';
import { fetchFootprint } from '../api';

const DAY = 86400;
// The server caps one footprint request at 2 days, and cells thinner than a
// couple of pixels are unreadable anyway — bound one sweep to the most recent
// visible days rather than hammering the store for a months-wide view.
const MAX_DAYS_PER_SWEEP = 14;

// Footprint levels for the visible range, fetched day by day at the chart's
// own interval (the server aggregates) and cached per symbol:interval so
// panning only fetches days it hasn't seen. `excludeFrom` (unix seconds)
// keeps the replay session's day out — the replay socket owns that data.
// Returns { [barTime]: levels }.
export function useFootprintHistory(symbol, interval, enabled, view, excludeFrom = null) {
  const cacheRef = useRef({ key: null, days: new Set(), byTime: {} });
  const [byTime, setByTime] = useState({});

  useEffect(() => {
    if (!enabled || !view) return undefined;
    const key = `${symbol}:${interval}`;
    if (cacheRef.current.key !== key) {
      cacheRef.current = { key, days: new Set(), byTime: {} };
      setByTime({});
    }
    const cache = cacheRef.current;
    let cancelled = false;
    const t = setTimeout(async () => {
      let fromDay = Math.floor(view.from / DAY);
      let toDay = Math.floor(view.to / DAY);
      if (excludeFrom != null) toDay = Math.min(toDay, Math.floor((excludeFrom - 1) / DAY));
      fromDay = Math.max(fromDay, toDay - MAX_DAYS_PER_SWEEP + 1);
      for (let d = toDay; d >= fromDay; d--) {
        if (cache.days.has(d)) continue;
        try {
          const fp = await fetchFootprint(symbol, interval, d * DAY, (d + 1) * DAY);
          if (cancelled || cacheRef.current !== cache) return;
          cache.days.add(d);
          for (const b of fp.bars || []) cache.byTime[b.time] = b.levels;
          setByTime({ ...cache.byTime });
        } catch {
          return; // leave the day unmarked; a later pan retries
        }
      }
    }, 300);
    return () => { cancelled = true; clearTimeout(t); };
  }, [symbol, interval, enabled, view, excludeFrom]);

  return byTime;
}
