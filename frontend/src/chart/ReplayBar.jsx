import { useState } from 'react';
import { REPLAY_SPEEDS, formatEtClock, etToUnix, etDateString } from './time';

// Event-time replay controls (PLATFORM-SPEC.md Phase 5 task 3): play/pause,
// speed 0.25–100×, step tick / bar, jump-to-timestamp in ET, ET clock,
// "book approximate" badge above 25×.
export default function ReplayBar({ replay, onPlayPause, onSpeed, onStep, onSeek, onExit }) {
  const clockS = replay.clock != null ? replay.clock / 1e9 : null;
  const [jump, setJump] = useState('10:15:00');
  const dateStr = clockS != null ? etDateString(clockS) : replay.date;
  const busy = replay.status !== 'ready';

  const submitJump = (e) => {
    e.preventDefault();
    if (!dateStr || !jump) return;
    onSeek(etToUnix(dateStr, jump.length === 5 ? `${jump}:00` : jump));
  };

  return (
    <div className="replay-bar replay-bar-wide">
      <button className="replay-btn" title={replay.paused ? 'Play' : 'Pause'} onClick={onPlayPause} disabled={busy || replay.ended}>
        {replay.paused ? (
          <svg viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z" /></svg>
        ) : (
          <svg viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="5" width="4" height="14" /><rect x="14" y="5" width="4" height="14" /></svg>
        )}
      </button>
      <button className="replay-btn" title="Step one print" onClick={() => onStep('tick')} disabled={busy}>
        <svg viewBox="0 0 24 24" fill="currentColor"><path d="M6 5v14l8-7z" /><rect x="16" y="5" width="2.5" height="14" /></svg>
      </button>
      <button className="replay-btn replay-btn-text" title="Step one bar" onClick={() => onStep('bar')} disabled={busy}>bar ›</button>
      <select className="replay-speed" value={replay.speed} onChange={(e) => onSpeed(Number(e.target.value))} title="Speed" disabled={busy}>
        {REPLAY_SPEEDS.map((s) => <option key={s} value={s}>{s}×</option>)}
      </select>
      <span className="replay-clock mono" title={dateStr}>{formatEtClock(clockS)} ET</span>
      {replay.bookMode === 'approx' && <span className="replay-badge" title="Above 25× (or without the day cached) the ladder shows 60-second checkpoints">book approximate</span>}
      {replay.bookMode === 'off' && <span className="replay-badge muted">book off</span>}
      {replay.ended && <span className="replay-badge muted">end of day</span>}
      <form className="replay-jump" onSubmit={submitJump}>
        <input type="time" step="1" value={jump} onChange={(e) => setJump(e.target.value)} title="Jump to ET time" />
        <button className="replay-btn replay-btn-text" type="submit" disabled={busy}>go</button>
      </form>
      <button className="replay-btn replay-exit" title="Stop replay" onClick={onExit}>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M6 6l12 12M18 6L6 18" /></svg>
      </button>
    </div>
  );
}
