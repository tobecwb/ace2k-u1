import assert from 'node:assert/strict';
import { test } from 'node:test';
import { CLOSE, colourDistance, SIMILAR } from '../js/print-plan.js';
import { ejectList, ejectSkipped, runEjectAll, stepDone, stepsOf } from '../js/steps.js';

const white = { id: 'w', colour: '#ffffff', material: 'PETG', tag: true };
const lanes = [
  {
    key: 'a:1',
    head: 1,
    empty: false,
    material: 'PLA',
    colour: '#00ff00',
    spool: { id: 'g', colour: '#00ff00', material: 'PLA', tag: true },
  },
  { key: 'a:2', head: 1, empty: false, material: 'PETG', colour: '#ffffff', spool: white },
  { key: 'b:1', head: 2, empty: true, material: null, colour: null, spool: null },
  {
    key: 'b:2',
    head: 2,
    empty: false,
    material: null,
    colour: null,
    spool: { id: 'u', colour: null, material: null, tag: false },
    unknown: true,
  },
];
const placement = {
  fits: true,
  colours: 2,
  lanes: 4,
  now: 3,
  best: 1,
  choice: {},
  takeOut: ['a:1', 'b:2'],
  lines: [
    { tools: [0], key: 'b:1', act: 'move', from: 'a:2' },
    { tools: [1], key: 'a:1', act: 'put', from: null },
  ],
};

test('steps come out, move, put, check, in that order', () => {
  const s = stepsOf(placement, lanes, []);
  assert.deepEqual(
    s.map((x) => x.kind),
    ['out', 'out', 'move', 'put'],
  );
  assert.equal(s[1].unknown, true);
  assert.equal(s[2].spool?.id, 'w');
});

test('there lines give no step, check lines come last, a missing bay gives a null spool', () => {
  const p = {
    ...placement,
    takeOut: ['zz'],
    lines: [
      { tools: [0], key: 'a:2', act: 'check', from: null },
      { tools: [1], key: 'a:1', act: 'there', from: null },
      { tools: [2], key: 'b:1', act: 'move', from: 'nowhere' },
    ],
  };
  assert.deepEqual(stepsOf(p, lanes, []), [
    { kind: 'out', key: 'zz', spool: null },
    { kind: 'move', from: 'nowhere', key: 'b:1', spool: null, tools: [2] },
    { kind: 'check', key: 'a:2', tools: [0] },
  ]);
});

test('a move whose spool was ejected becomes put back', () => {
  const after = lanes.map((l) =>
    l.key === 'a:2' ? { ...l, empty: true, material: null, colour: null, spool: null } : l,
  );
  const inHand = { ...white, from: 'a:2' };
  const s = stepsOf(placement, after, [inHand]);
  assert.deepEqual(
    s.find((x) => x.tools?.includes(0)),
    { kind: 'putBack', key: 'b:1', spool: inHand, tools: [0] },
  );
  assert.equal(stepsOf(placement, after, []).find((x) => x.tools?.includes(0))?.kind, 'move');
});

test('a step is done only when the change is certain', () => {
  const colourOf = () => ({ colour: '#ffffff', material: 'PETG' });
  const out = { kind: 'out', key: 'a:1', spool: null };
  assert.equal(stepDone(out, lanes, colourOf), false);
  const emptied = lanes.map((l) => (l.key === 'a:1' ? { ...l, empty: true } : l));
  assert.equal(stepDone(out, emptied, colourOf), true);
  const move = { kind: 'move', from: 'a:2', key: 'b:1', spool: white, tools: [0] };
  const placed = lanes.map((l) =>
    l.key === 'b:1' ? { ...l, empty: false, material: 'PETG', colour: '#fefefe', spool: { ...white } } : l,
  );
  assert.equal(stepDone(move, placed, colourOf), true);
  const untagged = placed.map((l) => (l.key === 'b:1' ? { ...l, spool: { ...white, tag: false } } : l));
  assert.equal(stepDone(move, untagged, colourOf), false);
});

test('a spool between the similar and the close threshold still counts as done', () => {
  const colourOf = () => ({ colour: '#ffffff', material: 'PETG' });
  const d = colourDistance('#ffffff', '#c8c8c8');
  assert.ok(d > SIMILAR && d < 25, `distance ${d}`);
  const bay = lanes.map((l) =>
    l.key === 'b:1' ? { ...l, empty: false, material: 'PETG', colour: '#c8c8c8', spool: { ...white } } : l,
  );
  assert.equal(stepDone({ kind: 'put', key: 'b:1', tools: [0] }, bay, colourOf), true);
});

test('the done test cuts at CLOSE: a spool just inside it is done, one just outside it is not', () => {
  const colourOf = () => ({ colour: '#ffffff', material: 'PETG' });
  const put = { kind: 'put', key: 'b:1', tools: [0] };
  /** @param {string} colour */
  const bay = (colour) =>
    lanes.map((l) => (l.key === 'b:1' ? { ...l, empty: false, material: 'PETG', colour, spool: { ...white } } : l));
  assert.ok(colourDistance('#ffffff', '#bababa') < CLOSE && colourDistance('#ffffff', '#b8b8b8') > CLOSE);
  assert.equal(stepDone(put, bay('#bababa'), colourOf), true);
  assert.equal(stepDone(put, bay('#b8b8b8'), colourOf), false);
});

test('a step is not done for a missing bay, a check, an empty bay, another material or a far colour', () => {
  const colourOf = () => ({ colour: '#ffffff', material: 'PETG' });
  const filled = (patch) =>
    lanes.map((l) =>
      l.key === 'b:1' ? { ...l, empty: false, material: 'PETG', colour: '#ffffff', spool: { ...white }, ...patch } : l,
    );
  const put = { kind: 'put', key: 'b:1', tools: [0] };
  assert.equal(stepDone({ kind: 'put', key: 'zz', tools: [0] }, lanes, colourOf), false);
  assert.equal(stepDone({ kind: 'check', key: 'b:1', tools: [0] }, filled({}), colourOf), false);
  assert.equal(stepDone(put, lanes, colourOf), false);
  assert.equal(stepDone(put, filled({ material: 'PLA' }), colourOf), false);
  assert.equal(stepDone(put, filled({ colour: null }), colourOf), false);
  assert.equal(stepDone(put, filled({ colour: '#ff0000' }), colourOf), false);
  assert.equal(
    stepDone(put, filled({}), () => ({ colour: null, material: 'PETG' })),
    false,
  );
});

test('eject all covers every bay the steps empty, and lists the ones it must leave (stays)', () => {
  const s = stepsOf(placement, lanes, []);
  assert.deepEqual(ejectList(s), ['a:1', 'b:2', 'a:2']);
  assert.deepEqual(ejectSkipped(s), []);
  assert.deepEqual(ejectList(s, new Set(['b:2'])), ['a:1', 'a:2']);
  assert.deepEqual(ejectSkipped(s, new Set(['b:2'])), ['b:2']);
  const bare = [
    { kind: 'move', from: 'x', key: 'y', spool: null, tools: [] },
    { kind: 'put', key: 'p', tools: [] },
  ];
  assert.deepEqual(ejectList(bare), []);
});

test('eject all runs one at a time and stops at the first failure', async () => {
  const seen = [];
  const eject = async (k) => {
    if (k === 'b') throw new Error('in use');
  };
  const r = await runEjectAll(['a', 'b', 'c'], eject, (k, st) => seen.push(`${k}:${st}`));
  assert.deepEqual(r, { done: ['a'], failed: { key: 'b', msg: 'in use' } });
  assert.deepEqual(seen, ['a:running', 'a:done', 'b:running', 'b:failed']);
});

test('a second run while one runs is refused', async () => {
  let release = () => {};
  const slow = () => new Promise((res) => (release = res));
  const first = runEjectAll(['a'], slow, () => {});
  assert.equal(await runEjectAll(['b'], slow, () => {}), null);
  release();
  assert.deepEqual(await first, { done: ['a'], failed: null });
});

test('eject all takes a spool with no material out of its bay too (cold), unless it must stay', () => {
  const steps = [
    { kind: 'out', key: 'a', spool: null, unknown: true },
    { kind: 'out', key: 'b', spool: { id: 'x', colour: null, material: null, tag: false } },
    { kind: 'out', key: 'c', spool: white },
  ];
  assert.deepEqual(ejectList(steps), ['a', 'b', 'c']);
  assert.deepEqual(ejectList(steps, new Set(['a'])), ['b', 'c']);
});

test('any refusal is named, whatever it throws, and the run flag resets', async () => {
  for (const [thrown, said] of [
    ['boom', 'boom'],
    [new Error('bang'), 'bang'],
    [undefined, 'undefined'],
    [null, 'null'],
  ]) {
    const seen = [];
    const r = await runEjectAll(
      ['a'],
      async () => {
        throw thrown;
      },
      (k, st, msg) => seen.push([k, st, msg]),
    );
    assert.equal(r?.failed?.key, 'a');
    assert.equal(r?.failed?.msg, said);
    assert.equal(seen[1][2], r?.failed?.msg);
  }
  assert.deepEqual(
    await runEjectAll(
      ['z'],
      async () => {},
      () => {},
    ),
    { done: ['z'], failed: null },
  );
});

test('the run flag resets when onEach throws', async () => {
  await assert.rejects(
    runEjectAll(
      ['a'],
      async () => {},
      () => {
        throw new Error('ui');
      },
    ),
  );
  assert.deepEqual(
    await runEjectAll(
      ['b'],
      async () => {},
      () => {},
    ),
    { done: ['b'], failed: null },
  );
});

test('stepDone handles put back steps and an unknown step material', () => {
  const bay = (material) =>
    lanes.map((l) => (l.key === 'b:1' ? { ...l, empty: false, material, colour: '#ffffff', spool: { ...white } } : l));
  const back = { kind: 'putBack', key: 'b:1', spool: white, tools: [0] };
  assert.equal(
    stepDone(back, bay('PETG'), () => ({ colour: '#ffffff', material: 'PETG' })),
    true,
  );
  assert.equal(
    stepDone(back, bay('PLA'), () => ({ colour: '#ffffff', material: 'PETG' })),
    false,
  );
  assert.equal(
    stepDone(back, bay('PETG'), () => ({ colour: '#ffffff', material: null })),
    true,
  );
});
