import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { headOfIn, swaps } from '../js/print-plan.js';
import { estimateMs } from '../js/reading.js';
import { bands, liveAt, segmentsOf } from '../js/timeline.js';

/** @param {string} name @returns {any} */
const demo = (name) => JSON.parse(readFileSync(new URL(`../dev/demo-${name}.json`, import.meta.url), 'utf8'));
const NAMES = ['status', 'one', 'mine', 'four', 'unknown', 'printing', 'dryer-fault'];

test('every scenario parses and keeps its adapter and print state', () => {
  for (const n of NAMES) {
    const s = demo(n);
    assert.ok(s.ace2k_u1 && s.print_stats && s.print_task_config, n);
  }
});

test('the unit count of each scenario', () => {
  assert.equal(demo('one').ace2k_units, undefined);
  assert.equal(demo('mine').ace2k_units.units.length, 3);
  assert.equal(demo('four').ace2k_units.units.length, 4);
  for (const n of ['mine', 'four']) {
    for (const u of demo(n).ace2k_units.units) assert.equal(demo(n)[u].lanes.length, 4, `${n} ${u}`);
  }
});

test('mine: head 0 is the printer’s own spool, heads 1-3 are units a, b and c', () => {
  const { heads, current } = demo('mine').ace2k_u1;
  assert.equal(heads[0], null);
  assert.equal(current[0], null);
  ['a', 'b', 'c'].forEach((u, i) => {
    assert.deepEqual(
      heads[i + 1],
      [1, 2, 3, 4].map((lane) => ({ unit: u, lane })),
    );
  });
});

test('mine: unit a is drying, b and c are idle', () => {
  const s = demo('mine');
  assert.deepEqual(
    ['a', 'b', 'c'].map((u) => s[`ace2k ${u}`].dryer.state),
    ['heating', 'idle', 'idle'],
  );
  assert.ok(s['ace2k a'].dryer.remaining > 0 && s['ace2k a'].dryer.target > 0);
});

test('unknown: a tag spool, a spool set by hand, a spool of no material and an empty bay', () => {
  const s = demo('unknown');
  const [tagged, byHand, bare, empty] = s.ace2k.lanes;
  assert.equal(tagged.tag.state, 'read');
  assert.deepEqual([tagged.mode, tagged.error], ['error', 'stuck'], 'the tagged lane is stuck, for the error view');
  assert.equal(byHand.tag.record, null);
  assert.ok(s.ace2k_u1.spools['-:2']);
  assert.ok(bare.insert && bare.tag.record === null);
  assert.equal(s.ace2k_u1.spools['-:3'], undefined);
  assert.equal(empty.insert, false);
});

test('printing: a plan with real offsets from a five-colour file, 40 % printed', () => {
  const s = demo('printing');
  const p = s.ace2k_u1.plan;
  assert.equal(s.print_stats.state, 'printing');
  assert.ok(p.order.length > 0 && p.order_at.length === p.order.length && p.size > 0);
  assert.equal(s.virtual_sdcard.file_position, Math.round(p.size * 0.4));
  assert.equal(s.virtual_sdcard.progress, 0.4);
});

test('printing: heads swap spools, the plan matches its bays and "now" and "next" are real', () => {
  const s = demo('printing');
  const p = s.ace2k_u1.plan;
  const headOf = headOfIn(p.lanes);
  const counts = swaps(p.order, p.choice, headOf);
  assert.ok(Object.values(counts).some((n) => Number(n) > 0));
  assert.deepEqual(p.heads, counts);
  const segs = segmentsOf(p);
  assert.ok(bands(segs, p.choice, headOf).some((b) => b.swaps > 0));
  const live = liveAt(segs, p.choice, headOf, s.virtual_sdcard.file_position / p.size);
  assert.ok(live.next);
  assert.ok(p.lanes.some((l) => l.key === live.now?.key));
  // Each tool's lane exists, is not empty and carries the tool's colour; the bays agree with the config.
  p.tools.forEach((/** @type {any} */ tool, /** @type {number} */ i) => {
    const lane = p.lanes.find((/** @type {any} */ l) => l.key === tool.lane);
    assert.ok(lane && !lane.empty, `tool ${i}`);
    assert.equal(p.choice[i], tool.lane);
    if (p.order.includes(i)) assert.equal(lane.colour, tool.colour, `colour of tool ${i}`);
  });
  p.lanes.forEach((/** @type {any} */ l) => {
    const [unit, n] = l.key.split(':');
    assert.equal(s[`ace2k ${unit}`].lanes[Number(n) - 1].insert, !l.empty);
    assert.equal(s.print_task_config.filament_exist[l.head], true);
  });
});

test('printing: each head holds the bay the plan has loaded at the print position', () => {
  const s = demo('printing');
  const p = s.ace2k_u1.plan;
  const headOf = headOfIn(p.lanes);
  const pos = s.virtual_sdcard.file_position / p.size;
  const loaded = new Map();
  for (const g of segmentsOf(p)) if (g.x <= pos) loaded.set(headOf(p.choice[g.tool]), p.choice[g.tool]);
  for (const [head, key] of loaded) {
    const cur = s.ace2k_u1.current[head];
    assert.equal(`${cur.unit}:${cur.lane}`, key, `T${head}`);
  }
  assert.equal(liveAt(segmentsOf(p), p.choice, headOf, pos).now.key, 'a:2');
});

test('dryer-fault: a unit whose dryer is in fault (the heater did not warm up)', () => {
  assert.equal(demo('dryer-fault').ace2k.dryer.state, 'fault');
});

test('files: the big nine-colour file has its size, a card and a plan that arrives after its estimate', () => {
  const d = demo('files');
  const big = d.files.find((/** @type {any} */ f) => f.path === 'big-9-colours.gcode');
  assert.equal(big.size, 254594261);
  assert.ok(d.files.every((/** @type {any} */ f) => f.size > 0));
  const card = d.cards['big-9-colours.gcode'];
  assert.equal(card.colours.length, 9);
  assert.equal(card.time, 2 * 86400 + 9 * 3600 + 51 * 60);
  const plan = d.plans['big-9-colours.gcode'];
  assert.equal(plan.tools.length, 9);
  assert.ok(plan.delayMs > /** @type {number} */ (estimateMs(big.size)));
});
