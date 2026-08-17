import { dataToPx, timeToLogical } from './geometry';

// Read-only rendering of one backtest trade: profit/loss zones between
// entry and stop/target (same visual language as the manual position tool),
// entry→exit line, and an exit marker. Trades are anchored in real time
// (unix seconds), so they land on the right bars at any chart interval.
export default function TradeShape({ trade, chart, series, bars, intervalSeconds }) {
  if (!bars.length) return null;

  const toLogical = (t) => {
    const l = timeToLogical(bars, intervalSeconds, t);
    return l == null ? null : Math.round(l);
  };
  const entryLogical = toLogical(trade.entryTime);
  const exitLogical = toLogical(trade.exitTime);
  if (entryLogical == null || exitLogical == null) return null;

  const entry = dataToPx(chart, series, entryLogical, trade.entryPrice);
  const exit = dataToPx(chart, series, exitLogical, trade.exitPrice);
  const stopPx = trade.stopPrice != null ? dataToPx(chart, series, entryLogical, trade.stopPrice) : { y: null };
  const targetPx = trade.targetPrice != null ? dataToPx(chart, series, entryLogical, trade.targetPrice) : { y: null };
  if (entry.x == null || exit.x == null) return null;

  const won = trade.pnl >= 0;
  const x1 = Math.min(entry.x, exit.x);
  const w = Math.max(2, Math.abs(exit.x - entry.x));

  return (
    <g pointerEvents="none" opacity={0.9}>
      {targetPx.y != null && (
        <rect x={x1} y={Math.min(entry.y, targetPx.y)} width={w} height={Math.abs(targetPx.y - entry.y)} fill="rgba(62,207,110,0.14)" />
      )}
      {stopPx.y != null && (
        <rect x={x1} y={Math.min(entry.y, stopPx.y)} width={w} height={Math.abs(stopPx.y - entry.y)} fill="rgba(239,68,68,0.14)" />
      )}
      <line x1={entry.x} y1={entry.y} x2={exit.x} y2={exit.y} stroke={won ? '#3ecf6e' : '#ef4444'} strokeWidth={1.5} strokeDasharray="5 3" />
      {/* entry marker: triangle pointing in trade direction */}
      {trade.direction === 'long' ? (
        <path d={`M ${entry.x - 5} ${entry.y + 7} L ${entry.x + 5} ${entry.y + 7} L ${entry.x} ${entry.y + 1} Z`} fill="#3ecf6e" />
      ) : (
        <path d={`M ${entry.x - 5} ${entry.y - 7} L ${entry.x + 5} ${entry.y - 7} L ${entry.x} ${entry.y - 1} Z`} fill="#ef4444" />
      )}
      {/* exit marker */}
      <circle cx={exit.x} cy={exit.y} r={3.5} fill={won ? '#3ecf6e' : '#ef4444'} stroke="#0a0a0c" strokeWidth={1} />
      <text x={exit.x + 6} y={exit.y + 4} fontSize="10" fontFamily="monospace" fill={won ? '#3ecf6e' : '#ef4444'}>
        {`${trade.pnl >= 0 ? '+' : ''}${trade.pnl}`}
      </text>
    </g>
  );
}
