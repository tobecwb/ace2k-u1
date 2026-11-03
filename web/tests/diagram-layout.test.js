import { test } from 'node:test';
import assert from 'node:assert/strict';
import { layout, livePath, pale, deep } from '../js/diagram-layout.js';

// A lane of the diagram's model. head is 0-3 or null; a lane with no spool is empty.
const lane = (key, head, { empty = false, current = false, colour = '#dd3333' } = {}) => ({
  key,
  n: Number(key.slice(-1)),
  head,
  empty,
  spool: empty ? null : { colour, material: 'PLA', tag: true },
  current,
  // In its head with its spool in the bay: the path holds filament of its colour.
  filled: current && !empty,
  pathColour: current && !empty ? colour : null,
});
// A unit whose four lanes feed heads[i] (one head for all when heads is a number).
const unit = (name, heads, opts = {}) => ({
  name,
  lanes: [1, 2, 3, 4].map((n) => {
    const key = name ? `${name}:${n}` : String(n);
    const h = Array.isArray(heads) ? heads[n - 1] : heads;
    return lane(key, h, opts[n] ?? {});
  }),
});
const heads = (u1 = []) =>
  [0, 1, 2, 3].map((head) => ({
    head,
    u1Spool: u1.includes(head) ? { colour: '#eeeeee', material: 'PLA' } : null,
    loaded: null,
  }));

// The four set-ups of the approved mockup, and the U1 owner's own.
const ONE_PER_HEAD = { units: [unit(null, [0, 1, 2, 3])], heads: heads() };
const ALL_IN_T0 = { units: [unit(null, 0)], heads: heads([1, 2, 3]) };
const SHARING = { units: [unit('a', [0, 1, 2, 3]), unit('b', [0, 1, 2, 3])], heads: heads() };
const MINE = {
  units: [
    unit('a', 1, { 2: { current: true }, 4: { empty: true } }),
    unit('b', 2, { 1: { current: true } }),
    unit('c', 3),
  ],
  heads: heads([0]),
};
const many = (n) => ({
  units: Array.from({ length: n }, (_, i) => unit(String.fromCharCode(97 + i), i % 4)),
  heads: heads(),
});

const centre = (r) => r.x + r.w / 2;
const blockCentre = (l) => (l.units[0].x + l.units.at(-1).x + l.units.at(-1).w) / 2;

test('the operator set-up: each unit sits under the head it feeds', () => {
  const l = layout(MINE);
  assert.deepEqual(
    l.units.map((u) => Math.abs(centre(u) - centre(l.heads[u.name.charCodeAt(0) - 96]))),
    [0, 0, 0],
  );
  assert.equal(l.width, 1200);
});

test('four units, one per head, each sit under their head', () => {
  const l = layout(many(4));
  for (const [i, u] of l.units.entries()) assert.ok(Math.abs(centre(u) - centre(l.heads[i])) <= 1);
});

test('one unit, or units sharing heads, are one centred block', () => {
  for (const m of [ONE_PER_HEAD, ALL_IN_T0, SHARING, many(5)]) {
    const l = layout(m);
    assert.ok(Math.abs(blockCentre(l) - l.width / 2) <= 1, JSON.stringify(m.units.map((u) => u.name)));
  }
  // Two units that each feed one head, but the same one, share it: centred too.
  const same = layout({ units: [unit('a', 1), unit('b', 1)], heads: heads() });
  assert.ok(Math.abs(blockCentre(same) - same.width / 2) <= 1);
  // A unit with no hooked lane feeds no head: centred.
  const none = layout({ units: [unit('a', null), unit('b', 2)], heads: heads() });
  assert.ok(Math.abs(blockCentre(none) - none.width / 2) <= 1);
});

/** @param {{ x: number, y: number, w: number, h: number }} a @param {{ x: number, y: number, w: number, h: number }} b */
const meet = (a, b) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;

test('1, 2, 3, 4 and 8 units: no two bays overlap, and the width grows to fit', () => {
  for (const n of [1, 2, 3, 4, 8]) {
    const l = layout({ units: Array.from({ length: n }, (_, i) => unit(`u${i}`, null)), heads: heads() });
    const bays = l.units.flatMap((u) => u.bays);
    assert.equal(bays.length, 4 * n);
    for (let i = 0; i < bays.length; i++)
      for (let j = i + 1; j < bays.length; j++) assert.ok(!meet(bays[i], bays[j]), `${n} units: ${i} and ${j}`);
    for (const b of bays) assert.ok(b.x >= 0 && b.x + b.w <= l.width, `${n} units: a bay inside the view`);
    assert.ok(l.width >= 1200);
  }
  const eight = layout(many(8));
  assert.ok(eight.width > 1200);
  // The heads stay centred over the wider view.
  assert.ok(Math.abs((eight.heads[0].x + eight.heads[3].x + eight.heads[3].w) / 2 - eight.width / 2) <= 1);
});

test('the geometry of the mockup: heads, units and bays', () => {
  const l = layout(MINE);
  assert.deepEqual(
    l.heads.map((h) => [h.head, h.x, h.y, h.w, h.h]),
    [
      [0, 18, 26, 264, 96],
      [1, 318, 26, 264, 96],
      [2, 618, 26, 264, 96],
      [3, 918, 26, 264, 96],
    ],
  );
  assert.deepEqual(
    l.units.map((u) => [u.name, u.x, u.y, u.w, u.h]),
    [
      ['a', 314, 330, 272, 182],
      ['b', 614, 330, 272, 182],
      ['c', 914, 330, 272, 182],
    ],
  );
  assert.deepEqual(
    l.units[0].bays.map((b) => [b.key, b.x, b.y, b.w, b.h]),
    [
      ['a:1', 329, 342, 56, 128],
      ['a:2', 391, 342, 56, 128],
      ['a:3', 453, 342, 56, 128],
      ['a:4', 515, 342, 56, 128],
    ],
  );
  assert.equal(l.height, 520);
  const four = layout(SHARING);
  assert.deepEqual(
    four.units.map((u) => u.x),
    [319, 609],
  );
});

test('one rail per hooked lane; a lane with no head has none', () => {
  assert.equal(layout(MINE).rails.length, 12);
  assert.equal(layout(ALL_IN_T0).rails.length, 4);
  const partly = { units: [unit(null, [0, null, 2, null])], heads: heads() };
  assert.deepEqual(
    layout(partly).rails.map((r) => r.key),
    ['1', '3'],
  );
});

test('a rail is dashed when its lane is empty; a bus segment when only empty lanes pass it', () => {
  const l = layout(MINE);
  assert.deepEqual(
    l.rails.filter((r) => r.dashed).map((r) => r.key),
    ['a:4'],
  );
  // Unit a: lanes at 357, 419, 481, 543 and the riser of T1 at 450. Only a:4 passes 481–543.
  const t1 = l.segments.filter((s) => s.head === 1);
  assert.deepEqual(
    t1.map((s) => [s.d, s.dashed]),
    [
      ['M357 296 L419 296', false],
      ['M419 296 L450 296', false],
      ['M450 296 L481 296', false],
      ['M481 296 L543 296', true],
      ['M450 296 L450 140', false],
    ],
  );
  // Each fed head has its own bus level, 22 px apart; T0, fed by the U1, has none.
  assert.deepEqual([...new Set(l.segments.map((s) => s.head))], [1, 2, 3]);
  assert.deepEqual(
    l.rails.filter((r) => r.key.endsWith(':1')).map((r) => r.y),
    [296, 274, 252],
  );
  // A head whose every lane is empty: its riser is dashed too.
  const empty = layout({
    units: [unit(null, 0, { 1: { empty: true }, 2: { empty: true }, 3: { empty: true }, 4: { empty: true } })],
    heads: heads(),
  });
  assert.ok(empty.segments.every((s) => s.dashed));
});

test('the lane in a head is one live path in its colour, from its bay to the nozzle', () => {
  const l = layout(MINE);
  const live = l.rails.filter((r) => r.live);
  assert.deepEqual(
    live.map((r) => [r.key, r.colour]),
    [
      ['a:2', '#dd3333'],
      ['b:1', '#dd3333'],
    ],
  );
  assert.equal(live[0].d, 'M419 342 L419 310 Q419 296 433 296 L436 296 Q450 296 450 282 L450 142');
  // A plain lane rail is its stub from the bay up to the bus.
  assert.equal(l.rails.find((r) => r.key === 'a:1').d, 'M357 342 L357 296');
  // A lane to the right of its head turns left on its way to the riser.
  const right = layout({ units: [unit('a', 1, { 4: { current: true } }), unit('b', 2)], heads: heads() });
  assert.equal(
    right.rails.find((r) => r.live).d,
    'M543 342 L543 310 Q543 296 529 296 L464 296 Q450 296 450 282 L450 142',
  );
});

test('livePath: straight up under the head; a close lane turns with half the gap as radius', () => {
  assert.equal(livePath(450, 296, 450), 'M450 342 L450 142');
  assert.equal(livePath(440, 296, 450), 'M440 342 L440 301 Q440 296 445 296 L445 296 Q450 296 450 291 L450 142');
});

test('pale: white, light grey and yellow are pale; red, black and nothing are not', () => {
  for (const c of ['#ffffff', '#fff', '#eeeeee', '#ddd', '#ffcc33', '#FC3']) assert.ok(pale(c), c);
  for (const c of ['#d33', '#dd3333', '#222222', '#3a3', null, 'red', '']) assert.ok(!pale(c), String(c));
});

test('deep: black and near-black are deep; colours and nothing are not', () => {
  for (const c of ['#000', '#111111', '#222']) assert.ok(deep(c), c);
  for (const c of ['#d33', '#ffffff', '#666666', null, 'black']) assert.ok(!deep(c), String(c));
});
