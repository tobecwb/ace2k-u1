import { test } from 'node:test';
import assert from 'node:assert/strict';
import { segmentsOf, bands, liveAt } from '../js/timeline.js';
import { swapKinds } from '../js/print-plan.js';

const plan = { order: [0, 1, 0, 2, 1], order_at: [0, 100, 300, 600, 800], size: 1000 };
const choice = { 0: '1', 1: '2', 2: '3' };
const headOf = (k) => ({ 1: 0, 2: 0, 3: 1 })[k] ?? null;

test('segments are file fractions', () => {
  const s = segmentsOf(plan);
  assert.deepEqual(
    s.map((x) => [x.tool, x.x, x.e]),
    [
      [0, 0, 0.1],
      [1, 0.1, 0.3],
      [0, 0.3, 0.6],
      [2, 0.6, 0.8],
      [1, 0.8, 1],
    ],
  );
});

test('without offsets the segments share the file equally', () => {
  const s = segmentsOf({ order: [0, 1] });
  assert.deepEqual(
    s.map((x) => [x.x, x.e]),
    [
      [0, 0.5],
      [0.5, 1],
    ],
  );
});

test('offsets that go back or past the file fall back to equal shares', () => {
  for (const order_at of [
    [0, 8, 5],
    [0, 5, 12],
    [-1, 5, 8],
    [0, null, 8],
  ])
    assert.deepEqual(
      segmentsOf({ order: [0, 1, 2], order_at, size: 10 }).map((x) => [x.x, x.e]),
      [
        [0, 1 / 3],
        [1 / 3, 2 / 3],
        [2 / 3, 1],
      ],
    );
  // Two changes at one offset (a T repeated with nothing between) are kept.
  assert.deepEqual(
    segmentsOf({ order: [0, 1], order_at: [5, 5], size: 10 }).map((x) => x.x),
    [0.5, 0.5],
  );
});

test('a change on a lane the head already holds (merged tools) is no swap in progress', () => {
  const ch = { 0: '1', 1: '1', 2: '3' };
  const s = segmentsOf({ order: [0, 1, 2], order_at: [0, 500, 800], size: 1000 });
  const a = liveAt(s, ch, headOf, 0.51);
  assert.deepEqual(a.now, { tool: 1, head: 0, key: '1' });
  assert.equal(a.swapping, null);
});

test('offsets of the wrong length or a zero size fall back to equal shares', () => {
  assert.deepEqual(
    segmentsOf({ order: [0, 1], order_at: [0], size: 10 }).map((x) => x.e),
    [0.5, 1],
  );
  assert.deepEqual(
    segmentsOf({ order: [0, 1], order_at: [0, 5], size: 0 }).map((x) => x.e),
    [0.5, 1],
  );
});

test('bands count the same swaps as print-plan', () => {
  const b = bands(segmentsOf(plan), choice, headOf);
  const want = swapKinds(plan.order, choice, (k) => (headOf(k) === 0 ? 0 : null));
  assert.equal(b.find((x) => x.head === 0)?.swaps, want.stops);
  assert.deepEqual(b.find((x) => x.head === 0)?.marks, [0.1, 0.3, 0.8]);
  assert.equal(b.find((x) => x.head === 1)?.swaps, 0);
});

test('inactive parts carry the lane loaded on the head; a waiting head shows its first tool', () => {
  const b = bands(segmentsOf(plan), choice, headOf);
  const h1 = b.find((x) => x.head === 1);
  assert.deepEqual(
    h1?.parts.map((p) => [p.tool, p.active]),
    [
      [2, false],
      [2, false],
      [2, false],
      [2, true],
      [2, false],
    ],
  );
  const h0 = b.find((x) => x.head === 0);
  assert.deepEqual(
    h0?.parts.map((p) => [p.tool, p.active]),
    [
      [0, true],
      [1, true],
      [0, true],
      [0, false],
      [1, true],
    ],
  );
});

test('heads are ordered numerically and lanes without a head are skipped', () => {
  const ch = { 0: 'a', 1: 'b', 2: 'c' };
  const hf = (k) => ({ a: 10, b: 2 })[k] ?? null;
  const b = bands(segmentsOf({ order: [0, 1, 2] }), ch, hf);
  assert.deepEqual(
    b.map((x) => x.head),
    [2, 10],
  );
});

test('merged tools on one lane are no swap', () => {
  const ch = { 0: '1', 1: '1' };
  const b = bands(segmentsOf({ order: [0, 1] }), ch, headOf);
  assert.equal(b[0].swaps, 0);
  assert.deepEqual(b[0].marks, []);
});

test('now, swapping and next follow the position', () => {
  const s = segmentsOf(plan);
  const a = liveAt(s, choice, headOf, 0.05);
  assert.deepEqual(a.now, { tool: 0, head: 0, key: '1' });
  assert.deepEqual(a.next, { head: 0, from: 0, to: 1, x: 0.1, free: false });
  assert.equal(a.nextStop, a.next);
  assert.deepEqual(liveAt(s, choice, headOf, 0.12, 0.05).swapping, { head: 0, from: 0, to: 1 });
  assert.equal(liveAt(s, choice, headOf, 0.25, 0.05).swapping, null);
  assert.equal(liveAt(s, choice, headOf, 0.85).next, null);
});

test('a head first used later does not announce a swap for it; a laneless segment is ignored', () => {
  const ch = { 0: '1', 1: '3', 2: 'x', 3: '3' };
  const s = segmentsOf({ order: [0, 1, 2, 3] });
  assert.equal(liveAt(s, ch, headOf, 0.1).next, null);
  const b = liveAt(s, ch, headOf, 0.3);
  assert.deepEqual(b.now, { tool: 1, head: 1, key: '3' });
  assert.equal(b.swapping, null);
  assert.equal(liveAt(s, ch, headOf, 0.6).now, null);
});

test('a position before every segment has no current change', () => {
  const s = segmentsOf(plan);
  assert.equal(liveAt(s, choice, headOf, -0.1).now, null);
});

test('no plan, no timeline', () => {
  assert.deepEqual(segmentsOf(null), []);
  assert.deepEqual(bands([], {}, headOf), []);
  assert.deepEqual(liveAt([], {}, headOf, 0.5), { now: null, swapping: null, next: null, nextStop: null });
});

test('the swap in progress lasts about one swap of print time, never a third of a long part', () => {
  // One long part: tool 1 from 10 % to 90 % of the file. A swap covers 1 % of the file at this print's speed.
  const s = segmentsOf({ order: [0, 1], order_at: [0, 100], size: 1000 });
  assert.deepEqual(liveAt(s, choice, headOf, 0.105, 0.01).swapping, { head: 0, from: 0, to: 1 });
  // 2 % in: the swap is over and the card shows what prints, although that is far below 35 % of the part.
  assert.equal(liveAt(s, choice, headOf, 0.12, 0.01).swapping, null);
  assert.equal(liveAt(s, choice, headOf, 0.12, 0.01).now?.tool, 1);
  // A slow print whose swap would cover half the part is still cut at 35 % of it.
  assert.ok(liveAt(s, choice, headOf, 0.3, 0.5).swapping);
  assert.equal(liveAt(s, choice, headOf, 0.5, 0.5).swapping, null);
  // The speed not known: 5 % of the part.
  assert.ok(liveAt(s, choice, headOf, 0.14).swapping);
  assert.equal(liveAt(s, choice, headOf, 0.16).swapping, null);
});

test("segments measure the offsets against the frame (the G-code bytes), clamped to it; offsets not the file's fall back", () => {
  const frame = { start: 30, end: 1000 };
  const segs = segmentsOf({ order: [0, 1], order_at: [30, 515], size: 1000 }, frame);
  assert.deepEqual(
    segs.map((x) => [x.x, x.e]),
    [
      [0, 0.5],
      [0.5, 1],
    ],
  );
  // Changes in the header: only the last stays, at 0 (the tool the G-code starts with); none of zero length.
  const header = segmentsOf({ order: [0, 1, 2], order_at: [5, 10, 515], size: 1000 }, frame);
  assert.deepEqual(
    header.map((x) => [x.tool, x.x, x.e]),
    [
      [1, 0, 0.5],
      [2, 0.5, 1],
    ],
  );
  // A change at the frame's start itself: the header's go.
  const atStart = segmentsOf({ order: [0, 1], order_at: [10, 30], size: 1000 }, frame);
  assert.deepEqual(
    atStart.map((x) => [x.tool, x.x]),
    [[1, 0]],
  );
  // Changes in the trailing block print none of the G-code: they go.
  const tail = segmentsOf({ order: [0, 1, 2], order_at: [30, 515, 1000], size: 1100 }, { start: 30, end: 999 });
  assert.deepEqual(
    tail.map((x) => x.tool),
    [0, 1],
  );
  assert.ok([...header, ...atStart, ...tail].every((x) => x.e > x.x));
  // Without a file size, the frame's end and the trailing allowance bound the offsets.
  const noSize = segmentsOf({ order: [0, 1], order_at: [30, 1050] }, frame);
  assert.deepEqual(
    noSize.map((x) => x.tool),
    [0],
  );
  // One past the file's size: not the file's, equal shares.
  const bad = segmentsOf({ order: [0, 1, 2], order_at: [100, 515, 1500], size: 1000 }, frame);
  assert.deepEqual(
    bad.map((x) => x.x),
    [0, 1 / 3, 2 / 3],
  );
});

test('bands with the stops: late background swaps get their own marks, every swap its cost, each head its time', () => {
  const segs = [
    { tool: 0, x: 0, e: 0.25 },
    { tool: 1, x: 0.25, e: 0.5 },
    { tool: 2, x: 0.5, e: 0.75 },
    { tool: 1, x: 0.75, e: 0.8 },
    { tool: 0, x: 0.8, e: 1 },
  ];
  const ch = { 0: 'a', 1: 'c', 2: 'b' };
  const hf = (/** @type {string} */ k) => ({ a: 0, b: 0, c: 1 })[k] ?? null;
  const stops = [
    null,
    null,
    { kind: /** @type {const} */ ('late'), s: 100 },
    null,
    { kind: /** @type {const} */ ('ready'), s: 15 },
  ];
  const [h0] = bands(segs, ch, hf, [0], stops);
  assert.deepEqual([h0.marks, h0.freeMarks, h0.lateMarks], [[], [0.8], [0.5]]);
  assert.equal(h0.free, 2);
  assert.deepEqual(h0.cost, [
    { x: 0.5, kind: 'late', s: 100 },
    { x: 0.8, kind: 'ready', s: 15 },
  ]);
  assert.equal(h0.stopS, 115);
  const [plain] = bands(segs, ch, hf, [0]);
  assert.deepEqual([plain.lateMarks, plain.cost, plain.stopS], [[], [], null]);
});
