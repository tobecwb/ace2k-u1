// How a print uses each head, over the file from start to end. The page draws the bands and marks
// the swaps; during a print it finds the change in progress and the next swap. No effects.
import { TAIL_BYTES } from './slicer-file.js';

/** @typedef {{ tool: number, x: number, e: number }} Segment */
/** @typedef {(key: string) => number | null} HeadOf */

// The G-code's own bytes in a file (Moonraker's gcode_start_byte / gcode_end_byte), or null when not known.
/** @typedef {{ start: number, end: number } | null} Frame */

// How far past the frame's end an offset may sit when the plan gives no file size: the config block the page reads
// at the end of a file (conn.js fileCard reads its last TAIL_BYTES).

// The file's order as segments, x and e as shares of the print: the offsets over the frame (the G-code's bytes, as
// the print's progress counts them: clock.js shareOf), else over the file's size. Without offsets each change gets an
// equal share, and so do offsets that cannot be the file's: one per change, rising, within its size (or the frame's
// end and TAIL_BYTES when no size is known). A change outside the frame prints none of its G-code: of the changes
// before its start only the last stays, at 0 (the tool the G-code starts with), unless a change sits at the start
// itself; the changes after its end go. No segment has zero length from a clamped offset.
/**
 * @param {{ order?: number[], order_at?: number[], size?: number } | null} plan @param {Frame} [frame]
 * @returns {Segment[]}
 */
export function segmentsOf(plan, frame = null) {
  const order = plan?.order ?? [];
  if (!order.length) return [];
  const at = plan?.order_at;
  const size = plan?.size ?? 0;
  const lo = frame ? frame.start : 0;
  const hi = frame ? frame.end : size;
  const top = size > 0 ? size : hi + TAIL_BYTES;
  const ok =
    Array.isArray(at) &&
    at.length === order.length &&
    hi > lo &&
    at.every((v, i) => Number.isFinite(v) && v >= 0 && v <= top && (i === 0 || v >= at[i - 1]));
  if (!ok)
    return order.map((tool, i) => ({
      tool,
      x: i / order.length,
      e: i + 1 < order.length ? (i + 1) / order.length : 1,
    }));
  const offs = /** @type {number[]} */ (at);
  // The changes kept, as [tool, share]: the last one before the frame (at 0, unless one sits at its start), then the
  // ones inside it.
  const first = offs.findIndex((v) => v >= lo);
  const lead = first < 0 ? order.length : first;
  /** @type {[number, number][]} */
  const kept = [];
  if (lead > 0 && (first < 0 || offs[first] > lo)) kept.push([order[lead - 1], 0]);
  for (let i = lead; i < order.length && offs[i] <= hi; i += 1) kept.push([order[i], (offs[i] - lo) / (hi - lo)]);
  return kept.map(([tool, x], k) => ({ tool, x, e: k + 1 < kept.length ? kept[k + 1][1] : 1 }));
}

// Per head: its parts (active where it prints, else the lane it holds), its swap marks and count.
/**
 * @param {Segment[]} segs
 * @param {Record<number, string>} choice
 * @param {HeadOf} headOf
 */
export function bands(segs, choice, headOf) {
  const heads = [...new Set(segs.map((s) => headOf(choice[s.tool])).filter((h) => h !== null))].sort(
    (a, b) => /** @type {number} */ (a) - /** @type {number} */ (b),
  );
  return heads.map((head) => {
    const first = /** @type {Segment} */ (segs.find((s) => headOf(choice[s.tool]) === head));
    let loaded = first.tool;
    /** @type {number[]} */
    const marks = [];
    const parts = segs.map((s) => {
      const active = headOf(choice[s.tool]) === head;
      if (active && choice[s.tool] !== choice[loaded]) marks.push(s.x);
      if (active) loaded = s.tool;
      return { x: s.x, e: s.e, tool: loaded, active };
    });
    return { head, swaps: marks.length, parts, marks };
  });
}

// What each head holds at pos, the change printing at pos and what its head held just before.
/**
 * @param {Segment[]} segs
 * @param {Record<number, string>} choice
 * @param {HeadOf} headOf
 * @param {number} pos
 */
function scanTo(segs, choice, headOf, pos) {
  /** @type {Map<number, number>} */
  const loaded = new Map();
  /** @type {Segment | null} */
  let cur = null;
  let prevOnHead = /** @type {number | null} */ (null);
  for (const s of segs) {
    const h = headOf(choice[s.tool]);
    if (h === null || s.x > pos) continue;
    if (pos < s.e) {
      cur = s;
      prevOnHead = loaded.get(h) ?? null;
    }
    loaded.set(h, s.tool);
  }
  return { loaded, cur, prevOnHead };
}

// Heads that print nothing up to pos hold the lane of their first change.
/**
 * @param {Segment[]} segs
 * @param {Record<number, string>} choice
 * @param {HeadOf} headOf
 * @param {Map<number, number>} loaded
 */
function fillFirst(segs, choice, headOf, loaded) {
  for (const s of segs) {
    const h = headOf(choice[s.tool]);
    if (h !== null && !loaded.has(h)) loaded.set(h, s.tool);
  }
}

// A swap stops the print for about SWAP_S. The window that shows it is the share of the file the print covers in
// that time (swapShare), and never more than this share of the change's own part, so a short change shows its colour.
const SWAP_PART_MAX = 0.35;
// While the print's speed is not known yet, the window is this share of the change's part.
const SWAP_PART_UNKNOWN = 0.05;

// Whether the change `cur` is still in its swap from lane `prev`: within the swap's window from its start.
/**
 * @param {Segment | null} cur
 * @param {number | null} prev
 * @param {Record<number, string>} choice
 * @param {number} pos
 * @param {number | null} swapShare
 */
function inSwap(cur, prev, choice, pos, swapShare) {
  if (!cur || prev === null) return false;
  const part = cur.e - cur.x;
  const win = swapShare === null ? part * SWAP_PART_UNKNOWN : Math.min(part * SWAP_PART_MAX, swapShare);
  return choice[prev] !== choice[cur.tool] && pos - cur.x < win;
}

// The next change after pos that loads another lane on its head.
/**
 * @param {Segment[]} segs
 * @param {Record<number, string>} choice
 * @param {HeadOf} headOf
 * @param {number} pos
 * @param {Map<number, number>} loaded
 */
function nextSwap(segs, choice, headOf, pos, loaded) {
  const ahead = new Map(loaded);
  for (const s of segs) {
    const h = headOf(choice[s.tool]);
    if (s.x <= pos || h === null) continue;
    const was = /** @type {number} */ (ahead.get(h));
    if (choice[was] !== choice[s.tool]) return { head: h, from: was, to: s.tool, x: s.x };
    ahead.set(h, s.tool);
  }
  return null;
}

// The change printing at pos, a swap in progress, and the next change that loads another lane
// on its head. swapShare is the share of the file the print covers during one swap (swapShareOf), or null.
/**
 * @param {Segment[]} segs
 * @param {Record<number, string>} choice
 * @param {HeadOf} headOf
 * @param {number} pos
 * @param {number | null} [swapShare]
 */
export function liveAt(segs, choice, headOf, pos, swapShare = null) {
  const { loaded, cur, prevOnHead } = scanTo(segs, choice, headOf, pos);
  fillFirst(segs, choice, headOf, loaded);
  const now = cur
    ? { tool: cur.tool, head: /** @type {number} */ (headOf(choice[cur.tool])), key: choice[cur.tool] }
    : null;
  const swapping =
    now && inSwap(cur, prevOnHead, choice, pos, swapShare)
      ? { head: now.head, from: /** @type {number} */ (prevOnHead), to: now.tool }
      : null;
  return { now, swapping, next: nextSwap(segs, choice, headOf, pos, loaded) };
}
