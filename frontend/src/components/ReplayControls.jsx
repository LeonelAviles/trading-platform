const SPEEDS = [
  ['0.5x', 2000],
  ['1x', 1000],
  ['2x', 500],
  ['5x', 200],
  ['10x', 100],
];

export default function ReplayControls({ replay, total, onPlayPause, onStep, onSpeed, onExit }) {
  if (replay.phase === 'select') {
    return (
      <div className="replay-bar">
        <span className="replay-hint">Click a bar on the chart to start replay from there</span>
        <button className="replay-btn" onClick={onExit}>Cancel</button>
      </div>
    );
  }

  const atEnd = replay.idx >= total - 1;
  return (
    <div className="replay-bar">
      <button className="replay-btn" title={replay.playing ? 'Pause' : 'Play'} onClick={onPlayPause} disabled={atEnd}>
        {replay.playing ? (
          <svg viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="5" width="4" height="14" /><rect x="14" y="5" width="4" height="14" /></svg>
        ) : (
          <svg viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z" /></svg>
        )}
      </button>
      <button className="replay-btn" title="Step forward one bar" onClick={onStep} disabled={atEnd}>
        <svg viewBox="0 0 24 24" fill="currentColor"><path d="M6 5v14l8-7z" /><rect x="16" y="5" width="2.5" height="14" /></svg>
      </button>
      <select
        className="replay-speed"
        value={replay.speed}
        onChange={(e) => onSpeed(Number(e.target.value))}
        title="Replay speed"
      >
        {SPEEDS.map(([label, ms]) => (
          <option key={ms} value={ms}>{label}</option>
        ))}
      </select>
      <span className="replay-pos">{replay.idx + 1} / {total}{atEnd ? ' · end' : ''}</span>
      <button className="replay-btn replay-exit" title="Exit replay" onClick={onExit}>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M6 6l12 12M18 6L6 18" /></svg>
      </button>
    </div>
  );
}
