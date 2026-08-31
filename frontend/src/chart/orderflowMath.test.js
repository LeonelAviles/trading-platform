import { describe, it, expect } from 'vitest';
import {
  aggregateBubbles, bubbleRadius, footprintImbalances, stackedRuns, valueArea, footprintFromTrades, percentile,
} from './orderflowMath';
import { etToUnix, formatEtClock, etOffsetMinutes } from './time';

describe('delta bubbles', () => {
  it('aggregates prints by 500 ms window and price', () => {
    const t0 = 1_781_270_940_000_000_000;
    const trades = [
      { ts: t0, price: 5300, size: 10, side: 'B' },
      { ts: t0 + 100e6, price: 5300, size: 4, side: 'A' },
      { ts: t0 + 200e6, price: 5300.25, size: 7, side: 'B' },
      { ts: t0 + 600e6, price: 5300, size: 5, side: 'B' },   // next window
    ];
    const b = aggregateBubbles(trades);
    expect(b).toHaveLength(3);
    expect(b[0]).toMatchObject({ price: 5300, netDelta: 6, volume: 14, prints: 2 });
    expect(b[1]).toMatchObject({ price: 5300.25, netDelta: 7 });
    expect(b[2]).toMatchObject({ price: 5300, netDelta: 5 });
  });
  it('clamps radius to [4, 26]', () => {
    expect(bubbleRadius(0)).toBe(4);
    expect(bubbleRadius(16)).toBeCloseTo(3 + 2.2 * 4);
    expect(bubbleRadius(10_000)).toBe(26);
  });
  it('percentile', () => {
    expect(percentile([5, 1, 3], 0.5)).toBe(3);
    expect(percentile([], 0.95)).toBe(0);
  });
});

describe('footprint', () => {
  const levels = [
    { price: 5300.00, bid: 20, ask: 2 },
    { price: 5300.25, bid: 3, ask: 30 },   // ask 30 vs bid below 20 -> not 3x
    { price: 5300.50, bid: 1, ask: 12 },   // ask 12 vs bid below 3 -> buy imbalance
    { price: 5300.75, bid: 0, ask: 9 },    // ask 9 vs bid below 1 -> buy imbalance
    { price: 5301.00, bid: 0, ask: 6 },    // ask 6 vs bid below 0 -> buy imbalance (min 5)
  ];
  it('marks diagonal imbalances', () => {
    const { buy, sell } = footprintImbalances(levels, { ratio: 3, minVolume: 5 });
    expect([...buy].sort()).toEqual([5300.5, 5300.75, 5301]);
    expect(sell.has(5300)).toBe(false);    // bid 20 vs ask above 30: 20 < 90
  });
  it('finds stacked runs of ≥3', () => {
    const { buy, sorted } = footprintImbalances(levels, { ratio: 3, minVolume: 5 });
    const runs = stackedRuns(sorted.map((l) => l.price), buy, 3);
    expect(runs).toEqual([[5300.5, 5300.75, 5301]]);
  });
  it('builds per-bar footprints from trades', () => {
    const t0 = 1_781_270_940;
    const fp = footprintFromTrades([
      { ts: t0 * 1e9, price: 5300, size: 3, side: 'B' },
      { ts: (t0 + 10) * 1e9, price: 5300, size: 2, side: 'A' },
      { ts: (t0 + 61) * 1e9, price: 5301, size: 1, side: 'A' },
    ], 60);
    expect(fp[t0]).toEqual([{ price: 5300, bid: 2, ask: 3 }]);
    expect(fp[t0 + 60]).toEqual([{ price: 5301, bid: 1, ask: 0 }]);
  });
});

describe('value area', () => {
  it('expands from the POC toward the larger neighbour', () => {
    const va = valueArea([[1, 10], [2, 50], [3, 30], [4, 5], [5, 5]], 0.7);
    expect(va.poc).toBe(2);
    expect(va.val).toBe(2);   // 50 + 30 already covers 80% -> the area never reaches bin 1
    expect(va.vah).toBe(3);
  });
});

describe('ET time', () => {
  it('converts an ET wall time to unix (EDT)', () => {
    // 2026-06-12 09:30 EDT = 13:30 UTC
    expect(etToUnix('2026-06-12', '09:30')).toBe(Date.UTC(2026, 5, 12, 13, 30) / 1000);
    expect(etOffsetMinutes(Date.UTC(2026, 5, 12, 13, 30) / 1000)).toBe(-240);
  });
  it('converts in EST too', () => {
    expect(etToUnix('2026-01-15', '09:30:05')).toBe(Date.UTC(2026, 0, 15, 14, 30, 5) / 1000);
    expect(formatEtClock(Date.UTC(2026, 0, 15, 14, 30, 5) / 1000)).toBe('09:30:05');
  });
});
