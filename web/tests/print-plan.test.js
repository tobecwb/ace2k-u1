import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { t } from '../js/i18n.js';
import {
  nextOptions,
  flowHeadsOf,
  printScript,
  planScript,
  swaps,
  chosen,
  choiceFor,
  carriedChoice,
  refusalAction,
  blockReason,
  blocked,
  printGate,
  sentSwaps,
  noFitWords,
  MAX_GROUPS,
  COLOUR_NAMES,
  colourName,
  bayProblem,
  usedTest,
  colourDistance,
  CLOSE,
  SIMILAR,
  liveLanes,
  similarPairs,
  highTools,
} from '../js/print-plan.js';
import { cancelScript } from '../js/model.js';
import { loadEnglish } from './helpers.js';

loadEnglish();
const plan = {
  file: 'eight.gcode',
  set: false,
  tools: [
    { colour: '#000000', material: 'PLA', lane: 'a:1', why: 'closest' },
    { colour: '#ffffff', material: 'PLA', lane: 'a:2', why: 'closest' },
    { colour: '#800080', material: 'PLA', lane: null, why: 'no_colour' },
  ],
  heads: { 0: 4 },
};
const lanes = [
  { key: 'a:1', head: 0, material: 'PLA' },
  { key: 'a:2', head: 0, material: 'PLA' },
  { key: 'b:4', head: 3, material: 'PLA' },
];

test('Print sets the plan, then starts the file the way the screen does', () => {
  const choice = { 0: 'a:1', 1: 'a:2', 2: 'b:4' };
  const options = { bedLevel: true, flow: false, flowHeads: [], timeLapse: false, endUnload: false };
  assert.equal(
    printScript('eight.gcode', choice, lanes, [0, 1, 2], options),
    [
      'ACEK_PLAN_SET FILE="eight.gcode" T0=a:1 T1=a:2 T2=b:4',
      'SDCARD_PRINT_FILE_WITH_PARAMETERS FILENAME="eight.gcode" MAP_TABLE="[[0, 0], [1, 0], [2, 3]]"' +
        ' BED_LEVEL=1 FLOW_CALIBRATE=0 FLOW_CALIBRATE_EXTRUDERS="[]" TIME_LAPSE_CAMERA=0' +
        ' END_UNLOAD_FILAMENT="[0, 0, 0, 0]" FILAMENT_USED_MM="[1, 1, 1]"',
    ].join('\n'),
  );
});

test('the options reach the start: flow on the chosen heads, time-lapse, unload on the used heads', () => {
  const choice = { 0: 'a:1', 2: 'b:4' };
  const options = { bedLevel: false, flow: true, flowHeads: [3, 1], timeLapse: true, endUnload: true };
  const line = printScript('x.gcode', choice, lanes, null, options).split('\n')[1];
  assert.ok(line.includes('BED_LEVEL=0 FLOW_CALIBRATE=1 FLOW_CALIBRATE_EXTRUDERS="[3]" TIME_LAPSE_CAMERA=1'));
  assert.ok(line.includes('END_UNLOAD_FILAMENT="[1, 0, 0, 1]" FILAMENT_USED_MM="[1, 0, 1]"'));
  // the adapter unloads the other lanes of a shared head that the print used
  assert.equal(
    printScript('x.gcode', choice, lanes, null, options).split('\n')[0],
    'ACEK_PLAN_SET FILE="x.gcode" T0=a:1 T2=b:4 END_UNLOAD=1',
  );
});

test("the swap count is the plan module's rule", () => {
  const choice = { 0: 'a:1', 1: 'a:2', 2: 'b:1' };
  const head = { 'a:1': 0, 'a:2': 0, 'b:1': 1 };
  assert.deepEqual(
    swaps([0, 2, 1, 2, 0, 1], choice, (k) => head[k]),
    { 0: 3, 1: 0 },
  );
});

test('a file name with a space is quoted everywhere; a quote in it is refused', () => {
  const choice = { 1: 'a:2', 0: 'a:1', 2: 'b:4' }; // keys in any order, as JSON gives them
  const script = printScript('two words.gcode', choice, lanes).split('\n');
  assert.equal(script[0], 'ACEK_PLAN_SET FILE="two words.gcode" T0=a:1 T1=a:2 T2=b:4');
  assert.ok(
    script[script.length - 1].startsWith('SDCARD_PRINT_FILE_WITH_PARAMETERS FILENAME="two words.gcode" MAP_TABLE='),
  );
  assert.equal(planScript('two words.gcode'), 'ACEK_PLAN FILE="two words.gcode"');
  assert.throws(() => planScript('a"b.gcode'), { message: t('print.bad_name', { name: 'a"b.gcode' }) });
  assert.throws(() => printScript('a;b.gcode', choice, lanes));
});

test('Print refused for a file changed since its plan re-plans it, saying so', () => {
  const r = refusalAction('ace2k_u1: two words.gcode changed since its plan: plan it again', 'two words.gcode');
  assert.equal(r.script, 'ACEK_PLAN FILE="two words.gcode"');
  assert.equal(r.toast, t('print.replanned', { file: 'two words.gcode' }));
  assert.equal(refusalAction('ace2k_u1: T2: no hooked lane b:9', 'eight.gcode'), null);
});

// The adapter's refusal lines. tests/test_page_refusals.py produces each one and compares it.
const refusals = JSON.parse(readFileSync(new URL('./fixtures/adapter-refusals.json', import.meta.url), 'utf8'));

test('the adapter\'s refusal lines: only "changed since its plan" plans the file again', () => {
  assert.ok(refusals.length >= 2);
  for (const { case: name, file, line, replan } of refusals) {
    const r = refusalAction(line, file);
    assert.deepEqual(r?.script ?? null, replan ? `ACEK_PLAN FILE="${file}"` : null, name);
  }
});

test("the page's choice belongs to its file: a re-plan with a new mtime carries the lanes still offered", () => {
  const p1 = { ...plan, mtime: 100, lanes };
  const print = { choice: { 0: 'a:2', 2: 'b:4' }, choiceId: 'eight.gcode\n100', choiceTools: plan.tools };
  assert.deepEqual(choiceFor(print, p1), { 0: 'a:2', 2: 'b:4' });
  assert.deepEqual(choiceFor(print, { ...p1, mtime: 200 }), { 0: 'a:2', 2: 'b:4' });
  assert.deepEqual(choiceFor(print, { ...p1, file: 'other.gcode' }), {});
  assert.deepEqual(choiceFor(print, null), {});
  // a:2 is no longer offered to T0, because its spool is PETG now. T2 is gone from the file.
  const p2 = { ...p1, mtime: 200, tools: plan.tools.slice(0, 2), lanes: [lanes[0], { ...lanes[1], material: 'PETG' }] };
  assert.deepEqual(carriedChoice(print, p2), {
    choice: {},
    dropped: [
      { n: 0, key: 'a:2' },
      { n: 2, key: 'b:4' },
    ],
    merges: {},
    pinned: {},
  });
  const p3 = { ...p1, mtime: 300, lanes: undefined };
  assert.equal(carriedChoice(print, p3).dropped.length, 2); // no lane listed, so none is offered
  // the bay of b:4 is empty now
  const p4 = { ...p1, mtime: 400, lanes: [lanes[0], lanes[1], { ...lanes[2], empty: true }] };
  assert.deepEqual(carriedChoice(print, p4).dropped, [{ n: 2, key: 'b:4' }]);
});

test('a carried choice is dropped when its tool changed colour or material (the file sliced again)', () => {
  const p1 = { ...plan, mtime: 100, lanes };
  const print = { choice: { 0: 'a:2', 1: 'a:1', 2: 'b:4' }, choiceId: 'eight.gcode\n100', choiceTools: plan.tools };
  const resliced = {
    ...p1,
    mtime: 200,
    tools: [
      { ...plan.tools[0], colour: '#FF0000' }, // another colour
      { ...plan.tools[1], colour: '#FFFFFF' }, // the same colour, in capitals
      { ...plan.tools[2], material: 'PETG' }, // another material
    ],
  };
  assert.deepEqual(carriedChoice(print, resliced), {
    choice: { 1: 'a:1' },
    dropped: [
      { n: 0, key: 'a:2' },
      { n: 2, key: 'b:4' },
    ],
    merges: {},
    pinned: {},
  });
  // a material written with other case or spaces is the same material
  const spaced = { ...p1, mtime: 300, tools: plan.tools.map((x) => ({ ...x, material: ' pla ' })) };
  assert.deepEqual(carriedChoice(print, spaced).dropped, []);
  // With no record of the tools that the choice was made for, nothing is carried.
  assert.equal(carriedChoice({ ...print, choiceTools: null }, { ...p1, mtime: 200 }).dropped.length, 3);
});

test('a tool whose material is not known keeps its carried lane, as its select lists it and the adapter accepts it', () => {
  const tools = [{ colour: '#000000', material: null, lane: null, why: 'no_material' }];
  const p1 = { ...plan, mtime: 100, tools, lanes };
  const print = { choice: { 0: 'b:4' }, choiceId: 'eight.gcode\n100', choiceTools: tools };
  assert.deepEqual(carriedChoice(print, { ...p1, mtime: 200 }), {
    choice: { 0: 'b:4' },
    dropped: [],
    merges: {},
    pinned: {},
  });
  // a lane whose own material is not known is kept as well
  const unknownLane = { ...p1, mtime: 300, lanes: [{ key: 'b:4', head: 3, material: null }] };
  assert.deepEqual(carriedChoice(print, unknownLane).choice, { 0: 'b:4' });
});

test("a set plan's choice comes before the suggestion, the page's change before both", () => {
  const set = { ...plan, set: true, choice: { 0: 'a:2', 1: 'a:1', 2: 'b:4' } };
  assert.deepEqual(chosen(set, {}), { 0: 'a:2', 1: 'a:1', 2: 'b:4' });
  assert.deepEqual(chosen(set, { 0: 'a:1' }), { 0: 'a:1', 1: 'a:1', 2: 'b:4' });
});

test('flow calibration never goes out with no head: none ticked among the used means every used head', () => {
  const choice = { 0: 'a:1', 2: 'b:4' };
  const line = printScript('x.gcode', choice, lanes, null, { flow: true, flowHeads: [2] }).split('\n')[1];
  assert.ok(line.includes('FLOW_CALIBRATE=1 FLOW_CALIBRATE_EXTRUDERS="[0, 3]"'));
  assert.deepEqual(flowHeadsOf({ flow: true, flowHeads: [3, 9] }, [0, 3]), [3]);
  assert.deepEqual(flowHeadsOf({ flow: false, flowHeads: [3] }, [0, 3]), []);
});

test('nextOptions: flow on ticks the used heads, a head box toggles one, plain options flip', () => {
  const o = nextOptions({}, { opt: 'flow', on: true }, [0, 3]);
  assert.deepEqual([o.flow, o.flowHeads], [true, [0, 3]]);
  assert.deepEqual(nextOptions(o, { opt: 'flow_head', on: false, head: '3' }, [0, 3]).flowHeads, [0]);
  assert.equal(nextOptions(o, { opt: 'flow', on: false }, [0, 3]).flowHeads.length, 0);
  assert.equal(nextOptions({}, { opt: 'timeLapse', on: true }, []).timeLapse, true);
  assert.equal(nextOptions({}, { opt: 'bedLevel', on: false }, []).bedLevel, false);
  assert.deepEqual(nextOptions({}, { opt: 'nonsense', on: true }, []), nextOptions({}, { opt: 'x', on: false }, []));
});

test('nextOptions: unticking the last flow head turns flow calibration off', () => {
  const o = nextOptions({}, { opt: 'flow', on: true }, [0, 3]);
  const one = nextOptions(o, { opt: 'flow_head', on: false, head: '3' }, [0, 3]);
  const none = nextOptions(one, { opt: 'flow_head', on: false, head: '0' }, [0, 3]);
  assert.deepEqual([none.flow, none.flowHeads], [false, []]);
  assert.deepEqual(flowHeadsOf(none, [0, 3]), []);
  const line = printScript('x.gcode', { 0: 'a:1', 2: 'b:4' }, lanes, null, none).split('\n')[1];
  assert.ok(line.includes('FLOW_CALIBRATE=0 FLOW_CALIBRATE_EXTRUDERS="[]"'));
});

test('blockReason: a print on, tools without a spool, of another material, beyond T31; null when nothing blocks', () => {
  assert.equal(blockReason(plan, { 0: 'a:1', 1: 'a:2', 2: 'b:4' }, lanes), null);
  assert.equal(blockReason(plan, { 0: 'a:1', 1: 'a:2', 2: 'b:4' }, lanes, true), t('print.busy'));
  const other = [...lanes, { key: 'a:3', head: 1, material: 'PETG' }];
  assert.equal(
    blockReason(plan, { 0: 'a:3' }, other),
    `${t('print.choose_first', { tools: 'color 2, color 3' })} ${t('print.material_first', { tools: 'color 1' })}`,
  );
  const big = { ...plan, tools: Array.from({ length: 33 }, () => ({ material: 'PLA' })), order: [0, 32] };
  assert.equal(blockReason(big, { 0: 'a:1', 32: 'a:2' }, lanes), t('print.tool_limit', { tools: 'color 33' }));
});

test('printGate: Print sends the placement; a put pending, another material, no bay or a print on blocks', () => {
  const place = (choice, fits, takeOut) => ({ fits, choice, colours: 3, lanes: 2, takeOut });
  const now = (choice, at = lanes, fits = true, takeOut = []) => ({
    plan,
    lanes: at,
    pick: { 0: 'b:4' },
    place: place(choice, fits, takeOut),
  });
  const pick = { 0: 'a:1', 1: 'a:2', 2: 'b:4' };
  assert.deepEqual(printGate(now(pick), false), { choice: pick, block: null, asIs: false });
  // An empty bay still to fill: the colour has no spool yet.
  const emptyA2 = lanes.map((l) => (l.key === 'a:2' ? { ...l, empty: true, material: null } : l));
  assert.equal(printGate(now(pick, emptyA2), false).block, t('print.no_spool', { tools: 'color 2', key: 'a:2' }));
  // A spool of another material in a bay of the placement, a colour with no bay, a print on.
  const petg = lanes.map((l) => (l.key === 'a:2' ? { ...l, material: 'PETG' } : l));
  assert.equal(printGate(now(pick, petg), false).block, t('print.material_first', { tools: 'color 2' }));
  // The same spool, when the steps take it out first: a put still pending, the colour has no spool yet.
  assert.equal(
    printGate(now(pick, petg, true, ['a:2']), false).block,
    t('print.put_pending', { tools: 'color 2', key: 'a:2' }),
  );
  assert.equal(printGate(now({ 0: 'a:1', 1: 'a:2' }), false).block, t('print.choose_first', { tools: 'color 3' }));
  assert.equal(printGate(now(pick), true).block, t('print.busy'));
  // A tool the file never prints needs no bay.
  assert.equal(printGate({ ...now({ 0: 'a:1' }), plan: { ...plan, order: [0] } }, false).block, null);
  // More colours than bays: blocked, nothing to send.
  assert.deepEqual(printGate(now(pick, lanes, false), false), {
    choice: {},
    block: t('print.no_fit_n', { n: 3, m: 2 }),
    asIs: false,
  });
  // No placement (no T order read): the picks, gated as before the guide.
  assert.deepEqual(printGate({ ...now(pick), place: null }, false), {
    choice: { 0: 'b:4' },
    block: blockReason(plan, { 0: 'b:4' }, lanes),
    asIs: true,
  });
});

test('printGate: a bay with no material set blocks; a put pending into a bay blocks whatever the bay holds', () => {
  const pick = { 0: 'a:1', 1: 'a:2', 2: 'b:4' };
  const now = (at, takeOut = []) => ({
    plan,
    lanes: at,
    pick,
    place: { fits: true, choice: pick, colours: 3, lanes: 3, takeOut, lines: [] },
  });
  const unsetA2 = lanes.map((l) => (l.key === 'a:2' ? { ...l, materialSet: false } : l));
  assert.equal(printGate(now(unsetA2), false).block, t('print.material_unset', { tools: 'color 2' }));
  // Set, or not said (an older adapter): no block.
  assert.equal(printGate(now(lanes.map((l) => ({ ...l, materialSet: true }))), false).block, null);
  // A put pending into a:2: empty, "no spool"; a spool the steps take out, whatever it is (no material set too),
  // "put its spool there", as the step says.
  const emptyA2 = unsetA2.map((l) => (l.key === 'a:2' ? { ...l, empty: true, material: null } : l));
  assert.equal(printGate(now(emptyA2), false).block, t('print.no_spool', { tools: 'color 2', key: 'a:2' }));
  assert.equal(printGate(now(unsetA2, ['a:2']), false).block, t('print.put_pending', { tools: 'color 2', key: 'a:2' }));
  const petg = lanes.map((l) => (l.key === 'a:2' ? { ...l, material: 'PETG', materialSet: true } : l));
  assert.equal(printGate(now(petg, ['a:2']), false).block, t('print.put_pending', { tools: 'color 2', key: 'a:2' }));
  // A spool of no known material the steps keep, whatever its status says (unread, an older adapter): the Define
  // reason.
  const bare = lanes.map((l) => (l.key === 'a:2' ? { ...l, material: null, unknown: true } : l));
  assert.equal(printGate(now(bare), false).block, t('print.material_unset', { tools: 'color 2' }));
  // Two colours joined on one bay still to fill: the bay once, the root colour once.
  const two = { ...plan, tools: [plan.tools[0], plan.tools[0], plan.tools[2]], order: [0, 1, 2] };
  const both = { 0: 'a:2', 1: 'a:2', 2: 'b:4' };
  const joined = { ...now(emptyA2), plan: two, pick: both, merges: { 1: 0 } };
  joined.place = { ...joined.place, choice: both };
  assert.equal(printGate(joined, false).block, t('print.no_spool', { tools: 'color 1', key: 'a:2' }));
});

test('printGate: two spools that change bays: the colours have no spool in their bays yet, so Print waits', () => {
  // PLA in a:1 and PETG in a:2; the file prints PETG first: the steps swap them.
  const swapPlan = {
    ...plan,
    tools: [
      { colour: '#000000', material: 'PETG', lane: null, why: 'closest' },
      { colour: '#ffffff', material: 'PLA', lane: null, why: 'closest' },
    ],
    order: [0, 1],
  };
  const at = [
    { key: 'a:1', head: 0, material: 'PLA' },
    { key: 'a:2', head: 0, material: 'PETG' },
  ];
  const choice = { 0: 'a:1', 1: 'a:2' };
  const lines = [
    { tools: [0], key: 'a:1', act: 'move', from: 'a:2' },
    { tools: [1], key: 'a:2', act: 'move', from: 'a:1' },
  ];
  const place = { fits: true, choice, colours: 2, lanes: 2, takeOut: [], lines };
  assert.deepEqual(printGate({ plan: swapPlan, lanes: at, pick: choice, place }, false), {
    choice,
    block: `${t('print.put_pending', { tools: 'color 1', key: 'a:1' })} ${t('print.put_pending', { tools: 'color 2', key: 'a:2' })}`,
    asIs: false,
  });
  // Without the moves the same bays are another material.
  const still = { ...place, lines: [] };
  assert.equal(
    printGate({ plan: swapPlan, lanes: at, pick: choice, place: still }, false).block,
    t('print.material_first', { tools: 'color 1, color 2' }),
  );
});

test('usedTest: the tools the file prints; with no T line read every tool', () => {
  assert.deepEqual([0, 1, 2].map(usedTest([0, 2, 0])), [true, false, true]);
  assert.deepEqual([0, 5].map(usedTest([])), [true, true]);
  assert.deepEqual([0, 5].map(usedTest(null)), [true, true]);
});

test('blockReason: a tool whose lane has no material set blocks Print; a lane that does not say does not', () => {
  const unset = lanes.map((l) => (l.key === 'a:2' ? { ...l, materialSet: false } : l));
  assert.deepEqual(blocked(plan, { 0: 'a:1', 1: 'a:2', 2: 'b:4' }, unset).unset, [1]);
  assert.equal(
    blockReason(plan, { 0: 'a:1', 1: 'a:2', 2: 'b:4' }, unset),
    t('print.material_unset', { tools: 'color 2' }),
  );
  assert.deepEqual(blocked(plan, { 0: 'a:1', 1: 'a:2', 2: 'b:4' }, lanes).unset, []);
});

test('blocked: one rule (bayProblem), one reason per tool: empty, then another material, then no material set', () => {
  const pick = { 0: 'a:1', 1: 'a:2', 2: 'b:4' };
  const odd = lanes.map((l) =>
    l.key === 'a:1'
      ? { ...l, empty: true, materialSet: false }
      : l.key === 'a:2'
        ? { ...l, material: 'TPU', materialSet: false }
        : { ...l, materialSet: false },
  );
  assert.deepEqual(blocked(plan, pick, odd), { missing: [0], wrong: [1], unset: [2] });
  // A status that says nothing (null or undefined) does not block a known material; a spool of no known material does.
  const quiet = lanes.map((l, i) => ({ ...l, materialSet: i ? undefined : null, material: i ? l.material : null }));
  assert.deepEqual(blocked(plan, pick, quiet), { missing: [], wrong: [], unset: [0] });
  assert.equal(bayProblem(plan.tools[0], 'nowhere', lanes), 'wrong');
  assert.equal(bayProblem(plan.tools[0], null, lanes), 'missing');
});

test("colourDistance is the host's CIE76 distance: close blacks match, black and white do not", () => {
  assert.ok(colourDistance('#000000', '#0a0a0a') < 5);
  assert.ok(colourDistance('#000000', '#ffffff') > 90);
  assert.ok(Math.abs(colourDistance('#000000', '#FFFFFF') - 100) < 1e-3);
  assert.equal(CLOSE, 25);
  assert.equal(SIMILAR, 12);
});

test('a pair closer than SIMILAR is offered to join; one between SIMILAR and CLOSE is not, yet still matches', () => {
  const near = colourDistance('#ff0000', '#ee1111');
  assert.ok(near < SIMILAR);
  const mid = colourDistance('#ff0000', '#dd1111');
  assert.ok(mid >= SIMILAR && mid < CLOSE);
  const plan = (second) => ({
    file: 'e.gcode',
    mtime: 1,
    order: [0, 1],
    tools: [
      { colour: '#ff0000', material: 'PLA', lane: null, why: 'closest' },
      { colour: second, material: 'PLA', lane: null, why: 'closest' },
    ],
  });
  assert.deepEqual(similarPairs(plan('#ee1111'), {}), [[0, 1]]);
  assert.deepEqual(similarPairs(plan('#dd1111'), {}), []);
});

test('similarPairs skips the tools that the planner already shares, and the tools of no material', () => {
  const plan = {
    file: 's.gcode',
    mtime: 1,
    order: [0, 1, 2, 3],
    tools: [
      { colour: '#ff0000', material: 'PLA', lane: null, why: 'closest' },
      { colour: '#FF0000', material: 'pla', lane: null, why: 'closest' },
      { colour: '#0000ff', material: null, lane: null, why: 'closest' },
      { colour: '#0000ee', material: null, lane: null, why: 'closest' },
    ],
  };
  assert.deepEqual(similarPairs(/** @type {any} */ (plan), {}), []);
});

const pairPlan = (second = 'PLA', order = [0, 1, 2]) => ({
  file: 'p.gcode',
  mtime: 1,
  order,
  tools: [
    { colour: '#ff0000', material: 'PLA', lane: null, why: 'closest' },
    { colour: '#ee1111', material: second, lane: null, why: 'closest' },
    { colour: '#0000ff', material: 'PLA', lane: null, why: 'closest' },
  ],
});

test('similarPairs lists the used, unmerged tools of one material whose colours look the same', () => {
  assert.deepEqual(similarPairs(pairPlan(), {}), [[0, 1]]);
  assert.deepEqual(similarPairs(pairPlan(), { 1: 0 }), []);
  assert.deepEqual(similarPairs(pairPlan('PETG'), {}), []);
  // A tool the file never prints is no pair, nor a tool with no colour.
  assert.deepEqual(similarPairs(pairPlan('PLA', [0, 2]), {}), []);
  const plain = pairPlan();
  plain.tools[1] = { ...plain.tools[1], colour: null };
  assert.deepEqual(similarPairs(plain, {}), []);
});

test('similarPairs gives the first pair first, and a merge the planner ignores leaves its pair', () => {
  const plan = pairPlan();
  plan.tools.push({ colour: '#0000ee', material: 'PLA', lane: null, why: 'closest' });
  plan.order = [0, 1, 2, 3];
  assert.deepEqual(similarPairs(plan, {}), [
    [0, 1],
    [2, 3],
  ]);
  // A merge into a tool that does not exist is ignored, so the pair stays.
  assert.deepEqual(similarPairs(plan, { 3: 9 }), [
    [0, 1],
    [2, 3],
  ]);
});

// A unit's lanes: lane 1 has a tag, lane 2 a record, lane 3 nothing known, lane 4 an empty bay.
const unitLanes = () => [
  {
    insert: true,
    mode: 'idle',
    tag: { state: 'read', uid: '04AB', record: { material: 'PLA', color_rgba: 'FF0000FF' } },
  },
  { insert: true, mode: 'idle', tag: { state: 'none' } },
  { insert: true, mode: 'idle' },
  { insert: false, mode: 'idle' },
];
const oneUnit = () => ({
  ace2k: { lanes: unitLanes() },
  ace2k_u1: {
    spools: { '-:2': { type: 'PETG', vendor: null, color: '#00ff00' } },
    lanes: { '-:1': { material_set: true }, '-:3': { material_set: false } },
  },
});

test("liveLanes reads one unit's bays from the live status, keyed as the plan", () => {
  const lanes = liveLanes(/** @type {any} */ (oneUnit()), null);
  assert.deepEqual(
    lanes.map((l) => l.key),
    ['1', '2', '3', '4'],
  );
  assert.deepEqual(lanes[0], {
    key: '1',
    colour: '#ff0000',
    material: 'PLA',
    empty: false,
    head: 0,
    materialSet: true,
    colour_name: null,
    spool: { id: '1:04AB', colour: '#ff0000', material: 'PLA', tag: true, uid: '04AB' },
    unknown: false,
  });
  assert.deepEqual(
    [lanes[1].material, lanes[1].colour, lanes[1].spool?.id, lanes[1].spool?.tag],
    ['PETG', '#00ff00', '2:PETG:#00ff00', false],
  );
  assert.deepEqual([lanes[2].material, lanes[2].unknown, lanes[2].materialSet], [null, true, false]);
  assert.equal(lanes[2].spool?.id, '3::');
  assert.deepEqual([lanes[3].empty, lanes[3].spool, lanes[3].unknown], [true, null, false]);
});

test('liveLanes: a bay is empty when its insert reads nothing, whatever the lane does, with one unit or several', () => {
  const one = oneUnit();
  one.ace2k.lanes[3] = { insert: false, mode: 'feeding' }; // a lane moving with no insert is still empty
  one.ace2k.lanes[2] = { insert: true, mode: 'error', error: 'stuck' }; // a stuck spool is still in its bay
  const lanes = liveLanes(/** @type {any} */ (one), null);
  assert.deepEqual(
    lanes.map((l) => [l.key, l.empty]),
    [
      ['1', false],
      ['2', false],
      ['3', false],
      ['4', true],
    ],
  );
  const two = {
    ace2k_units: { units: ['ace2k a', 'ace2k b'] },
    'ace2k a': { lanes: unitLanes() },
    'ace2k b': { lanes: [{ insert: false, mode: 'loading' }, ...unitLanes().slice(1)] },
    ace2k_u1: {},
  };
  // No head is mapped here, so the plan lists the bays to keep them.
  const keys = ['a', 'b'].flatMap((u) => [1, 2, 3, 4].map((n) => ({ key: `${u}:${n}` })));
  const both = liveLanes(/** @type {any} */ (two), /** @type {any} */ ({ lanes: keys }));
  assert.deepEqual(
    both.filter((l) => l.empty).map((l) => l.key),
    ['a:4', 'b:1', 'b:4'],
  );
});

test("liveLanes keeps the plan's colour name while the spool keeps the plan's colour", () => {
  const plan = {
    file: 'x',
    mtime: 1,
    lanes: [
      { key: '1', colour: '#ff0000', material: 'PLA', empty: false, head: 0, colour_name: 'red' },
      { key: '2', colour: '#123456', material: 'PETG', empty: false, head: 1, colour_name: 'navy' },
      { key: '3', colour: null, material: null, empty: false, head: 2 },
    ],
  };
  const lanes = liveLanes(/** @type {any} */ (oneUnit()), /** @type {any} */ (plan));
  assert.deepEqual(
    lanes.map((l) => l.colour_name),
    ['red', null, null, null],
  );
});

test('liveLanes reads several units, keyed "a:1".."b:4" with each unit\'s heads', () => {
  const s = {
    ace2k_units: { units: ['ace2k a', 'ace2k b'] },
    'ace2k a': { lanes: unitLanes() },
    'ace2k b': { lanes: [{ insert: false }, { insert: true, tag: { uid: '77', record: { material: 'PLA' } } }] },
    ace2k_u1: {
      heads: [
        [
          { unit: 'a', lane: 1 },
          { unit: 'b', lane: 2 },
        ],
        [{ unit: 'a', lane: 2 }],
        [{ unit: 'a', lane: 3 }],
        [{ unit: 'a', lane: 4 }],
      ],
      spools: { 'a:2': { type: 'PETG', color: '#00ff00' } },
    },
  };
  const lanes = liveLanes(/** @type {any} */ (s), null);
  assert.deepEqual(
    lanes.map((l) => `${l.key}@${l.head}`),
    ['a:1@0', 'a:2@1', 'a:3@2', 'a:4@3', 'b:2@0'],
  );
  const b2 = lanes[4];
  assert.deepEqual([b2.material, b2.colour, b2.spool?.id, b2.materialSet], ['PLA', null, 'b:2:77', undefined]);
});

test('liveLanes with one named unit keys "1".."4" and reads the adapter\'s "a:2" entries', () => {
  const s = {
    ace2k_units: { units: ['ace2k a'] },
    'ace2k a': { lanes: unitLanes() },
    ace2k_u1: {
      spools: { 'a:2': { type: 'PETG', color: '#00ff00' } },
      lanes: { 'a:2': { material_set: true }, 'a:3': { material_set: false } },
    },
  };
  const lanes = liveLanes(/** @type {any} */ (s), null);
  assert.deepEqual(
    lanes.map((l) => l.key),
    ['1', '2', '3', '4'],
  );
  assert.deepEqual(
    lanes.map((l) => l.materialSet),
    [undefined, true, false, undefined],
  );
  assert.deepEqual([lanes[1].material, lanes[1].spool?.id], ['PETG', '2:PETG:#00ff00']);
});

test('liveLanes keeps a lane with no head while the plan lists it', () => {
  const s = {
    ace2k_units: { units: ['ace2k a', 'ace2k b'] },
    'ace2k a': { lanes: unitLanes() },
    'ace2k b': { lanes: [] },
    ace2k_u1: { heads: [[{ unit: 'a', lane: 1 }], null, null, null] },
  };
  const plan = { file: 'x', mtime: 1, lanes: [{ key: 'b:3', head: null, empty: true, colour: null, material: null }] };
  const lanes = liveLanes(/** @type {any} */ (s), /** @type {any} */ (plan));
  assert.deepEqual(
    lanes.map((l) => `${l.key}@${l.head}:${l.empty}`),
    ['a:1@0:false', 'b:3@null:true'],
  );
});

test('liveLanes with no unit in the status gives the four empty bays of the unnamed unit', () => {
  const lanes = liveLanes(/** @type {any} */ ({}), null);
  assert.deepEqual(
    lanes.map((l) => `${l.key}:${l.empty}`),
    ['1:true', '2:true', '3:true', '4:true'],
  );
});

// The plan of the old Print panel's tests: three tools, the third with no lane suggested.
const eight = {
  file: 'eight.gcode',
  set: false,
  tools: [
    { colour: '#000000', material: 'PLA', lane: 'a:1', why: 'closest' },
    { colour: '#ffffff', material: 'PLA', lane: 'a:2', why: 'closest' },
    { colour: '#800080', material: 'PLA', lane: null, why: 'no_colour' },
  ],
  heads: { 0: 4 },
};
const eightLanes = [
  { key: 'a:1', head: 0, material: 'PLA' },
  { key: 'a:2', head: 0, material: 'PLA' },
  { key: 'b:4', head: 3, material: 'PLA' },
];

test('materials match without case or spaces, as the adapter matches them', () => {
  const pla = [
    { key: 'a:1', head: 0, material: 'pla ' },
    { key: 'a:2', head: 0, material: 'PLA' },
    { key: 'b:4', head: 3, material: ' Pla' },
  ];
  assert.deepEqual(blocked(eight, { 0: 'a:1', 1: 'a:2', 2: 'b:4' }, pla), { missing: [], wrong: [], unset: [] });
});

test('a tool number beyond T31: the start script refuses it', () => {
  const big = { ...eight, tools: Array.from({ length: 33 }, () => ({ material: 'PLA' })), order: [0, 32] };
  const pick = { 0: 'a:1', 32: 'a:2' };
  assert.deepEqual(highTools(big.order, pick), [32]);
  assert.throws(() => printScript('x.gcode', pick, eightLanes, big.order), {
    message: 'The U1 prints at most 32 colors: color 33 cannot be printed.',
  });
});

test('cancelScript sends CANCEL_PRINT only while a print is on', () => {
  for (const state of ['printing', 'paused']) assert.equal(cancelScript({ print_stats: { state } }), 'CANCEL_PRINT');
  for (const state of ['standby', 'complete', 'cancelled'])
    assert.equal(cancelScript({ print_stats: { state } }), null);
  assert.equal(cancelScript({}), null);
});

test('printGate as it is: every colour on a bay of a head with its material set prints, even when the planner cannot plan', () => {
  const two = {
    ...plan,
    tools: [
      { colour: '#ff0000', material: 'PLA', lane: 'a:1' },
      { colour: '#00ff00', material: 'PLA', lane: 'a:1' },
      { colour: '#0000ff', material: 'PLA', lane: 'a:2' },
    ],
    order: [0, 1, 2],
  };
  const at = [
    { key: 'a:1', head: 0, material: 'PLA', materialSet: true },
    { key: 'a:2', head: 1, material: 'PLA', materialSet: true },
  ];
  const noFit = { fits: false, colours: 3, lanes: 2, choice: {}, takeOut: [], lines: [], now: 1, best: null };
  const now = (lanes, pick = { 0: 'a:1', 1: 'a:1', 2: 'a:2' }) => ({ plan: two, lanes, pick, place: noFit });
  // Three colours, two bays: the planner cannot plan, but the bays as they are can print.
  assert.deepEqual(printGate(now(at), false), { choice: { 0: 'a:1', 1: 'a:1', 2: 'a:2' }, block: null, asIs: true });
  assert.equal(printGate(now(at), true).block, t('print.busy'));
  // A bay the adapter does not say about (an older status) does not block; one it says has no material set does.
  const unsaid = at.map((l) => ({ ...l, materialSet: undefined }));
  assert.equal(printGate(now(unsaid), false).asIs, true);
  const unset = at.map((l) => ({ ...l, materialSet: false }));
  assert.equal(printGate(now(unset), false).block, t('print.no_fit_n', { n: 3, m: 2 }));
  // A user's pick on that bay names it to define; a merged tool's own pick is not sent, so it does not count.
  const picked = { ...now(unset), user: { 2: 'a:2' } };
  assert.equal(printGate(picked, false).block, t('print.define_first', { key: 'a:2', n: 3 }));
  assert.equal(printGate(picked, true).block, `${t('print.busy')} ${t('print.define_first', { key: 'a:2', n: 3 })}`);
  const half = at.map((l) => (l.key === 'a:2' ? { ...l, materialSet: false } : l));
  const merged = { ...now(half, { 0: 'a:1', 1: 'a:1', 2: 'a:1' }), user: { 2: 'a:2' }, merges: { 2: 0 } };
  assert.equal(printGate(merged, false).block, null);
  // A bay on no head is no bay: the no-fit block stands, and on the plain picks it is "choose a bay".
  const headless = at.map((l) => (l.key === 'a:2' ? { ...l, head: null } : l));
  assert.equal(printGate(now(headless), false).asIs, false);
  assert.equal(
    blockReason(two, { 0: 'a:1', 1: 'a:1', 2: 'a:2' }, headless),
    t('print.choose_first', { tools: 'color 3' }),
  );
  // sentSwaps counts on the live heads, and is null while a colour has no bay on a head.
  assert.equal(sentSwaps(two, { 0: 'a:1', 1: 'a:1', 2: 'a:2' }, at), 0);
  assert.equal(sentSwaps(two, { 0: 'a:1', 1: 'a:1', 2: 'a:2' }, headless), null);
});

test("the page's colour names are the host's COLOUR_NAMES, in the same order", () => {
  const py = readFileSync(new URL('../../klippy/extras/ace2k_u1_plan.py', import.meta.url), 'utf8');
  const block = /COLOUR_NAMES = \(([\s\S]*?)\n\)/.exec(py)?.[1] ?? '';
  const host = [...block.matchAll(/\("([^"]+)", "(#[0-9a-f]{6})"\)/g)].map((m) => [m[1], m[2]]);
  assert.ok(host.length > 20);
  assert.deepEqual(COLOUR_NAMES, host);
});

test('colourName: #rgb, #rrggbb or #rrggbbaa, any case, the nearest name; null for what is not a colour', () => {
  assert.deepEqual(['#E72F1D', '#e72f1dff', '#000000', '#FEC187FF', '#1f6feb'].map(colourName), [
    'red',
    'red',
    'black',
    'beige',
    'blue',
  ]);
  assert.deepEqual([null, undefined, '', 'red', '#12345', '#1234567'].map(colourName), [
    null,
    null,
    null,
    null,
    null,
    null,
  ]);
});

test('noFitWords: join colours while two may be joined; else the file needs more bays', () => {
  const place = { fits: false, colours: 3, lanes: 2, lines: [], takeOut: [], now: null, best: null, choice: {} };
  const tools = (ms) => ms.map((material, n) => ({ colour: `#00000${n}`, material, lane: null }));
  const same = { ...plan, tools: tools(['PLA', 'PLA', 'PETG']), order: [0, 1, 2] };
  assert.equal(noFitWords(place, same), t('print.no_fit_n', { n: 3, m: 2 }));
  const apart = { ...plan, tools: tools(['PLA', 'PETG', 'ABS']), order: [0, 1, 2] };
  assert.equal(noFitWords(place, apart), t('print.no_fit_bays', { n: 3, m: 2 }));
  // The two PLA colours already joined: no join is left.
  assert.equal(noFitWords(place, same, { 1: 0 }), t('print.no_fit_bays', { n: 3, m: 2 }));
  assert.equal(noFitWords({ ...place, tooMany: true }), t('print.too_many', { n: 3, max: MAX_GROUPS }));
});
