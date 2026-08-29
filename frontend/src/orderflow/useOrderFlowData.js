import { useEffect, useRef, useState } from 'react';
import { fetchDomHeatmap } from '../api';

const DEBOUNCE_MS = 280;
const MAX_HEATMAP_SPAN_SECONDS = 6 * 3600;
const RANGE_PADDING_FRAC = 0.1;
const HEATMAP_DATA_REVISION = 4;

function covers(cached, symbol, from, to, priceRange) {
  return cached?.revision === HEATMAP_DATA_REVISION
    && cached.symbol === symbol
    && cached.start <= from
    && cached.end >= to
    && (priceRange == null || (
      cached.minPrice != null
      && cached.maxPrice != null
      && cached.minPrice <= priceRange.min
      && cached.maxPrice >= priceRange.max
    ));
}

function paddedRange(from, to) {
  const span = to - from;
  const pad = Math.min(
    span * RANGE_PADDING_FRAC,
    Math.max(0, MAX_HEATMAP_SPAN_SECONDS - span) / 2,
  );
  return { start: from - pad, end: to + pad };
}

function paddedPriceRange(range) {
  if (!range) return null;
  const span = range.max - range.min;
  const pad = span * RANGE_PADDING_FRAC;
  return { min: range.min - pad, max: range.max + pad };
}

// Fetches persistent resting-liquidity snapshots for the visible time and
// price window. Padded time coverage avoids refetching after a small pan;
// price-scale changes deliberately refetch because the server returns every
// active price level inside the visible vertical range.
export default function useOrderFlowData(symbol, enabled, visibleRange, visiblePriceRange) {
  const [heatmapData, setHeatmapData] = useState(null);
  const [heatmapLoading, setHeatmapLoading] = useState(false);
  const [retryToken, setRetryToken] = useState(0);
  const coverageRef = useRef(null);
  const inFlightRef = useRef(null);
  const visibleFrom = visibleRange?.from;
  const visibleTo = visibleRange?.to;
  const visibleMinPrice = visiblePriceRange?.min;
  const visibleMaxPrice = visiblePriceRange?.max;

  const tooWideForHeatmap = visibleFrom != null
    && visibleTo - visibleFrom > MAX_HEATMAP_SPAN_SECONDS;

  useEffect(() => {
    if (!enabled || !symbol || visibleFrom == null || visibleTo == null) {
      setHeatmapData(null);
      setHeatmapLoading(false);
      coverageRef.current = null;
      return undefined;
    }

    const span = visibleTo - visibleFrom;
    const visiblePrices = visibleMinPrice != null && visibleMaxPrice != null
      ? { min: visibleMinPrice, max: visibleMaxPrice }
      : null;
    if (span <= 0 || span > MAX_HEATMAP_SPAN_SECONDS) {
      setHeatmapData(null);
      setHeatmapLoading(false);
      coverageRef.current = null;
      return undefined;
    }
    if (covers(coverageRef.current, symbol, visibleFrom, visibleTo, visiblePrices)) {
      return undefined;
    }

    let cancelled = false;
    let retryTimer;
    const timer = setTimeout(() => {
      setHeatmapLoading(true);
      // Keep one request active so rapid pan/zoom changes cannot deliver stale
      // coverage after the latest viewport request.
      if (inFlightRef.current) {
        retryTimer = setTimeout(() => setRetryToken((value) => value + 1), 500);
        return;
      }
      const range = paddedRange(visibleFrom, visibleTo);
      const priceRange = paddedPriceRange(visiblePrices);
      const request = fetchDomHeatmap(symbol, range.start, range.end, {
        minPrice: priceRange?.min,
        maxPrice: priceRange?.max,
      });
      inFlightRef.current = request;
      request
        .then((data) => {
          if (cancelled) return;
          coverageRef.current = {
            revision: HEATMAP_DATA_REVISION,
            symbol,
            ...range,
            minPrice: priceRange?.min,
            maxPrice: priceRange?.max,
          };
          setHeatmapData(data);
          setHeatmapLoading(false);
        })
        .catch(() => {
          if (cancelled) return;
          coverageRef.current = null;
          setHeatmapData(null);
          setHeatmapLoading(false);
          // A transient backend failure used to leave the overlay blank until
          // the user panned or refreshed. Retry after the failed request has
          // fully settled, so synchronous backend work cannot pile up.
          retryTimer = setTimeout(() => setRetryToken((value) => value + 1), 2000);
        })
        .finally(() => {
          if (inFlightRef.current === request) inFlightRef.current = null;
          // If the viewport changed while this request was running, trigger
          // exactly one new effect for the latest range.
          if (cancelled) setRetryToken((value) => value + 1);
        });
    }, DEBOUNCE_MS);

    return () => {
      cancelled = true;
      clearTimeout(timer);
      clearTimeout(retryTimer);
    };
  }, [
    symbol,
    enabled,
    visibleFrom,
    visibleTo,
    visibleMinPrice,
    visibleMaxPrice,
    retryToken,
  ]);

  return { heatmapData, heatmapLoading, tooWideForHeatmap };
}
