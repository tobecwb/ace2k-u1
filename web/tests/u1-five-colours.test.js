import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { html } from '../js/ui/html.js';
import { PlanView } from '../js/ui/PlanView.js';
import { Timeline } from '../js/ui/Timeline.js';
import { runAction } from '../js/actions.js';
import { asIsChoice, headOfIn, printGate, sentKinds, swapKinds } from '../js/print-plan.js';
import { PrintBar } from '../js/ui/PrintBar.js';
import { planNow } from '../js/state.js';
import { asIsChecks, autoAdvance, guideOf, stepNext } from '../js/steps.js';
import { DASH } from '../js/format.js';
import { startPrint } from '../js/printflow.js';
import { renderHtml, textOf } from './ui-helpers.js';
import { loadEnglish } from './helpers.js';
import { t } from '../js/i18n.js';

loadEnglish();

// A status of a real U1 with two units, taken with the five-colour file planned. a:1 and a:2 share T0 and hold
// spools set by hand (blue and red PLA); a:3 (black PLA, T1) and a:4 (white PLA, T2) are named only from their
// heads' settings on the U1: their tags wait to be read again after a restart, so the adapter has no material set
// for them; b:1 (brown PLA) feeds T3. The file prints white, red, brown, peach and black; red and peach have no
// bay as it is.
const real = () => JSON.parse(readFileSync(new URL('./fixtures/u1-five-colours.json', import.meta.url), 'utf8'));
const FILE = 'objeto_5_cores_PLA_1h36m.gcode';

// The picks given are the user's own, set in the colour picker (overrides), unless print says otherwise.
/** @param {any} status @param {Record<string, any>} [print] */
const appOf = (status, print = {}) => ({
  status,
  online: true,
  klippy: 'ready',
  selected: null,
  pending: new Set(),
  edit: null,
  events: [],
  printOptions: {},
  folds: {},
  print: {
    open: true,
    files: [],
    file: FILE,
    choice: {},
    choiceId: null,
    overrides: Object.keys(print.choice ?? {}).map(Number),
    ...print,
  },
  ui: { tab: 'print', panel: null, sheet: false },
});

// A ctx whose run goes through the real actions, with a fake send that records each script.
function live(app, answer = true) {
  const sent = [];
  const confirm = [];
  const ctx = {
    app,
    t,
    demo: false,
    toast: () => {},
    render: () => {},
    confirm: (q) => {
      confirm.push(q);
      return answer;
    },
    sendScript: async (script) => {
      sent.push(script);
      return true;
    },
  };
  ctx.run = (id, data = {}) => runAction(ctx, id, data);
  ctx.startPrint = () => startPrint(ctx);
  return { ctx, sent, confirm };
}

// The material of a:3 and a:4 set with Edit spool, as the adapter then reports it.
function defined(s) {
  const u = s.ace2k_u1;
  u.spools = { ...u.spools, 'a:3': { type: 'PLA', color: '#000000', vendor: 'Generic' } };
  u.spools['a:4'] = { type: 'PLA', color: '#eff0f1', vendor: 'Generic' };
  u.lanes['a:3'] = { ...u.lanes['a:3'], material_set: true };
  u.lanes['a:4'] = { ...u.lanes['a:4'], material_set: true };
  return s;
}

// The user's picks in the colour chips: red (colour 2) from a:2, peach (colour 5) from the blue spool of a:1.
const PICKS = { 1: 'a:2', 4: 'a:1' };

test('real U1: a bay the page names from its head but whose material the adapter has not set gets a Define line', () => {
  const app = appOf(real());
  const out = textOf(renderHtml(html`<${PlanView} app=${app} ctx=${live(app).ctx} />`));
  for (const key of ['a:3', 'a:4']) {
    assert.ok(out.includes(t('print.unset_bay', { key })), key);
    assert.ok(out.includes(t('print.define', { key })), key);
  }
  // a:1 and a:2 have their material set by hand: no line.
  assert.ok(!out.includes(t('print.define', { key: 'a:1' })));
  // Print is blocked, and the bar says why: colour 1 (white) prints from a:4, whose material is not set; colour 5's
  // spool is still to put in a:3.
  assert.ok(out.includes(t('print.material_unset', { tools: 'color 1' })));
  assert.ok(out.includes(t('print.put_pending', { tools: 'color 5', key: 'a:3' })));
});

test('real U1: Define opens the bay card with Edit spool already open', () => {
  // The spools of a:1 and a:2 before they were set by hand: no material at all.
  const s = real();
  delete s.ace2k_u1.spools['a:1'];
  delete s.ace2k_u1.spools['a:2'];
  s.ace2k_u1.lanes['a:1'].material_set = false;
  s.ace2k_u1.lanes['a:2'].material_set = false;
  const app = appOf(s);
  const out = textOf(renderHtml(html`<${PlanView} app=${app} ctx=${live(app).ctx} />`));
  assert.ok(out.includes(t('print.unknown_bay', { key: 'a:1' })));
  live(app).ctx.run('define', { key: 'a:1' });
  assert.deepEqual(app.ui.panel, { kind: 'bay', key: 'a:1' });
  assert.deepEqual([app.edit?.unit, app.edit?.lane], ['a', 1]);
});

test('real U1: Print sends the bays the user picked, more swaps and all, and the guide never fights them', async () => {
  const app = appOf(defined(real()), { choice: PICKS, choiceId: `${FILE}\n${real().ace2k_u1.plan.mtime}` });
  const now = /** @type {NonNullable<ReturnType<typeof planNow>>} */ (planNow(app));
  // The colours the user set print from their bays: the guide asks nothing, and the other colours keep their spools.
  assert.deepEqual(guideOf(app)?.steps, []);
  assert.deepEqual(
    now.place?.lines.map((l) => [l.tools, l.key, l.act]),
    [
      [[0], 'a:4', 'there'],
      [[1], 'a:2', 'there'],
      [[3], 'b:1', 'there'],
      [[4], 'a:1', 'there'],
      [[6], 'a:3', 'there'],
    ],
  );
  const gate = printGate(now, false);
  assert.deepEqual([gate.block, gate.asIs], [null, true]);
  const out = renderHtml(html`<${PlanView} app=${app} ctx=${live(app).ctx} />`);
  assert.ok(!/class="btn go" disabled/.test(out));
  // Peach (colour 5) prints from the blue spool: Print asks, naming it.
  const no = live(app, false);
  await no.ctx.run('print_go');
  assert.equal(no.confirm.length, 1);
  // The blue spool has no colour name from the host: it is named by its material, with the colour after the bay.
  const blue = t('print.as_is_colour_hex', { head: 'T0', n: 5, what: 'PLA', key: 'a:1', colour: '#1f6feb' });
  assert.ok(no.confirm[0].includes(blue), no.confirm[0]);
  // Red (colour 2) prints from the red spool: nothing to ask about it.
  assert.ok(!no.confirm[0].includes('color 2'));
  assert.deepEqual(no.sent, []);
  // A confirm, never a block: OK prints the bays as they are.
  const { ctx, sent } = live(app);
  await ctx.run('print_go');
  assert.match(sent[0], /^ACEK_PLAN_SET FILE="objeto_5_cores_PLA_1h36m\.gcode" T0=a:4 T1=a:2 T3=b:1 T4=a:1 T6=a:3\n/);
});

test("real U1: a pick on a bay with no material set blocks with the bay to define; the adapter's own falls back", () => {
  // The user picks red (colour 2) on a:3, whose material the adapter has not set: Print is blocked, naming a:3.
  const app = appOf(real(), { choice: { 1: 'a:3', 4: 'a:1' }, choiceId: `${FILE}\n${real().ace2k_u1.plan.mtime}` });
  const now = /** @type {NonNullable<ReturnType<typeof planNow>>} */ (planNow(app));
  const gate = printGate(now, false);
  assert.equal(gate.block, t('print.define_first', { key: 'a:3', n: 2 }));
  const out = textOf(renderHtml(html`<${PlanView} app=${app} ctx=${live(app).ctx} />`));
  assert.ok(out.includes(t('print.define', { key: 'a:3' })));
  // Without user picks, white (colour 1) prints from a:4 as the adapter matched it: not "as is", so the suggestion,
  // which keeps a:4, blocks on its material; and the colours whose spool the steps still have to put have none.
  const plain = printGate(/** @type {any} */ (planNow(appOf(real()))), false);
  assert.deepEqual(
    [plain.asIs, plain.block],
    [
      false,
      [
        t('print.material_unset', { tools: 'color 1' }),
        t('print.put_pending', { tools: 'color 2', key: 'a:1' }),
        t('print.put_pending', { tools: 'color 5', key: 'a:3' }),
        t('print.put_pending', { tools: 'color 7', key: 'a:2' }),
      ].join(' '),
    ],
  );
});

test('real U1: the bar, its summary and the Define lines read the bays Print sends', () => {
  const app = appOf(defined(real()), { choice: PICKS, choiceId: `${FILE}\n${real().ace2k_u1.plan.mtime}` });
  const now = /** @type {NonNullable<ReturnType<typeof planNow>>} */ (planNow(app));
  const gate = printGate(now, false);
  const out = textOf(renderHtml(html`<${PlanView} app=${app} ctx=${live(app).ctx} />`));
  // No steps: the bays the user set are the bays Print sends, never "N steps left".
  assert.ok(!/steps? left in the bays/.test(out));
  // The summary counts the swaps of those bays on the live lanes.
  const n = /** @type {{ stops: number }} */ (sentKinds(now.plan, gate.choice, now.lanes)).stops;
  assert.ok(out.includes(t('print.bar', { swaps: t('print.swaps_n', { n }), time: '' }).split(' · ')[0]));
  // No Define line: every bay Print sends has its material set. b:2 to b:4 are not hooked.
  assert.ok(!out.includes(t('print.define', { key: 'a:3' })));
  // A bare PrintBar (no gate given) works the gate out itself and shows the same.
  const bar = textOf(
    renderHtml(html`<${PrintBar} app=${app} ctx=${live(app).ctx} now=${now} summary="S" left=${0} />`),
  );
  assert.ok(bar.includes(t('print.ready')));
});

test('real U1: before the materials are set, the head-use bar shows the suggestion Print would send', () => {
  const app = appOf(real());
  const now = /** @type {NonNullable<ReturnType<typeof planNow>>} */ (planNow(app));
  const gate = printGate(now, false);
  assert.deepEqual([gate.asIs, gate.choice], [false, now.place?.choice]);
  const bar = renderHtml(
    html`<${Timeline} plan=${now.plan} choice=${gate.choice} headOf=${headOfIn(now.lanes)} t=${t} />`,
  );
  assert.ok(renderHtml(html`<${PlanView} app=${app} ctx=${live(app).ctx} />`).includes(bar));
});

test('real U1: the head-use bar shows the bays as they are, and follows the spools as they change', () => {
  const app = appOf(defined(real()), { choice: PICKS, choiceId: `${FILE}\n${real().ace2k_u1.plan.mtime}` });
  const now = /** @type {NonNullable<ReturnType<typeof planNow>>} */ (planNow(app));
  const asIs = asIsChoice(now.plan, now.pick, now.merges);
  const headOf = headOfIn(now.lanes);
  const bar = renderHtml(html`<${Timeline} plan=${now.plan} choice=${asIs} headOf=${headOf} t=${t} />`);
  const out = renderHtml(html`<${PlanView} app=${app} ctx=${live(app).ctx} />`);
  assert.ok(out.includes(bar));
  // T0 swaps between red (a:2) and peach (a:1), as the user set them: the planner counts the same.
  const n = swapKinds(now.plan.order, asIs, (k) => (headOf(k) === 0 ? 0 : null)).stops;
  assert.ok(n > 0);
  assert.equal(swapKinds(now.plan.order, asIs, headOf).stops, now.place?.best);
  assert.ok(textOf(bar).includes(`T0 ${t('timeline.count', { n })}`));
  // Peach set to b:1 instead, together with brown, which the user leaves on it: T0 prints only red, and the bar
  // follows. (Brown not set, b:1 would be peach's alone, and the suggestion would place brown elsewhere.)
  const moved = appOf(app.status, { ...app.print, choice: { 1: 'a:2', 4: 'b:1', 3: 'b:1' }, overrides: [1, 3, 4] });
  const after = textOf(renderHtml(html`<${PlanView} app=${moved} ctx=${live(moved).ctx} />`));
  assert.ok(after.includes(`T0 ${t('timeline.count', { n: 0 })}`));
});

test('real U1: the guide past its last step keeps working on every status', () => {
  const app = appOf(real());
  const g = guideOf(app);
  assert.ok(g?.steps.length);
  for (let k = 0; k < g.trail.length; k += 1) stepNext(app);
  // Each status runs the auto advance; past the last step nothing is shown, and nothing throws.
  autoAdvance(app);
  autoAdvance(app);
  assert.equal(app.print.shown, null);
});

test('real U1: the confirm names a spool by the host colour name; a spool of no material blocks', () => {
  // White (colour 1) picked on a:3, whose spool the host names "black", and black (colour 7) set to it too.
  const app = appOf(defined(real()), {
    choice: { ...PICKS, 0: 'a:3', 6: 'a:3' },
    choiceId: `${FILE}\n${real().ace2k_u1.plan.mtime}`,
  });
  const now = /** @type {NonNullable<ReturnType<typeof planNow>>} */ (planNow(app));
  const gate = printGate(now, false);
  assert.equal(gate.asIs, true);
  const lines = asIsChecks(t, now, gate.choice, []);
  assert.ok(
    lines.includes(t('print.as_is_colour', { head: 'T1', n: 1, what: 'black PLA', key: 'a:3' })),
    lines.join('|'),
  );
  assert.ok(!lines.join(' ').includes('#000000'));
  // A spool whose material is not known, even on a bay the adapter says nothing about (status unread): a colour prints
  // only from a known material, so the user's pick of it asks to define it.
  for (const materialSet of [false, null, undefined]) {
    const lanes = now.lanes.map((l) => (l.key === 'a:3' ? { ...l, unknown: true, material: null, materialSet } : l));
    assert.equal(printGate({ ...now, lanes }, false).block, t('print.define_first', { key: 'a:3', n: 1 }));
  }
});

test('real U1: a bay whose material status is not known (null) gets no Define line and does not block', () => {
  const s = defined(real());
  s.ace2k_u1.lanes['a:4'] = { ...s.ace2k_u1.lanes['a:4'], material_set: null };
  const app = appOf(s, { choice: PICKS, choiceId: `${FILE}\n${real().ace2k_u1.plan.mtime}` });
  const out = textOf(renderHtml(html`<${PlanView} app=${app} ctx=${live(app).ctx} />`));
  assert.ok(!out.includes(t('print.define', { key: 'a:4' })));
  assert.equal(printGate(/** @type {any} */ (planNow(app)), false).block, null);
});

test('real U1: with no T order read the bar shows a dash, not "0 swaps"', () => {
  const s = real();
  s.ace2k_u1.plan = { ...s.ace2k_u1.plan, order: [] };
  const app = appOf(s);
  const out = renderHtml(html`<${PlanView} app=${app} ctx=${live(app).ctx} />`);
  assert.match(out, new RegExp(`class="big3">${DASH}<`));
});

test('real U1: the start reads one snapshot: the guide is built from the plan inputs it is given', () => {
  const app = appOf(real());
  const now = /** @type {NonNullable<ReturnType<typeof planNow>>} */ (planNow(app));
  const copy = { ...now };
  assert.equal(guideOf(app, copy)?.now, copy);
});
