import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { html } from '../js/ui/html.js';
import { ColourRows } from '../js/ui/ColourChips.js';
import { ColourPicker, costWords } from '../js/ui/ColourPicker.js';
import { pickCost, placement, userPick } from '../js/planner.js';
import { runAction } from '../js/actions.js';
import { NEW, asIsChoice, liveLanes, printGate } from '../js/print-plan.js';
import { planNow } from '../js/state.js';
import { autoAdvance, guideOf, stepId, stepText } from '../js/steps.js';
import { keepChoice, startPrint } from '../js/printflow.js';
import { buttons, rendered, renderHtml, textIn, textOf } from './ui-helpers.js';
import { loadEnglish } from './helpers.js';
import { t } from '../js/i18n.js';

loadEnglish();

/** @param {string} colour @param {string | null} lane */
const tool = (colour, lane) => ({ colour, material: 'PLA', lane, temp: null, first_temp: null, why: 'closest' });

// The U1 as the operator saw it on 2026-11-07: a four-colour file of PLA (black, white, magenta, red). a:1-a:4 feed
// head 1 and hold black, white, pink and red; b:1 alone feeds head 4 and is empty. The adapter matched black, white
// and red; magenta has no bay ("too_far"). The suggestion moves the white to b:1, which saves swaps on head 1.
const real = () => JSON.parse(readFileSync(new URL('./fixtures/u1-four-colours-pick.json', import.meta.url), 'utf8'));
// The same, as the live plan reads before any plan was set on the U1: no set choice, magenta with no lane at all.
const unset = () => {
  const s = real();
  s.ace2k_u1.plan = { ...s.ace2k_u1.plan, set: false, choice: null };
  return s;
};

/** @param {any} status */
const appOf = (status) => ({
  status,
  online: true,
  klippy: 'ready',
  selected: null,
  pending: new Set(),
  edit: null,
  events: [],
  printOptions: {},
  folds: {},
  print: { open: true, files: [], file: status.ace2k_u1.plan.file, choice: {}, choiceId: null },
  ui: { tab: 'print', panel: null, sheet: false },
});

// A ctx whose run goes through the real actions, with a fake send that records each script and a confirm that says OK.
/** @param {any} app */
function live(app) {
  /** @type {string[]} */
  const sent = [];
  const ctx = {
    app,
    t,
    demo: false,
    toast: () => {},
    render: () => {},
    confirm: () => true,
    sendScript: async (/** @type {string} */ script) => {
      sent.push(script);
      return true;
    },
  };
  ctx.run = (/** @type {string} */ id, data = {}) => runAction(ctx, id, data);
  ctx.startPrint = () => startPrint(ctx);
  return { ctx, sent };
}

// The colour picker's choice of bay key for colour n (0-based), then a status, as the page runs it.
/** @param {any} app @param {any} ctx @param {number} n @param {string} key */
const set = (app, ctx, n, key) => {
  ctx.run('pick', { n: String(n), key });
  app.status = { ...app.status };
  autoAdvance(app);
};

// What each colour's row says, as the page draws it: "color n … → <what it prints from>".
/** @param {any} app @param {any} ctx */
const rows = (app, ctx) => textOf(renderHtml(html`<${ColourRows} app=${app} ctx=${ctx} now=${planNow(app)} />`));
// The bay of colour n's line in the placement, and its act.
/** @param {any} app @param {number} n */
const line = (app, n) => {
  const l = planNow(app)?.place?.lines.find((x) => x.tools.includes(n));
  return [l?.key, l?.act];
};

for (const [name, status] of [
  ['set plan', real],
  ['live plan', unset],
]) {
  test(`${name}: colours set straight 1 → a:1 … 4 → a:4 show as set, the guide asks nothing, Print sends them`, async () => {
    const app = appOf(status());
    const { ctx, sent } = live(app);
    autoAdvance(app);
    // The suggestion before: the white moves to b:1.
    assert.deepEqual(line(app, 1), ['b:1', 'move']);
    for (const [n, key] of ['a:1', 'a:2', 'a:3', 'a:4'].entries()) set(app, ctx, n, key);
    assert.deepEqual(app.print.merges, {});
    for (const n of [0, 1, 2, 3]) assert.deepEqual(line(app, n), [`a:${n + 1}`, 'there'], `color ${n + 1}`);
    const text = rows(app, ctx);
    assert.ok(!text.includes('= color') && !text.includes('b:1'), text);
    for (const n of [1, 2, 3, 4]) assert.ok(text.includes(`a:${n}`), text);
    assert.deepEqual(guideOf(app)?.steps, []);
    const now = /** @type {NonNullable<ReturnType<typeof planNow>>} */ (planNow(app));
    assert.deepEqual(printGate(now, false).choice, { 0: 'a:1', 1: 'a:2', 2: 'a:3', 3: 'a:4' });
    await ctx.run('print_go');
    assert.match(sent[0] ?? '', /^ACEK_PLAN_SET FILE="4_cores_bench_e05\.gcode" T0=a:1 T1=a:2 T2=a:3 T3=a:4\n/);
  });
}

test('a colour set, then another: each shows its bay, and the colours not set keep their spools', () => {
  const app = appOf(real());
  const { ctx } = live(app);
  autoAdvance(app);
  set(app, ctx, 1, 'a:2');
  // White stays in a:2: no step at all, and no other spool is moved to b:1 in its place.
  assert.deepEqual(app.print.overrides, [1]);
  assert.deepEqual(
    [0, 1, 2, 3].map((n) => line(app, n)),
    [
      ['a:1', 'there'],
      ['a:2', 'there'],
      ['a:3', 'there'],
      ['a:4', 'there'],
    ],
  );
  assert.deepEqual(guideOf(app)?.steps, []);
  set(app, ctx, 2, 'a:3');
  assert.deepEqual(
    [line(app, 1), line(app, 2)],
    [
      ['a:2', 'there'],
      ['a:3', 'there'],
    ],
  );
  assert.deepEqual(guideOf(app)?.steps, []);
});

test('a bay another colour prints from is a pick, never a join: the screenshot of 2026-11-07', () => {
  const app = appOf(real());
  const { ctx } = live(app);
  autoAdvance(app);
  // Magenta (colour 3) set to the white spool in a:2, then white (colour 2) set to a:2 as well.
  set(app, ctx, 2, 'a:2');
  set(app, ctx, 1, 'a:2');
  assert.deepEqual(app.print.merges, {});
  // Both rows show a:2, neither "= color 3" nor b:1, and the guide moves no spool of a:2.
  const text = rows(app, ctx);
  assert.ok(!text.includes('= color') && !text.includes('b:1'), text);
  assert.deepEqual(
    [line(app, 1), line(app, 2)],
    [
      ['a:2', 'there'],
      ['a:2', 'there'],
    ],
  );
  const steps = guideOf(app)?.steps ?? [];
  assert.ok(!steps.some((s) => s.key === 'a:2' || s.from === 'a:2'), JSON.stringify(steps));
  assert.ok(!steps.some((s) => s.tools?.some((n) => n === 1 || n === 2)), JSON.stringify(steps));
  const now = /** @type {NonNullable<ReturnType<typeof planNow>>} */ (planNow(app));
  assert.deepEqual(
    [asIsChoice(now.plan, now.pick, now.merges)[1], asIsChoice(now.plan, now.pick, now.merges)[2]],
    ['a:2', 'a:2'],
  );
});

test('two similar colours set to one spool print from it, shown plainly', async () => {
  const app = appOf(real());
  const { ctx, sent } = live(app);
  autoAdvance(app);
  // Magenta and red both from the red spool of a:4, by the user's choice.
  set(app, ctx, 2, 'a:4');
  set(app, ctx, 3, 'a:4');
  assert.deepEqual(app.print.merges, {});
  assert.deepEqual(
    [line(app, 2), line(app, 3)],
    [
      ['a:4', 'there'],
      ['a:4', 'there'],
    ],
  );
  const steps = guideOf(app)?.steps ?? [];
  assert.ok(!steps.some((s) => s.key === 'a:4' || s.from === 'a:4'), JSON.stringify(steps));
  assert.ok(!steps.some((s) => s.tools?.some((n) => n === 2 || n === 3)), JSON.stringify(steps));
  // The others: nothing set, the suggestion as before.
  set(app, ctx, 0, 'a:1');
  set(app, ctx, 1, 'a:2');
  await ctx.run('print_go');
  assert.match(sent[0] ?? '', / T0=a:1 T1=a:2 T2=a:4 T3=a:4\n/);
});

test('a colour with no spool loaded ("too_far") set to any loaded spool prints from it, with no step', () => {
  const app = appOf(unset());
  const { ctx } = live(app);
  autoAdvance(app);
  assert.equal(app.status.ace2k_u1.plan.tools[2].why, 'too_far');
  for (const key of ['a:3', 'a:2']) {
    set(app, ctx, 2, key);
    assert.deepEqual([line(app, 2), app.print.merges], [[key, 'there'], {}], key);
    const steps = guideOf(app)?.steps ?? [];
    assert.ok(!steps.some((s) => s.tools?.includes(2) || s.key === key || s.from === key), JSON.stringify(steps));
    assert.ok(!rows(app, ctx).includes('= color'), key);
  }
  // On the white spool, magenta leaves white (not set by the user) to the suggestion: a white spool to put elsewhere.
  assert.notEqual(line(app, 1)[0], 'a:2');
});

// The cost the picker shows for colour n on bay key, and the change in best swaps that the click then makes.
/** @param {any} app @param {any} ctx @param {number} n @param {string} key */
const shownAndReal = (app, ctx, n, key) => {
  const now = /** @type {NonNullable<ReturnType<typeof planNow>>} */ (planNow(app));
  const shown = pickCost(now.plan, now.raw, now.planLanes, now.merges, n, key, now.fixed);
  const html1 = textOf(renderHtml(html`<${ColourPicker} app=${app} ctx=${ctx} n=${n} />`));
  const before = now.place?.best ?? 0;
  set(app, ctx, n, key);
  return { shown, real: (planNow(app)?.place?.best ?? 0) - before, html1 };
};

test('a bay set by the user that runs empty keeps the colour on it, and the guide asks to put a spool there', () => {
  const app = appOf(real());
  const { ctx } = live(app);
  autoAdvance(app);
  set(app, ctx, 1, 'a:2');
  const lanes = app.status['ace2k a'].lanes;
  lanes[1] = { ...lanes[1], insert: false, tag: { state: 'unknown', uid: null, source: null, record: null } };
  app.status = { ...app.status };
  autoAdvance(app);
  assert.equal(app.print.choice[1], 'a:2');
  assert.deepEqual(line(app, 1), ['a:2', 'put']);
  const steps = guideOf(app)?.steps ?? [];
  assert.ok(
    steps.some((s) => s.kind === 'put' && s.key === 'a:2' && s.tools.includes(1)),
    JSON.stringify(steps),
  );
  assert.ok(!steps.some((s) => s.key === 'a:2' && !s.tools?.includes(1)), JSON.stringify(steps));
});

test('a new spool is not a bay set by the user: its put step, once done, stays a suggestion', () => {
  const app = appOf(real());
  const { ctx } = live(app);
  autoAdvance(app);
  set(app, ctx, 2, 'new');
  assert.deepEqual(app.print.overrides ?? [], []);
  const g = /** @type {NonNullable<ReturnType<typeof guideOf>>} */ (guideOf(app));
  const put = g.steps.find((s) => s.kind === 'put' && s.tools.includes(2));
  assert.ok(put, JSON.stringify(g.steps));
  app.print = { ...app.print, trail: g.trail, step: g.trail.indexOf(stepId(/** @type {any} */ (put))) };
  ctx.run('step_done');
  assert.equal(app.print.choice[2], put?.key);
  assert.ok(!(app.print.overrides ?? []).includes(2), JSON.stringify(app.print.overrides));
});

test('an override made for another plan does not come back on a new pick', () => {
  const app = appOf(real());
  app.print = { ...app.print, choice: { 2: 'a:3' }, choiceId: 'other.gcode\n1', overrides: [2] };
  const { ctx } = live(app);
  set(app, ctx, 0, 'a:1');
  assert.deepEqual(app.print.overrides, [0]);
});

test('the cost the picker shows is the change the click makes, also on a bay the user took from another colour', () => {
  const app = appOf(real());
  const { ctx } = live(app);
  autoAdvance(app);
  // Magenta on the white spool: white loses a:2 to it. Then magenta moves to a:3, and white has a:2 back.
  set(app, ctx, 2, 'a:2');
  const { shown, real: made } = shownAndReal(app, ctx, 2, 'a:3');
  assert.equal(shown, made);
});

test('the cost the picker shows counts a spool held in hand in its bay, as the plan does', () => {
  const app = appOf(real());
  const { ctx } = live(app);
  autoAdvance(app);
  // Eject all took the white spool out of a:2: it is in hand, and still counts in a:2.
  const lanes = app.status['ace2k a'].lanes;
  lanes[1] = { ...lanes[1], insert: false, tag: { state: 'unknown', uid: null, source: null, record: null } };
  app.status = { ...app.status };
  const white = { id: 'w', from: 'a:2', material: 'PLA', colour: '#eff0f1', tag: false, uid: null };
  app.print = { ...app.print, held: [white] };
  const now = /** @type {NonNullable<ReturnType<typeof planNow>>} */ (planNow(app));
  const picker = textOf(renderHtml(html`<${ColourPicker} app=${app} ctx=${ctx} n=${1} />`));
  const d = pickCost(now.plan, now.raw, now.planLanes, now.merges, 1, 'a:3', now.fixed);
  assert.ok(picker.includes(`a:3 PLA ${costWords(t, d, now.place?.fits ?? true).text}`), picker);
});

test('a choice that changes nothing costs nothing', () => {
  const app = appOf(real());
  autoAdvance(app);
  const now = /** @type {NonNullable<ReturnType<typeof planNow>>} */ (planNow(app));
  for (const l of now.place?.lines ?? [])
    if (l.act === 'there')
      for (const n of l.tools)
        assert.equal(pickCost(now.plan, now.raw, now.planLanes, now.merges, n, l.key, now.fixed), 0, l.key);
});

// Two colours that look the same (white PLA) and a third, on one head of two lanes and one head of one.
const twins = {
  file: 'twins.gcode',
  mtime: 1,
  tools: [tool('#ffffff', 'a:1'), tool('#ffffff', 'b:1'), tool('#000000', 'a:2')],
  order: [0, 2, 1, 2, 0, 2, 1],
};
const twinLanes = [
  { key: 'a:1', head: 0, material: 'PLA', colour: '#ffffff', empty: false },
  { key: 'a:2', head: 0, material: 'PLA', colour: '#000000', empty: false },
  { key: 'b:1', head: 1, material: 'PLA', colour: '#ffffff', empty: false },
];

test('twins the user set to two bays print from those two bays', () => {
  const p = placement(twins, { 0: 'a:1', 1: 'b:1', 2: 'a:2' }, twinLanes, {}, [0, 1]);
  const at = (n) => p?.lines.find((l) => l.tools.includes(n))?.key;
  assert.deepEqual([at(0), at(1), p?.choice[0], p?.choice[1]], ['a:1', 'b:1', 'a:1', 'b:1']);
});

test('a twin picked new does not take away the bay the user set for its twin', () => {
  const p = placement(twins, { 0: 'a:1', 1: NEW, 2: 'a:2' }, twinLanes, {}, [0]);
  const of = (n) => p?.lines.find((l) => l.tools.includes(n));
  assert.deepEqual([of(0)?.key, of(0)?.act], ['a:1', 'there']);
  assert.notEqual(of(1)?.key, 'a:1');
});

test('a bay the user set that now holds another material: the guide takes it out and puts the right one', () => {
  const lanes = twinLanes.map((l) => (l.key === 'a:2' ? { ...l, material: 'PETG' } : l));
  const p = placement(twins, { 0: 'a:1', 1: 'b:1', 2: 'a:2' }, lanes, {}, [2]);
  const of = p?.lines.find((l) => l.tools.includes(2));
  assert.deepEqual([of?.key, of?.act, p?.takeOut.includes('a:2')], ['a:2', 'put', true]);
});

test('a twin left unset shares the bay its twin was set to, in whichever order the two colours come', () => {
  // White 1 not set, on b:1 as the adapter matched it; white 2 set by the user to b:1.
  const p = placement(twins, { 0: 'b:1', 1: 'b:1', 2: 'a:2' }, twinLanes, {}, [1]);
  assert.equal(p?.colours, 2);
  assert.deepEqual(
    p?.lines.map((l) => [l.tools, l.key]),
    [
      [[0, 1], 'b:1'],
      [[2], 'a:2'],
    ],
  );
});

// Red, blue, and blue again; a:1 holds a red spool.
const shared = {
  file: 'shared.gcode',
  mtime: 1,
  tools: [tool('#ff0000', 'a:1'), tool('#0000ff', null), tool('#0000ff', 'a:1')],
  order: [0, 1, 2, 0, 1, 2],
};
const sharedLanes = [
  { key: 'a:1', head: 0, material: 'PLA', colour: '#ff0000', empty: false },
  { key: 'a:2', head: 0, material: 'PLA', colour: '#000000', empty: false },
  { key: 'b:1', head: 1, material: 'PLA', colour: '#ffffff', empty: false },
];

test('a twin left unset of any colour set to a bay shares it, not only the first one set there', () => {
  // Red and blue set to a:1 by the user; the second blue, not set, picks a:1 as well.
  const pick = { 0: 'a:1', 1: 'a:1', 2: 'a:1' };
  assert.equal(userPick(shared, pick, [0, 1])[2], 'a:1');
  const p = placement(shared, pick, sharedLanes, {}, [0, 1]);
  assert.deepEqual([p?.colours, p?.lines.map((l) => [l.tools, l.key, l.act])], [1, [[[0, 1, 2], 'a:1', 'there']]]);
});

test('two materials set to one bay: no step that can never be done, and Print says why it cannot', () => {
  const plan = { ...shared, tools: [shared.tools[0], { ...shared.tools[1], material: 'PETG' }], order: [0, 1, 0, 1] };
  const pick = { 0: 'a:1', 1: 'a:1' };
  const p = /** @type {NonNullable<ReturnType<typeof placement>>} */ (placement(plan, pick, sharedLanes, {}, [0, 1]));
  assert.deepEqual([p.lines.map((l) => l.act), p.takeOut], [['there'], []]);
  const gate = printGate({ plan, lanes: sharedLanes, pick, user: pick, merges: {}, place: p }, false);
  assert.ok(gate.block, JSON.stringify(gate));
});

test("Done on the put into the user's own bay keeps the bay the user's", () => {
  const app = appOf(real());
  const { ctx } = live(app);
  autoAdvance(app);
  // Black was settled on a:1 earlier (a done step, not the user's choice); then the user sets white to a:1.
  app.print = {
    ...app.print,
    choice: { 0: 'a:1' },
    choiceId: `${app.status.ace2k_u1.plan.file}\n${app.status.ace2k_u1.plan.mtime}`,
  };
  set(app, ctx, 1, 'a:1');
  const lanes = app.status['ace2k a'].lanes;
  lanes[0] = { ...lanes[0], insert: false, tag: { state: 'unknown', uid: null, source: null, record: null } };
  app.status = { ...app.status };
  autoAdvance(app);
  const g = /** @type {NonNullable<ReturnType<typeof guideOf>>} */ (guideOf(app));
  const put = g.steps.find((s) => s.kind === 'put' && s.key === 'a:1');
  assert.ok(put?.tools.includes(1), JSON.stringify(g.steps));
  app.print = { ...app.print, trail: g.trail, step: g.trail.indexOf(stepId(/** @type {any} */ (put))) };
  ctx.run('step_done');
  assert.ok((app.print.overrides ?? []).includes(1), JSON.stringify(app.print.overrides));
  const now = /** @type {NonNullable<ReturnType<typeof planNow>>} */ (planNow(app));
  assert.notEqual(now.pick[0], 'a:1');
});

test('Done on a step of two colours the user set to one bay settles both', () => {
  const app = appOf(real());
  const { ctx } = live(app);
  autoAdvance(app);
  set(app, ctx, 2, 'a:4');
  set(app, ctx, 3, 'a:4');
  const lanes = app.status['ace2k a'].lanes;
  lanes[3] = { ...lanes[3], insert: false, tag: { state: 'unknown', uid: null, source: null, record: null } };
  app.status = { ...app.status };
  autoAdvance(app);
  const g = /** @type {NonNullable<ReturnType<typeof guideOf>>} */ (guideOf(app));
  const put = g.steps.find((s) => s.kind === 'put' && s.key === 'a:4');
  assert.deepEqual(put?.tools, [2, 3], JSON.stringify(g.steps));
  app.print = { ...app.print, trail: g.trail, step: g.trail.indexOf(stepId(/** @type {any} */ (put))) };
  ctx.run('step_done');
  const p = app.print;
  assert.deepEqual(
    [p.choice[2], p.choice[3], p.pinned?.[2] ?? null, (p.overrides ?? []).includes(2), (p.overrides ?? []).includes(3)],
    ['a:4', 'a:4', p.pinned?.[3] ?? null, true, true],
  );
});

// Bay i of a unit (0-based) as the status reads it: empty, a spool of no material, or a spool of a material and colour
// (an Edit spool record, no tag), then a status, as the page runs it.
/**
 * @param {any} app @param {any} ctx @param {number} i @param {{ type: string, color: string } | 'empty' | 'unset'} spool
 * @param {string} [unit]
 */
const bay = (app, ctx, i, spool, unit = 'a') => {
  const s = structuredClone(app.status);
  const noTag = { state: 'unknown', uid: null, source: null, record: null };
  const lanes = s[`ace2k ${unit}`].lanes;
  lanes[i] = { ...lanes[i], insert: spool !== 'empty', tag: noTag };
  const spools = { ...s.ace2k_u1.spools };
  delete spools[`${unit}:${i + 1}`];
  if (typeof spool === 'object') spools[`${unit}:${i + 1}`] = { ...spool, vendor: 'Generic' };
  s.ace2k_u1.spools = spools;
  app.status = s;
  keepChoice(ctx);
  autoAdvance(app);
};
// The file planned again (ACEK_PLAN, as the Print tab's click asks): a new plan of the same file, its lanes read from
// the bays as they are now, then a status.
/** @param {any} app @param {any} ctx */
const replan = (app, ctx) => {
  const s = structuredClone(app.status);
  const plan = s.ace2k_u1.plan;
  const lanes = liveLanes(app.status, plan).map(({ key, head, colour, material, empty }) => ({
    key,
    head,
    colour,
    material,
    empty,
  }));
  s.ace2k_u1.plan = { ...plan, mtime: plan.mtime + 1, lanes };
  app.status = s;
  keepChoice(ctx);
  autoAdvance(app);
};
// What keeps Print disabled, as the bar says it, or null.
/** @param {any} app */
const block = (app) => printGate(/** @type {NonNullable<ReturnType<typeof planNow>>} */ (planNow(app)), false).block;
// The picker's buttons of colour n, as their words and props.
/** @param {any} app @param {any} ctx @param {number} n */
const pickerButtons = (app, ctx, n) =>
  buttons(rendered(html`<${ColourPicker} app=${app} ctx=${ctx} n=${n} />`).nodes).map((v) => ({
    text: textIn(v).replace(/\s+/g, ' ').trim(),
    props: v.props,
  }));
// A live ctx whose toasts are kept.
/** @param {any} app */
const toasting = (app) => {
  const l = live(app);
  /** @type {string[]} */
  const toasts = [];
  l.ctx.toast = (/** @type {string} */ m) => toasts.push(m);
  return { ...l, toasts };
};

test('an empty bay is picked like a full one: the colour is set there, the guide puts a new spool, Print waits', async () => {
  const app = appOf(real());
  const { ctx, sent } = live(app);
  bay(app, ctx, 1, 'empty');
  // The empty a:2 is offered for colour 2 (white), and its click is a pick of a:2.
  const empty = pickerButtons(app, ctx, 1).find((b) => b.text.startsWith('a:2'));
  assert.ok(empty && !empty.props.disabled && empty.text.includes(t('units.empty')), JSON.stringify(empty?.text));
  empty.props.onClick();
  app.status = { ...app.status };
  autoAdvance(app);
  assert.deepEqual([app.print.choice[1], app.print.overrides], ['a:2', [1]]);
  assert.deepEqual(line(app, 1), ['a:2', 'put']);
  assert.ok(rows(app, ctx).includes('new spool → a:2'), rows(app, ctx));
  const now = /** @type {NonNullable<ReturnType<typeof planNow>>} */ (planNow(app));
  const put = guideOf(app)?.steps.find((x) => x.kind === 'put' && x.key === 'a:2');
  assert.equal(put && stepText(t, put, now), 'Put a new PLA spool for color 2 in bay a:2');
  assert.equal(block(app), 'No spool for color 2: put one in a:2 or pick a bay.');
  // A spool goes in with no material set: the pick stays, and Print names the bay to define.
  bay(app, ctx, 1, 'unset');
  assert.equal(app.print.choice[1], 'a:2');
  assert.equal(block(app), t('print.define_first', { key: 'a:2', n: 2 }));
  // Its material is set (a tag read or Edit spool): Print is free, and sends colour 2 from a:2.
  bay(app, ctx, 1, { type: 'PLA', color: '#ffffff' });
  assert.deepEqual([app.print.choice[1], block(app)], ['a:2', null]);
  // A spool of another material instead: refused, as for a full bay of another material.
  bay(app, ctx, 1, { type: 'PETG', color: '#ffffff' });
  assert.equal(app.print.choice[1], 'a:2');
  assert.ok(block(app)?.includes('a:2'), String(block(app)));
  // The right one back: Print sends colour 2 from a:2.
  bay(app, ctx, 1, { type: 'PLA', color: '#ffffff' });
  assert.deepEqual([app.print.choice[1], block(app)], ['a:2', null]);
  await ctx.run('print_go');
  assert.match(sent[0] ?? '', / T1=a:2 /);
});

test("an empty bay of another head is picked on the same terms: the colour's line puts a new spool there", () => {
  const app = appOf(real());
  const { ctx } = live(app);
  autoAdvance(app);
  const b1 = pickerButtons(app, ctx, 2).find((b) => b.text.startsWith('b:1'));
  assert.ok(b1 && !b1.props.disabled, JSON.stringify(b1?.text));
  set(app, ctx, 2, 'b:1');
  assert.deepEqual([line(app, 2), app.print.overrides], [['b:1', 'put'], [2]]);
  assert.ok(block(app)?.includes('b:1'), String(block(app)));
});

test('an Edit spool under a pick keeps the pick on its colour and bay: the 2026-11-08 bench', () => {
  for (const order of [
    [0, 1],
    [1, 0],
  ]) {
    const app = appOf(real());
    const { ctx, toasts } = toasting(app);
    // a:1 holds a white PLA, a:2 a black one (Edit spool records); colour 1 (black) set to a:1.
    bay(app, ctx, 0, { type: 'PLA', color: '#ffffff' });
    bay(app, ctx, 1, { type: 'PLA', color: '#000000' });
    set(app, ctx, 0, 'a:1');
    // Then a:1 is edited to black and a:2 to white, in either order.
    for (const i of order) bay(app, ctx, i, { type: 'PLA', color: i === 0 ? '#000000' : '#ffffff' });
    // a:1 now holds colour 1's own colour: nothing to say.
    assert.deepEqual(toasts, [], String(order));
    assert.deepEqual([app.print.choice[0], app.print.overrides], ['a:1', [0]], String(order));
    assert.deepEqual(line(app, 0), ['a:1', 'there'], String(order));
    // No other colour prints from a:1.
    const now = /** @type {NonNullable<ReturnType<typeof planNow>>} */ (planNow(app));
    assert.ok(
      now.place?.lines.every((l) => l.key !== 'a:1' || l.tools.join() === '0'),
      JSON.stringify(now.place?.lines),
    );
    // Edited to another material: the pick stays on colour 1, and Print refuses it, naming the bay and both materials.
    bay(app, ctx, 0, { type: 'PETG', color: '#000000' });
    assert.deepEqual([app.print.choice[0], line(app, 0)], ['a:1', ['a:1', 'put']], String(order));
    assert.ok(
      block(app)?.includes(t('print.bay_material', { key: 'a:1', what: 'PETG', want: 'PLA', n: 1 })),
      String(block(app)),
    );
  }
});

test('a new spool says the planner picks its bay, and its line and Print name that bay', () => {
  const app = appOf(real());
  const { ctx } = live(app);
  autoAdvance(app);
  const fresh = pickerButtons(app, ctx, 2).find((b) => b.text.startsWith('put a new'));
  assert.ok(fresh?.text.startsWith('put a new PLA #EB087E spool — the planner picks the bay'), fresh?.text);
  set(app, ctx, 2, 'new');
  const [key, act] = line(app, 2);
  assert.equal(act, 'put');
  assert.ok(rows(app, ctx).includes(`new spool → ${key}`), rows(app, ctx));
  assert.ok(block(app)?.includes(String(key)), String(block(app)));
});

test('an empty bay the user set survives a re-plan: no "dropped" toast, and Print still says what it lacks', () => {
  const app = appOf(real());
  const { ctx, toasts } = toasting(app);
  bay(app, ctx, 1, 'empty');
  set(app, ctx, 1, 'a:2');
  replan(app, ctx);
  assert.deepEqual([app.print.choice[1], app.print.overrides, toasts], ['a:2', [1], []]);
  assert.equal(block(app), 'No spool for color 2: put one in a:2 or pick a bay.');
});

test('a bay the user set, edited to another material, survives a re-plan, and Print says why it cannot', () => {
  const app = appOf(real());
  const { ctx, toasts } = toasting(app);
  set(app, ctx, 1, 'a:2');
  bay(app, ctx, 1, { type: 'PETG', color: '#ffffff' });
  replan(app, ctx);
  assert.deepEqual([app.print.choice[1], app.print.overrides, toasts], ['a:2', [1], []]);
  assert.equal(block(app), t('print.bay_material', { key: 'a:2', what: 'PETG', want: 'PLA', n: 2 }));
});

test('a bay the user set refilled with the same material in another colour: kept, said once, Print allowed', () => {
  const app = appOf(real());
  const { ctx, toasts } = toasting(app);
  set(app, ctx, 1, 'a:2');
  bay(app, ctx, 1, { type: 'PLA', color: '#000000' });
  assert.deepEqual([app.print.choice[1], line(app, 1)], ['a:2', ['a:2', 'there']]);
  assert.equal(toasts.length, 1);
  assert.equal(toasts[0], 'a:2 now holds black for color 2');
  bay(app, ctx, 1, { type: 'PLA', color: '#000000' });
  assert.equal(toasts.length, 1, 'once per change');
  assert.ok(rows(app, ctx).includes('a:2 · black'), rows(app, ctx));
  assert.equal(block(app), null);
});

test("the user's spool moved to another bay while another fills the user's bay: the pick is the bay", () => {
  const app = appOf(real());
  const { ctx } = live(app);
  set(app, ctx, 1, 'a:2');
  // The white spool goes to the empty b:1, and a black one into a:2.
  bay(app, ctx, 0, { type: 'PLA', color: '#ffffff' }, 'b');
  bay(app, ctx, 1, { type: 'PLA', color: '#000000' });
  assert.deepEqual([app.print.choice[1], line(app, 1)], ['a:2', ['a:2', 'there']]);
  // The white in b:1 is free: colour 2 does not own it, and no step moves it into a:2.
  const now = /** @type {NonNullable<ReturnType<typeof planNow>>} */ (planNow(app));
  assert.ok(!now.place?.lines.some((l) => l.key === 'b:1' && l.tools.includes(1)), JSON.stringify(now.place?.lines));
  const steps = guideOf(app)?.steps ?? [];
  assert.ok(!steps.some((x) => x.key === 'a:2'), JSON.stringify(steps));
});

test('a bay whose spool eject all holds in hand: the picker shows it as the planner reads it, that spool', () => {
  const app = appOf(real());
  const { ctx } = live(app);
  autoAdvance(app);
  const lanes = app.status['ace2k a'].lanes;
  lanes[1] = { ...lanes[1], insert: false, tag: { state: 'unknown', uid: null, source: null, record: null } };
  app.status = { ...app.status };
  const white = { id: 'w', from: 'a:2', material: 'PLA', colour: '#eff0f1', tag: false, uid: null };
  app.print = { ...app.print, held: [white] };
  const a2 = pickerButtons(app, ctx, 1).find((b) => b.text.startsWith('a:2'));
  assert.ok(a2?.text.startsWith('a:2 PLA') && !a2.text.includes(t('units.empty')), a2?.text);
  assert.ok(!a2?.props.disabled);
});

test('the first spool put in an empty bay the user set: compared with the colour once, by its own colour', () => {
  const app = appOf(real());
  const { ctx, toasts } = toasting(app);
  bay(app, ctx, 1, 'empty');
  set(app, ctx, 1, 'a:2');
  // A spool of no material first: no colour to compare, and Print says to define it.
  bay(app, ctx, 1, 'unset');
  assert.deepEqual([toasts, block(app)], [[], t('print.define_first', { key: 'a:2', n: 2 })]);
  // Defined as black PLA: colour 2 is white, so the page says it once.
  bay(app, ctx, 1, { type: 'PLA', color: '#000000' });
  bay(app, ctx, 1, { type: 'PLA', color: '#000000' });
  assert.deepEqual([toasts, block(app)], [['a:2 now holds black for color 2'], null]);
  // Emptied and filled with a white one: the colour's own, nothing to say.
  bay(app, ctx, 1, 'empty');
  bay(app, ctx, 1, { type: 'PLA', color: '#ffffff' });
  assert.equal(toasts.length, 1);
});

test('a bay the user set whose spool eject all holds in hand: picked as that spool, Print waits for it back', () => {
  const app = appOf(real());
  const { ctx } = live(app);
  autoAdvance(app);
  const lanes = app.status['ace2k a'].lanes;
  lanes[1] = { ...lanes[1], insert: false, tag: { state: 'unknown', uid: null, source: null, record: null } };
  app.status = { ...app.status };
  const white = { id: 'w', from: 'a:2', material: 'PLA', colour: '#eff0f1', tag: false, uid: null };
  app.print = { ...app.print, held: [white] };
  set(app, ctx, 1, 'a:2');
  // The pick is pinned on the spool in hand, as on a full bay.
  assert.deepEqual([app.print.overrides, app.print.pinned?.[1]], [[1], { uid: null, look: 'pla:#eff0f1' }]);
  // It is out of the unit: Print names the bay until it is back.
  assert.ok(block(app)?.includes('a:2'), String(block(app)));
});
