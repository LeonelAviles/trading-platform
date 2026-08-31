// ET <-> unix helpers for the replay controls. The chart, the session picker
// and the clock all speak America/New_York; the wire speaks unix ns.

const ET = 'America/New_York';

const partsFmt = new Intl.DateTimeFormat('en-US', {
  timeZone: ET, hourCycle: 'h23',
  year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit',
});

export function etParts(unixSeconds) {
  const p = {};
  for (const { type, value } of partsFmt.formatToParts(new Date(unixSeconds * 1000))) p[type] = value;
  return {
    year: Number(p.year), month: Number(p.month), day: Number(p.day),
    hour: Number(p.hour), minute: Number(p.minute), second: Number(p.second),
  };
}

// Offset (minutes) of ET from UTC at the given instant.
export function etOffsetMinutes(unixSeconds) {
  const p = etParts(unixSeconds);
  const asUtc = Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute, p.second) / 1000;
  return Math.round((asUtc - unixSeconds) / 60);
}

// 'YYYY-MM-DD' + 'HH:MM[:SS]' in ET -> unix seconds. Two passes settle the
// offset across a DST boundary.
export function etToUnix(dateStr, timeStr) {
  const [y, m, d] = dateStr.split('-').map(Number);
  const [hh, mm, ss = 0] = timeStr.split(':').map(Number);
  const naive = Date.UTC(y, m - 1, d, hh, mm, ss) / 1000;
  let guess = naive - etOffsetMinutes(naive) * 60;
  guess = naive - etOffsetMinutes(guess) * 60;
  return guess;
}

export function formatEtClock(unixSeconds, { seconds = true, date = false } = {}) {
  if (unixSeconds == null) return '—';
  const p = etParts(unixSeconds);
  const pad = (n) => String(n).padStart(2, '0');
  const t = `${pad(p.hour)}:${pad(p.minute)}${seconds ? `:${pad(p.second)}` : ''}`;
  return date ? `${p.year}-${pad(p.month)}-${pad(p.day)} ${t}` : t;
}

export function etDateString(unixSeconds) {
  const p = etParts(unixSeconds);
  const pad = (n) => String(n).padStart(2, '0');
  return `${p.year}-${pad(p.month)}-${pad(p.day)}`;
}

export const REPLAY_SPEEDS = [0.25, 0.5, 1, 2, 5, 10, 25, 100];
