import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { html } from '../js/ui/html.js';
import { ColourPicker } from '../js/ui/ColourPicker.js';
import { ColourRows } from '../js/ui/ColourChips.js';
import { runAction } from '../js/actions.js';
import { asIsChoice, printGate, sentKinds } from '../js/print-plan.js';
import {
  asIsChecks,
  autoAdvance,
  ejectList,
  ejectSkipped,
  guideOf,
  stepId,
  stepSub,
  wrongSpoolIn,
  asShown,
} from '../js/steps.js';
import { EjectAll } from '../js/ui/EjectAll.js';
import { GuidedStep } from '../js/ui/GuidedStep.js';
import { PlanView } from '../js/ui/PlanView.js';
import { Diagram } from '../js/ui/Diagram.js';
import { diagramModel, headView } from '../js/diagram-model.js';
import { marksOf } from '../js/ui/Printing.js';
import { editAction, laneActions, paused, printingNow, sliceFor, SWATCHES } from '../js/model.js';
import { SidePanel } from '../js/ui/SidePanel.js';
import { SpoolEditor } from '../js/ui/SpoolEditor.js';
import { laneAt } from '../js/lane-state.js';
import { MobileLists } from '../js/ui/MobileLists.js';
import { startPrint } from '../js/printflow.js';
import { options } from 'preact';
import { planNow } from '../js/state.js';
import { renderHtml, textOf } from './ui-helpers.js';
import { loadEnglish } from './helpers.js';
import { t } from '../js/i18n.js';

loadEnglish();

// A status of a real U1, the five-colour file planned: a:1 empty after an eject, a:2 red PLA set by hand (T0, behind
// the splitter), a:3 black PLA (T1) and a:4 white PLA (T2) with their tags pending, b:1 brown PLA (T3). The adapter
// matched white to a:4, red to a:2, brown to b:1 and black to a:3; skin (colour 5) has no bay. Unlike the demo files,
// almost every colour of the file already has a bay from the adapter's match.
const real = () => JSON.parse(readFileSync(new URL('./fixtures/u1-five-colours-b.json', import.meta.url), 'utf8'));
const FILE = 'objeto_5_cores_PLA_1h36m.gcode';
const SKIN = 4;

const appOf = () => ({
  status: real(),
  online: true,
  klippy: 'ready',
  selected: null,
  pending: new Set(),
  edit: null,
  events: [],
  printOptions: {},
  folds: {},
  print: { open: true, files: [], file: FILE, choice: {}, choiceId: null },
  ui: { tab: 'print', panel: { kind: 'pick', n: String(SKIN) }, sheet: false },
});
const live = (app) => {
  const ctx = { app, t, demo: false, toast: () => {}, render: () => {} };
  ctx.run = (id, data = {}) => runAction(ctx, id, data);
  return ctx;
};
// The bay each colour prints from, as Print sends it as it is.
const sent = (app) => {
  const now = /** @type {NonNullable<ReturnType<typeof planNow>>} */ (planNow(app));
  return asIsChoice(now.plan, now.pick, now.merges);
};

const BAYS = ['a:2', 'a:3', 'a:4', 'b:1'];
const USED = [0, 1, 3, 4, 6];
// The bay each colour's row shows: its own line in the placement (never "= color m" for a pick).
const rowKey = (app, n) => planNow(app)?.place?.lines.find((l) => l.tools.includes(n))?.key;

for (const [key, other] of [
  ['a:4', 0],
  ['a:2', 1],
  ['a:3', 6],
]) {
  test(`real U1: skin set to ${key} prints from it; color ${other + 1}, left to the suggestion, gives the bay up`, () => {
    const app = appOf();
    const ctx = live(app);
    // Nothing set by the user yet: the picker says no bay is shared.
    const picker = textOf(renderHtml(html`<${ColourPicker} app=${app} ctx=${ctx} n=${SKIN} />`));
    assert.ok(!picker.includes(t('print.shared_with', { m: other + 1 })), picker);
    ctx.run('pick', { n: String(SKIN), key });
    // A pick, never a join.
    assert.deepEqual([app.print.choice[SKIN], app.print.merges, app.print.overrides], [key, {}, [SKIN]]);
    const after = sent(app);
    // Skin prints from that bay; the other colour, which the user did not set, no longer counts on it.
    assert.deepEqual([after[SKIN], after[other]], [key, null]);
    // The row shows the bay, with no step for it, and the other colour is placed elsewhere.
    const chips = textOf(renderHtml(html`<${ColourRows} app=${app} ctx=${ctx} now=${planNow(app)} />`));
    assert.ok(!chips.includes('= color'), chips);
    assert.equal(rowKey(app, SKIN), key);
    assert.notEqual(rowKey(app, other), key);
    assert.ok(!guideOf(app)?.steps.some((x) => x.tools?.includes(SKIN)));
    const again = renderHtml(html`<${ColourPicker} app=${app} ctx=${ctx} n=${SKIN} />`);
    assert.match(again, new RegExp(`class="opt sel"[^>]*>(?:(?!</button>).)*${key}`, 's'));
  });

  test(`real U1: color ${other + 1} and skin both set to ${key} by the user share its spool, shown plainly`, () => {
    const app = appOf();
    const ctx = live(app);
    ctx.run('pick', { n: String(other), key });
    // The picker says the bay is shared with the colour the user set on it.
    const picker = textOf(renderHtml(html`<${ColourPicker} app=${app} ctx=${ctx} n=${SKIN} />`));
    assert.ok(picker.includes(t('print.shared_with', { m: other + 1 })), picker);
    ctx.run('pick', { n: String(SKIN), key });
    assert.deepEqual(app.print.merges, {});
    const after = sent(app);
    assert.deepEqual([after[SKIN], after[other]], [key, key]);
    // Both rows show the bay, never "= color m", and the guide moves neither nor that spool.
    const chips = textOf(renderHtml(html`<${ColourRows} app=${app} ctx=${ctx} now=${planNow(app)} />`));
    assert.ok(!chips.includes('= color'), chips);
    assert.deepEqual([rowKey(app, SKIN), rowKey(app, other)], [key, key]);
    const steps = guideOf(app)?.steps ?? [];
    assert.ok(!steps.some((x) => x.tools?.includes(SKIN) || x.tools?.includes(other)), JSON.stringify(steps));
    assert.ok(!steps.some((x) => x.key === key || x.from === key), JSON.stringify(steps));
    const now = /** @type {NonNullable<ReturnType<typeof planNow>>} */ (planNow(app));
    assert.notEqual(sentKinds(now.plan, after, now.lanes), null);
  });
}

// Nothing is joined, and every colour the user set to a bay shows that bay, as its own line, with no step.
/** @param {any} app @param {string} why */
const assertShowsPicks = (app, why) => {
  assert.deepEqual(app.print.merges, {}, why);
  for (const j of app.print.overrides ?? []) {
    const k = app.print.choice[j];
    if (k === 'new') continue;
    const line = planNow(app)?.place?.lines.find((l) => l.tools.includes(j));
    assert.deepEqual([line?.key, line?.act], [k, 'there'], `${why}: color ${j + 1}`);
  }
};

test('real U1: whatever colour picks whatever bay, again and again, each row shows its pick and nothing joins', () => {
  const app = appOf();
  const ctx = live(app);
  for (let round = 0; round < 2; round += 1)
    for (const n of USED)
      for (const key of [...BAYS, 'new', BAYS[(n + round) % BAYS.length]]) {
        ctx.run('pick', { n: String(n), key });
        assertShowsPicks(app, `color ${n + 1} on ${key}`);
      }
});

test('real U1: tapping again the bay a colour is set to changes nothing', () => {
  const app = appOf();
  const ctx = live(app);
  ctx.run('pick', { n: String(SKIN), key: 'a:4' });
  const before = app.print;
  ctx.run('pick', { n: String(SKIN), key: 'a:4' });
  assert.deepEqual([app.print.choice, app.print.merges], [before.choice, before.merges]);
  assert.equal(app.print.choice[SKIN], 'a:4');
});

test('real U1: "Save and use" on a bay another colour prints from sets the colour to it, no join', async () => {
  const app = appOf();
  const ctx = live(app);
  ctx.sendScript = async () => true;
  ctx.run('edit_open', { lane: '2', unit: 'a', use: String(SKIN), material: 'PLA', colour: '#fec187' });
  await ctx.run('edit_save', { lane: '2' });
  await new Promise((r) => setTimeout(r, 0));
  assert.deepEqual([app.print.choice[SKIN], app.print.merges, app.print.overrides], ['a:2', {}, [SKIN]]);
  assert.equal(rowKey(app, SKIN), 'a:2');
});

test('real U1: a colour of no material set to a bay prints from it, and the start warns', () => {
  const app = appOf();
  app.status.ace2k_u1.plan.tools[SKIN].material = null;
  const ctx = live(app);
  ctx.run('pick', { n: String(SKIN), key: 'a:4' });
  assert.deepEqual([app.print.choice[SKIN], app.print.merges], ['a:4', {}]);
  const now = /** @type {NonNullable<ReturnType<typeof planNow>>} */ (planNow(app));
  const lines = asIsChecks(t, now, asIsChoice(now.plan, now.pick, now.merges), []);
  assert.ok(
    lines.includes(t('print.as_is_tool_unknown', { head: 'T2', n: 5, material: 'PLA', key: 'a:4' })),
    lines.join('|'),
  );
  // Another known material is still refused in the picker.
  app.status.ace2k_u1.plan.tools[SKIN].material = 'PETG';
  const out = textOf(renderHtml(html`<${ColourPicker} app=${app} ctx=${ctx} n=${SKIN} />`));
  assert.ok(out.includes(t('print.other_material')));
});

test('real U1: twins share a spool by themselves: re-picking its own bay never makes a colour follow its twin', () => {
  const app = appOf();
  const plan = app.status.ace2k_u1.plan;
  plan.order = [...plan.order, 7, 0]; // color 8 is white too, now printed
  const ctx = live(app);
  ctx.run('pick', { n: '7', key: 'a:4' });
  ctx.run('pick', { n: '0', key: 'a:4' });
  assert.deepEqual([app.print.merges[0], app.print.merges[7]], [undefined, undefined]);
  assert.deepEqual([rowKey(app, 0), rowKey(app, 7)], ['a:4', 'a:4']);
});

test('real U1: the picker marks the bay a colour really prints from, also past a merge that no longer joins', () => {
  const app = appOf();
  const ctx = live(app);
  ctx.run('merge', { n: String(SKIN), into: '6' });
  const joined = renderHtml(html`<${ColourPicker} app=${app} ctx=${ctx} n=${SKIN} />`);
  assert.match(joined, /class="opt sel"[^>]*>(?:(?!<\/button>).)*a:3/s);
  // Black becomes PETG: the merge no longer joins, so skin prints from its own pick, a:2.
  app.status.ace2k_u1.plan.tools[6].material = 'PETG';
  app.print = { ...app.print, choice: { ...app.print.choice, [SKIN]: 'a:2' } };
  const stale = renderHtml(html`<${ColourPicker} app=${app} ctx=${ctx} n=${SKIN} />`);
  assert.match(stale, /class="opt sel"[^>]*>(?:(?!<\/button>).)*a:2/s);
});

test('real U1: a pick is a pick from the plan shown, also before a file is chosen in the page', () => {
  const app = appOf();
  app.print = { ...app.print, file: null };
  live(app).run('pick', { n: String(SKIN), key: 'a:4' });
  assert.deepEqual([app.print.choice[SKIN], app.print.merges], ['a:4', {}]);
});

test('real U1: eject all leaves a:3, loaded in T1 with no material set for the adapter, and says so', () => {
  const app = appOf();
  const g = /** @type {NonNullable<ReturnType<typeof guideOf>>} */ (guideOf(app));
  // The steps take the black spool out of a:3 (it moves to a:1), but its eject would heat T1 for no material.
  assert.ok(g.steps.some((s) => s.kind === 'move' && s.from === 'a:3'));
  assert.deepEqual([ejectList(g.steps, g.stays).includes('a:3'), ejectSkipped(g.steps, g.stays)], [false, ['a:3']]);
  app.print = { ...app.print, eject: { phase: 'ask' } };
  const out = textOf(renderHtml(html`<${EjectAll} app=${app} ctx=${live(app)} g=${g} />`));
  assert.ok(out.includes(t('eject_all.skipped', { key: 'a:3' })), out);
  // Waiting in its bay (not in the head), the same spool is rolled back cold: it stays in the run.
  const cold = appOf();
  cold.status.ace2k_u1.current = [null, null, null, null];
  const c = /** @type {NonNullable<ReturnType<typeof guideOf>>} */ (guideOf(cold));
  assert.ok(ejectList(c.steps, c.stays).includes('a:3'));
});

test('real U1: a tagged red spool put in the bay of a black colour keeps the put step and says what is there', () => {
  // The black spool was ejected from a:3: the steps put a black PLA in the empty a:1.
  const app = appOf();
  const a = app.status['ace2k a'].lanes;
  a[2] = { ...a[2], insert: false, tag: { state: 'unknown', uid: null, source: null, record: null } };
  const before = /** @type {NonNullable<ReturnType<typeof guideOf>>} */ (guideOf(app));
  const put = before.steps.find((s) => s.key === 'a:1' && s.kind === 'put');
  assert.ok(put, JSON.stringify(before.steps));
  const n = /** @type {{ tools: number[] }} */ (put).tools[0];
  // The operator puts a tagged red Snapmaker PLA in a:1.
  const lanes = app.status['ace2k a'].lanes;
  lanes[0] = {
    ...lanes[0],
    insert: true,
    tag: {
      state: 'read',
      uid: null,
      source: 'read',
      record: { brand: 'Snapmaker', material: 'PLA', color_rgba: 'E72F1DFF' },
    },
  };
  app.status = { ...app.status };
  const g = /** @type {NonNullable<ReturnType<typeof guideOf>>} */ (guideOf(app));
  const step = g.steps.find((s) => s.key === 'a:1' && s.kind !== 'out');
  // Never "check" (the page knows the colour): a put, with the red spool to take out first.
  assert.equal(step?.kind, 'put');
  assert.ok(g.steps.some((s) => s.kind === 'out' && s.key === 'a:1'));
  const sub = stepSub(t, /** @type {any} */ (step), g.now);
  assert.equal(sub, t('step.sub.put_over', { key: 'a:1', what: 'red PLA', n: n + 1, want: 'black' }));
  assert.equal(
    sub,
    "Bay a:1 holds a red PLA spool, not color 7's black: eject it and put a black one, or press Done to print color 7 with the red PLA.",
  );
  assert.ok(!sub.includes(t('step.sub.check')));
  // The card offers to eject that spool right there, the bay card's own Eject.
  const runs = [];
  const ctx = { ...live(app), run: (id, data) => runs.push([id, data]) };
  const nodes = [];
  const prev = options.vnode;
  options.vnode = (v) => {
    if (v.type === 'button') nodes.push(v);
    prev?.(v);
  };
  let out;
  try {
    out = textOf(renderHtml(html`<${GuidedStep} app=${app} ctx=${ctx} g=${{ ...g, cur: step }} />`));
  } finally {
    options.vnode = prev;
  }
  assert.ok(out.includes(t('step.eject_bay', { key: 'a:1' })), out);
  const button = nodes.find((v) =>
    []
      .concat(v.props.children)
      .join('')
      .includes(t('step.eject_bay', { key: 'a:1' })),
  );
  button?.props.onClick();
  assert.deepEqual(runs, [['lane', { lane: '1', unit: 'a', id: 'eject' }]]);
  // Once the bay empties, the step asks for the right spool again, with no eject button.
  lanes[0] = { ...lanes[0], insert: false, tag: { state: 'unknown', uid: null, source: null, record: null } };
  app.status = { ...app.status };
  const again = /** @type {NonNullable<ReturnType<typeof guideOf>>} */ (guideOf(app));
  const back = again.steps.find((s) => s.key === 'a:1' && s.kind !== 'out');
  assert.deepEqual(
    [back?.kind, stepSub(t, /** @type {any} */ (back), again.now)],
    ['put', t('step.sub.put', { n: n + 1 })],
  );
  const card = textOf(renderHtml(html`<${GuidedStep} app=${app} ctx=${ctx} g=${{ ...again, cur: back }} />`));
  assert.ok(!card.includes(t('step.eject_bay', { key: 'a:1' })));
});

test('real U1: the step words and the Eject button read one test, over every tool of a merged group', () => {
  // Skin (colour 5) joins black (colour 7), whose spool left a:3: the group is asked in a:3. A tagged skin-coloured
  // spool goes in: close to one colour of the group (skin), though not to its root (black).
  const app = appOf();
  const a = app.status['ace2k a'].lanes;
  a[2] = { ...a[2], insert: false, tag: { state: 'unknown', uid: null, source: null, record: null } };
  live(app).run('merge', { n: '4', into: '6' });
  const empty = /** @type {NonNullable<ReturnType<typeof guideOf>>} */ (guideOf(app));
  assert.ok(empty.steps.some((s) => s.kind === 'put' && s.key === 'a:3' && s.tools.includes(4) && s.tools.includes(6)));
  a[2] = {
    ...a[2],
    insert: true,
    tag: { state: 'read', uid: null, source: 'read', record: { material: 'PLA', color_rgba: 'FEC187FF' } },
  };
  // T1 is a head of its own: the U1 writes the head's setting from the tag, and the page reads that first.
  app.status.print_task_config.filament_color_rgba[1] = 'FEC187FF';
  app.status = { ...app.status };
  const g = /** @type {NonNullable<ReturnType<typeof guideOf>>} */ (guideOf(app));
  // A check of a:3 for the group: the spool is close to skin, so it may be the group's; no Eject is offered. For black alone it is clearly another colour, and the card offers to eject it.
  /** @param {number[]} tools */
  const card = (tools) => {
    const step = /** @type {any} */ ({ kind: 'check', key: 'a:3', tools });
    const out = textOf(
      renderHtml(html`<${GuidedStep} app=${app} ctx=${live(app)} g=${{ ...g, steps: [step], cur: step }} />`),
    );
    return [wrongSpoolIn(g.now, step), out.includes(t('step.eject_bay', { key: 'a:3' })), stepSub(t, step, g.now)];
  };
  // Close to skin but not to black: some of the group's colours, a check that says so.
  // It names the colour it is close to: skin, colour 5.
  assert.deepEqual(card([4, 6]), [null, false, t('step.sub.check_some', { n: 5 })]);
  assert.deepEqual(card([6]).slice(0, 2), ['colour', true]);
});

test('real U1: Done on a put over a red spool picks that bay for black, and Print asks about it', async () => {
  const app = appOf();
  const a = app.status['ace2k a'].lanes;
  a[2] = { ...a[2], insert: false, tag: { state: 'unknown', uid: null, source: null, record: null } };
  a[0] = {
    ...a[0],
    insert: true,
    tag: { state: 'read', uid: null, source: 'read', record: { material: 'PLA', color_rgba: 'E72F1DFF' } },
  };
  // The tags were read: the adapter has the materials set.
  for (const k of ['a:1', 'a:4'])
    app.status.ace2k_u1.lanes[k] = { ...app.status.ace2k_u1.lanes[k], material_set: true };
  app.status = { ...app.status };
  const ctx = live(app);
  // The guide on the put over a:1; Done there.
  const g = /** @type {NonNullable<ReturnType<typeof guideOf>>} */ (guideOf(app));
  app.print = { ...app.print, trail: g.trail, step: g.trail.indexOf('i:a:1') };
  assert.equal(guideOf(app)?.cur?.kind, 'put');
  ctx.run('step_done', {});
  // Skin and brown are then both set to the brown spool, so every colour has a bay.
  ctx.run('pick', { n: '4', key: 'b:1' });
  ctx.run('pick', { n: '3', key: 'b:1' });
  const now = /** @type {NonNullable<ReturnType<typeof planNow>>} */ (planNow(app));
  assert.equal(asIsChoice(now.plan, now.pick, now.merges)[6], 'a:1');
  // Print sends black from a:1 and the confirm says so.
  const confirm = [];
  const sentScripts = [];
  Object.assign(ctx, {
    confirm: (q) => {
      confirm.push(q);
      return true;
    },
    sendScript: async (x) => {
      sentScripts.push(x);
      return true;
    },
  });
  ctx.startPrint = () => startPrint(ctx);
  await ctx.run('print_go');
  assert.ok(confirm[0]?.includes('color 7'), JSON.stringify(confirm));
  assert.match(sentScripts[0] ?? '', / T6=a:1/);
});

test('real U1: the tagged black PLA put in a:1 for colour 7 is its spool: no check, the guide moves on', () => {
  // The black spool left a:3: the steps put a black PLA in the empty a:1 for colour 7.
  const app = appOf();
  const a = app.status['ace2k a'].lanes;
  a[2] = { ...a[2], insert: false, tag: { state: 'unknown', uid: null, source: null, record: null } };
  const before = /** @type {NonNullable<ReturnType<typeof guideOf>>} */ (guideOf(app));
  assert.ok(before.steps.some((s) => s.kind === 'put' && s.key === 'a:1' && s.tools.includes(6)));
  app.print = { ...app.print, trail: before.trail, step: before.trail.indexOf('i:a:1') };
  // The operator puts the black Bambu PLA in a:1; its tag is read.
  a[0] = {
    ...a[0],
    insert: true,
    tag: {
      state: 'read',
      uid: null,
      source: 'read',
      record: { brand: 'Bambu', material: 'PLA', color_rgba: '000000FF' },
    },
  };
  app.status = { ...app.status };
  autoAdvance(app);
  const g = /** @type {NonNullable<ReturnType<typeof guideOf>>} */ (guideOf(app));
  // No step for a:1 is left (no check), and colour 7 prints from it.
  assert.ok(!g.steps.some((s) => s.key === 'a:1'), JSON.stringify(g.steps));
  assert.deepEqual(
    g.now.place?.lines.find((l) => l.tools.includes(6)),
    { tools: [6], key: 'a:1', act: 'there', from: null },
  );
  assert.notEqual(g.cur && stepId(g.cur), 'i:a:1');
});

// The black spool gone from a:3, and a tagged black PLA (or the given record) put in a:1 for colour 7.
function blackInA1(record = { brand: 'Bambu', material: 'PLA', color_rgba: '000000FF' }, edit = (_plan) => {}) {
  const app = appOf();
  edit(app.status.ace2k_u1.plan);
  const a = app.status['ace2k a'].lanes;
  a[2] = { ...a[2], insert: false, tag: { state: 'unknown', uid: null, source: null, record: null } };
  const before = /** @type {NonNullable<ReturnType<typeof guideOf>>} */ (guideOf(app));
  app.print = { ...app.print, trail: before.trail, step: before.trail.indexOf('i:a:1') };
  autoAdvance(app); // the put is shown
  a[0] = { ...a[0], insert: true, tag: { state: 'read', uid: null, source: 'read', record } };
  app.status = { ...app.status };
  return { app, a };
}

test('real U1: the matching black spool is settled and pinned: a bay emptied later on the same head moves nothing', () => {
  const { app, a } = blackInA1();
  autoAdvance(app);
  assert.equal(app.print.choice[6], 'a:1');
  assert.ok(app.print.pinned?.[6]?.look);
  // a:2 (T0, behind the same splitter) empties: colour 7 stays on a:1.
  a[1] = { ...a[1], insert: false, tag: { state: 'unknown', uid: null, source: null, record: null } };
  app.status = { ...app.status };
  autoAdvance(app);
  const now = /** @type {NonNullable<ReturnType<typeof planNow>>} */ (planNow(app));
  assert.equal(now.place?.choice[6], 'a:1');
  assert.equal(app.print.choice[6], 'a:1');
});

test('real U1: a put shown for a colour of no material passes when the tagged spool is of its colour', () => {
  // The file gives colour 7 no material (a plan of its own: another mtime): the planner cannot compare materials, so
  // the line is a check.
  const { app } = blackInA1(undefined, (plan) => {
    plan.tools[6].material = null;
    plan.mtime += 1;
  });
  const g = /** @type {NonNullable<ReturnType<typeof guideOf>>} */ (guideOf(app));
  assert.equal(g.steps.find((s) => s.key === 'a:1')?.kind, 'check');
  // It was shown as a put, and the tagged black spool is of its colour: it passes by itself.
  app.print = { ...app.print, shown: { id: 'i:a:1', kind: 'put' } };
  autoAdvance(app);
  assert.equal(app.print.choice[6], 'a:1');
  assert.ok(!guideOf(app)?.steps.some((s) => s.key === 'a:1'));
});

// The black spool gone from a:3, and a:1 holding what the operator left there: a spool whose tag is pending (no
// material), or nothing during the re-insert.
function sevenInA1(a1) {
  const app = appOf();
  const a = app.status['ace2k a'].lanes;
  a[2] = { ...a[2], insert: false, tag: { state: 'unknown', uid: null, source: null, record: null } };
  a[0] = { ...a[0], ...a1 };
  // White's a:4 and colour 5's bay need a spool too: their tags are taken as read, so only colour 7 is at stake.
  app.status.ace2k_u1.lanes['a:4'] = { ...app.status.ace2k_u1.lanes['a:4'], material_set: true };
  app.status = { ...app.status };
  return app;
}
const gateOf = (app) => printGate(/** @type {NonNullable<ReturnType<typeof planNow>>} */ (planNow(app)), false);

test('real U1: a put still pending for colour 7 in a:1 (a spool of no material, or an empty bay) disables Print', () => {
  for (const a1 of [
    { insert: true, tag: { state: 'pending', uid: null, source: null, record: null } },
    { insert: false, tag: { state: 'unknown', uid: null, source: null, record: null } },
  ]) {
    const app = sevenInA1(a1);
    const gate = gateOf(app);
    assert.equal(gate.choice[6], 'a:1');
    assert.equal(gate.asIs, false);
    // An empty bay has no spool; a spool whose tag is pending, which the steps take out, waits for colour 7's spool.
    const why = a1.insert
      ? t('print.put_pending', { tools: 'color 7', key: 'a:1' })
      : t('print.no_spool', { tools: 'color 7', key: 'a:1' });
    assert.ok(gate.block?.includes(why), gate.block ?? '');
    const out = renderHtml(html`<${PlanView} app=${app} ctx=${live(app)} />`);
    assert.match(out, /class="btn go" disabled/);
  }
});

test('real U1: colour 7 joined to a colour that has its spool prints: Print enabled', () => {
  const app = sevenInA1({ insert: false, tag: { state: 'unknown', uid: null, source: null, record: null } });
  // Colour 7 joins brown (colour 4, b:1); colour 5 (skin) joins white (colour 1, a:4).
  live(app).run('merge', { n: '6', into: '3' });
  live(app).run('merge', { n: '4', into: '0' });
  const gate = gateOf(app);
  assert.deepEqual([gate.block, gate.choice[6]], [null, 'b:1']);
  assert.ok(!/class="btn go" disabled/.test(renderHtml(html`<${PlanView} app=${app} ctx=${live(app)} />`)));
});

test('real U1: asShown passes a shown put only when the material cannot be compared and every colour is close', () => {
  const app = appOf();
  const now = /** @type {NonNullable<ReturnType<typeof planNow>>} */ (planNow(app));
  const bare = { ...now.plan.tools[6], material: null };
  const tools = [...now.plan.tools];
  tools[6] = bare;
  /** @param {object} lane @param {number[]} ts */
  const shown = (lane, ts) => {
    const n2 = { ...now, plan: { ...now.plan, tools }, lanes: [{ key: 'x', head: 0, empty: false, ...lane }] };
    app.print = { ...app.print, shown: { id: 'i:x', kind: 'put' } };
    return asShown(app, n2, { kind: 'check', key: 'x', tools: ts }).kind;
  };
  // Black (no material in the file) on a black PLA: the material cannot be compared, the colour is close: a put.
  assert.equal(shown({ colour: '#000000', material: 'PLA' }, [6]), 'put');
  // Close to some colours only (black and skin on a black spool), or a colour not known: stays a check.
  assert.equal(shown({ colour: '#000000', material: 'PLA' }, [4, 6]), 'check');
  assert.equal(shown({ colour: null, material: 'PLA' }, [6]), 'check');
});

test('real U1: settling a matching spool never overrides the user, nor reads a spool held in hand', () => {
  // The user picked a:3 (empty now) for colour 7: the matching black in a:1 does not replace that pick.
  const picked = blackInA1();
  picked.app.print = { ...picked.app.print, choice: { 6: 'a:3' }, choiceId: `${FILE}\n${real().ace2k_u1.plan.mtime}` };
  autoAdvance(picked.app);
  assert.equal(picked.app.print.choice[6], 'a:3');
  // The black spool taken out of a:1 by eject all, in hand: a:1 is empty, so nothing is settled.
  const held = blackInA1();
  const a = held.app.status['ace2k a'].lanes;
  a[0] = { ...a[0], insert: false, tag: { state: 'unknown', uid: null, source: null, record: null } };
  held.app.status = { ...held.app.status };
  held.app.print = {
    ...held.app.print,
    held: [{ id: 'h', from: 'a:1', colour: '#000000', material: 'PLA', tag: true, uid: null }],
  };
  autoAdvance(held.app);
  assert.equal(held.app.print.choice?.[6], undefined);
});

test("real U1: a pick the page settled is kept apart from the user's: settled again by the page, never the user's", () => {
  const { app } = blackInA1();
  autoAdvance(app);
  assert.deepEqual([app.print.choice[6], app.print.auto], ['a:1', [6]]);
  // That pick goes stale (a:3, empty now) while the black spool still matches in a:1: the page settles it again.
  app.print = { ...app.print, choice: { ...app.print.choice, 6: 'a:3' } };
  autoAdvance(app);
  assert.equal(app.print.choice[6], 'a:1');
  // The user's own pick of a:3 is never moved, and it leaves the page's list.
  live(app).run('pick', { n: '6', key: 'a:3' });
  assert.deepEqual([app.print.choice[6], app.print.auto], ['a:3', []]);
  autoAdvance(app);
  assert.equal(app.print.choice[6], 'a:3');
});

test('real U1: 8 colors on 5 bays: the picker offers its joins, and three joins make the plan fit', () => {
  const app = appOf();
  const plan = app.status.ace2k_u1.plan;
  const colours = ['#000000', '#ff0000', '#ffffff', '#00a000', '#ffe000', '#800080', '#1e50e0', '#ff8c00'];
  plan.file = '8_cores_torre_purga.gcode';
  plan.mtime += 1;
  plan.tools = colours.map((colour) => ({
    colour,
    material: 'PLA',
    temp: 220,
    first_temp: 220,
    lane: null,
    why: 'too_far',
  }));
  plan.order = [0, 1, 2, 3, 4, 5, 6, 7, 0, 1, 2, 3, 4, 5, 6, 7];
  app.print = { ...app.print, file: plan.file };
  const now = /** @type {NonNullable<ReturnType<typeof planNow>>} */ (planNow(app));
  assert.equal(now.place?.fits, false);
  // The plan says what to do.
  const view = textOf(renderHtml(html`<${PlanView} app=${app} ctx=${live(app)} />`));
  assert.ok(view.includes(t('print.no_fit_n', { n: 8, m: 5 })), view);
  assert.ok(t('print.no_fit_n', { n: 8, m: 5 }).includes('join colors'));
  // Every join and every bay with a spool is offered: none is disabled for its cost, and none says "does not fit".
  const nodes = [];
  const prev = options.vnode;
  options.vnode = (v) => {
    if (v.type === 'button') nodes.push(v);
    prev?.(v);
  };
  try {
    renderHtml(html`<${ColourPicker} app=${app} ctx=${live(app)} n=${0} />`);
  } finally {
    options.vnode = prev;
  }
  // (An empty bay stays disabled: it has no spool to pick.)
  const text = (v) => textOf(renderHtml(v));
  const offered = nodes.filter(
    (v) => (v.props.class === 'row' || v.props.class === 'opt') && !text(v).includes(t('units.empty')),
  );
  assert.ok(offered.length >= 7 + 4, String(offered.length));
  assert.deepEqual(offered.filter((v) => v.props.disabled).map(text), []);
  assert.ok(!offered.some((v) => text(v).includes(t('print.no_fit'))));
  // Three joins: 8 colors on 5 bays, and the guide appears.
  const ctx = live(app);
  ctx.run('merge', { n: '5', into: '1' });
  ctx.run('merge', { n: '6', into: '2' });
  ctx.run('merge', { n: '7', into: '3' });
  const after = /** @type {NonNullable<ReturnType<typeof planNow>>} */ (planNow(app));
  assert.equal(after.place?.fits, true);
  assert.ok(guideOf(app)?.steps.length);
});

test('real U1: a put whose bay spool is in the hand (eject all) says so, and offers no Eject: one source, the live bay', () => {
  const app = appOf();
  const now = /** @type {NonNullable<ReturnType<typeof planNow>>} */ (planNow(app));
  const live1 = now.lanes.map((l) => (l.key === 'a:2' ? { ...l, empty: true, material: null, colour: null } : l));
  const held = { ...now, lanes: live1, planLanes: now.lanes };
  const step = /** @type {any} */ ({ kind: 'put', key: 'a:2', tools: [6] });
  assert.equal(stepSub(t, step, held), t('step.sub.in_hand', { key: 'a:2', n: 7 }));
  assert.equal(stepSub(t, { ...step, kind: 'check' }, held), t('step.sub.in_hand', { key: 'a:2', n: 7 }));
  assert.equal(wrongSpoolIn(held, step), null);
  // The red spool really in a:2: the words and the Eject agree (another colour than black).
  assert.equal(wrongSpoolIn(now, step), 'colour');
  assert.ok(stepSub(t, step, now).includes('red PLA'));
});

test('real U1 diagram: an empty bay its head still names draws an empty rail (no glow); the open bay is marked', () => {
  // a:1 is empty after an eject, and T0 still names it as its lane.
  const app = { ...appOf(), ui: { tab: 'units', panel: { kind: 'bay', key: 'a:2' }, sheet: false } };
  assert.deepEqual(app.status.ace2k_u1.current[0], { unit: 'a', lane: 1 });
  const out = renderHtml(html`<${Diagram} app=${app} ctx=${live(app)} />`);
  // Three lanes are in their heads (a:3, a:4, b:1): three glows, none for the empty a:1.
  assert.equal((out.match(/class="glow"/g) ?? []).length, 3);
  assert.equal((out.match(/class="rail live"/g) ?? []).length, 3);
  // a:1 stays T0's lane for the adapter, but no cue says it is loaded: no head-lane border, no lit chip.
  const a1 = out.slice(out.indexOf('aria-label="Bay a:1:'), out.indexOf('</g>', out.indexOf('aria-label="Bay a:1:')));
  assert.ok(a1.length > 0 && !/bay-tile on/.test(a1) && !/chip on/.test(a1), a1);
  const filled = (st, key) =>
    diagramModel({ ...app, status: st })
      .units.flatMap((u) => u.lanes)
      .find((l) => l.key === key)?.filled;
  assert.deepEqual([filled(app.status, 'a:1'), filled(app.status, 'a:3')], [false, true]);
  // A run-out tail still in T0 (its sensor sees filament), of a:1's last spool (its Edit spool record, black PLA):
  // the rail glows again.
  const tail = {
    ...app,
    status: {
      ...app.status,
      'filament_motion_sensor e0_filament': { filament_detected: true },
      ace2k_u1: {
        ...app.status.ace2k_u1,
        spools: { ...app.status.ace2k_u1.spools, 'a:1': { type: 'PLA', color: '#000000', vendor: 'Generic' } },
      },
    },
  };
  assert.equal(filled(tail.status, 'a:1'), true);
  const tailOut = renderHtml(html`<${Diagram} app=${tail} ctx=${live(tail)} />`);
  assert.equal((tailOut.match(/class="glow"/g) ?? []).length, 4);
  // Its glow, border and chip show again, the glow in the lane's last known colour (its record's black).
  const t1 = tailOut.slice(
    tailOut.indexOf('aria-label="Bay a:1:'),
    tailOut.indexOf('</g>', tailOut.indexOf('aria-label="Bay a:1:')),
  );
  assert.ok(/bay-tile on/.test(t1) && /chip on/.test(t1));
  assert.equal((tailOut.match(/class="glow"[^>]*style="--fil:/g) ?? []).length, 4);
  assert.ok(tailOut.includes('class="glow" d="') && /class="glow"[^>]*--fil:#000000/.test(tailOut));
  // A colour not known at all glows in the unknown grey, never a bright default.
  const css = readFileSync(new URL('../css/units.css', import.meta.url), 'utf8');
  assert.match(css, /\.dg \.glow \{\n {2}stroke: var\(--fil, var\(--unknown\)\);/);
  // On a phone, the row is lit as in its head only with the tail.
  const rows = (a) => renderHtml(html`<${MobileLists} app=${a} ctx=${live(a)} />`);
  const lit = (markup) => /class="mrow on[^"]*"[^>]*aria-label="Bay a:1:/.test(markup);
  assert.deepEqual([lit(rows(app)), lit(rows(tail))], [false, true]);
  // The open bay a:2 has the selected ring, and only it.
  assert.equal((out.match(/class="sel-ring"/g) ?? []).length, 1);
  assert.match(out, /<g class="bay sel"[^>]*aria-current="true"[^>]*aria-label="Bay a:2:/);
  // The panel closed: no mark.
  const closed = renderHtml(html`<${Diagram} app=${{ ...app, ui: { ...app.ui, panel: null } }} ctx=${live(app)} />`);
  assert.ok(!closed.includes('sel-ring'));
  // On a phone, the open bay's row is marked too.
  const phone = renderHtml(html`<${MobileLists} app=${app} ctx=${live(app)} />`);
  assert.equal((phone.match(/class="mrow[^"]*\bsel\b/g) ?? []).length, 1);
  assert.match(phone, /class="mrow[^"]*\bsel\b"[^>]*aria-current="true"[^>]*aria-label="Bay a:2:/);
});

test("real U1: Edit spool under a file's plan offers the file's colors first, each titled; outside a plan, the palette", () => {
  const app = appOf();
  const ctx = live(app);
  ctx.run('define', { key: 'a:2' });
  const at = /** @type {any} */ (laneAt(app, 'a:2'));
  const editor = (a) => renderHtml(html`<${SpoolEditor} app=${a} ctx=${live(a)} at=${at} edit=${app.edit} />`);
  const out = editor(app);
  // The five colors the file prints (colour 8, white too, is configured but never printed), in tool order, then the
  // palette.
  const titled = [...out.matchAll(/class="sw[^"]*" style="--fil:(#[0-9a-f]{6})" title="([^"]+)"/g)].map((m) => [
    m[1],
    m[2],
  ]);
  assert.deepEqual(titled, [
    ['#ffffff', 'color 1'],
    ['#d32c34', 'color 2'],
    ['#5b3f27', 'color 4'],
    ['#fec187', 'color 5'],
    ['#000000', 'color 7'],
  ]);
  assert.ok(out.indexOf('title="color 7"') < out.indexOf(`--fil:${SWATCHES[2]}`));
  // White and black of the palette are not drawn twice.
  assert.equal((out.match(/--fil:#ffffff"/g) ?? []).length, 1);
  // Outside a plan (the Units tab): the palette only.
  const units = editor({ ...app, ui: { ...app.ui, tab: 'units' } });
  assert.ok(!/title="color/.test(units));
  assert.equal((units.match(/class="sw/g) ?? []).length, SWATCHES.length);
});

test('real U1: while the print is printing, a bay spool is not changed (Edit spool, Define, Read tag, Save); paused, it is', () => {
  /** @param {string} state */
  const at = (state) => {
    const app = appOf();
    app.status = { ...app.status, print_stats: { ...app.status.print_stats, state } };
    return app;
  };
  const running = at('printing');
  // The bay panel (the phone sheet is the same panel): Edit spool and Read tag again disabled, with the reason.
  const panel = { ...running, ui: { ...running.ui, panel: { kind: 'bay', key: 'a:2' } } };
  const card = renderHtml(html`<${SidePanel} app=${panel} ctx=${live(panel)} />`);
  const off = (id) => new RegExp(`data-id="${id}"[^>]*disabled[^>]*title="${t('reason.print_running')}"`);
  assert.match(card, off('edit_spool'));
  // b:2 holds a tagged spool: its Read tag again waits too.
  const tagged = { ...running, ui: { ...running.ui, panel: { kind: 'bay', key: 'b:2' } } };
  assert.match(renderHtml(html`<${SidePanel} app=${tagged} ctx=${live(tagged)} />`), off('read_tag'));
  // Define opens the bay without its editor.
  live(running).run('define', { key: 'a:3' });
  assert.deepEqual([running.ui.panel, running.edit], [{ kind: 'bay', key: 'a:3' }, null]);
  // The plan's Define lines are disabled with the reason.
  const plan = renderHtml(html`<${PlanView} app=${running} ctx=${live(running)} />`);
  assert.match(plan, new RegExp(`class="btn warn" disabled[^>]*title="${t('reason.print_running')}"`));
  // Paused: as before.
  const paused = at('paused');
  live(paused).run('define', { key: 'a:3' });
  assert.equal(paused.edit?.lane, 3);
  assert.equal(editAction(sliceFor(paused.status, 'a'), 1, true)?.enabled, true);
  assert.equal(editAction(sliceFor(running.status, 'a'), 1, true)?.reason, 'print_running');
});

test('every diagram lane says whether its path holds filament: filled is a boolean in every demo and the real U1', () => {
  for (const name of ['four', 'unknown', 'one', 'mine', 'printing', 'dryer-fault', 'status']) {
    const status = JSON.parse(readFileSync(new URL(`../dev/demo-${name}.json`, import.meta.url), 'utf8'));
    const app = { ...appOf(), status, ui: { tab: 'units', panel: null, sheet: false } };
    const lanes = diagramModel(app).units.flatMap((u) => u.lanes);
    assert.ok(lanes.length > 0, name);
    for (const l of lanes) assert.equal(typeof l.filled, 'boolean', `${name} ${l.key}`);
  }
  const lanes = diagramModel(appOf()).units.flatMap((u) => u.lanes);
  assert.ok(lanes.every((l) => typeof l.filled === 'boolean'));
});

test('real U1: the head card and the printing marks follow the path rule', () => {
  // a:1 empty after an eject, though T0 still names it: T0's card shows no lane, no colour.
  const app = { ...appOf(), ui: { tab: 'units', panel: null, sheet: false } };
  const model = diagramModel(app);
  const t0 = headView(model, model.heads[0], t);
  assert.deepEqual([t0.colour, t0.line1], [null, '—']);
  // A run-out tail in T0 of a:1's last spool (black PLA): the card names it, in its colour.
  const s = app.status;
  const tail = {
    ...app,
    status: {
      ...s,
      'filament_motion_sensor e0_filament': { filament_detected: true },
      ace2k_u1: {
        ...s.ace2k_u1,
        spools: { ...s.ace2k_u1.spools, 'a:1': { type: 'PLA', color: '#000000', vendor: 'Generic' } },
      },
    },
  };
  const m2 = diagramModel(tail);
  assert.deepEqual(headView(m2, m2.heads[0], t).colour, '#000000');
  assert.ok(headView(m2, m2.heads[0], t).line1.startsWith('a:1 · PLA'));
  // The print view marks a bay "in head" only with filament in its path: not the emptied a:1.
  const marks = marksOf({ ...app, status: { ...s, print_stats: { ...s.print_stats, state: 'printing' } } }, null);
  assert.equal(marks['a:1'], undefined);
  assert.equal(marks['a:3'], 'in_head');
});

test("Read tag waits for a pause; Define is disabled with the bay panel's own reason", () => {
  const app = appOf();
  app.status = { ...app.status, print_stats: { ...app.status.print_stats, state: 'printing' } };
  const b = sliceFor(app.status, 'b');
  assert.ok(laneActions(b, 1, true).some((a) => a.id === 'read_tag' && a.reason === 'print_running'));
  // The Define line's title is the bay panel's reason for Edit spool.
  const plan = renderHtml(html`<${PlanView} app=${app} ctx=${live(app)} />`);
  const ea = editAction(sliceFor(app.status, 'a'), 2, true);
  assert.equal(ea?.reason, 'print_running');
  assert.ok(plan.includes(`title="${t('reason.print_running')}"`));
});

test('while the print is printing, Define never opens an editor, even on a bay whose Edit spool is not offered', () => {
  const app = appOf();
  app.status = { ...app.status, print_stats: { ...app.status.print_stats, state: 'printing' } };
  // b:2 holds a tagged spool: no Edit spool at all.
  assert.equal(editAction(sliceFor(app.status, 'b'), 1, true), null);
  live(app).run('define', { key: 'b:2' });
  assert.deepEqual([app.ui.panel, app.edit], [{ kind: 'bay', key: 'b:2' }, null]);
  // The pill reads the same two states.
  assert.deepEqual([printingNow(app.status), paused(app.status)], [true, false]);
});
