// Text formats shared by the views.
// The text of no value.
export const DASH = '—';
/** @param {unknown} v @returns {v is number} */
export const isNum = (v) => typeof v === 'number' && Number.isFinite(v);

/** @param {unknown} v */
export const temp = (v) => (isNum(v) ? `${v.toFixed(1)} °C` : DASH);
// A reading beside its target, as Fluidd shows a heater: "45.2 / 50.0 °C".
// A target of 0, a missing target, or one that is not a number means the heater is off.
// Then the reading shows alone.
/** @param {unknown} v */
export const hasTarget = (v) => isNum(v) && v > 0;
// Returns the numbers only, without the unit: "45.2 / 50.0", "45.2" or "—".
/** @param {unknown} value @param {unknown} target */
export const readingWithTarget = (value, target) => {
  if (!isNum(value)) return DASH;
  return hasTarget(target) ? `${value.toFixed(1)} / ${/** @type {number} */ (target).toFixed(1)}` : value.toFixed(1);
};
/** @param {unknown} value @param {unknown} target */
export const tempWithTarget = (value, target) => (isNum(value) ? `${readingWithTarget(value, target)} °C` : DASH);
// A fraction of the file kept within 0..1. Anything that is not a number counts as 0.
/** @param {number} v */
export const unit = (v) => (Number.isFinite(v) ? Math.min(1, Math.max(0, v)) : 0);
// A share of the file as a whole percent, rounded down as the U1 shows it. The tiny allowance keeps a share
// stored as 0.39999999999999997 at 40.
/** @param {number} v */
export const floorPct = (v) => Math.floor(unit(v) * 100 + 1e-9);
/** @param {unknown} v */
export const pct = (v) => (isNum(v) ? `${Math.round(v)} %` : DASH);
/** @param {unknown} v */
export const mm = (v) => (isNum(v) ? `${v.toFixed(1)} mm` : DASH);

/** @param {number} n */
const pad = (n) => String(n).padStart(2, '0');

/** @param {number} t  Unix seconds. */
export function clock(t) {
  const d = new Date(t * 1000);
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

const DAY_FILE = /^\d{4}-\d{2}-\d{2}\.jsonl$/;

// Returns the recorder's day files from a Moonraker directory listing (result.files), newest first.
// The names carry the dates of the printer, so the newest file is today on the printer.
/**
 * @param {unknown} files
 * @param {number} keepDays
 * @returns {string[]}
 */
export function historyDays(files, keepDays) {
  return (Array.isArray(files) ? files : [])
    .map((/** @type {{ filename?: unknown } | null | undefined} */ f) => f?.filename)
    .filter((/** @type {unknown} */ n) => typeof n === 'string' && DAY_FILE.test(n))
    .map((n) => /** @type {string} */ (n))
    .sort()
    .reverse()
    .slice(0, keepDays);
}

// Whole seconds as m:ss ("0:07", "1:15"), for the reading bar's seconds left; 0:00 for nothing or less.
/** @param {number} s @returns {string} */
export function fmtMS(s) {
  const n = Number.isFinite(s) && s > 0 ? Math.floor(s) : 0;
  return `${Math.floor(n / 60)}:${String(n % 60).padStart(2, '0')}`;
}

// Seconds as h:mm. A positive value under a minute shows 0:01, so a near event never reads 0:00.
/** @param {number} s @returns {string} */
export function fmtHM(s) {
  const m = s > 0 ? Math.max(1, Math.round(s / 60)) : 0;
  return `${Math.floor(m / 60)}:${String(m % 60).padStart(2, '0')}`;
}
