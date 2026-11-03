// The clock of a running print, defined once: its progress, the time left and every estimate of the Print tab
// (the header's times, the next spool swap, the swap's orange, the top bar's %) read it. Its functions are pure: the
// largest share shown of a print is kept by state.js printClock.
import { isNum } from './format.js';

/** @typedef {import('./timeline.js').Frame} Frame */
/**
 * The clock. p is the share of the G-code printed (0..1), or null while it is not known (no progress reported
 * yet). done is the time printed. left is the time left in seconds, null while it is not known.
 * phase: 'running' (left may be known), 'finishing' (the last bytes: no time is given), 'complete' (nothing left),
 * 'stopped' (cancelled or in error: nothing is extrapolated) or 'unknown' (no progress yet).
 * @typedef {{ p: number | null, done: number, left: number | null,
 *   phase: 'running' | 'finishing' | 'complete' | 'stopped' | 'unknown' }} Clock
 */

// From this share on, the time printed over the share printed gives the file's own rate.
export const FILE_FLOOR = 0.02;
// From this share on the print is finishing: the page gives no time, never a 0:00 while the print still runs.
export const END_SHARE = 0.999;

// True when the card has the G-code's bytes (Moonraker's gcode_start_byte / gcode_end_byte).
/** @param {{ gcodeStart?: number | null, gcodeEnd?: number | null } | null | undefined} card */
export const hasBytes = (card) => isNum(card?.gcodeStart) && isNum(card?.gcodeEnd);

// The frame of a file: its G-code's bytes from its card (Moonraker's gcode_start_byte / gcode_end_byte), else the
// whole file (virtual_sdcard.file_size, else the plan's size), else null.
/**
 * @param {{ gcodeStart?: number | null, gcodeEnd?: number | null } | null | undefined} card
 * @param {unknown} fileSize @param {unknown} planSize
 * @returns {Frame}
 */
export function frameOf(card, fileSize, planSize) {
  const start = card?.gcodeStart;
  const end = card?.gcodeEnd;
  if (hasBytes(card) && /** @type {number} */ (end) > /** @type {number} */ (start))
    return { start: /** @type {number} */ (start), end: /** @type {number} */ (end) };
  for (const size of [fileSize, planSize])
    if (isNum(size) && /** @type {number} */ (size) > 0) return { start: 0, end: /** @type {number} */ (size) };
  return null;
}

/** @param {number} v */
const clamp01 = (v) => Math.min(1, Math.max(0, v));

// The share printed. Once the file's card has come (cardLoaded) and a frame is known, the file position over the
// frame. Else (before the card, or a card with no frame or no position) Klipper's progress over the whole file, so
// the page shows a share at once and switches to the frame when it can. Without either, the last share (last).
/**
 * @param {{ position: unknown, progress: unknown, frame: Frame, cardLoaded: boolean, last: number | null }} at
 * @returns {number | null}
 */
export function shareOf({ position, progress, frame, cardLoaded, last }) {
  if (cardLoaded && frame && isNum(position))
    return clamp01(/** @type {number} */ (position - frame.start) / (frame.end - frame.start));
  if (isNum(progress)) return clamp01(/** @type {number} */ (progress));
  return last;
}

// The time left, a blend as Mainsail's: left_file = done / p − done (the file's own rate, from FILE_FLOOR on) and
// left_slicer = slicer × (1 − p); left = left_file × p + left_slicer × (1 − p), so the observed rate takes over as
// the print advances. Either alone when the other is not known; null when neither is.
/** @param {number} done @param {number} p @param {number | null | undefined} slicerS @returns {number | null} */
export function leftOf(done, p, slicerS) {
  const file = p >= FILE_FLOOR && done > 0 ? done / p - done : null;
  const slicer = isNum(slicerS) ? /** @type {number} */ (slicerS) * (1 - p) : null;
  if (file !== null && slicer !== null) return file * p + slicer * (1 - p);
  return file ?? slicer;
}

// The clock from the print's state, the time printed, the share printed and the slicer's time.
/**
 * @param {string | null | undefined} state @param {number} done @param {number | null} p
 * @param {number | null | undefined} slicerS @returns {Clock}
 */
export function clockOf(state, done, p, slicerS) {
  if (state === 'complete') return { p: p ?? 1, done, left: 0, phase: 'complete' };
  if (state === 'cancelled' || state === 'error') return { p, done, left: null, phase: 'stopped' };
  if (p === null) return { p, done, left: null, phase: 'unknown' };
  if (p >= END_SHARE) return { p, done, left: null, phase: 'finishing' };
  const left = leftOf(done, p, slicerS);
  return { p, done, left: left === null ? null : Math.round(left), phase: 'running' };
}

// The print's total time, done + left, or null while left is not known.
/** @param {Clock} c */
export const totalOf = (c) => (c.left === null ? null : c.done + c.left);

// Seconds until the print reaches share x, on the clock: (x − p) / (1 − p) × left, never under a minute. null while
// the clock gives no time, and once the print is complete or stopped.
/** @param {Clock} c @param {number} x @returns {number | null} */
export function etaTo(c, x) {
  if (c.phase === 'complete' || c.phase === 'stopped') return null;
  if (c.left === null || c.p === null || c.p >= 1) return null;
  return Math.max(60, Math.round(((x - c.p) / (1 - c.p)) * c.left));
}

// The share of the print covered in swapS seconds, at the clock's rate ((1 − p) / left), or null while it gives none.
/** @param {Clock} c @param {number} swapS @returns {number | null} */
export function swapShareOf(c, swapS) {
  if (!c.left || c.p === null) return null;
  return (swapS * (1 - c.p)) / c.left;
}
