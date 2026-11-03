import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  placement,
  pickCost,
  planStats,
  SWAP_S,
  search,
  spoolMismatch,
  spoolVerdict,
  otherMaterials,
  colourVerdict,
  knownMaterials,
} from '../js/planner.js';
import { chosen, MAX_GROUPS, mergeRoot, NEW, printGate, swaps } from '../js/print-plan.js';
import { loadEnglish } from './helpers.js';
import { t } from '../js/i18n.js';

loadEnglish();

const files = JSON.parse(readFileSync(new URL('./fixtures/planner-files.json', import.meta.url), 'utf8'));

// The wiring of the U1 on 2026-10-26: a:1 and a:2 share the first head, then one lane per head.
const today = [
  { key: 'a:1', head: 0, colour: '#ffffff', material: 'PLA' },
  { key: 'a:2', head: 0, colour: '#d32c34', material: 'PLA' },
  { key: 'a:3', head: 1, colour: '#5b3f27', material: 'PLA' },
  { key: 'a:4', head: 2, colour: '#fec187', material: 'PLA' },
  { key: 'b:1', head: 3, colour: '#000000', material: 'PLA' },
];
const fivePicks = { 0: 'a:1', 1: 'a:2', 3: 'a:3', 4: 'a:4', 6: 'b:1' };
/** @param {string} file @param {{ tools: object[], order: number[] }} f @param {Record<number, string>} picks */
const planOf = (file, f, picks = {}, mtime = 1) => ({
  file,
  mtime,
  order: f.order,
  tools: f.tools.map((tool, n) => ({ ...tool, lane: picks[n] ?? null, why: 'closest' })),
});
/** Empty lanes, caps[h] of them on head h. */
const emptyLanes = (caps) =>
  caps.flatMap((c, h) =>
    Array.from({ length: c }, (_, i) => ({
      key: `${'wxyz'[h]}:${i + 1}`,
      head: h,
      colour: null,
      material: null,
      empty: true,
    })),
  );
const acts = (p) =>
  p.lines.map((l) => `${l.tools.map((n) => `T${n}`).join(',')} ${l.key} ${l.act}${l.from ? ` ${l.from}` : ''}`);

test('the 5-colour file on the wiring of today: 110 swaps now, 1 suggested, two spools change places', () => {
  const plan = planOf('five.gcode', files.five, fivePicks);
  const p = placement(plan, chosen(plan, {}), today);
  assert.equal(p.fits, true);
  assert.equal(p.now, 110);
  assert.equal(p.best, 1);
  assert.deepEqual(acts(p), ['T0 b:1 move a:1', 'T1 a:2 there', 'T3 a:3 there', 'T4 a:4 there', 'T6 a:1 move b:1']);
  assert.deepEqual(p.takeOut, []);
  assert.equal(SWAP_S, 90);
});

test('the 9-colour file: 362 swaps on 1, 4, 4 and 4 lanes; it does not fit the 5 lanes of today', () => {
  const plan = planOf('nine.gcode', files.nine);
  const wide = placement(plan, chosen(plan, {}), emptyLanes([1, 4, 4, 4]));
  assert.equal(wide.best, 362);
  assert.ok(wide.lines.every((l) => l.act === 'put'));
  const narrow = placement(plan, chosen(plan, {}), today);
  assert.deepEqual(
    { fits: narrow.fits, colours: narrow.colours, lanes: narrow.lanes, best: narrow.best, lines: narrow.lines },
    { fits: false, colours: 9, lanes: 5, best: null, lines: [] },
  );
});

test('two tools with one pick: the first keeps it', () => {
  const lanes = [
    { key: 'a:1', head: 0, colour: '#000000', material: 'PLA' },
    { key: 'a:2', head: 1, colour: null, material: null, empty: true },
  ];
  const plan = {
    file: 'two.gcode',
    mtime: 1,
    order: [0, 1, 0, 1],
    tools: [
      { colour: '#000000', material: 'PLA', lane: 'a:1' },
      { colour: '#111111', material: 'PLA', lane: 'a:1' },
    ],
  };
  const p = placement(plan, chosen(plan, {}), lanes);
  assert.deepEqual(acts(p), ['T0 a:1 there', 'T1 a:2 put']);
});

test('an empty bay or another material is no spool to keep or move', () => {
  const lanes = [
    { key: 'a:1', head: 0, colour: null, material: null, empty: true },
    { key: 'a:2', head: 1, colour: '#ffffff', material: 'PETG' },
  ];
  const plan = {
    file: 'm.gcode',
    mtime: 1,
    order: [0, 1],
    tools: [
      { colour: '#000000', material: 'PLA', lane: 'a:1' },
      { colour: '#ffffff', material: 'PLA', lane: 'a:2' },
    ],
  };
  const p = placement(plan, chosen(plan, {}), lanes);
  assert.ok(p.lines.every((l) => l.act === 'put'));
  // The PETG spool sits in a suggested lane and no tool can use it: take it out.
  assert.deepEqual(p.takeOut, ['a:2']);
});

test('a missing spool goes to an empty bay before a lane that holds another spool', () => {
  // The first head (a:1, a:2): a:1 holds a PLA spool that the file does not pick, a:2 is empty.
  const lanes = [
    { key: 'a:1', head: 0, colour: '#00ff00', material: 'PLA' },
    { key: 'a:2', head: 0, colour: null, material: null, empty: true },
  ];
  const plan = { file: 'e.gcode', mtime: 1, order: [0], tools: [{ colour: '#000000', material: 'PLA', lane: null }] };
  const p = placement(plan, chosen(plan, {}), lanes);
  assert.deepEqual(acts(p), ['T0 a:2 put']);
  assert.deepEqual(p.takeOut, []);
});

test('a lane whose material is not known can be kept', () => {
  const lanes = [{ key: 'a:1', head: 0, colour: '#000000', material: null }];
  const plan = { file: 'u.gcode', mtime: 1, order: [0], tools: [{ colour: '#000000', material: 'PLA', lane: 'a:1' }] };
  assert.deepEqual(acts(placement(plan, chosen(plan, {}), lanes)), ['T0 a:1 there']);
});

test('a tool that the file never prints gets no line; now waits for every printed tool to have a lane', () => {
  const lanes = emptyLanes([1, 1, 0, 0]);
  const plan = {
    file: 'n.gcode',
    mtime: 1,
    order: [0, 2, 0],
    tools: [
      { colour: '#000000', material: 'PLA', lane: null },
      { colour: '#ffffff', material: 'PLA', lane: null },
      { colour: '#ff0000', material: 'PLA', lane: null },
    ],
  };
  const p = placement(plan, chosen(plan, {}), lanes);
  assert.deepEqual(
    p.lines.map((l) => l.tools),
    [[0], [2]],
  );
  assert.equal(p.now, null);
  assert.equal(p.best, 0);
});

test('no T order, no block', () => {
  const tools = [{ colour: '#000000', material: 'PLA', lane: 'a:1' }];
  assert.equal(placement({ file: 'a', mtime: 1, tools }, {}, today), null);
  assert.equal(placement({ file: 'a', mtime: 1, tools, order: [] }, {}, today), null);
  assert.equal(placement({ file: 'a', mtime: 1, tools: [], order: [3] }, {}, today), null);
  assert.equal(placement(null, {}, today), null);
});

test('a new plan of the same file is worked out again', () => {
  const plan = planOf('five.gcode', files.five, fivePicks);
  const first = placement(plan, chosen(plan, {}), today);
  assert.equal(placement(plan, chosen(plan, {}), today), first); // the same inputs: the kept result
  const moved = today.map((l) => (l.key === 'a:1' ? { ...l, empty: true } : l));
  const again = { ...plan, mtime: 2 };
  const second = placement(again, chosen(again, {}), moved);
  assert.notEqual(second, first);
  assert.equal(second.lines.find((l) => l.tools.includes(0)).act, 'put'); // white's bay is now empty
});

// A brute force over every head for every group of printed tools, with the planner's own rules.
// Tools of the same colour and material form one group, and a tool with no colour is its own.
// A group owns the spool of its first tool, in tool order, that owns one. Each group is one lane,
// so the swaps are counted on the file's own T order. It checks the pair (swaps, spools moved),
// not the placement, because ties differ.
const groupKey = (tool, n) =>
  tool.colour ? `${tool.colour.toLowerCase()}|${(tool.material ?? '').trim().toLowerCase()}` : `#${n}`;
function brute(plan, pick, lanes) {
  const used = [...new Set(plan.order)].sort((a, b) => a - b);
  const keys = [...new Set(used.map((n) => groupKey(plan.tools[n], n)))];
  const group = new Map(used.map((n) => [n, keys.indexOf(groupKey(plan.tools[n], n))]));
  const caps = [0, 1, 2, 3].map((h) => lanes.filter((l) => l.head === h).length);
  const taken = new Set();
  const own = keys.map(() => null);
  for (const n of used) {
    const g = group.get(n);
    const l = lanes.find((x) => x.key === pick[n]);
    if (own[g] || !l || l.empty || taken.has(l.key)) continue;
    if (l.material && plan.tools[n].material && l.material !== plan.tools[n].material) continue;
    taken.add(l.key);
    own[g] = l;
  }
  let best = null;
  const heads = [];
  const walk = (g) => {
    if (g === keys.length) {
      const choice = Object.fromEntries(used.map((n) => [n, `${heads[group.get(n)]}:${group.get(n)}`]));
      const s = Object.values(swaps(plan.order, choice, (key) => Number(key.split(':')[0]))).reduce((a, b) => a + b, 0);
      const moved = keys.filter((_, k) => own[k]?.head !== heads[k]).length;
      if (!best || s < best[0] || (s === best[0] && moved < best[1])) best = [s, moved];
      return;
    }
    for (let h = 0; h < 4; h += 1) {
      if (heads.filter((x) => x === h).length >= caps[h]) continue;
      heads.push(h);
      walk(g + 1);
      heads.pop();
    }
  };
  walk(0);
  return best;
}

// Random cases for the brute force. With palette, the tools draw their colour from it, so some tools
// repeat one colour and material. Without it, every tool has a colour of its own.
function randomCases(count, seed, palette = null) {
  const rnd = (k) => {
    seed = (seed * 48271) % 2147483647;
    return seed % k;
  };
  const wirings = [
    [1, 1, 1, 1],
    [2, 1, 1, 1],
    [1, 4, 4, 4],
    [2, 2, 2, 2],
    [4, 4, 0, 0],
    [3, 0, 2, 1],
  ];
  const cases = [];
  for (let c = 0; c < count; c += 1) {
    const nt = 2 + rnd(palette ? 7 : 6);
    const lanes = wirings[rnd(wirings.length)].flatMap((k, h) =>
      Array.from({ length: k }, (_, i) => ({
        key: `${'abcd'[h]}:${i + 1}`,
        head: h,
        colour: null,
        material: ['PLA', 'PLA', 'PETG', null][rnd(4)],
        empty: rnd(5) === 0,
      })),
    );
    const order = [];
    for (let i = 0; i < 40; i += 1) {
      const n = rnd(nt);
      if (order.at(-1) !== n) order.push(n);
    }
    const tools = Array.from({ length: nt }, (_, n) => ({
      colour: palette ? palette[rnd(palette.length)] : `#00000${n}`,
      material: rnd(4) ? 'PLA' : 'PETG',
      lane: lanes[rnd(lanes.length)].key,
    }));
    cases.push({ plan: { file: `f${seed}-${c}`, mtime: c, order, tools }, lanes });
  }
  return cases;
}

// Checks the planner against the brute force on cases, and returns the number of cases that fit.
// Each group of tools gets one lane, and two groups never share one.
function checkCases(cases) {
  let checked = 0;
  cases.forEach(({ plan, lanes }, c) => {
    const pick = chosen(plan, {});
    const p = placement(plan, pick, lanes);
    const used = [...new Set(plan.order)];
    const groups = new Set(used.map((n) => groupKey(plan.tools[n], n)));
    if (groups.size > lanes.length) {
      assert.equal(p.fits, false, `case ${c}`);
      assert.equal(p.colours, groups.size, `case ${c}`);
      return;
    }
    const laneOf = new Map();
    for (const n of used) {
      const g = groupKey(plan.tools[n], n);
      if (laneOf.has(g)) assert.equal(p.choice[n], laneOf.get(g), `case ${c}: a group split over two lanes`);
      laneOf.set(g, p.choice[n]);
    }
    assert.equal(new Set(laneOf.values()).size, groups.size, `case ${c}: two groups in one lane`);
    const moved = new Set(p.lines.filter((l) => l.act !== 'there').map((l) => l.key)).size;
    assert.deepEqual([p.best, moved], brute(plan, pick, lanes), `case ${c}`);
    checked += 1;
  });
  return checked;
}

test('the search equals a brute force on 400 random cases, and never gives two tools one lane', () => {
  assert.ok(checkCases(randomCases(400, 7)) > 300);
});

test('with tools that repeat a colour and material, the search equals a brute force over the groups', () => {
  const checked = checkCases(randomCases(300, 11, ['#000000', '#FFFFFF', '#ffffff', '#ff0000', null]));
  assert.ok(checked > 200, `${checked}`);
});

test('two tools of the same colour and material share one spool', () => {
  // Five printed tools on four lanes: T4 is T1 again, with the colour in capitals and spaces in the material.
  const plan = {
    file: 'same.gcode',
    mtime: 1,
    order: [0, 1, 2, 3, 4, 0, 4, 1, 2],
    tools: [
      { colour: '#000000', material: 'PLA', lane: null },
      { colour: '#ff0000', material: 'PLA', lane: null },
      { colour: '#00ff00', material: 'PLA', lane: null },
      { colour: '#0000ff', material: 'PLA', lane: null },
      { colour: '#FF0000', material: ' pla ', lane: null },
    ],
  };
  const lanes = emptyLanes([1, 1, 1, 1]);
  const p = placement(plan, chosen(plan, {}), lanes);
  assert.equal(p.fits, true);
  assert.equal(p.colours, 4);
  assert.equal(p.choice[1], p.choice[4]);
  assert.equal(p.lines.length, 4);
  assert.deepEqual(p.lines[1].tools, [1, 4]);
  assert.deepEqual([p.best, 4], brute(plan, chosen(plan, {}), lanes));
  assert.equal(p.best, 0);
});

test('two tools of the same colour and material on one lane are both already there', () => {
  const lanes = [
    { key: 'a:1', head: 0, colour: '#000000', material: 'PLA' },
    { key: 'a:2', head: 0, colour: '#ffffff', material: 'PLA' },
  ];
  const plan = {
    file: 'twice.gcode',
    mtime: 1,
    order: [0, 1, 2, 0, 1],
    tools: [
      { colour: '#000000', material: 'PLA', lane: 'a:1' },
      { colour: '#ffffff', material: 'PLA', lane: 'a:2' },
      { colour: '#000000', material: 'PLA', lane: 'a:1' },
    ],
  };
  const p = placement(plan, chosen(plan, {}), lanes);
  assert.deepEqual(acts(p), ['T0,T2 a:1 there', 'T1 a:2 there']);
  assert.deepEqual(p.takeOut, []);
  assert.equal(p.now, p.best);
});

test('a group owns the spool of its first tool that owns one', () => {
  // T0 has no pick, T2 is T0 again and picks a:2. So the group of T0 and T2 keeps a:2.
  const lanes = [
    { key: 'a:1', head: 0, colour: '#ffffff', material: 'PLA' },
    { key: 'a:2', head: 1, colour: '#000000', material: 'PLA' },
  ];
  const plan = {
    file: 'own.gcode',
    mtime: 1,
    order: [0, 1, 2, 1],
    tools: [
      { colour: '#000000', material: 'PLA', lane: null },
      { colour: '#ffffff', material: 'PLA', lane: 'a:1' },
      { colour: '#000000', material: 'PLA', lane: 'a:2' },
    ],
  };
  assert.deepEqual(acts(placement(plan, chosen(plan, {}), lanes)), ['T0,T2 a:2 there', 'T1 a:1 there']);
});

test('now is a dash while a printed tool picks no lane, a lane not hooked, or a lane with no head', () => {
  const tools = [
    { colour: '#000000', material: 'PLA', lane: 'a:1' },
    { colour: '#ffffff', material: 'PLA', lane: 'a:2' },
  ];
  const plan = { file: 'now.gcode', mtime: 1, order: [0, 1, 0], tools };
  const lanes = [
    { key: 'a:1', head: 0, colour: '#000000', material: 'PLA' },
    { key: 'a:2', head: 1, colour: '#ffffff', material: 'PLA' },
  ];
  assert.equal(placement(plan, chosen(plan, {}), lanes).now, 0);
  assert.equal(placement(plan, chosen(plan, { 1: 'z:9' }), lanes).now, null);
  const headless = lanes.map((l) => (l.key === 'a:2' ? { ...l, head: null } : l));
  assert.equal(placement(plan, chosen(plan, {}), headless).now, null);
});

test('inside a head a lane that a moved tool leaves comes before a lane with an unused spool', () => {
  // a:1 holds a spool that the file does not use, a:2 holds T0's, and a:3 holds T1's.
  // T0 alternates with T1 and T2, and T1 and T2 meet once, so T0 goes alone to the head of b:1.
  // T2 takes a:2, which T0 leaves, and a:1 needs no take-out.
  const lanes = [
    { key: 'a:1', head: 0, colour: '#00ff00', material: 'PLA' },
    { key: 'a:2', head: 0, colour: '#000000', material: 'PLA' },
    { key: 'a:3', head: 0, colour: '#ffffff', material: 'PLA' },
    { key: 'b:1', head: 1, colour: null, material: null, empty: true },
  ];
  const plan = {
    file: 'vacate.gcode',
    mtime: 1,
    order: [1, 0, 1, 0, 2, 0, 2],
    tools: [
      { colour: '#000000', material: 'PLA', lane: 'a:2' },
      { colour: '#ffffff', material: 'PLA', lane: 'a:3' },
      { colour: '#ff0000', material: 'PLA', lane: null },
    ],
  };
  const p = placement(plan, chosen(plan, {}), lanes);
  assert.equal(p.best, 1);
  assert.deepEqual(acts(p), ['T0 b:1 move a:2', 'T1 a:3 there', 'T2 a:2 put']);
  assert.deepEqual(p.takeOut, []);
});

test('a change of a lane that the planner does not read keeps the result', () => {
  const plan = planOf('five.gcode', files.five, fivePicks, 3);
  const first = placement(plan, chosen(plan, {}), today);
  const unset = today.map((l) => ({ ...l, materialSet: false }));
  assert.equal(placement(plan, chosen(plan, {}), unset), first);
  // A tool that the file never prints may change its pick too.
  assert.equal(placement(plan, { ...chosen(plan, {}), 2: 'a:4' }, unset), first);
});

test('a group with no spool, on a lane whose spool nobody owns, asks to check that spool unless its colour differs', () => {
  // The suggestion gave T0 no pick. a:1 holds a PLA spool that no tool picks, of a colour not known: it may be T0's.
  const lanes = [{ key: 'a:1', head: 0, colour: null, material: 'PLA' }];
  const plan = { file: 'c.gcode', mtime: 1, order: [0], tools: [{ colour: '#000000', material: 'PLA', lane: null }] };
  const p = placement(plan, chosen(plan, {}), lanes);
  assert.deepEqual(acts(p), ['T0 a:1 check']);
  assert.deepEqual(p.takeOut, []);
  // A lane whose material is not known, or a tool whose material is not known, is checked too.
  const unknown = [{ ...lanes[0], material: null }];
  assert.deepEqual(acts(placement({ ...plan, mtime: 2 }, chosen(plan, {}), unknown)), ['T0 a:1 check']);
  const anyTool = { ...plan, mtime: 3, tools: [{ ...plan.tools[0], material: null }] };
  assert.deepEqual(acts(placement(anyTool, chosen(anyTool, {}), lanes)), ['T0 a:1 check']);
  // A spool whose colour is known: close to T0's and of its material, it is T0's spool (there); clearly not T0's (red
  // for black), one is put and that spool comes out. The same plan with the bay's colour changed is planned again,
  // not read from the memo.
  const near = [{ ...lanes[0], colour: '#0a0a0a' }];
  assert.deepEqual(acts(placement({ ...plan, mtime: 4 }, chosen(plan, {}), near)), ['T0 a:1 there']);
  // Close, but of a material not known: checked.
  const nearAny = [{ ...near[0], material: null }];
  assert.deepEqual(acts(placement({ ...plan, mtime: 4 }, chosen(plan, {}), nearAny)), ['T0 a:1 check']);
  const red = [{ ...lanes[0], colour: '#e72f1d' }];
  const p2 = placement({ ...plan, mtime: 4 }, chosen(plan, {}), red);
  assert.deepEqual([acts(p2), p2?.takeOut], [['T0 a:1 put'], ['a:1']]);
  // A tool whose colour is not known cannot be told apart from any spool: checked.
  const noColour = { ...plan, mtime: 5, tools: [{ ...plan.tools[0], colour: null }] };
  assert.deepEqual(acts(placement(noColour, chosen(noColour, {}), red)), ['T0 a:1 check']);
});

test('a group with no spool, on a lane of another material, puts one and takes that spool out', () => {
  const lanes = [{ key: 'a:1', head: 0, colour: '#00ff00', material: 'PETG' }];
  const plan = { file: 'o.gcode', mtime: 1, order: [0], tools: [{ colour: '#000000', material: 'PLA', lane: null }] };
  const p = placement(plan, chosen(plan, {}), lanes);
  assert.deepEqual(acts(p), ['T0 a:1 put']);
  assert.deepEqual(p.takeOut, ['a:1']);
});

// work() searches only when the groups fit, but a search over heads that cannot hold the groups
// must say so: no placement, never a free one with no swaps.
test('a search over heads that cannot hold the groups finds no placement', () => {
  assert.deepEqual(search([0, 1, 0], 2, [1, 0, 0, 0], [-1, -1]), {
    swaps: Infinity,
    moved: Infinity,
    masks: [],
  });
  const fits = search([0, 1, 0], 2, [1, 1, 0, 0], [-1, -1]);
  assert.equal(fits.swaps, 0);
  assert.equal(fits.masks.length, 4);
});

// Merges, new-spool picks and costs. Tools 0 and 1 are two reds of PLA, tool 2 a white PETG.
const two = {
  file: 'm.gcode',
  mtime: 1,
  set: false,
  heads: {},
  choice: null,
  raw: [],
  tools: [
    { colour: '#ff0000', material: 'PLA', lane: '1', why: 'closest' },
    { colour: '#ee1111', material: 'PLA', lane: '2', why: 'closest' },
    { colour: '#ffffff', material: 'PETG', lane: '3', why: 'closest' },
  ],
  order: [0, 1, 0, 2, 1, 0],
  lanes: [],
};
const four = [
  { key: '1', head: 0, empty: false, material: 'PLA', colour: '#ff0000' },
  { key: '2', head: 1, empty: false, material: 'PLA', colour: '#ee1111' },
  { key: '3', head: 2, empty: false, material: 'PETG', colour: '#ffffff' },
  { key: '4', head: 3, empty: true, material: null, colour: null },
];

test('a merge puts two tools of the same material on one spool', () => {
  const p = placement(two, { 0: '1', 1: '2', 2: '3' }, four, { 1: 0 });
  assert.equal(p?.choice[0], p?.choice[1]);
  assert.equal(p?.colours, 2);
  assert.equal(p?.lines.filter((l) => l.tools.includes(0)).length, 1);
  assert.deepEqual(p?.lines.find((l) => l.tools.includes(0))?.tools, [0, 1]);
});

test('a merge into a later tool gives one group, in the order of its first tool', () => {
  const p = placement(two, { 0: '1', 1: '2', 2: '3' }, four, { 0: 1 });
  assert.deepEqual(p?.lines[0].tools, [0, 1]);
  assert.equal(p?.choice[0], p?.choice[1]);
});

test('a merge across materials is ignored', () => {
  const p = placement(two, { 0: '1', 1: '2', 2: '3' }, four, { 2: 0 });
  assert.notEqual(p?.choice[2], p?.choice[0]);
  assert.equal(p?.colours, 3);
});

test('a merge into a tool the file never prints is ignored', () => {
  const plan = { ...two, order: [0, 2, 0] };
  const p = placement(plan, { 0: '1', 2: '3' }, four, { 0: 1 });
  assert.equal(p?.colours, 2);
  assert.deepEqual(p?.lines[0].tools, [0]);
});

test('a chain of merges ends on one root, and a cycle of merges still makes one group', () => {
  const plan = {
    ...two,
    tools: [...two.tools.slice(0, 2), { colour: '#dd2222', material: 'PLA', lane: '3', why: 'closest' }],
  };
  const chain = placement(plan, {}, four, { 2: 1, 1: 0 });
  assert.equal(chain?.colours, 1);
  const cycle = placement(plan, {}, four, { 0: 1, 1: 0 });
  assert.equal(cycle?.colours, 2);
  assert.equal(cycle?.choice[0], cycle?.choice[1]);
  // A chain that runs into a cycle: 0 -> 1 -> 2 -> 1. All three share one group.
  const tail = placement(plan, {}, four, { 0: 1, 1: 2, 2: 1 });
  assert.equal(tail?.colours, 1);
  assert.deepEqual(tail?.lines[0].tools, [0, 1, 2]);
});

test('a merge chain never crosses materials; a tool of no material may join a colour, never the other way round', () => {
  const plan = {
    ...two,
    order: [0, 1, 2],
    tools: [
      { colour: '#ff0000', material: 'PLA', lane: '1', why: 'closest' },
      { colour: '#ee1111', material: null, lane: '2', why: 'closest' },
      { colour: '#dd2222', material: 'PETG', lane: '3', why: 'closest' },
    ],
  };
  // PLA -> (no material) -> PETG: PLA never joins a tool of no material; that tool joins PETG.
  assert.equal(placement(plan, {}, four, { 0: 1, 1: 2 })?.colours, 2);
  assert.equal(mergeRoot(plan, { 0: 1, 1: 2 }, 0), 0);
  // (no material) -> PLA -> PETG: the tool joins PLA, and the chain stops before PETG.
  assert.equal(mergeRoot(plan, { 1: 0, 0: 2 }, 1), 0);
  // A tool of no material joins a PLA one.
  assert.equal(placement(plan, {}, four, { 1: 0 })?.colours, 2);
});

test('a NEW pick owns no spool and gets a put line', () => {
  const p = placement(two, { 0: NEW, 1: '2', 2: '3' }, four, {});
  const line = p?.lines.find((l) => l.tools.includes(0));
  assert.equal(line?.act, 'put');
});

test('a NEW pick on a group whose lane holds an unowned spool puts one and takes that spool out', () => {
  const full = four.map((l) => (l.key === '4' ? { ...l, empty: false, material: 'PLA', colour: '#00ff00' } : l));
  // Tool 1 is new too, so lanes 1 and 2 have spools that no group owns.
  const p = placement(two, { 0: NEW, 1: NEW, 2: '3' }, full, {});
  const reds = p?.lines.filter((l) => l.tools.includes(0) || l.tools.includes(1)) ?? [];
  assert.deepEqual(
    reds.map((l) => l.act),
    ['put', 'put'],
  );
  assert.deepEqual([...(p?.takeOut ?? [])].sort(), reds.map((l) => l.key).sort());
});

test('a NEW pick on one tool of a colour group leaves the whole group with no spool', () => {
  const plan = { ...two, tools: [two.tools[0], { ...two.tools[0], lane: '1' }, two.tools[2]] };
  const p = placement(plan, { 0: NEW, 1: '1', 2: '3' }, four, {});
  const line = p?.lines.find((l) => l.tools.includes(1));
  assert.deepEqual(line?.tools, [0, 1]);
  assert.equal(line?.act, 'put');
});

test('a lane with an unknown spool is never owned: its line puts a spool and the unknown one comes out', () => {
  const lanes = [
    { key: '1', head: 0, empty: false, material: null, colour: null, unknown: true },
    { key: '2', head: 1, empty: false, material: 'PLA', colour: '#ee1111' },
    { key: '3', head: 2, empty: false, material: 'PETG', colour: '#ffffff' },
  ];
  const p = placement(two, { 0: '1', 1: '2', 2: '3' }, lanes, {});
  const line = p?.lines.find((l) => l.tools.includes(0));
  assert.deepEqual([line?.key, line?.act], ['1', 'put']);
  assert.deepEqual(p?.takeOut, ['1']);
});

test('with no merges argument the placement is the one of an empty merges object', () => {
  const pick = { 0: '1', 1: '2', 2: '3' };
  const a = placement(two, pick, four);
  const b = placement(two, pick, four, {});
  assert.deepEqual(a, b);
});

test('pickCost is the change in best swaps', () => {
  const pick = { 0: '1', 1: '2', 2: '3' };
  const base = placement(two, pick, four, {})?.best ?? 0;
  const merged = placement(two, pick, four, { 1: 0 })?.best ?? 0;
  assert.equal(pickCost(two, pick, four, {}, 1, { merge: 0 }), merged - base);
  assert.equal(pickCost(two, pick, four, {}, 0, '1'), 0);
});

test('pickCost of a lane drops the tool from the merges, and NEW is priced too', () => {
  const pick = { 0: '1', 1: '2', 2: '3' };
  const merged = placement(two, pick, four, { 1: 0 })?.best ?? 0;
  const apart = placement(two, pick, four, {})?.best ?? 0;
  assert.equal(pickCost(two, pick, four, { 1: 0 }, 1, '2'), apart - merged);
  assert.equal(pickCost(two, pick, four, {}, 0, NEW), 0);
});

test("pickCost of a merge drops the tool's own pick: a NEW tool that joins keeps the group's spool", () => {
  const pick = { 0: '1', 1: NEW, 2: '3' };
  const base = placement(two, pick, four, {})?.best ?? 0;
  const joined = placement(two, { 0: '1', 2: '3' }, four, { 1: 0 });
  assert.equal(pickCost(two, pick, four, {}, 1, { merge: 0 }), (joined?.best ?? 0) - base);
  assert.equal(joined?.lines.find((l) => l.tools.includes(1))?.act, 'there');
  // With the NEW pick kept, the joined group would own nothing.
  assert.equal(placement(two, pick, four, { 1: 0 })?.lines.find((l) => l.tools.includes(1))?.act, 'put');
});

test('pickCost is 0 with no plan to place, and null for a choice that does not fit', () => {
  assert.equal(pickCost({ ...two, order: [] }, {}, four, {}, 0, '1'), 0);
  const pair = four.slice(0, 2);
  // Joined, the three tools are two colours on two lanes; apart they are three.
  assert.equal(pickCost(two, {}, pair, { 1: 0 }, 1, '2'), null);
  // From a plan that does not fit, a choice that fits gives its swaps.
  const fits = placement(two, {}, pair, { 1: 0 });
  assert.equal(pickCost(two, {}, pair, {}, 1, { merge: 0 }), fits?.best);
});

test('more colours than the search plans: not planned, and the page says why instead of freezing', () => {
  const n = MAX_GROUPS + 4;
  const tools = Array.from({ length: n }, (_, i) => ({
    colour: `#${(i * 12).toString(16).padStart(2, '0')}4080`,
    material: 'PLA',
    lane: null,
    why: 'closest',
  }));
  const plan = { file: 'many.gcode', mtime: 1, order: tools.map((_, i) => i), tools };
  const lanes = emptyLanes([6, 6, 6, 6]); // 24 bays: room for every colour
  const before = planStats.searches;
  const start = Date.now();
  const p = placement(plan, chosen(plan, {}), lanes);
  assert.ok(Date.now() - start < 1000);
  assert.equal(planStats.searches, before); // no search ran
  assert.deepEqual(
    { fits: p.fits, tooMany: p.tooMany, colours: p.colours, lanes: p.lanes, best: p.best },
    { fits: false, tooMany: true, colours: n, lanes: 24, best: null },
  );
  const gate = printGate({ plan, lanes, pick: chosen(plan, {}), place: p }, false);
  assert.equal(gate.block, t('print.too_many', { n, max: MAX_GROUPS }));
  // More colours than bays is still the plain "does not fit".
  const few = placement(plan, chosen(plan, {}), emptyLanes([2, 2, 2, 2]));
  assert.equal(few.tooMany, undefined);
  assert.equal(printGate({ plan, lanes, pick: {}, place: few }, false).block, t('print.no_fit_n', { n, m: 8 }));
});

test("a picker's costs run N + 1 searches: the pick as it is stays in the memo", () => {
  const plan = planOf('picker.gcode', files.five, fivePicks, 77);
  const pick = chosen(plan, {});
  const options = ['a:2', 'a:3', 'a:4', 'b:1', NEW];
  const before = planStats.searches;
  for (const v of options) pickCost(plan, pick, today, {}, 0, v);
  assert.equal(planStats.searches - before, options.length + 1);
  // Drawn again: every result is in the memo.
  for (const v of options) pickCost(plan, pick, today, {}, 0, v);
  assert.equal(planStats.searches - before, options.length + 1);
});

test('spoolVerdict: one test of a bay spool against every tool of a group', () => {
  const black = { colour: '#000000', material: 'PLA', lane: null };
  const skin = { colour: '#fec187', material: 'PLA', lane: null };
  const bare = { colour: '#000000', material: null, lane: null };
  const lane = (colour, material, unknown = false) => ({ colour, material, unknown });
  assert.equal(spoolVerdict(lane('#000000', null, true), [black]), 'unknown');
  assert.equal(spoolVerdict(lane('#000000', 'PETG'), [black, bare]), 'material');
  assert.equal(spoolVerdict(lane('#e72f1d', 'PLA'), [black]), 'colour');
  assert.equal(spoolVerdict(lane('#0a0a0a', 'PLA'), [black, bare]), 'match');
  assert.equal(spoolVerdict(lane('#fec187', 'PLA'), [skin, black]), 'some');
  assert.equal(spoolVerdict(lane(null, 'PLA'), [black]), 'unsure');
  assert.equal(spoolVerdict(lane('#000000', null), [black]), 'unsure');
  assert.equal(spoolVerdict(lane('#000000', 'PLA'), [bare]), 'unsure');
  assert.equal(spoolMismatch(lane('#fec187', 'PLA'), [skin, black]), null);
});

test('spoolVerdict: a group joined across materials, and a match needs the same material for every known one', () => {
  const pla = { colour: '#000000', material: 'PLA', lane: null };
  const petg = { colour: '#000000', material: 'PETG', lane: null };
  const lane = (material) => ({ colour: '#000000', material, unknown: false });
  // Some of the group's materials but not all: a check that names the conflict; none of them: another material.
  assert.equal(spoolVerdict(lane('PLA'), [pla, petg]), 'mixed');
  assert.equal(spoolVerdict(lane('ABS'), [pla, petg]), 'material');
  assert.deepEqual(otherMaterials(lane('PLA'), [pla, petg]), ['PETG']);
  // The same known material for every one: a match; a spool material not known: unsure.
  assert.equal(spoolVerdict(lane('pla'), [pla, pla]), 'match');
  assert.equal(spoolVerdict(lane(null), [pla, pla]), 'unsure');
  assert.equal(colourVerdict({ colour: '#000000' }, [pla, { ...pla, colour: '#ffffff' }]), 'some');
});

test('a group of mixed materials cannot print from one spool of either: a mismatch, told before the colour', () => {
  const pla = { colour: '#000000', material: 'PLA', lane: null };
  const petg = { colour: '#000000', material: ' petg ', lane: null };
  const lane = { colour: '#000000', material: 'PLA', unknown: false };
  assert.equal(spoolMismatch(lane, [pla, petg]), 'mixed');
  // The materials are told before the colours: a mixed group over a spool of another colour is still 'mixed'.
  assert.equal(spoolVerdict({ ...lane, colour: '#ff0000' }, [pla, petg]), 'mixed');
  // knownMaterials: each once, compared as the adapter compares them, in the first spelling.
  assert.deepEqual(knownMaterials([pla, { ...pla, material: 'pla' }, petg, { ...pla, material: null }]), [
    'PLA',
    'petg',
  ]);
});
