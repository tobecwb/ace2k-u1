import { TABS } from './state.js';

// The page's motion: every animation answers a click and lasts MOTION_MS; with the system's "reduce motion" on,
// nothing moves. tokens.css carries the same value as --dur (a test keeps them equal).

// How long an animation lasts, in ms: long enough to see what changed, short enough not to wait for (operator, 150).
export const MOTION_MS = 150;

// MOTION_MS, or 0 with "reduce motion" on. mm is window.matchMedia (absent in tests and old browsers).
/** @param {((q: string) => { matches: boolean }) | undefined} mm */
export const motionMs = (mm) => (mm?.('(prefers-reduced-motion: reduce)').matches ? 0 : MOTION_MS);

/** @template T @typedef {{ shown: T | null, leaving: boolean }} Presence */

// The next state of something that animates out: open shows it at once; closed keeps the old one shown and leaving,
// until App clears it after the exit; a new one during an exit replaces the old one at once.
/** @template T @param {Presence<T>} prev @param {T | null} next @returns {Presence<T>} */
export function presence(prev, next) {
  if (next) return { shown: next, leaving: false };
  if (!prev.shown) return prev;
  return { shown: prev.shown, leaving: true };
}

// The side a tab's content comes from: right when the new tab is after the old one in the bar, left before it.
/** @param {string | null} from @param {string} to @returns {'left' | 'right' | null} */
export function travel(from, to) {
  const order = /** @type {readonly string[]} */ (TABS);
  const i = from === null ? -1 : order.indexOf(from);
  const j = order.indexOf(to);
  if (i < 0 || j < 0 || i === j) return null;
  return j > i ? 'right' : 'left';
}
