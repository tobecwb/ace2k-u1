// Draws the samples as one SVG chart, with °C on the left axis and % on the right.
// The module has no effects, so it runs under node.
import { esc } from './esc.js';
import { noDryerState } from './dryer-form.js';

/** @typedef {import('./types.js').Sample} Sample */
/** @typedef {'ch' | 'hl' | 'hr' | 'rh'} SeriesKey */
/**
 * The running dryer's target. from is the start of its cycle, in unix seconds.
 * @typedef {{ from: number | null, value: number }} LiveTarget
 */
/** @typedef {(t: number) => string} XScale */
/** @typedef {(v: number) => string} YScale */

/** @type {{ key: SeriesKey, axis: 'c' | 'p', css: string, width: number }[]} */
export const SERIES = [
  { key: 'hl', axis: 'c', css: 'var(--line-hl)', width: 1.5 },
  { key: 'hr', axis: 'c', css: 'var(--line-hr)', width: 1.5 },
  { key: 'ch', axis: 'c', css: 'var(--line-ch)', width: 2 },
  { key: 'rh', axis: 'p', css: 'var(--line-rh)', width: 2 },
];
// One SVG unit is one CSS pixel, because the chart is drawn at the width of its container.
const MARGIN_L = 32; // room for "100°" at 11 px
const MARGIN_R = 38; // room for "100%" at 11 px
const TOP = 8;
const BOT_PAD = 20; // room for the time labels
const DASH = '4 3';
const EVENT_R = 4; // the radius of an event's dot

// height is the room that the page gives the chart on a wide screen. Without it, the height follows the width.
/** @param {number} width @param {number | null} [height] */
export function chartBox(width, height = null) {
  const W = Math.max(200, Math.round(width) || 300);
  const H =
    Number.isFinite(height) && /** @type {number} */ (height) > 0
      ? Math.max(120, Math.round(/** @type {number} */ (height)))
      : W >= 600
        ? 220
        : 180;
  return { W, H, L: MARGIN_L, R: W - MARGIN_R, TOP, BOT: H - BOT_PAD };
}
const GAP_S = 90;

/** @param {Sample[]} samples @param {string[]} hidden @param {number | null} extra @returns {[number, number]} */
function tempRange(samples, hidden, extra) {
  /** @type {number[]} */
  const vals = [];
  if (typeof extra === 'number' && !hidden.includes('tgt')) vals.push(extra);
  for (const s of samples) {
    for (const k of /** @type {const} */ (['ch', 'hl', 'hr', 'tgt'])) {
      const v = s[k];
      if (!hidden.includes(k) && typeof v === 'number') vals.push(v);
    }
  }
  if (!vals.length) return [15, 70];
  const lo = Math.floor((Math.min(...vals) - 2) / 5) * 5;
  let hi = Math.ceil((Math.max(...vals) + 2) / 5) * 5;
  if (hi - lo < 20) hi = lo + 20;
  return [lo, hi];
}

/** @param {Sample[]} samples @param {SeriesKey} key @returns {Sample[][]} */
function runs(samples, key) {
  /** @type {Sample[][]} */
  const out = [];
  /** @type {Sample[]} */
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

// The four grid lines and the axis labels. °C is on the left, % on the right, and the ends of the time window below.
/**
 * @param {{ H: number, L: number, R: number, BOT: number }} box
 * @param {number} lo
 * @param {number} hi
 * @param {{ ago: string, now: string }} labels
 */
function frame({ H, L, R, BOT }, lo, hi, labels) {
  const parts = ['<g class="grid">'];
  for (let i = 0; i < 4; i++) {
    const y = (TOP + (i * (BOT - TOP)) / 3).toFixed(1);
    parts.push(`<line x1="${L}" y1="${y}" x2="${R}" y2="${y}"/>`);
  }
  parts.push('</g><g class="ticks">');
  parts.push(`<text x="2" y="${TOP + 4}">${hi}°</text><text x="2" y="${BOT + 4}">${lo}°</text>`);
  parts.push(`<text x="${R + 4}" y="${TOP + 4}">100%</text><text x="${R + 4}" y="${BOT + 4}">0%</text>`);
  parts.push(
    `<text x="${L}" y="${H - 3}">${esc(labels.ago)}</text><text x="${R}" y="${H - 3}" text-anchor="end">${esc(labels.now)}</text>`,
  );
  parts.push('</g>');
  return parts;
}

// The chamber's target, drawn dashed in the chamber's colour, as a step. It is 0 while the dryer
// is off, the target while it starts or heats (the samples carry it then), and 0 again from the
// cool-down on. 0 °C is mostly below the axis, so the off level then sits on the plot's bottom edge.
// While the dryer runs (liveOn), the live value replaces the samples from the start of the cycle,
// or from the window's left edge, and the line runs on to the right edge.
// A gap in the samples breaks the line, as it breaks the readings. So does a sample with no dryer
// state, as the one the recorder writes at Klipper's start before the unit reports.
/**
 * @param {Sample[]} samples
 * @param {{ x: XScale, yC: YScale, t0: number, t1: number, bot: number }} scale
 * @param {LiveTarget | null} live
 * @param {boolean} liveOn
 */
function targetLines(samples, { x, yC, t0, t1, bot }, live, liveOn) {
  const liveFrom = liveOn ? Math.max(t0, /** @type {LiveTarget} */ (live).from ?? t0) : Infinity;
  // The off level: 0 °C, kept on the plot's bottom edge.
  const offLevel = Math.min(Number(yC(0)), bot).toFixed(1);
  /** @param {number | null | undefined} v  The target, or none: the dryer is off. */
  const level = (v) => (typeof v === 'number' ? yC(v) : offLevel);
  /** @type {[number, string][][]} */
  const out = [];
  /** @type {[number, string][]} */
  let cur = [];
  for (const s of samples) {
    if (s.t < t0 || s.t >= liveFrom) continue;
    const noState = noDryerState(s);
    if (noState || gapBefore(cur, s.t)) {
      out.push(cur);
      cur = [];
    }
    if (!noState) cur.push([s.t, level(s.tgt)]);
  }
  if (liveOn) {
    const y = level(/** @type {LiveTarget} */ (live).value);
    if (gapBefore(cur, liveFrom)) {
      out.push(cur);
      cur = [];
    }
    cur.push([liveFrom, y], [t1, y]);
  }
  out.push(cur);
  return out.filter((run) => run.length > 1).map((run) => targetStep(run, x));
}

// Whether a point at time t is too far after the last point of the run to join it.
/** @param {[number, string][]} run @param {number} t */
const gapBefore = (run, t) => run.length > 0 && t - run[run.length - 1][0] > GAP_S;

// One run of the target as a step: it holds each level until the next point, then jumps.
/** @param {[number, string][]} run @param {XScale} x */
function targetStep(run, x) {
  /** @type {string[]} */
  const pts = [];
  run.forEach(([t, y], i) => {
    const prev = i > 0 ? run[i - 1][1] : y;
    if (y !== prev) pts.push(`${x(t)},${prev}`);
    if (y !== prev || i === 0 || i === run.length - 1) pts.push(`${x(t)},${y}`);
  });
  return `<polyline data-series="tgt" fill="none" style="stroke:var(--line-ch)" stroke-width="1" stroke-dasharray="${DASH}" points="${pts.join(' ')}"/>`;
}

// The readings that are not hidden. A gap in the samples splits a reading into separate runs.
/** @param {Sample[]} samples @param {string[]} hidden @param {{ x: XScale, yC: YScale, yP: YScale }} scale */
function seriesLines(samples, hidden, { x, yC, yP }) {
  const parts = [];
  for (const ser of SERIES) {
    if (hidden.includes(ser.key)) continue;
    const y = ser.axis === 'c' ? yC : yP;
    for (const run of runs(samples, ser.key)) {
      const pts = run.map((s) => `${x(s.t)},${y(/** @type {number} */ (s[ser.key]))}`).join(' ');
      parts.push(
        `<polyline data-series="${ser.key}" fill="none" style="stroke:${ser.css}" stroke-width="${ser.width}" stroke-linejoin="round" points="${pts}"/>`,
      );
    }
  }
  return parts;
}

/**
 * An event on the time axis. tone picks its colour: err, dryer or info.
 * @typedef {{ t: number, tone: string }} ChartEvent
 */

// The events inside the window, each as a dot on the time axis and a faint line down to the plot's floor.
// The dots sit in a lane just under the plot's top edge, and a dot never leaves the plot sideways,
// so none touches the axis labels outside it. The line keeps the event's exact time.
/** @param {ChartEvent[]} events @param {{ t0: number, t1: number, bot: number, x: XScale, left: number, right: number }} scale */
function eventDots(events, { t0, t1, bot, x, left, right }) {
  return events
    .filter((e) => e.t >= t0 && e.t <= t1)
    .map(
      (e) =>
        `<line class="evline" x1="${x(e.t)}" y1="${TOP}" x2="${x(e.t)}" y2="${bot}"/><circle class="ev ev-${esc(e.tone)}" cx="${Math.min(right - EVENT_R, Math.max(left + EVENT_R, Number(x(e.t)))).toFixed(1)}" cy="${TOP + EVENT_R + 2}" r="${EVENT_R}"/>`,
    );
}

// live is set while the dryer starts or heats. The chamber's target then holds its value
// from the start of the cycle, or the window's left edge, to the right edge (see targetLines).
/**
 * @param {Sample[]} samples
 * @param {{ t0: number, t1: number, hidden?: string[], labels: { ago: string, now: string },
 *   width: number, height?: number | null, live?: LiveTarget | null, events?: ChartEvent[] }} opts
 */
export function chartSvg(samples, { t0, t1, hidden = [], labels, width, height = null, live = null, events = [] }) {
  const box = chartBox(width, height);
  const { W, H, L, R, BOT } = box;
  const liveOn = Boolean(live) && typeof live?.value === 'number';
  const [lo, hi] = tempRange(samples, hidden, liveOn ? /** @type {LiveTarget} */ (live).value : null);
  const span = Math.max(1, t1 - t0);
  const scale = {
    t0,
    t1,
    bot: BOT,
    x: (/** @type {number} */ t) => (L + ((t - t0) / span) * (R - L)).toFixed(1),
    yC: (/** @type {number} */ v) => (BOT - ((v - lo) / (hi - lo)) * (BOT - TOP)).toFixed(1),
    yP: (/** @type {number} */ v) => (BOT - (v / 100) * (BOT - TOP)).toFixed(1),
  };
  return [
    `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" class="chart-svg">`,
    ...frame(box, lo, hi, labels),
    ...(hidden.includes('tgt') ? [] : targetLines(samples, scale, live, liveOn)),
    ...seriesLines(samples, hidden, scale),
    ...eventDots(events, { ...scale, left: L, right: R }),
    '</svg>',
  ].join('');
}

/** @param {Sample[]} samples @param {number} t @returns {Sample | null} */
export function nearest(samples, t) {
  /** @type {Sample | null} */
  let best = null;
  for (const s of samples) if (!best || Math.abs(s.t - t) < Math.abs(best.t - t)) best = s;
  return best;
}

// Returns the time under the pointer. x is the pointer offset in the plot box, and boxW is the width of the box.
// dims comes from the dataset of the chart wrapper. w is the SVG width, l and r are the plot edges,
// and t0 and t1 are the ends of the time window.
/**
 * @param {number} x
 * @param {number} boxW
 * @param {{ w: number, l: number, r: number, t0: number, t1: number }} dims
 */
export function tooltipAt(x, boxW, { w, l, r, t0, t1 }) {
  const fx = (x / boxW) * w;
  return t0 + ((fx - l) / (r - l)) * (t1 - t0);
}

// Returns the sample that the tooltip shows for the time tAt. It is the nearest sample from t0 on, or null.
/** @param {Sample[]} samples @param {number} t0 @param {number} tAt */
export function tooltipSample(samples, t0, tAt) {
  return nearest(
    samples.filter((x) => x.t >= t0),
    tAt,
  );
}

// Returns where the tooltip sits, in px inside the chart box. It sits 8 px below and to the right of the pointer,
// and stays inside the width of the box. pointer is relative to the box.
/** @param {{ x: number, y: number }} pointer @param {number} boxW @param {number} tipW */
export function tooltipPlace(pointer, boxW, tipW) {
  return { left: Math.max(0, Math.min(pointer.x + 8, boxW - tipW)), top: pointer.y + 8 };
}

// The tooltip text for a sample. It holds the time, the four readings, and the target when one is set.
// The page passes its formatters, and t to look up the catalogue.
/**
 * @param {Sample} s
 * @param {{ clock: (t: number) => string, temp: (v: unknown) => string, pct: (v: unknown) => string,
 *   t: (key: string) => string }} fmt
 */
export function tooltipText(s, { clock, temp, pct, t }) {
  return `${clock(s.t)} · ${t('chart.chamber')} ${temp(s.ch)} · ${t('chart.heater_l')} ${temp(s.hl)} · ${t('chart.heater_r')} ${temp(s.hr)} · ${t('chart.humidity')} ${pct(s.rh)}${typeof s.tgt === 'number' ? ` · ${t('chart.target')} ${temp(s.tgt)}` : ''}`;
}
