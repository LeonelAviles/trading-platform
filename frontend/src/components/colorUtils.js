export function hexToRgb(hex) {
  const clean = hex.replace('#', '');
  const full = clean.length === 3 ? clean.split('').map((c) => c + c).join('') : clean;
  const n = parseInt(full, 16);
  return { r: (n >> 16) & 255, g: (n >> 8) & 255, b: n & 255 };
}

export function rgbToHex(r, g, b) {
  return '#' + [r, g, b].map((v) => Math.round(v).toString(16).padStart(2, '0')).join('');
}

// Accepts either '#rrggbb' or 'rgba(r,g,b,a)'/'rgb(r,g,b)'.
export function parseColor(str) {
  if (!str) return { r: 0, g: 0, b: 0, a: 1 };
  if (str.startsWith('rgb')) {
    const parts = str.match(/[\d.]+/g).map(Number);
    return { r: parts[0], g: parts[1], b: parts[2], a: parts.length > 3 ? parts[3] : 1 };
  }
  return { ...hexToRgb(str), a: 1 };
}

export function toRgbaString({ r, g, b, a }) {
  return `rgba(${Math.round(r)},${Math.round(g)},${Math.round(b)},${a})`;
}

function hslToHex(h, s, l) {
  s /= 100; l /= 100;
  const k = (n) => (n + h / 30) % 12;
  const a = s * Math.min(l, 1 - l);
  const f = (n) => l - a * Math.max(-1, Math.min(k(n) - 3, Math.min(9 - k(n), 1)));
  return rgbToHex(255 * f(0), 255 * f(8), 255 * f(4));
}

// A 6x9 preset grid: one grayscale row, then five hue rows from light tint
// to dark shade — the same shape as TradingView's palette, not pixel-exact.
export function generatePalette() {
  const hues = [0, 30, 48, 145, 168, 190, 220, 270, 320];
  const grays = [255, 224, 192, 160, 128, 96, 64, 32, 0].map((v) => rgbToHex(v, v, v));
  const rows = [grays];
  for (const lightness of [78, 60, 45, 33, 22]) {
    rows.push(hues.map((h) => hslToHex(h, 70, lightness)));
  }
  return rows;
}
