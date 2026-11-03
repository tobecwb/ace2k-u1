import { fmtHM } from './format.js';

// The timeline's zoom: the stretch of the file the bars show, as { a, b }, fractions of the file from 0 to 1. The
// strip under the bars draws the whole print and a window on this stretch; every change goes through clampView.

/** @typedef {{ a: number, b: number }} View */
/** @typedef {import('./types.js').UiCtx} UiCtx */

// The narrowest stretch, a share of the file: at 1 % a single swap of a long print is still wide enough to see.
export const ZOOM_MIN = 0.01;
/** @type {Readonly<View>} */
export const WHOLE = Object.freeze({ a: 0, b: 1 });

// A number, or the fallback when it is not one.
/** @param {number} v @param {number} d */
const num = (v, d) => (Number.isFinite(v) ? v : d);

// The stretch kept inside 0..1 and at least ZOOM_MIN wide: one too narrow grows around its centre, one past an edge
// slides back inside with its width, one wider than the file is the whole file.
/** @param {View} v @returns {View} */
export function clampView(v) {
  let a = num(v.a, 0);
  let b = num(v.b, 1);
  if (b < a) [a, b] = [b, a];
  let w = Math.min(1, Math.max(ZOOM_MIN, b - a));
  if (b - a < ZOOM_MIN) a = (a + b) / 2 - ZOOM_MIN / 2;
  if (w >= 1) return { a: 0, b: 1 };
  a = Math.min(Math.max(0, a), 1 - w);
  w = Math.round(w * 1e12) / 1e12;
  return { a: Math.round(a * 1e12) / 1e12, b: Math.round((a + w) * 1e12) / 1e12 };
}

// The stretch half as wide, around the same centre (+).
/** @param {View} v */
export const zoomIn = (v) => {
  const c = (v.a + v.b) / 2;
  const w = (v.b - v.a) / 2;
  return clampView({ a: c - w / 2, b: c + w / 2 });
};

// The stretch twice as wide, around the same centre, slid back inside at an edge (−).
/** @param {View} v */
export const zoomOut = (v) => {
  const c = (v.a + v.b) / 2;
  const w = (v.b - v.a) * 2;
  return clampView({ a: c - w / 2, b: c + w / 2 });
};

// The window dragged: the same width, starting at a.
/** @param {View} v @param {number} a */
export const moveTo = (v, a) => clampView({ a, b: a + (v.b - v.a) });

// One edge of the window dragged to x; the other edge stays, and the stretch never gets narrower than ZOOM_MIN.
/** @param {View} v @param {'a' | 'b'} edge @param {number} x */
export function resize(v, edge, x) {
  if (edge === 'a') return clampView({ a: Math.min(x, v.b - ZOOM_MIN), b: v.b });
  return clampView({ a: v.a, b: Math.max(x, v.a + ZOOM_MIN) });
}

// A drag across the strip from one point to another: the stretch between them.
/** @param {number} from @param {number} to */
export const pickView = (from, to) => clampView({ a: Math.min(from, to), b: Math.max(from, to) });

// A fraction as a percent to three decimals, so 0.8 to 1 reads 20 and not 19.999999999999996. The one rounding of
// every percent the timeline draws.
/** @param {number} v */
export const pct = (v) => Math.round(v * 100000) / 1000;

// Where a part of the file from x to e sits on the bars showing the stretch, in percent; null when it is wholly
// outside the stretch.
/** @param {number} x @param {number} e @param {View} v @returns {{ left: number, width: number } | null} */
export function place(x, e, v) {
  const lo = Math.max(x, v.a);
  const hi = Math.min(e, v.b);
  if (hi <= lo) return null;
  const w = v.b - v.a;
  return { left: pct((lo - v.a) / w), width: pct((hi - lo) / w) };
}

// Where a point x of the file sits on the bars showing the stretch, in percent; null when it is outside the stretch.
/** @param {number} x @param {View} v @returns {number | null} */
export function placeAt(x, v) {
  if (x < v.a || x > v.b) return null;
  return pct((x - v.a) / (v.b - v.a));
}

// The axis under the bars: the stretch's start, middle and end, in the slicer's estimated time (≈ h:mm) when the
// file has one (timeS, seconds), else in percent of the file.
/** @param {UiCtx['t']} t @param {View} v @param {number | null} timeS @returns {string[]} */
export function axisLabels(t, v, timeS) {
  return [v.a, (v.a + v.b) / 2, v.b].map((f) =>
    timeS ? t('timeline.at_time', { time: fmtHM(f * timeS) }) : t('timeline.at_pct', { pct: Math.round(f * 100) }),
  );
}
