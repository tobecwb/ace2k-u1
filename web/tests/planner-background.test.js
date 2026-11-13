import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { pickCosts, placement, search } from '../js/planner.js';
import { SEEDS, bgStop } from '../js/swap-time.js';
import { chosen, headOfIn, sentKinds, swapKinds } from '../js/print-plan.js';

// The colour planner with heads that swap in the background (background_heads): a swap at a T of such a head is
// free when a T of another head came between its previous T and this one (the head was parked). Every other swap
// stops the print. The planner keeps the fewest stops, then the fewest swaps, then the fewest spool moves.

const files = JSON.parse(readFileSync(new URL('./fixtures/planner-files.json', import.meta.url), 'utf8'));

/** Empty lanes, caps[h] of them on head h, keyed a:1, b:1 and so on. @param {number[]} caps */
const emptyLanes = (caps) =>
  caps.flatMap((c, h) =>
    Array.from({ length: c }, (_, i) => ({
      key: `${'abcd'[h]}:${i + 1}`,
      head: h,
      colour: null,
      material: null,
      empty: true,
    })),
  );
/** A plan of count tools of distinct colours, all PLA, picks by tool. */
const planOf = (file, order, count, picks = {}) => ({
  file,
  mtime: 1,
  order,
  tools: Array.from({ length: count }, (_, n) => ({
    colour: `#0000${String(n).padStart(2, '0')}`,
    material: 'PLA',
    lane: picks[n] ?? null,
    why: 'closest',
  })),
});
/** @param {number[]} period @param {number} times */
const repeat = (period, times) => Array.from({ length: times }, () => period).flat();

test('swapKinds: a swap of a parked head in bg is free; one right after the same head is a stop', () => {
  const lanes = emptyLanes([4, 4, 0, 0]);
  const headOf = headOfIn(lanes);
  const choice = { 0: 'a:1', 1: 'a:2', 2: 'b:1' };
  const order = repeat([0, 1, 2], 3);
  // Head 1 sees 0 1 0 1 0 1: 0 -> 1 comes right after (a stop), 1 -> 0 comes after a T of head 2 (free).
  assert.deepEqual(swapKinds(order, choice, headOf, [0]), { stops: 3, free: 2 });
  // Without bg every swap is a stop.
  assert.deepEqual(swapKinds(order, choice, headOf), { stops: 5, free: 0 });
  // A head not in bg counts every swap as a stop, parked or not.
  assert.deepEqual(swapKinds(order, choice, headOf, [1]), { stops: 5, free: 0 });
  // What Print sends: the stops, and the two kinds.
  const plan = planOf('k.gcode', order, 3);
  assert.deepEqual(sentKinds(plan, choice, lanes, [0]), { stops: 3, free: 2 });
  assert.equal(sentKinds(plan, { 0: 'a:1' }, lanes, [0]), null);
});

test('two background heads, colours alternating heads: the planner keeps the heads apart and every swap is free', () => {
  const order = repeat([0, 1, 2, 3], 10);
  const lanes = emptyLanes([4, 4, 0, 0]);
  const plan = planOf('alt.gcode', order, 4);
  const p = placement(plan, chosen(plan, {}), lanes, {}, [], [0, 1]);
  assert.equal(p?.best, 0);
  assert.equal(p?.free, 38);
  const headOf = headOfIn(lanes);
  const head = (n) => headOf(p?.choice[n] ?? '');
  assert.equal(head(0), head(2));
  assert.equal(head(1), head(3));
  assert.notEqual(head(0), head(1));
  // Without a background head the fewest swaps are 29 (colour 0 alone on a head), all stops, and no free count.
  const old = placement(plan, chosen(plan, {}), lanes);
  assert.equal(old?.best, 29);
  assert.equal('free' in (old ?? {}), false);
});

test('the pick as it is counts the same rule: free on a parked background head, a stop on any other', () => {
  const order = repeat([0, 1, 2, 3], 10);
  const lanes = emptyLanes([4, 4, 0, 0]);
  const plan = planOf('now.gcode', order, 4, { 0: 'a:1', 1: 'b:1', 2: 'a:2', 3: 'b:2' });
  const pick = chosen(plan, {});
  const both = placement(plan, pick, lanes, {}, [], [0, 1]);
  assert.deepEqual([both?.now, both?.nowFree], [0, 38]);
  // Head 2 without background swap: its 19 swaps all stop the print.
  const one = placement(plan, pick, lanes, {}, [], [0]);
  assert.deepEqual([one?.now, one?.nowFree], [19, 19]);
  // The suggestion: the fewest stops there are, and of those the fewest swaps.
  assert.equal(one?.best, bruteStops(order, 4, [4, 4, 0, 0], [0]));
  assert.ok((one?.best ?? 0) + (one?.free ?? 0) < 38);
  // A pick with a colour on no head: neither count is known.
  const gone = placement(
    planOf('gone.gcode', order, 4, { 0: 'a:1' }),
    chosen(plan, {}),
    emptyLanes([1, 0, 0, 0]),
    {},
    [],
    [0],
  );
  assert.equal(gone?.now, null);
  assert.equal(gone?.nowFree, null);
  assert.equal(gone?.fits, false);
  assert.equal(gone?.free, null);
});

test('consecutive Ts of one head stop the print even when it swaps in the background', () => {
  // Three colours on one head only: each swap comes right after the same head.
  const order = repeat([0, 1, 2], 4);
  const plan = planOf('one.gcode', order, 3);
  const p = placement(plan, chosen(plan, {}), emptyLanes([4, 0, 0, 0]), {}, [], [0]);
  assert.deepEqual([p?.best, p?.free], [11, 0]);
});

test('with no background head the placement is the one of before (the 5-colour file)', () => {
  const plan = {
    file: 'five.gcode',
    mtime: 1,
    order: files.five.order,
    tools: files.five.tools.map((tool) => ({ ...tool, lane: null, why: 'closest' })),
  };
  const lanes = emptyLanes([2, 1, 1, 1]);
  const before = placement(plan, chosen(plan, {}), lanes);
  assert.equal(before?.best, 1);
  assert.deepEqual(placement(plan, chosen(plan, {}), lanes, {}, [], []), before);
});

// The bench file's pattern: 5 colours, heads 1 and 2 fed by 4 lanes each and both in background_heads.
const BENCH = repeat([0, 1, 2, 3, 4, 0, 1, 2, 3, 0, 1, 2, 4], 10);

// The fewest stops, and of those the fewest swaps, over every way to put the groups on heads, by brute force (each
// head holds at most caps[h]), as [stops, swaps].
// The lanes of groups put on heads (at: group -> head), in emptyLanes' keys, or null when a head gets too many.
/** @param {number[]} at @param {number[]} caps @returns {Record<number, string> | null} */
function lanesOf(at, caps) {
  const used = caps.map(() => 0);
  /** @type {Record<number, string>} */
  const choice = {};
  for (const [n, h] of at.entries()) {
    used[h] += 1;
    if (used[h] > caps[h]) return null;
    choice[n] = `${'abcd'[h]}:${used[h]}`;
  }
  return choice;
}
/** The seconds of one head's groups (mask), with no print times known: a free swap is a ready arrival.
 * @param {number[]} order @param {number} mask @param {boolean} bg */
function headSeconds(order, mask, bg) {
  let last = -1;
  let at = -1;
  let s = 0;
  order.forEach((g, i) => {
    if (!((mask >> g) & 1)) return;
    if (last !== -1 && last !== g) s += bg && at !== i - 1 ? bgStop(SEEDS, null).s : SEEDS.inline;
    last = g;
    at = i;
  });
  return s;
}
/** The least estimated seconds stopped, and of those the fewest stops, then swaps, over every way to put the groups on
 * heads (each head holds at most caps[h]), as [stops, swaps, seconds].
 * @param {number[]} order @param {number} count @param {number[]} caps @param {number[]} bg */
function brute(order, count, caps, bg) {
  const lanes = emptyLanes(caps);
  const headOf = headOfIn(lanes);
  /** @type {number[]} */
  let best = [Infinity, Infinity, Infinity];
  const at = Array(count).fill(0);
  const walk = (g) => {
    if (g === count) {
      const choice = lanesOf(at, caps);
      if (!choice) return;
      const k = swapKinds(order, choice, headOf, bg);
      const masks = caps.map((_, h) => at.reduce((m, x, n) => (x === h ? m | (1 << n) : m), 0));
      const sec = masks.reduce((a, m, h) => a + Math.round(headSeconds(order, m, bg.includes(h))), 0);
      const mine = [k.stops, k.stops + k.free, sec];
      if (sec < best[2] || (sec === best[2] && mine[1] < best[1])) best = mine;
      return;
    }
    for (let h = 0; h < caps.length; h += 1) {
      at[g] = h;
      walk(g + 1);
    }
  };
  walk(0);
  return best;
}
/** The fewest stops (brute). @param {number[]} order @param {number} count @param {number[]} caps @param {number[]} bg */
const bruteStops = (order, count, caps, bg) => brute(order, count, caps, bg)[0];

test('the bench pattern on heads 1 and 2: the fewest stops are found, one per pass of the 5-colour cycle', () => {
  const plan = planOf('bench.gcode', BENCH, 5);
  const lanes = emptyLanes([4, 4, 0, 0]);
  const p = placement(plan, chosen(plan, {}), lanes, {}, [], [0, 1]);
  // 0 1 2 3 4 0 is a cycle of five colours: two heads cannot alternate it, so each pass keeps one stop.
  assert.equal(bruteStops(BENCH, 5, [4, 4, 0, 0], [0, 1]), 10);
  assert.deepEqual([p?.best, p?.free], [10, 118]);
  // As it was: 99 swaps that all stop the print.
  assert.equal(placement(plan, chosen(plan, {}), lanes)?.best, 99);
});

test('the bench pattern with one more lane on heads 3 and 4: no stop at all', () => {
  const plan = planOf('bench3.gcode', BENCH, 5);
  const p = placement(plan, chosen(plan, {}), emptyLanes([4, 4, 1, 1]), {}, [], [0, 1]);
  assert.equal(bruteStops(BENCH, 5, [4, 4, 1, 1], [0, 1]), 0);
  assert.deepEqual([p?.best, p?.free], [0, 39]);
});

test('the search keeps the least seconds stopped, then the fewest stops and swaps, on random orders', () => {
  let seed = 7;
  const rnd = (k) => {
    seed = (seed * 48271) % 2147483647;
    return seed % k;
  };
  for (let c = 0; c < 200; c += 1) {
    const count = 2 + rnd(4);
    const order = [];
    while (order.length < 6 + rnd(30)) {
      const g = rnd(count);
      if (order.at(-1) !== g) order.push(g);
    }
    const caps = [
      [2, 2, 1, 0],
      [3, 3, 0, 0],
      [1, 1, 1, 2],
    ][rnd(3)];
    const bg = [[0], [0, 1], [1, 3], []][rnd(4)];
    if (caps.reduce((a, b) => a + b, 0) < count) continue;
    const found = search(order, count, caps, Array(count).fill(-1), 0, bg);
    // The lanes of the groups the search put on each head, and their two counts.
    const at = Array.from({ length: count }, (_, g) => found.masks.findIndex((mask) => (mask >> g) & 1));
    const choice = /** @type {Record<number, string>} */ (lanesOf(at, caps));
    const k = swapKinds(order, choice, headOfIn(emptyLanes(caps)), bg);
    const want = brute(order, count, caps, bg);
    assert.deepEqual([found.seconds, k.stops, k.stops + k.free], [want[2], want[0], want[1]], `case ${c}`);
    assert.equal(found.swaps, k.stops);
  }
});

test('pickCosts counts stops: a bay that only adds free swaps costs nothing', () => {
  const order = repeat([0, 1, 2, 3], 10);
  const lanes = emptyLanes([4, 4, 0, 0]);
  const plan = planOf('cost.gcode', order, 4, { 0: 'a:1', 1: 'b:1', 2: 'a:2', 3: 'b:2' });
  const pick = chosen(plan, {});
  // Colours 0 to 2 keep the bays the user chose. Colour 3 on head 1 comes right after colour 2 of the same head,
  // and colour 0 right after it: stops.
  const d = pickCosts(plan, pick, lanes, {}, 3, 'a:3', [0, 1, 2], [0, 1]).d;
  assert.ok((d ?? 0) > 0);
  // Colour 3 on another bay of head 2: still free, no new stop.
  assert.equal(pickCosts(plan, pick, lanes, {}, 3, 'b:3', [0, 1, 2], [0, 1]).d, 0);
});
