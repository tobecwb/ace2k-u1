import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { pickCosts, placement, planStats, search } from '../js/planner.js';
import { chosen, headOfIn } from '../js/print-plan.js';
import { bgStop, changesOf, SEEDS, swapStops } from '../js/swap-time.js';

// The planner scores the estimated seconds stopped (swap-time.js): the least seconds, then the fewest stops, then
// the fewest swaps. These tests check the search against a brute force over every placement, by seconds.

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
/** A plan of count tools of distinct colours, all PLA. */
const planOf = (file, order, count) => ({
  file,
  mtime: 1,
  order,
  tools: Array.from({ length: count }, (_, n) => ({
    colour: `#0000${String(n).padStart(2, '0')}`,
    material: 'PLA',
    lane: null,
    why: 'closest',
  })),
});
/** @param {number[]} period @param {number} times */
const repeat = (period, times) => Array.from({ length: times }, () => period).flat();
/** The prefix sums of the print times. @param {number[]} durs */
const sums = (durs) => {
  const pre = new Float64Array(durs.length + 1);
  durs.forEach((d, i) => {
    pre[i + 1] = pre[i] + d;
  });
  return pre;
};

// The seconds a set of groups (mask) costs on one head, by the rule: windows of print time only.
function headSeconds(order, durs, mask, bg, times) {
  let last = -1;
  let at = -1;
  let s = 0;
  let stops = 0;
  let swaps = 0;
  order.forEach((g, i) => {
    if (!((mask >> g) & 1)) return;
    if (last !== -1 && last !== g) {
      swaps += 1;
      if (bg && at !== i - 1) {
        const w = durs ? durs.slice(at + 1, i).reduce((a, b) => a + b, 0) : null;
        s += bgStop(times, w).s;
      } else {
        stops += 1;
        s += times.inline;
      }
    }
    last = g;
    at = i;
  });
  return { s: Math.round(s), stops, swaps };
}

// Every way to put the groups on heads (each head holds at most caps[h]): [seconds, stops, swaps] of each.
function every(order, durs, count, caps, bg, times) {
  /** @type {number[][]} */
  const out = [];
  const at = Array(count).fill(0);
  const walk = (g) => {
    if (g === count) {
      const masks = caps.map((_, h) => at.reduce((m, x, n) => (x === h ? m | (1 << n) : m), 0));
      if (masks.some((m, h) => at.filter((x) => x === h).length > caps[h])) return;
      const parts = masks.map((m, h) => headSeconds(order, durs, m, bg.includes(h), times));
      out.push(['s', 'stops', 'swaps'].map((k) => parts.reduce((a, p) => a + p[k], 0)));
      return;
    }
    for (let h = 0; h < caps.length; h += 1) {
      at[g] = h;
      walk(g + 1);
    }
  };
  walk(0);
  return out;
}
const least = (all) => all.reduce((a, b) => (b[0] < a[0] || (b[0] === a[0] && b[2] < a[2]) ? b : a));

for (const fractional of [false, true])
  test(`the search keeps the least seconds, then the fewest swaps${fractional ? ' (fractional times)' : ''}`, () => {
    let seed = 11;
    const rnd = (k) => {
      seed = (seed * 48271) % 2147483647;
      return seed % k;
    };
    for (let c = 0; c < 200; c += 1) {
      const count = 2 + rnd(6);
      const order = [];
      while (order.length < 6 + rnd(24)) {
        const g = rnd(count);
        if (order.at(-1) !== g) order.push(g);
      }
      const caps = [
        [2, 2, 1, 0],
        [3, 3, 0, 0],
        [1, 1, 1, 2],
      ][rnd(3)];
      if (caps.reduce((a, b) => a + b, 0) < count) continue;
      const bg = [[0], [0, 1], [1, 3], []][rnd(4)];
      const times = { inline: 60 + rnd(60), ready: rnd(30), dock: rnd(120), resume: rnd(30) };
      const durs = order.map(() => (fractional ? rnd(800) / 10 + 0.05 : rnd(80)));
      const found = search(order, count, caps, Array(count).fill(-1), 0, bg, { pre: sums(durs), times });
      const want = least(every(order, durs, count, caps, bg, times));
      const masks = found.masks;
      const mine = masks.reduce((a, m, h) => a + headSeconds(order, durs, m, bg.includes(h), times).s, 0);
      const swaps = masks.reduce((a, m, h) => a + headSeconds(order, durs, m, bg.includes(h), times).swaps, 0);
      assert.deepEqual([found.seconds, mine, swaps], [want[0], want[0], want[2]], `case ${c}`);
    }
  });

test('one more stop that saves seconds wins over fewer stops', () => {
  // Two heads, head 0 in the background, windows of 0.2 s: a background swap then costs more than an inline one
  // (ready + the dock left + resume = 94.8 s against 94 s), so the least seconds keep the stops.
  const order = repeat([0, 1, 2, 1], 10);
  const durs = order.map(() => 0.2);
  const all = every(order, durs, 3, [2, 1], [0], SEEDS);
  const byStops = all.reduce((a, b) => (b[1] < a[1] ? b : a));
  const bySeconds = least(all);
  assert.deepEqual(byStops.slice(0, 2), [1801, 0]);
  assert.deepEqual(bySeconds.slice(0, 2), [1786, 19]);
  const found = search(order, 3, [2, 1], [-1, -1, -1], 0, [0], { pre: sums(durs), times: SEEDS });
  assert.deepEqual([found.seconds, found.swaps], [1786, 19]);
  // With long windows the dock is done in time: the background swaps win with no stop.
  const long = search(order, 3, [2, 1], [-1, -1, -1], 0, [0], { pre: sums(order.map(() => 60)), times: SEEDS });
  assert.deepEqual([long.seconds, long.swaps], [665, 0]);
});

test('placement reports nowS and bestS from the swap stops, null with now and best', () => {
  const order = repeat([0, 1, 2], 4);
  const plan = planOf('t.gcode', order, 3);
  const lanes = emptyLanes([4, 4, 0, 0]);
  const timing = { frame: null, timeS: 1200, times: SEEDS };
  const pick = chosen(plan, { 0: 'a:1', 1: 'a:2', 2: 'b:1' });
  const p = placement(plan, pick, lanes, {}, [], [0], timing);
  const changes = changesOf(plan, null, 1200);
  const headOf = headOfIn(lanes);
  assert.equal(p?.nowS, Math.round(swapStops(changes, pick, headOf, [0], SEEDS).total));
  assert.equal(p?.bestS, Math.round(swapStops(changes, p?.choice ?? {}, headOf, [0], SEEDS).total));
  assert.ok(Number.isInteger(p?.nowS) && Number.isInteger(p?.bestS));
  // A pick that names no hooked lane: now and nowS are null together.
  const open = placement(plan, chosen(plan, {}), lanes, {}, [], [0], timing);
  assert.equal(open?.now === null, open?.nowS === null);
  const tooMany = placement(
    planOf('m.gcode', order, 3),
    chosen(plan, {}),
    emptyLanes([1, 1, 0, 0]),
    {},
    [],
    [],
    timing,
  );
  assert.deepEqual([tooMany?.best, tooMany?.bestS], [null, null]);
});

test('the memo key holds the frame, the slicer time and the four averages', () => {
  const plan = planOf('memo.gcode', repeat([0, 1, 2, 3], 5), 4);
  const lanes = emptyLanes([4, 4, 0, 0]);
  const pick = chosen(plan, {});
  const at = (timing) => placement(plan, pick, lanes, {}, [], [0], timing);
  const base = { frame: null, timeS: 1000, times: { ...SEEDS } };
  const before = planStats.searches;
  const first = at(base);
  assert.equal(planStats.searches, before + 1);
  assert.equal(at({ ...base, times: { ...SEEDS } }), first);
  assert.equal(planStats.searches, before + 1);
  at({ ...base, times: { ...SEEDS, dock: 80 } });
  assert.equal(planStats.searches, before + 2);
  at({ ...base, timeS: 2000 });
  assert.equal(planStats.searches, before + 3);
  at({ ...base, frame: { start: 0, end: 10 } });
  assert.equal(planStats.searches, before + 4);
});

// A frame that cuts the file: the order [0,1,0] at 10, 20 and 30 over the frame 0 to 25 keeps the first two changes.
test('with a frame, now and best count the same changes', () => {
  const plan = { ...planOf('f.gcode', [0, 1, 0], 2), order_at: [10, 20, 30], size: 40 };
  const lanes = emptyLanes([2, 0, 0, 0]);
  const pick = chosen(plan, { 0: 'a:1', 1: 'a:2' });
  const timing = { frame: { start: 0, end: 25 }, timeS: 100, times: SEEDS };
  const p = placement(plan, pick, lanes, {}, [], [], timing);
  assert.equal(p?.now, p?.best);
  assert.equal(p?.nowS, p?.bestS);
});

// The search's window leaves out the other heads' stops (brief 7.1), the panel's figures do not. Here the search's
// best (4 swaps) shows more seconds stopped than the pick as it is (3 swaps), whose background swap on head b has an
// inline swap of head a in its window: the pick as it is stays the suggestion, with nothing to move.
test('a suggestion that shows more seconds than the pick as it is keeps the pick as it is', () => {
  const plan = planOf('w.gcode', [1, 0, 1, 2, 3, 1, 0, 1, 3, 2, 0], 4);
  const lanes = emptyLanes([2, 2, 0, 0]);
  const pick = { 0: 'b:1', 1: 'a:1', 2: 'a:2', 3: 'b:2' };
  const timing = { frame: null, timeS: 84, times: SEEDS };
  const changes = changesOf(plan, null, 84);
  const headOf = headOfIn(lanes);
  // The search's own best, worked out as the panel works it out, shows more than the pick as it is.
  const searched = { 0: 'b:1', 1: 'a:1', 2: 'b:2', 3: 'a:2' };
  /** @param {Record<number, string>} c */
  const shown = (c) => Math.round(swapStops(changes, c, headOf, [1, 2], SEEDS).total);
  assert.ok(shown(searched) > shown(pick));
  const p = placement(plan, pick, lanes, {}, [], [1, 2], timing);
  assert.equal(p?.fits, true);
  assert.deepEqual(p?.choice, pick);
  assert.deepEqual([p?.best, p?.bestS, p?.free], [p?.now, p?.nowS, p?.nowFree]);
  assert.ok(p?.lines.every((l) => l.act !== 'move'));
});

test('pickCosts with the timing of the placement: a pick that changes nothing costs nothing', () => {
  const plan = { ...planOf('p.gcode', [0, 1, 0], 2), order_at: [10, 20, 30], size: 40 };
  const lanes = emptyLanes([2, 0, 0, 0]);
  const pick = chosen(plan, { 0: 'a:1', 1: 'a:2' });
  const timing = { frame: { start: 0, end: 25 }, timeS: 100, times: SEEDS };
  const base = placement(plan, pick, lanes, {}, [], [], timing);
  assert.deepEqual(pickCosts(plan, pick, lanes, {}, 0, 'a:1', [], [], base, timing), { d: 0, free: 0 });
});

// The real nine-colour file (3445 changes), placed with the slicer's time and heads that swap in the background, in
// the time budget of the planner tests (planner.test.js): the timing adds no search state, so it stays fast.
test('the 3445-change nine-colour file places with timing and background heads within the time budget', () => {
  const files = JSON.parse(readFileSync(new URL('./fixtures/planner-files.json', import.meta.url), 'utf8'));
  const { tools, order } = files.nine;
  const plan = {
    file: 'nine.gcode',
    mtime: 1,
    order,
    tools: tools.map((tool) => ({ ...tool, lane: null, why: 'closest' })),
    order_at: order.map((_, i) => i * 1000),
    size: order.length * 1000 + 10,
  };
  assert.equal(order.length, 3445);
  const lanes = emptyLanes([1, 4, 4, 4]);
  for (const bg of [
    [0, 1, 2, 3],
    [1, 2],
  ]) {
    for (const timeS of [20000, 200000]) {
      const start = Date.now();
      const p = placement(plan, chosen(plan, {}), lanes, {}, [], bg, { frame: null, timeS, times: SEEDS });
      assert.ok(Date.now() - start < 1000, `${bg} ${timeS}`);
      assert.equal(p?.fits, true);
      assert.ok(Number.isFinite(p?.bestS) && (p?.free ?? 0) > 0, `${bg} ${timeS}`);
    }
  }
});
