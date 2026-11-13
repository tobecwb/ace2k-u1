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

// Whether a swap of head is free: the head swaps in the background (bg) and the part before was another head's.
/** @param {number[]} bg @param {number} head @param {number | null} before */
const isFree = (bg, head, before) => bg.includes(head) && before !== head;

// Per head: its parts (active where it prints, else the lane it holds), its swap marks and count. A swap of a head in
// bg (the heads that swap in the background) that comes after a part of another head (the head was parked) is free:
// it does not stop the print (print-plan.js swapKinds). marks are the stops, freeMarks the free swaps, free their
// count, and swaps counts both. With stops (one per segment: swapStops over the same segments), a free swap whose
// stop is late (called back before its dock work ended) goes to lateMarks, every swap adds {x, kind, s} to cost, and
// stopS is the head's time stopped; without stops, lateMarks and cost are empty and stopS is null. drawn lists the same
// marks as {x, kind, s} (kind stop, free or late, in that order; s the estimated stop in seconds, or null without
// one), so a mark carries its own estimate.
/**
 * @param {Segment[]} segs
 * @param {Record<number, string>} choice
 * @param {HeadOf} headOf
 * @param {number[]} [bg]
 * @param {(import('./swap-time.js').SwapStop | null)[]} [stops]
 */
export function bands(segs, choice, headOf, bg = [], stops) {
  const heads = [...new Set(segs.map((s) => headOf(choice[s.tool])).filter((h) => h !== null))].sort(
    (a, b) => /** @type {number} */ (a) - /** @type {number} */ (b),
  );
  // The head of the part before each part, among the parts on a head (null for none).
  /** @type {(number | null)[]} */
  const before = [];
  let prev = /** @type {number | null} */ (null);
  for (const s of segs) {
    before.push(prev);
    prev = headOf(choice[s.tool]) ?? prev;
  }
  return heads.map((head) => {
    const first = /** @type {Segment} */ (segs.find((s) => headOf(choice[s.tool]) === head));
    let loaded = first.tool;
    // Each swap of the head: where, whether it is free, and its stop.
    /** @type {{ x: number, free: boolean, stop: import('./swap-time.js').SwapStop | null }[]} */
    const swaps = [];
    const parts = segs.map((s, i) => {
      const active = headOf(choice[s.tool]) === head;
      if (active && choice[s.tool] !== choice[loaded])
        swaps.push({ x: s.x, free: isFree(bg, head, before[i]), stop: stops?.[i] ?? null });
      if (active) loaded = s.tool;
      return { x: s.x, e: s.e, tool: loaded, active };
    });
    /** @param {(w: (typeof swaps)[number]) => boolean} keep */
    const xs = (keep) => swaps.filter(keep).map((w) => w.x);
    /** @param {(typeof swaps)[number]} w */
    const late = (w) => w.stop?.kind === 'late';
    const marks = xs((w) => !w.free);
    const freeMarks = xs((w) => w.free && !late(w));
    const lateMarks = xs((w) => w.free && late(w));
    const free = freeMarks.length + lateMarks.length;
    const cost = swaps.flatMap((w) => (w.stop ? [{ x: w.x, kind: w.stop.kind, s: w.stop.s }] : []));
    /** @param {'stop' | 'free' | 'late'} kind @param {(w: (typeof swaps)[number]) => boolean} keep */
    const drawnOf = (kind, keep) => swaps.filter(keep).map((w) => ({ x: w.x, kind, s: w.stop?.s ?? null }));
    const drawn = [
      ...drawnOf('stop', (w) => !w.free),
      ...drawnOf('free', (w) => w.free && !late(w)),
      ...drawnOf('late', (w) => w.free && late(w)),
    ];
    const stopS = stops ? swaps.reduce((sum, w) => sum + (w.stop?.s ?? 0), 0) : null;
    return { head, swaps: swaps.length, free, parts, marks, freeMarks, lateMarks, cost, drawn, stopS };
  });
}

// What each head holds at pos, the change printing at pos, what its head held just before, and the head of the
// part before it (null for none).
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
  let last = /** @type {number | null} */ (null);
  let before = /** @type {number | null} */ (null);
  for (const s of segs) {
    const h = headOf(choice[s.tool]);
    if (h === null || s.x > pos) continue;
    if (pos < s.e) {
      cur = s;
      prevOnHead = loaded.get(h) ?? null;
      before = last;
    }
    loaded.set(h, s.tool);
    last = h;
  }
  return { loaded, cur, prevOnHead, before };
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

// A swap stops the print for about the inline average (swap-time.js). The window that shows it is the share of the
// file the print covers in that time (swapShare), and never more than this share of the change's own part, so a
// short change shows its colour.
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

// The next change after pos that loads another lane on its head, and whether it is free (isFree, on the heads in
// bg). With stopsOnly, the next such change that is not free.
/**
 * @param {Segment[]} segs
 * @param {Record<number, string>} choice
 * @param {HeadOf} headOf
 * @param {number} pos
 * @param {Map<number, number>} loaded
 * @param {number[]} bg
 * @param {boolean} [stopsOnly]
 */
function nextSwap(segs, choice, headOf, pos, loaded, bg, stopsOnly = false) {
  const ahead = new Map(loaded);
  let last = /** @type {number | null} */ (null);
  for (const s of segs) {
    const h = headOf(choice[s.tool]);
    if (h === null) continue;
    const before = last;
    last = h;
    if (s.x <= pos) continue;
    const was = /** @type {number} */ (ahead.get(h));
    if (choice[was] !== choice[s.tool]) {
      const free = isFree(bg, h, before);
      if (!stopsOnly || !free) return { head: h, from: was, to: s.tool, x: s.x, free };
    }
    ahead.set(h, s.tool);
  }
  return null;
}

// The change printing at pos, a swap in progress, the next change that loads another lane on its head, and the next
// one that stops the print (nextStop: the same as next when next is no free swap). swapShare is the share of the file
// the print covers during one swap (swapShareOf), or null. bg lists the heads whose swaps count free (isFree). Within
// the swap's window, a stop is in progress, and so is any swap whose head the live status names (seen): its background
// swap still runs (waiting), or the adapter changes its lane now (swapping).
/**
 * @param {Segment[]} segs
 * @param {Record<number, string>} choice
 * @param {HeadOf} headOf
 * @param {number} pos
 * @param {number | null} [swapShare]
 * @param {number[]} [bg]
 * @param {{ waiting: number[], swapping: number[] }} [seen]
 */
export function liveAt(segs, choice, headOf, pos, swapShare = null, bg = [], seen = { waiting: [], swapping: [] }) {
  const { loaded, cur, prevOnHead, before } = scanTo(segs, choice, headOf, pos);
  fillFirst(segs, choice, headOf, loaded);
  const now = cur
    ? { tool: cur.tool, head: /** @type {number} */ (headOf(choice[cur.tool])), key: choice[cur.tool] }
    : null;
  const shown =
    now !== null &&
    (!isFree(bg, now.head, before) || seen.waiting.includes(now.head) || seen.swapping.includes(now.head));
  const swapping =
    now && shown && inSwap(cur, prevOnHead, choice, pos, swapShare)
      ? { head: now.head, from: /** @type {number} */ (prevOnHead), to: now.tool }
      : null;
  const next = nextSwap(segs, choice, headOf, pos, loaded, bg);
  const nextStop = next?.free ? nextSwap(segs, choice, headOf, pos, loaded, bg, true) : next;
  return { now, swapping, next, nextStop };
}
