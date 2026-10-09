// Pure: samples → one SVG chart, °C on the left axis, % on the right.
export const SERIES = [
  { key: 'hl', axis: 'c', css: 'var(--line-hl)', width: 1.5 },
  { key: 'hr', axis: 'c', css: 'var(--line-hr)', width: 1.5 },
  { key: 'ch', axis: 'c', css: 'var(--line-ch)', width: 2 },
  { key: 'rh', axis: 'p', css: 'var(--line-rh)', width: 2 },
];
// One SVG unit is one CSS pixel: the chart is drawn at the container's width.
const MARGIN_L = 32; // room for "100°" at 11 px
const MARGIN_R = 38; // room for "100%" at 11 px
const TOP = 8;
const BOT_PAD = 20; // the time labels
const DASH = '4 3';

export function chartBox(width) {
  const W = Math.max(200, Math.round(width) || 300);
  const H = W >= 600 ? 220 : 180;
  return { W, H, L: MARGIN_L, R: W - MARGIN_R, TOP, BOT: H - BOT_PAD };
}
const GAP_S = 90;

function tempRange(samples, hidden, extra) {
  const vals = [];
  if (typeof extra === 'number' && !hidden.includes('tgt')) vals.push(extra);
  for (const s of samples) {
    for (const k of ['ch', 'hl', 'hr', 'tgt']) {
      if (!hidden.includes(k) && typeof s[k] === 'number') vals.push(s[k]);
    }
  }
  if (!vals.length) return [15, 70];
  let lo = Math.floor((Math.min(...vals) - 2) / 5) * 5;
  let hi = Math.ceil((Math.max(...vals) + 2) / 5) * 5;
  if (hi - lo < 20) hi = lo + 20;
  return [lo, hi];
}

function runs(samples, key) {
  const out = [];
  let cur = [];
  let prevT = null;
  for (const s of samples) {
    const v = s[key];
    if (typeof v !== 'number' || (prevT !== null && s.t - prevT > GAP_S)) {
      if (cur.length > 1) out.push(cur);
      cur = [];
    }
    if (typeof v === 'number') cur.push(s);
    prevT = s.t;
  }
  if (cur.length > 1) out.push(cur);
  return out;
}

const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

// live: while the dryer starts or heats, { from (unix s or null), value }: the target drawn as one
// dashed line from the cycle's start (or the window's left edge) to the right edge.
export function chartSvg(samples, { t0, t1, hidden = [], labels, width, live = null }) {
  const { W, H, L, R, BOT } = chartBox(width);
  const liveOn = live && typeof live.value === 'number';
  const [lo, hi] = tempRange(samples, hidden, liveOn ? live.value : null);
  const span = Math.max(1, t1 - t0);
  const x = (t) => (L + ((t - t0) / span) * (R - L)).toFixed(1);
  const yC = (v) => (BOT - ((v - lo) / (hi - lo)) * (BOT - TOP)).toFixed(1);
  const yP = (v) => (BOT - (v / 100) * (BOT - TOP)).toFixed(1);
  const parts = [`<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" class="chart-svg" role="img">`];
  parts.push('<g class="grid">');
  for (let i = 0; i < 4; i++) {
    const y = (TOP + (i * (BOT - TOP)) / 3).toFixed(1);
    parts.push(`<line x1="${L}" y1="${y}" x2="${R}" y2="${y}"/>`);
  }
  parts.push('</g><g class="ticks">');
  parts.push(`<text x="2" y="${TOP + 4}">${hi}°</text><text x="2" y="${BOT + 4}">${lo}°</text>`);
  parts.push(`<text x="${R + 4}" y="${TOP + 4}">100%</text><text x="${R + 4}" y="${BOT + 4}">0%</text>`);
  parts.push(`<text x="${L}" y="${H - 3}">${esc(labels.ago)}</text><text x="${R}" y="${H - 3}" text-anchor="end">${esc(labels.now)}</text>`);
  parts.push('</g>');
  if (!hidden.includes('tgt')) {
    const liveFrom = liveOn ? Math.max(t0, live.from ?? t0) : Infinity;
    const past = liveOn ? samples.filter((s) => s.t < liveFrom) : samples;
    for (const run of runs(past, 'tgt')) {
      const pts = run.map((s) => `${x(s.t)},${yC(s.tgt)}`).join(' ');
      parts.push(`<polyline data-series="tgt" fill="none" style="stroke:var(--line-tgt)" stroke-width="1" stroke-dasharray="${DASH}" points="${pts}"/>`);
    }
    if (liveOn) {
      const y = yC(live.value);
      parts.push(`<polyline data-series="tgt" data-live="1" fill="none" style="stroke:var(--line-tgt)" stroke-width="1" stroke-dasharray="${DASH}" points="${x(liveFrom)},${y} ${x(t1)},${y}"/>`);
    }
  }
  for (const ser of SERIES) {
    if (hidden.includes(ser.key)) continue;
    const y = ser.axis === 'c' ? yC : yP;
    for (const run of runs(samples, ser.key)) {
      const pts = run.map((s) => `${x(s.t)},${y(s[ser.key])}`).join(' ');
      parts.push(`<polyline data-series="${ser.key}" fill="none" style="stroke:${ser.css}" stroke-width="${ser.width}" stroke-linejoin="round" points="${pts}"/>`);
    }
  }
  parts.push('</svg>');
  return parts.join('');
}

export function nearest(samples, t) {
  let best = null;
  for (const s of samples) if (!best || Math.abs(s.t - t) < Math.abs(best.t - t)) best = s;
  return best;
}

