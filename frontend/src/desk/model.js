import { intervalToSeconds } from "../drawing/geometry";

// Desk comparisons describe recorded results, never a new validation or prop assessment.
export const finite = (value) =>
  typeof value === "number" && Number.isFinite(value) ? value : null;
export function metrics(run) {
  const m = run?.metrics || {};
  return {
    netPnl: finite(m.netPnl) ?? finite(run?.summary?.totalPnl),
    winRate: finite(m.winRate) ?? finite(run?.summary?.winRate),
    trades: finite(m.trades) ?? finite(run?.summary?.trades),
    profitFactor: finite(m.profitFactor),
    maxDrawdownPct: finite(m.maxDrawdownPct),
    expectancyR: finite(m.expectancyR),
  };
}
export function isES(strategy) {
  const symbol = strategy?.instrument?.symbol || strategy?.symbol || "";
  return (
    strategy?.instrument?.root === "ES" ||
    /^ES(?:1!|[HMUZ]\d{1,2})?$/.test(symbol)
  );
}
export function orderedRuns(runs) {
  return [...runs].sort(
    (a, b) =>
      String(b.createdAt || "").localeCompare(String(a.createdAt || "")) ||
      String(b.id).localeCompare(String(a.id)),
  );
}
export function cohortKey(run) {
  if (!run?.dateFrom || !run?.dateTo || !run?.windowKind || !run?.mode)
    return null;
  return JSON.stringify([run.dateFrom, run.dateTo, run.windowKind, run.mode]);
}
export function cohortLabel(run) {
  return `${run.dateFrom} → ${run.dateTo} · ${run.windowKind?.toUpperCase()} · ${run.mode}`;
}
export function cohorts(runs, strategies) {
  const ids = new Set(strategies.filter(isES).map((s) => s.id));
  const result = new Map();
  for (const run of orderedRuns(runs)) {
    const key = cohortKey(run);
    if (
      key &&
      run.status === "done" &&
      ids.has(run.strategyId) &&
      !result.has(key)
    )
      result.set(key, { key, label: cohortLabel(run) });
  }
  return [...result.values()];
}
export function leaders(strategies, runs, key, minimumTrades) {
  const ids = new Set(strategies.filter(isES).map((s) => s.id));
  const eligible = orderedRuns(runs).filter(
    (run) =>
      run.status === "done" &&
      key &&
      cohortKey(run) === key &&
      ids.has(run.strategyId) &&
      (metrics(run).trades ?? -1) >= minimumTrades,
  );
  const top = (field) =>
    eligible
      .filter((r) => metrics(r)[field] !== null)
      .sort((a, b) => metrics(b)[field] - metrics(a)[field])[0] || null;
  return {
    profitability: top("netPnl"),
    winRate: top("winRate"),
    eligible: eligible.length,
  };
}
export function price(value) {
  return finite(value) === null
    ? "—"
    : value.toLocaleString(undefined, {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      });
}
export function money(value) {
  return finite(value) === null
    ? "—"
    : `${value >= 0 ? "+" : "−"}$${Math.abs(value).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}
export function number(value, digits = 2, suffix = "") {
  return finite(value) === null ? "—" : `${value.toFixed(digits)}${suffix}`;
}

export function candlesForChart(bars) {
  const byTime = new Map();
  for (const bar of Array.isArray(bars) ? bars : []) {
    if (
      ["time", "open", "high", "low", "close"].every(
        (k) => finite(bar?.[k]) !== null,
      )
    )
      byTime.set(bar.time, bar);
  }
  return [...byTime.values()].sort((a, b) => a.time - b.time);
}
export function fillWindow(trade, interval) {
  const seconds = intervalToSeconds(interval);
  const padding = Math.max(1800, seconds);
  // The backend clips on candle OPEN, with UTC-aligned bar buckets. Include
  // the containing bar even when the entry occurs late inside a 1h/4h/day bar.
  const start = Math.floor(trade.entryTime / seconds) * seconds - padding;
  const end = Math.min(
    Math.max(trade.exitTime || trade.entryTime, trade.entryTime) + padding,
    start + Math.max(86400, seconds * 3),
  );
  return { start, end };
}
export function tradeMarkers(trade, bars, intervalSeconds = 60) {
  if (!trade || !bars.length) return [];
  const marker = (time, entry) => {
    if (
      finite(time) === null ||
      time < bars[0].time ||
      time >= bars.at(-1).time + intervalSeconds
    )
      return null;
    const bar = bars.findLast((b) => b.time <= time);
    if (!bar || time >= bar.time + intervalSeconds) return null;
    const long = trade.direction === "long";
    return {
      time: bar.time,
      position: (entry ? long : !long) ? "belowBar" : "aboveBar",
      color: entry ? "#9ce6c3" : "#f0c583",
      shape: (entry ? long : !long) ? "arrowUp" : "arrowDown",
      text: `${entry ? "Entry" : "Exit"} ${price(entry ? trade.entryPrice : trade.exitPrice)}`,
    };
  };
  return [marker(trade.entryTime, true), marker(trade.exitTime, false)]
    .filter(Boolean)
    .sort((a, b) => a.time - b.time);
}
