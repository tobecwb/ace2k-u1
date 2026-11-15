// The time each swap stops the print. The adapter measures it on the printer and keeps a long-term average of each
// kind (its status swap_times); before any measurement, and with an older adapter, the page uses the bench's values
// (SEEDS). No effects: it runs under node.
import { segmentsOf } from './timeline.js';
import { isNum } from './format.js';

/** @typedef {import('./timeline.js').Frame} Frame */
/** @typedef {import('./timeline.js').HeadOf} HeadOf */
/** @typedef {{ inline: number, ready: number, dock: number, resume: number, depart: number, light: number,
 *   unit: number, lightHeads: number[] }} SwapTimes  unit is the unit's lane work (its three step averages);
 *   lightHeads the heads that swap light (the adapter's background mode). */
/** @typedef {{ tool: number, s: number | null }} Change */
/** @typedef {{ kind: 'inline' | 'ready' | 'late', s: number } | { kind: 'light', s: number, depart: number }} SwapStop */
/** @typedef {{ s: number, n: number, since: number | null }} PrintStops */
/** @typedef {import('./clock.js').Stops} Stops */

// The values measured on the printer on 2026-11-10, in seconds. web/tests/fixtures/swap-time-seeds.json pins them
// for both the adapter and the page. depart and light are the light background swap brief's section 5
// estimates, not yet measured.
export const SEEDS = Object.freeze({ inline: 94, ready: 15, dock: 65, resume: 15, depart: 29, light: 41 });
export const STEP_SEEDS = Object.freeze({
  heat: 0,
  unload: 27,
  free: 1,
  'roll back': 11,
  'hand over': 0,
  push: 12,
  grip: 14,
  loaded: 0,
});
// The unit's lane work of a light swap, while the head is parked: the steps the unit runs between the head leaving
// and the T (roll back, hand over, push).
const UNIT_STEPS = /** @type {const} */ (['roll back', 'hand over', 'push']);
// An average over this is not a swap's (the adapter drops such a sample): the seed is used.
export const AVG_MAX_S = 600;

// An average from the adapter, or its seed when it is missing or not a swap's.
/** @param {{ s?: number | null } | undefined} avg @param {number} seed @returns {number} */
const avgOr = (avg, seed) => {
  const s = avg?.s;
  return typeof s === 'number' && Number.isFinite(s) && s >= 0 && s <= AVG_MAX_S ? s : seed;
};

// The six averages from the adapter's status, each checked (a missing or bad one is its seed), the unit's lane work
// (the sum of its three step averages), and the heads that swap light (the adapter's background mode).
/** @param {import('./types.js').Status | null | undefined} status @returns {SwapTimes} */
export function timesOf(status) {
  const st = status?.ace2k_u1?.swap_times;
  /** @type {Record<keyof typeof SEEDS, number>} */
  const out = { ...SEEDS };
  for (const k of /** @type {(keyof typeof SEEDS)[]} */ (Object.keys(SEEDS))) out[k] = avgOr(st?.[k], SEEDS[k]);
  const unit = UNIT_STEPS.reduce((sum, k) => sum + avgOr(st?.steps?.[k], STEP_SEEDS[k]), 0);
  return { ...out, unit, lightHeads: headsOfMode(status, (mode) => mode === 'light') };
}

// The heads (0-based) the adapter's background status lists whose mode passes test (a head not listed is null there;
// a missing mode, from an older adapter, is full). None while the adapter publishes no background status. The one
// reading of a head's mode: timesOf here (its lightHeads) and state.js bgHeads.
/**
 * @param {import('./types.js').Status | null | undefined} status @param {(mode: string) => boolean} test
 * @returns {number[]}
 */
export function headsOfMode(status, test) {
  const list = status?.ace2k_u1?.background;
  return Array.isArray(list) ? list.flatMap((entry, h) => (entry && test(entry.mode ?? 'full') ? [h] : [])) : [];
}

// The seeds as a complete SwapTimes: what the page uses before any measurement and in tests.
/** @type {SwapTimes} */
export const SEED_TIMES = Object.freeze(timesOf(null));

// Whether the slicer's time is a time the page can use: a finite number over 0. The one test for the estimates and
// for the panel's note that the file has no time.
/** @param {number | null | undefined} timeS @returns {timeS is number} */
export const timeKnown = (timeS) => typeof timeS === 'number' && Number.isFinite(timeS) && timeS > 0;

// The file's changes, each with its print time: its share of the G-code's bytes times the slicer's time (timeS).
// Bytes are not seconds; it is the only per-change time the page has. With no slicer time, s is null.
/**
 * @param {Parameters<typeof segmentsOf>[0]} plan @param {Frame} frame @param {number | null | undefined} timeS
 * @returns {Change[]}
 */
export function changesOf(plan, frame, timeS) {
  const known = timeKnown(timeS);
  return segmentsOf(plan, frame).map((g) => ({
    tool: g.tool,
    s: known ? Math.max(0, g.e - g.x) * /** @type {number} */ (timeS) : null,
  }));
}

// The stop of a background swap whose head stayed parked w seconds (null: not known). The head arrives ready when
// the dock had time; else the dock work left and the cost of going on with it at the T are added.
/** @param {SwapTimes} times @param {number | null} w @returns {SwapStop} */
export function bgStop(times, w) {
  if (w === null) return { kind: 'ready', s: times.ready };
  const left = Math.max(0, times.dock - w);
  return left > 0 ? { kind: 'late', s: times.ready + left + times.resume } : { kind: 'ready', s: times.ready };
}

// The stop of a light swap whose head stayed parked w seconds (null: not known): the head leaves (depart), the unit
// swaps the lane while it is parked, and at the T it loads (light). The unit work the window did not cover is added.
// depart is how much of the stop is the head leaving.
/** @param {SwapTimes} times @param {number | null} w @returns {{ kind: 'light', s: number, depart: number }} */
export function lightStop(times, w) {
  const left = w === null ? 0 : Math.max(0, times.unit - w);
  return { kind: 'light', s: times.depart + times.light + left, depart: times.depart };
}

// The stop of one change, or null when it swaps no lane. A swap happens at a change whose lane is not the last lane of
// its head (print-plan.js swapKinds). On a head in bg whose change before was another head's, it is a background swap
// (bgStop; lightStop on a light head), its window the time since the head's previous change ended; every other swap is inline.
/**
 * @param {{ key: string | null, head: number | null, last: Map<number, string>, bg: number[], prevHead: number | null,
 *   w: () => number | null }} at
 * @param {SwapTimes} times
 * @returns {SwapStop | null}
 */
function stopOf(at, times) {
  const { head, key, last } = at;
  if (head === null || !last.has(head) || last.get(head) === key) return null;
  if (times.lightHeads.includes(head) && at.prevHead !== head) return lightStop(times, at.w());
  if (at.bg.includes(head) && at.prevHead !== head) return bgStop(times, at.w());
  return { kind: 'inline', s: times.inline };
}

// Each change's swap stop (null when the change swaps no lane), and the total, in file order (see stopOf).
/**
 * @param {Change[]} changes @param {Record<number, string | null>} choice @param {HeadOf} headOf
 * @param {number[]} bg @param {SwapTimes} times
 * @returns {{ stops: (SwapStop | null)[], total: number }}
 */
export function swapStops(changes, choice, headOf, bg, times) {
  /** @type {Map<number, string>} */
  const last = new Map();
  /** @type {Map<number, number>} */
  const endAt = new Map(); // the clock when each head's last change ended
  /** @type {(SwapStop | null)[]} */
  const stops = [];
  let clock = 0;
  let known = true;
  let prevHead = /** @type {number | null} */ (null);
  let total = 0;
  for (const c of changes) {
    const key = choice[c.tool] ?? null;
    const head = key !== null ? headOf(key) : null;
    /** @returns {number | null} The time this head has been parked; read only for a background swap. */
    const w = () =>
      known && head !== null && endAt.has(head) ? clock - /** @type {number} */ (endAt.get(head)) : null;
    const stop = stopOf({ key, head, last, bg, prevHead, w }, times);
    stops.push(stop);
    if (stop) {
      total += stop.s;
      clock += stop.s;
    }
    if (c.s === null) known = false;
    else clock += c.s;
    if (head !== null) {
      last.set(head, /** @type {string} */ (key));
      endAt.set(head, clock);
      prevHead = head;
    }
  }
  return { stops, total };
}

// The swap stops of the running print that the adapter measured (its status print_stops), or null from an older
// adapter (no key) or with a value that is not a number of seconds.
/** @param {import('./types.js').Status | null | undefined} status @returns {PrintStops | null} */
export function printStopsOf(status) {
  const ps = status?.ace2k_u1?.print_stops;
  if (!ps || !isNum(ps.s) || ps.s < 0) return null;
  const since = isNum(ps.since) && ps.since >= 0 ? ps.since : null;
  return { s: ps.s, n: isNum(ps.n) ? ps.n : 0, since };
}

// The stops of a running print for its clock (clock.js Stops), at share p after done seconds printed. xs are the
// changes' shares and stops their estimated stops (swapStops), one per change. A stop at a change the print has
// reached (x ≤ p) is passed; the others are ahead. With the adapter's measured stops (measured), the passed time is the
// measured one, plus the part of the stop that runs now (since). That stop is the last change reached when the file
// position is still at its T line (within near, a share): then what is left of its estimate is ahead, at p. A stop
// anywhere else (a swap typed at the console, one the plan does not price) adds only its time so far. Without the
// measured stops (an older adapter), the passed time is the estimates.
/**
 * @param {{ xs: number[], stops: (SwapStop | null)[], p: number, done: number, near: number,
 *   measured: PrintStops | null }} at
 * @returns {Stops}
 */
export function runStops({ xs, stops, p, done, near, measured }) {
  /** @type {{ x: number, s: number }[]} */
  const ahead = [];
  let passed = 0;
  stops.forEach((stop, i) => {
    if (!stop) return;
    if (xs[i] > p) ahead.push({ x: xs[i], s: stop.s });
    else passed += stop.s;
  });
  if (!measured) return { passedS: passed, ahead };
  if (measured.since === null) return { passedS: measured.s, ahead };
  const inStop = Math.max(0, done - measured.since);
  const at = xs.findLastIndex((x) => x <= p);
  const mine = at >= 0 && p - xs[at] <= near ? stops[at] : null;
  const rest = mine ? Math.max(0, mine.s - inStop) : 0;
  return { passedS: measured.s + inStop, ahead: rest > 0 ? [{ x: p, s: rest }, ...ahead] : ahead };
}
