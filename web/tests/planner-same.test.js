import { test } from 'node:test';
import assert from 'node:assert/strict';
import { placement } from '../js/planner.js';
import { chosen } from '../js/print-plan.js';
import { placement as oldPlacement } from './planner-old.js';

// The faster search must give the same placement as the one before it: the same swaps, the same
// lanes and, among equal results, the same pick. So both run on many generated cases and must agree
// on the whole result.
// With tests/planner-old.js, this proves that the rewrite of the search kept the result and the
// order among equal results of the planner as of 2026-10-27. A change to the planner's rule on
// purpose replaces this pair by tests with fixed expected results, or deletes it.
const WIRINGS = [
  [1, 1, 1, 1],
  [2, 1, 1, 1],
  [1, 4, 4, 4],
  [2, 2, 2, 2],
  [4, 4, 0, 0],
  [3, 0, 2, 1],
  [3, 3, 3, 3],
  [4, 4, 4, 4],
];
const PALETTE = ['#ff0000', '#00ff00', '#0000ff', '#ffffff', '#000000', '#888888', null];

// The cases: tools up to max, a T order of 2 to 21 or of 30 to 199 lines, lanes of each wiring with
// materials, empty bays and spools picked by the tools. Some tools share a colour and material.
function cases(count, seed, max) {
  const rnd = (k) => {
    seed = (seed * 48271) % 2147483647;
    return seed % k;
  };
  const out = [];
  for (let c = 0; c < count; c += 1) {
    const lanes = WIRINGS[rnd(WIRINGS.length)].flatMap((k, h) =>
      Array.from({ length: k }, (_, i) => ({
        key: `${'abcd'[h]}:${i + 1}`,
        head: h,
        colour: null,
        material: ['PLA', 'PLA', 'PETG', null][rnd(4)],
        empty: rnd(5) === 0,
      })),
    );
    const nt = 1 + rnd(max);
    const distinct = rnd(2) === 0;
    const tools = Array.from({ length: nt }, (_, n) => ({
      colour: distinct ? `#0000${String(n).padStart(2, '0')}` : PALETTE[rnd(PALETTE.length)],
      material: rnd(4) ? 'PLA' : 'PETG',
      lane: rnd(6) ? lanes[rnd(lanes.length)].key : null,
    }));
    // A file of one tool has one T line: two in a row would be the same tool. Short files have
    // many placements of the fewest swaps, so they test which one is kept.
    const order = [];
    const length = nt === 1 ? 1 : rnd(2) ? 2 + rnd(20) : 30 + rnd(170);
    while (order.length < length) {
      const n = rnd(nt);
      if (order.at(-1) !== n) order.push(n);
    }
    out.push({ plan: { file: `same-${seed}-${c}`, mtime: c, order, tools }, lanes });
  }
  return out;
}

/** @param {{ plan: object, lanes: object[] }[]} list */
function compare(list) {
  let fits = 0;
  list.forEach(({ plan, lanes }, c) => {
    const pick = chosen(plan, {});
    const now = placement(plan, pick, lanes);
    assert.deepEqual(now, oldPlacement(plan, pick, lanes), `case ${c}`);
    if (now?.fits) fits += 1;
  });
  return fits;
}

test('the search gives the same placement as the search before it, on 3000 generated cases', () => {
  assert.ok(compare(cases(3000, 5, 10)) > 1500);
});

test('the same on 40 cases of up to 16 tools', () => {
  assert.ok(compare(cases(40, 9, 16)) > 10);
});
