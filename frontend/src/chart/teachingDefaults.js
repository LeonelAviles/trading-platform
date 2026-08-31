// Teaching-mode defaults per root (PLATFORM-SPEC.md Phase 6.1): 20/40 ticks
// for ES-sized contracts, 40/80 for NQ; persisted in localStorage.
export const TEACHING_DEFAULTS_KEY = 'teachingDefaults';

export function defaultTeaching(root) {
  const nq = root === 'NQ' || root === 'MNQ';
  return { stopTicks: nq ? 40 : 20, targetTicks: nq ? 80 : 40, contracts: 1, pauseOnQuestion: true, askNotes: true };
}

export function loadTeachingDefaults(root) {
  try {
    const saved = JSON.parse(localStorage.getItem(`${TEACHING_DEFAULTS_KEY}:${root}`));
    return saved ? { ...defaultTeaching(root), ...saved } : defaultTeaching(root);
  } catch {
    return defaultTeaching(root);
  }
}

export function saveTeachingDefaults(root, value) {
  try { localStorage.setItem(`${TEACHING_DEFAULTS_KEY}:${root}`, JSON.stringify(value)); } catch { /* quota */ }
}
