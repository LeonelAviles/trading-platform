// Formatting helpers shared by pages and components.
export function fmtWhen(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

export function signed(n, digits = 2) {
  if (n == null) return '—';
  return `${n >= 0 ? '+' : ''}${Number(n).toFixed(digits)}`;
}
