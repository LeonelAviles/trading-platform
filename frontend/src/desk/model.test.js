import { describe, expect, it } from "vitest";
import {
  candlesForChart,
  cohortKey,
  cohorts,
  isES,
  leaders,
  metrics,
  tradeMarkers,
} from "./model";

const strategies = [
  { id: "s1", instrument: { root: "ES", symbol: "ES1!" } },
  { id: "s2", symbol: "ESM6" },
  { id: "nq", instrument: { root: "NQ", symbol: "NQ1!" } },
];
const run = (id, strategyId, extra = {}) => ({
  id,
  strategyId,
  status: "done",
  createdAt: "2026-06-01",
  dateFrom: "2026-01-01",
  dateTo: "2026-05-31",
  windowKind: "is",
  mode: "ticks",
  summary: { totalPnl: 100, winRate: 60, trades: 150 },
  ...extra,
});

describe("recorded desk evidence", () => {
  it("preserves zero while unavailable and non-finite metrics stay unavailable", () => {
    expect(
      metrics({
        metrics: { netPnl: 0, profitFactor: null, maxDrawdownPct: Infinity },
        summary: { totalPnl: 99, trades: 0 },
      }),
    ).toMatchObject({
      netPnl: 0,
      trades: 0,
      profitFactor: null,
      maxDrawdownPct: null,
      winRate: null,
    });
  });
  it("limits the current desk to ES, excluding micros and NQ", () => {
    expect(strategies.map(isES)).toEqual([true, true, false]);
    expect(isES({ symbol: "MES1!" })).toBe(false);
  });
  it("never mixes windows, modes, unfinished jobs or instruments in leaders", () => {
    const good = run("a", "s1");
    const others = [
      run("b", "s2", { mode: "bars" }),
      run("c", "s2", { windowKind: "oos" }),
      run("d", "s2", { dateTo: "2026-06-30" }),
      run("e", "s2", { status: "running" }),
      run("f", "nq"),
    ];
    const result = leaders(strategies, [good, ...others], cohortKey(good), 100);
    expect(result.eligible).toBe(1);
    expect(result.profitability.id).toBe("a");
  });
  it("uses the latest completed result rather than cherry-picking an earlier qualifying run", () => {
    const older = run("a", "s1");
    const newer = run("b", "s1", {
      createdAt: "2026-07-01",
      summary: { totalPnl: -50, trades: 10, winRate: 30 },
    });
    expect(
      leaders(strategies, [older, newer], cohortKey(older), 100).profitability,
    ).toBeNull();
    expect(
      leaders(strategies, [older, newer], cohortKey(older), 1).profitability.id,
    ).toBe("b");
  });
  it("does not invent winners when history or metrics are absent", () => {
    expect(leaders(strategies, [], null, 100).winRate).toBeNull();
    expect(cohorts([run("a", "s1", { dateFrom: null })], strategies)).toEqual(
      [],
    );
    expect(
      leaders(
        strategies,
        [run("a", "s1", { summary: { trades: 120 } })],
        cohortKey(run("a", "s1")),
        100,
      ).profitability,
    ).toBeNull();
  });
});

describe("recorded fill preview", () => {
  const bars = [120, 180, 300].map((time) => ({
    time,
    open: 10,
    high: 12,
    low: 9,
    close: 11,
  }));
  it("sorts and deduplicates bars, excluding malformed candle data", () => {
    expect(
      candlesForChart([bars[1], bars[0], bars[1], { ...bars[2], close: null }]),
    ).toEqual(bars.slice(0, 2));
  });
  it("uses real fill prices and containing candle times", () => {
    const markers = tradeMarkers(
      {
        direction: "long",
        entryTime: 132,
        exitTime: 191,
        entryPrice: 10.25,
        exitPrice: 11.5,
      },
      bars,
    );
    expect(markers.map((m) => m.time)).toEqual([120, 180]);
    expect(markers.map((m) => m.text)).toEqual(["Entry 10.25", "Exit 11.50"]);
    expect(markers[0].position).toBe("belowBar");
  });
  it("does not attach fills in missing bars or beyond the loaded window to unrelated candles", () => {
    expect(
      tradeMarkers(
        { entryTime: 250, exitTime: 400, entryPrice: 10, exitPrice: 11 },
        bars,
      ),
    ).toEqual([]);
  });
});
