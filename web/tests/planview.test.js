import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { html } from '../js/ui/html.js';
import { App } from '../js/ui/App.js';
import { SidePanel } from '../js/ui/SidePanel.js';
import {
  PlanView,
  cmpLine,
  missingColours,
  scrollPlan,
  scrollTarget,
  showStep,
  swapsWords,
} from '../js/ui/PlanView.js';
import { ColourRows, LaneChip, swatch } from '../js/ui/ColourChips.js';
import { ColourPicker, costWords, joinable, unitHeading, toolWords } from '../js/ui/ColourPicker.js';
import { runAction } from '../js/actions.js';
import { keepChoice } from '../js/printflow.js';
import { pickCost, SWAP_S } from '../js/planner.js';
import {
  NEW,
  carriedChoice,
  colourDiffers,
  noFitWords,
  printGate,
  rekeyPicks,
  pinOf,
  lookOf,
} from '../js/print-plan.js';
import { hintKept, mergesOf, planNow } from '../js/state.js';
import { DASH, fmtHM } from '../js/format.js';
import { buttons, rendered, renderHtml, textIn, textOf } from './ui-helpers.js';
import { loadEnglish } from './helpers.js';
import { t } from '../js/i18n.js';

loadEnglish();

const read = (url) => JSON.parse(readFileSync(new URL(url, import.meta.url), 'utf8'));
// demo-unknown: one unit, bay 1 red PLA (tagged), 2 black PETG (set by hand), 3 a spool of no material, 4 empty.
// Each bay feeds its own head (T0-T3).
const unknown = () => read('../dev/demo-unknown.json');
// The same unit with bays 1 and 4 on one head, and a white PLA spool in bay 4: the swaps then depend on the pick.
function shared() {
  const s = unknown();
  s.ace2k_u1.heads = [
    [
      { unit: null, lane: 1 },
      { unit: null, lane: 4 },
    ],
    [{ unit: null, lane: 2 }],
    [{ unit: null, lane: 3 }],
    [],
  ];
  s.ace2k.lanes[3] = {
    ...s.ace2k.lanes[1],
    tag: { state: 'read', record: { brand: 'X', material: 'PLA', color_rgba: 'FFFFFFFF' } },
  };
  return s;
}

// The five-colour file of the fixtures, printing three of its colours: white (T0), red (T1) and black (T6), all PLA.
// Tools 2-5 and 7 are configured in the file and never printed. The adapter suggests bay 1 for red.
const five = read('./fixtures/planner-files.json').five;
const plan = {
  file: 'five.gcode',
  mtime: 1,
  tools: five.tools.map((x, n) => ({ ...x, lane: n === 1 ? '1' : null, why: 'closest' })),
  order: five.order.filter((n) => [0, 1, 6].includes(n)),
  raw: [],
  heads: {},
  set: false,
  choice: null,
  lanes: [],
};

const appOf = (status, print = {}, panel = null) => ({
  status: { ...status, ace2k_u1: { ...status.ace2k_u1, plan: status.ace2k_u1.plan ?? plan } },
  online: true,
  klippy: 'ready',
  selected: null,
  pending: new Set(),
  edit: null,
  print: { open: true, files: [], file: 'five.gcode', choice: {}, choiceId: null, ...print },
  ui: { tab: 'print', panel, sheet: false },
});

const button = (nodes, words) => buttons(nodes).find((n) => textIn(n).replace(/\s+/g, ' ').trim() === words);

// A ctx whose run goes through the real actions (render is a no-op), and records each call.
function live(app) {
  const runs = [];
  const ctx = { app, t, render: () => {}, toast: () => {} };
  ctx.run = (id, data = {}) => {
    runs.push([id, data]);
    return runAction(ctx, id, data);
  };
  return { ctx, runs };
}
const view = (app) => {
  const { ctx, runs } = live(app);
  return { ...rendered(html`<${PlanView} app=${app} ctx=${ctx} />`), runs, ctx };
};
const picker = (app, n) => {
  const { ctx, runs } = live(app);
  return { ...rendered(html`<${ColourPicker} app=${app} ctx=${ctx} n=${n} />`), runs, ctx };
};

test('the Print tab shows the grid until the chosen file has its plan, then the plan; back returns to the grid', () => {
  const app = appOf(shared());
  const { ctx } = live(app);
  assert.match(renderHtml(html`<${App} app=${app} ctx=${ctx} />`), /class="plan"/);
  app.ui.panel = { kind: 'pick', n: '0' };
  runAction(ctx, 'print_back', {});
  assert.deepEqual([app.print.file, app.ui.panel], [null, null]);
  assert.match(renderHtml(html`<${App} app=${app} ctx=${ctx} />`), /class="fgrid"/);
  // A file whose plan has not come yet (the adapter's plan is another file's) keeps the grid while its click is in
  // flight, and shows the other-device notice once it is not; another panel stays.
  app.print.file = 'other.gcode';
  app.ui.panel = { kind: 'status' };
  assert.equal(planNow(app), null);
  app.pending = new Set(['print:file']);
  assert.match(renderHtml(html`<${App} app=${app} ctx=${ctx} />`), /class="fgrid"/);
  app.pending = new Set();
  assert.match(renderHtml(html`<${App} app=${app} ctx=${ctx} />`), /class="plan other"/);
  runAction(ctx, 'print_back', {});
  assert.deepEqual(app.ui.panel, { kind: 'status' });
  assert.equal(renderHtml(html`<${PlanView} app=${app} ctx=${ctx} />`), '');
});

test('the Print tab without a head of several bays, or with the adapter unhooked, only says why; a print still shows', () => {
  for (const status of [unknown(), { ...shared(), ace2k_u1: { ...shared().ace2k_u1, hooked: false } }]) {
    const app = appOf(status);
    const { ctx } = live(app);
    // No file grid: the tab never opens (print_open), so nothing is listed or planned.
    const out = renderHtml(html`<${App} app=${app} ctx=${ctx} />`);
    assert.ok(textOf(out).includes(t('print.not_offered')));
    assert.doesNotMatch(out, /class="(plan|fgrid)"/);
    app.status = { ...app.status, print_stats: { state: 'printing', filename: 'five.gcode' } };
    assert.ok(!textOf(renderHtml(html`<${App} app=${app} ctx=${ctx} />`)).includes(t('print.not_offered')));
  }
});

test('the header: name, slicer time, thumbnail, and the swaps as it is and with the suggestion', () => {
  const app = appOf(shared(), {
    cards: { 'five.gcode': { modified: 1, card: { colours: null, time: 5760, thumb: '/t.png', modified: 1 } } },
  });
  const { out, nodes, runs } = view(app);
  const now = planNow(app);
  const p = now.place;
  // White and black have no bay yet: as it is cannot be counted, and says how many colours lack one.
  assert.equal(p.now, null);
  assert.equal(missingColours(now), 2);
  const text = textOf(out);
  assert.ok(text.includes('five'));
  assert.ok(text.includes(t('print.slicer_time', { time: '1:36' })));
  assert.match(out, /<img src="\/t.png" alt="Preview of five.gcode"/);
  assert.ok(text.includes(`${t('print.as_is')} ${DASH} ${t('print.missing_n', { n: 2 })}`));
  const best = `${swapsWords(t, p.best)} ${t('print.stopped', { time: fmtHM(p.best * SWAP_S) })}`;
  assert.ok(text.includes(`${t('print.with_suggestion')} ${best}`), text);
  button(nodes, t('print.back')).props.onClick();
  assert.deepEqual(runs[0], ['print_back', {}]);
});

test('as it is counts the swaps of the current pick; one swap reads singular; no card and no thumbnail are fine', () => {
  // White on bay 4 and red on bay 1 share T0: the user's pick gives a count. (A new spool has no bay yet.)
  const s = shared();
  s.ace2k_u1.plan = { ...plan, order: plan.order.filter((n) => n !== 6) };
  const app = appOf(s, { thumb: { file: 'five.gcode', url: '/th.png' } });
  const { ctx } = live(app);
  runAction(ctx, 'pick', { n: '0', key: '4' });
  const now = planNow(app);
  assert.notEqual(now.place.now, null);
  const { out } = view(app);
  assert.ok(textOf(out).includes(`${t('print.as_is')} ${swapsWords(t, now.place.now)}`));
  assert.match(out, /<img src="\/th.png"/);
  assert.equal(swapsWords(t, 1), '1 swap');
  assert.equal(swapsWords(t, 3), '3 swaps');
  assert.ok(!textOf(out).includes('in the slicer'));
});

test('more colours than bays: the suggestion says it does not fit, every chip has no bay', () => {
  const big = { ...plan, file: 'big.gcode', order: five.order }; // five printed colours, four bays
  const s = unknown();
  s.ace2k_u1.plan = big;
  const app = appOf(s, { file: 'big.gcode' });
  const { out } = view(app);
  assert.ok(textOf(out).includes(t('print.no_fit_n', { n: 5, m: 4 })));
  assert.equal((out.match(/class="lk alt">no bay</g) ?? []).length, 5);
});

test('a plan with no T order: dashes on both sides and no seat for any colour', () => {
  const s = unknown();
  s.ace2k_u1.plan = { ...plan, order: [] };
  const { out } = view(appOf(s));
  assert.equal((out.match(/<b>—<\/b>/g) ?? []).length, 2);
  // With no T line read, every tool of the file counts (usedTest): eight rows, none seated.
  assert.equal((out.match(/class="lk alt">no bay</g) ?? []).length, 8);
});

test('the unknown line names the bay with no material, and its button opens that bay with Edit spool open', () => {
  const app = appOf(unknown());
  const { out, nodes, runs } = view(app);
  assert.ok(textOf(out).includes(t('print.unknown_bay', { key: '3' })));
  assert.equal(t('print.unknown_bay', { key: '3' }), '3 has a spool with no material — the planner does not use it.');
  button(nodes, t('print.define', { key: '3' })).props.onClick();
  assert.deepEqual(runs, [['define', { key: '3' }]]);
  // No extra click: the bay's card opens with its spool editor, the material and colour pickers shown.
  assert.deepEqual(app.ui.panel, { kind: 'bay', key: '3' });
  assert.equal(app.edit?.lane, 3);
  const card = textOf(renderHtml(html`<${SidePanel} app=${{ ...app, folds: {} }} ctx=${live(app).ctx} />`));
  assert.ok(card.includes(t('lane.edit_type')) && card.includes(t('lane.edit_colour')), card);
});

test('the rows: one per printed colour, 1-based, never a T name; a row opens its picker', () => {
  const app = appOf(unknown(), {}, { kind: 'pick', n: '1' });
  const { out, nodes, runs } = view(app);
  const chips = buttons(nodes).filter((b) => String(b.props.class).startsWith('crow'));
  assert.deepEqual(
    chips.map((c) => textIn(c).match(/color \d+/)[0]),
    ['color 1', 'color 2', 'color 7'],
  );
  assert.deepEqual(
    chips.map((c) => c.props.class),
    ['crow', 'crow open', 'crow'],
  );
  assert.ok(!/\bT\d/.test(textOf(out).replace(/T[0-3]\b/g, '')));
  chips[2].props.onClick();
  assert.deepEqual(runs, [['panel', { kind: 'pick', n: '6' }]]);
});

test('a chip shows the spool it prints from, the bay, and the colour named in amber when it is not the file colour', () => {
  const app = appOf(unknown());
  const now = planNow(app);
  // Red (T1) stays in bay 1: the spool is the file's red (close), so no name; no pin, as the user picked nothing.
  const red = renderHtml(html`<${LaneChip} ctx=${{ t }} now=${now} n=${1} />`);
  assert.match(red, /--fil:#dd3333/);
  assert.match(red, /<span class="lk">1<\/span>/);
  assert.ok(!red.includes('📌'));
  // White and black get a new spool each.
  const white = textOf(renderHtml(html`<${LaneChip} ctx=${{ t }} now=${now} n=${0} />`));
  assert.match(white, /^new spool → [234]$/);
  // Picked by the user onto the red spool once red takes a new one, white is named in amber, with the pin.
  const { ctx } = live(app);
  runAction(ctx, 'pick', { n: '1', key: NEW });
  runAction(ctx, 'pick', { n: '0', key: '1' });
  runAction(ctx, 'pick', { n: '6', key: NEW });
  const after = planNow(app);
  const chip = renderHtml(html`<${LaneChip} ctx=${{ t }} now=${after} n=${0} />`);
  const line = after.place.lines.find((l) => l.tools.includes(0));
  assert.match(chip, /class="lk alt"/);
  assert.ok(textOf(chip).includes(t('print.key_colour', { key: line.key, colour: 'red' })));
  assert.ok(chip.includes('title="chosen by you"'));
  // A put the user asked for carries the pin too.
  assert.ok(renderHtml(html`<${LaneChip} ctx=${{ t }} now=${after} n=${6} />`).includes('📌'));
  // A spool that moves is drawn from the bay it comes from; a colour name the plan knows is used.
  const moved = { ...after, place: { ...after.place, lines: [{ tools: [1], key: '4', act: 'move', from: '1' }] } };
  moved.lanes = after.lanes.map((l) => (l.key === '1' ? { ...l, colour: '#00ff00', colour_name: 'green' } : l));
  const mv = renderHtml(html`<${LaneChip} ctx=${{ t }} now=${moved} n=${1} />`);
  assert.match(mv, /--fil:#00ff00/);
  assert.ok(textOf(mv).includes('4 · green'));
});

test('colourDiffers: at CLOSE or beyond, and never for a colour that is not known', () => {
  assert.equal(colourDiffers('#dd3333', '#d32c34'), false);
  assert.equal(colourDiffers('#dd3333', '#ffffff'), true);
  assert.equal(colourDiffers(null, '#ffffff'), false);
  assert.equal(colourDiffers('#ffffff', undefined), false);
});

test('a merge shows "= color 1" on the joined chip, and the placement follows on the next render', () => {
  const app = appOf(shared());
  const before = planNow(app).place.colours;
  const { ctx } = live(app);
  ctx.run('merge', { n: '1', into: '0' });
  assert.deepEqual(mergesOf(app.print), { 1: 0 });
  const { out } = view(app);
  assert.ok(textOf(out).includes('= color 1'));
  assert.equal(planNow(app).place.colours, before - 1);
});

test('the similar hint names the first pair; Join merges them, Keep apart hides it for this file only', () => {
  // Black (T6) and a near-black tool (T3) look the same: printed both, same material.
  const near = { ...plan, tools: plan.tools.map((x, n) => (n === 3 ? { ...x, colour: '#050505' } : x)) };
  near.order = [...plan.order, 3];
  const s = unknown();
  s.ace2k_u1.plan = near;
  const app = appOf(s);
  const { out, nodes, runs } = view(app);
  assert.ok(textOf(out).includes(t('print.similar', { a: 4, b: 7 })));
  assert.equal(t('print.similar', { a: 4, b: 7 }), 'color 4 and color 7 look the same?');
  button(nodes, t('print.join')).props.onClick();
  assert.deepEqual(runs[0], ['merge', { n: '6', into: '3' }]);
  assert.ok(!textOf(view(app).out).includes(t('print.similar_note')), 'joined: no pair is left');
  runAction(live(app).ctx, 'unpin', { n: '6' });
  const again = view(app);
  button(again.nodes, t('print.keep_apart')).props.onClick();
  assert.equal(hintKept(app.print, 'five.gcode'), true);
  assert.equal(hintKept(app.print, 'other.gcode'), false);
  assert.ok(!textOf(view(app).out).includes(t('print.similar_note')));
});

test('the picker: costs equal pickCost for two bays; another material, an empty bay and an unknown spool', () => {
  const app = appOf(shared());
  const now = planNow(app);
  const { out, nodes, runs } = picker(app, 0);
  const bays = buttons(nodes).filter((b) => String(b.props.class).startsWith('opt'));
  assert.equal(bays.length, 4);
  for (const [i, key] of [
    [0, '1'],
    [3, '4'],
  ]) {
    const d = pickCost(now.plan, now.pick, now.lanes, now.merges, 0, key);
    assert.ok(textIn(bays[i]).includes(costWords(t, d).text), `${key}: ${textIn(bays[i])}`);
    assert.ok(!bays[i].props.disabled);
  }
  // Bay 2 holds PETG: refused, and says why.
  assert.equal(bays[1].props.disabled, true);
  assert.ok(textIn(bays[1]).includes(t('print.other_material')));
  // Bay 3: a spool with no material: define and use, filled with the file colour.
  assert.ok(textIn(bays[2]).includes(t('print.define_use')));
  bays[2].props.onClick();
  assert.deepEqual(runs.at(-1), ['define_and_use', { n: '0', key: '3', material: 'PLA', colour: '#ffffff' }]);
  bays[0].props.onClick();
  assert.deepEqual(runs.at(-1), ['pick', { n: '0', key: '1' }]);
  assert.ok(textOf(out).includes(t('print.pick_spools')) && textOf(out).includes(t('units.unnamed')));
  // Nothing picked yet: no way back to offer.
  assert.equal(button(nodes, t('print.back_to_suggestion')), undefined);
});

test('the picker: an empty bay is picked like any other; a new spool and a join carry their costs; back to the suggestion', () => {
  const app = appOf(unknown());
  const now = planNow(app);
  const { nodes, runs } = picker(app, 0);
  const empty = buttons(nodes).find((b) => textIn(b).includes(t('units.empty')));
  assert.ok(!empty.props.disabled);
  empty.props.onClick();
  assert.equal(runs.at(-1)?.[0], 'pick');
  const fresh = button(
    nodes,
    `${t('print.put_new', { what: 'PLA #FFFFFF' })}${costWords(t, pickCost(now.plan, now.pick, now.lanes, now.merges, 0, NEW)).text}`,
  );
  assert.ok(fresh);
  fresh.props.onClick();
  assert.deepEqual(runs.at(-1), ['pick', { n: '0', key: NEW }]);
  // White may join red or black (both PLA, printed); not itself, not a tool the file never prints.
  assert.deepEqual(joinable(now, 0), [1, 6]);
  const join = buttons(nodes).find((b) => textIn(b).includes(t('print.join_with', { m: 2, what: 'PLA #D32C34' })));
  assert.ok(
    textIn(join).includes(costWords(t, pickCost(now.plan, now.pick, now.lanes, now.merges, 0, { merge: 1 })).text),
  );
  join.props.onClick();
  assert.deepEqual(runs.at(-1), ['merge', { n: '0', into: '1' }]);
  // Merged: the join is selected and the way back shows; back drops the merge.
  const merged = picker(app, 0);
  const sel = buttons(merged.nodes).find((b) =>
    textIn(b).includes(t('print.join_with', { m: 2, what: 'PLA #D32C34' })),
  );
  assert.equal(sel.props.class, 'row sel');
  button(merged.nodes, t('print.back_to_suggestion')).props.onClick();
  assert.deepEqual([app.print.merges, app.print.choice], [{}, {}]);
});

test('the picker marks the picked bay; while the plan does not fit, no choice is disabled for its cost', () => {
  const app = appOf(shared());
  runAction(live(app).ctx, 'pick', { n: '0', key: '4' });
  const { nodes } = picker(app, 0);
  const four = buttons(nodes).find((b) => textIn(b).startsWith('4'));
  assert.deepEqual([four.props.class, four.props['aria-pressed']], ['opt sel', true]);
  // Four bays, five printed colours: a new spool still does not fit, so it shows no number, and stays offered. Red
  // set by the user to its bay: that bay is shared with red (both colours would print from that spool), it says so.
  const s = unknown();
  s.ace2k_u1.plan = { ...plan, order: five.order };
  const bigApp = appOf(s);
  runAction(live(bigApp).ctx, 'pick', { n: '1', key: '1' });
  const big = picker(bigApp, 0);
  const fresh = buttons(big.nodes).find((b) => textIn(b).includes(t('print.put_new', { what: 'PLA #FFFFFF' })));
  assert.deepEqual([Boolean(fresh.props.disabled), textIn(fresh).includes(t('print.no_fit'))], [false, false]);
  const red = buttons(big.nodes).find((b) => textIn(b).startsWith('1'));
  assert.deepEqual(
    [Boolean(red.props.disabled), textIn(red).includes(t('print.shared_with', { m: 2 }))],
    [false, true],
  );
});

test('costWords: more, fewer, none, and does not fit', () => {
  assert.deepEqual(costWords(t, 2), { text: '+2', cls: 'c bad' });
  assert.deepEqual(costWords(t, -1), { text: '−1', cls: 'c good' });
  assert.deepEqual(costWords(t, 0), { text: '+0', cls: 'c' });
  assert.deepEqual(costWords(t, null), { text: t('print.no_fit'), cls: 'c bad' });
});

test('the picker of a colour that is gone, or a tool the file never prints, says so', () => {
  const app = appOf(unknown());
  assert.ok(textOf(picker(app, 3).out).includes(t('panel.gone')));
  assert.ok(textOf(picker(app, 99).out).includes(t('panel.gone')));
  app.print.file = null;
  assert.ok(textOf(picker(app, 0).out).includes(t('panel.gone')));
});

test('the side panel draws the picker under "Colour n"', () => {
  const app = appOf(unknown(), {}, { kind: 'pick', n: '6' });
  const { ctx } = live(app);
  const out = renderHtml(html`<${SidePanel} app=${app} ctx=${ctx} />`);
  assert.match(out, /<h2>Color 7<\/h2>/);
  assert.ok(out.includes('class="card picker"'));
});

test('a two-unit plan groups the picker by unit, and a chip of a merged tool shows its root colour', () => {
  const s = read('../dev/demo-four.json');
  const keys = Object.keys(s).filter((k) => k.startsWith('ace2k ')).length;
  assert.ok(keys > 1);
  s.ace2k_u1.plan = plan;
  const app = appOf(s);
  const out = textOf(picker(app, 0).out);
  assert.ok(out.includes('Unit a') && out.includes('Unit b'));
  const { ctx } = live(app);
  runAction(ctx, 'merge', { n: '6', into: '0' });
  const chips = rendered(html`<${ColourRows} app=${app} ctx=${ctx} now=${planNow(app)} />`);
  assert.ok(textOf(chips.out).includes('= color 1'));
});

test('a colour with no material and no colour: any bay is offered, no join, plain swatches; a new spool picked', () => {
  const bare = { ...plan, tools: plan.tools.map((x, n) => (n === 6 ? { ...x, colour: null, material: null } : x)) };
  const s = unknown();
  s.ace2k_u1.plan = bare;
  const app = appOf(s);
  const now = planNow(app);
  assert.deepEqual(joinable(now, 6), []);
  const first = picker(app, 6);
  const petg = buttons(first.nodes).find((b) => textIn(b).startsWith('2'));
  assert.ok(!petg.props.disabled, 'a colour of no material may take any spool');
  assert.match(first.out, /<span class="dot"><\/span>/);
  first.ctx.run('pick', { n: '6', key: NEW });
  const after = picker(app, 6);
  const fresh = buttons(after.nodes).find((b) => textIn(b).includes(t('print.put_new_any')));
  assert.equal(fresh.props.class, 'row sel');
  assert.ok(button(after.nodes, t('print.back_to_suggestion')));
  const chips = textOf(renderHtml(html`<${ColourRows} app=${app} ctx=${after.ctx} now=${planNow(app)} />`));
  assert.ok(chips.includes('color 7 →'));
  // A bay picked while the colour is merged is not marked: the merge wins.
  app.print.merges = { 6: 0 };
  app.print.choice = { 6: '2' };
  const merged = picker(app, 6);
  assert.equal(buttons(merged.nodes).find((b) => textIn(b).startsWith('2')).props.class, 'opt');
});

test('as it is honours the joins: a join that seats every colour turns the dash into a number', () => {
  // Red (T1) is in bay 1; white and black have no bay, so as it is cannot be counted.
  const app = appOf(unknown());
  assert.equal(planNow(app).place.now, null);
  const { ctx } = live(app);
  ctx.run('merge', { n: '0', into: '1' });
  ctx.run('merge', { n: '6', into: '1' });
  const now = planNow(app).place.now;
  assert.equal(typeof now, 'number');
  assert.ok(textOf(view(app).out).includes(`${t('print.as_is')} ${swapsWords(t, now)}`));
});

test('while the plan does not fit, a choice that makes it fit reads "fits · n swaps", not a delta', () => {
  assert.deepEqual(costWords(t, 2, false), { text: 'fits · 2 swaps', cls: 'c good' });
  assert.deepEqual(costWords(t, 1, false).text, 'fits · 1 swap');
  // A choice that still does not fit while the plan does not: no number (and no disabling), never "does not fit".
  assert.deepEqual(costWords(t, null, false), { text: '', cls: 'c' });
  assert.deepEqual(costWords(t, null, true).text, t('print.no_fit'));
  // Five printed colours, four bays: joining white with red seats them all.
  const s = unknown();
  s.ace2k_u1.plan = { ...plan, order: five.order };
  const app = appOf(s);
  const now = planNow(app);
  assert.equal(now.place.fits, false);
  const { nodes } = picker(app, 0);
  const join = buttons(nodes).find((b) => textIn(b).includes('join with color 2'));
  const d = pickCost(now.plan, now.pick, now.lanes, now.merges, 0, { merge: 1 });
  assert.notEqual(d, null);
  assert.ok(textIn(join).includes(costWords(t, d, false).text));
});

test('the picker names each unit with its heads, each new spool and join by colour, and lists the unknown spools', () => {
  const app = appOf(unknown());
  const { out, nodes, runs } = picker(app, 0);
  const text = textOf(out);
  assert.ok(text.includes('Unit → T0, T1, T2, T3'), text);
  assert.ok(text.includes('put a new PLA #FFFFFF spool'));
  assert.ok(text.includes('join with color 2 (PLA #D32C34) · −1 spool'));
  const row = buttons(nodes).find((b) => textIn(b).includes('the spool with no material in 3 is PLA #FFFFFF'));
  row.props.onClick();
  assert.deepEqual(runs.at(-1), ['define_and_use', { n: '0', material: 'PLA', colour: '#ffffff', key: '3' }]);
  // A colour with no material or colour says "this colour".
  const bare = { ...plan, tools: plan.tools.map((x, n) => (n === 6 ? { ...x, colour: null, material: null } : x)) };
  const s = unknown();
  s.ace2k_u1.plan = bare;
  assert.ok(textOf(picker(appOf(s), 6).out).includes('the spool with no material in 3 is this color'));
  assert.equal(toolWords(bare.tools[6]), '');
  // Units by name, and a unit whose bays feed no head has its name alone.
  assert.equal(unitHeading(t, 'a', [{ head: 2 }, { head: 0 }, { head: 2 }]), 'Unit a → T0, T2');
  assert.equal(unitHeading(t, 'b', [{ head: null }]), 'Unit b');
});

test('Keep apart twice keeps the file once', () => {
  const app = appOf(unknown());
  const { ctx } = live(app);
  ctx.run('hint_keep', {});
  ctx.run('hint_keep', {});
  assert.deepEqual(app.print.dismissed, ['five.gcode']);
});

test('a re-plan of the same file keeps NEW and the joins still valid; a join whose colour changed goes', () => {
  const tools = plan.tools;
  const print = {
    choice: { 0: NEW },
    merges: { 6: 1, 3: 1 },
    pinned: {},
    choiceId: 'five.gcode\n1',
    choiceTools: tools,
  };
  const recolour = tools.map((x, n) => (n === 3 ? { ...x, colour: '#123456' } : x));
  const got = carriedChoice(print, { ...plan, mtime: 2, tools: recolour });
  assert.deepEqual(got.choice, { 0: NEW });
  assert.deepEqual(got.merges, { 6: 1 });
  // Another material on one side: that join goes too.
  const petg = tools.map((x, n) => (n === 6 ? { ...x, material: 'PETG' } : x));
  assert.deepEqual(carriedChoice({ ...print, merges: { 6: 1 } }, { ...plan, mtime: 3, tools: petg }).merges, {});
});

// A bay as liveLanes gives it, for the pin tests: key, and a spool of material, colour and tag uid (none: empty).
const bayOf = (key, spool = null) => ({
  key,
  head: 0,
  empty: !spool,
  material: spool?.material ?? null,
  colour: spool?.colour ?? null,
  spool: spool ? { id: `${key}:x`, tag: Boolean(spool.uid), uid: null, ...spool } : null,
});
const WHITE = { material: 'PLA', colour: '#ffffff' };
const RED = { material: 'PLA', colour: '#d32c34' };

test('a pick pins its spool by tag uid and look; a spool of no material or no bay pins nothing', () => {
  assert.equal(lookOf(' Pla ', '#FFFFFF'), 'pla:#ffffff');
  assert.equal(lookOf(null, '#ffffff'), null);
  assert.deepEqual(pinOf(bayOf('2', { ...WHITE, uid: 'U7' })), { uid: 'U7', look: 'pla:#ffffff' });
  assert.equal(pinOf(bayOf('2')), null);
  const app = appOf(shared()); // a white PLA spool in bay 4
  const { ctx } = live(app);
  ctx.run('pick', { n: '0', key: '4' });
  assert.deepEqual(app.print.pinned, { 0: { uid: null, look: 'pla:#ffffff' } });
  ctx.run('pick', { n: '0', key: '3' }); // bay 3: a spool of no material
  ctx.run('pick', { n: '6', key: NEW });
  assert.deepEqual(app.print.pinned, {});
  ctx.run('pick', { n: '0', key: '4' });
  ctx.run('merge', { n: '0', into: '1' }); // a merge drops the pin
  assert.deepEqual(app.print.pinned, {});
});

for (const [shape, k] of [
  ['one unit', (n) => String(n)],
  ['several units', (n) => `b:${n}`],
]) {
  test(`the pin rules (${shape}): an empty bay keeps, the spool's new bay moves, another known spool drops`, () => {
    const pin = { uid: null, look: 'pla:#ffffff' };
    const choice = { 0: k(4) };
    // The spool in hand during a move: its bay is empty and it is in no bay. The pick waits.
    let got = rekeyPicks(choice, { 0: pin }, [bayOf(k(3)), bayOf(k(4))], []);
    assert.equal(got.choice, choice);
    assert.deepEqual(got.lost, []);
    // A bay gone from the status, or a spool of no material in it: kept as well.
    assert.equal(rekeyPicks(choice, { 0: pin }, [], []).choice, choice);
    assert.equal(rekeyPicks(choice, { 0: pin }, [bayOf(k(4), { material: null, colour: null })], []).choice, choice);
    // Put in bay 3: the pick follows it.
    got = rekeyPicks(choice, { 0: pin }, [bayOf(k(3), WHITE), bayOf(k(4))], []);
    assert.deepEqual(got.choice, { 0: k(3) });
    // Two white spools: the bay it was in wins.
    assert.equal(rekeyPicks(choice, { 0: pin }, [bayOf(k(3), WHITE), bayOf(k(4), WHITE)], []).choice, choice);
    // Another known spool in its bay, and the white one in no bay: dropped, and listed as lost.
    got = rekeyPicks({ ...choice, 2: NEW }, { 0: pin }, [bayOf(k(3)), bayOf(k(4), RED)], []);
    assert.deepEqual([got.choice, got.pinned, got.lost], [{ 2: NEW }, {}, [0]]);
  });

  test(`the pin rules (${shape}): a tag read after the pick, or a material in other case, keeps it`, () => {
    const pin = { uid: null, look: 'pla:#ffffff' };
    // The tag is read later: the spool now has a uid. Kept, and the pin learns the uid.
    let got = rekeyPicks({ 0: k(2) }, { 0: pin }, [bayOf(k(2), { ...WHITE, uid: 'U1' })], []);
    assert.deepEqual([got.choice, got.pinned], [{ 0: k(2) }, { 0: { uid: 'U1', look: 'pla:#ffffff' } }]);
    // Then the tag's record names another colour: the uid still matches, the pick stays.
    got = rekeyPicks(got.choice, got.pinned, [bayOf(k(2), { material: 'PLA', colour: '#fafafa', uid: 'U1' })], []);
    assert.deepEqual([got.choice, got.lost], [{ 0: k(2) }, []]);
    // "pla" and "#FFFFFF" in the bay: the same look.
    const same = { 0: k(2) };
    assert.equal(rekeyPicks(same, { 0: pin }, [bayOf(k(2), { material: 'pla ', colour: '#FFFFFF' })], []).choice, same);
  });

  test(`the pin rules (${shape}): after "Save and use" the old spool still showing keeps, the new one settles it`, () => {
    const pin = { uid: null, look: 'petg:#000000', was: { uid: null, look: 'pla:#ffffff' } };
    const choice = { 0: k(3) };
    // The status still shows the spool the bay held before the save: kept.
    let got = rekeyPicks(choice, { 0: pin }, [bayOf(k(3), WHITE)], []);
    assert.equal(got.choice, choice);
    // The saved spool shows: the pin keeps it, and forgets the old spool.
    got = rekeyPicks(choice, { 0: pin }, [bayOf(k(3), { material: 'PETG', colour: '#000000' })], []);
    assert.deepEqual(got.pinned, { 0: { uid: null, look: 'petg:#000000' } });
    // A third, known spool: dropped.
    assert.deepEqual(rekeyPicks(choice, { 0: pin }, [bayOf(k(3), RED)], []).lost, [0]);
  });
}

test('keepChoice: a bay the user set stays the pick, taken out, its spool moved away, or refilled with another', () => {
  const s = shared(); // white PLA in bay 4, on head T0 with bay 1
  const app = appOf(s);
  const { ctx } = live(app);
  ctx.run('pick', { n: '0', key: '4' });
  const toasts = [];
  const keep = () => keepChoice({ app, t, toast: (m) => toasts.push(m) });
  const white = s.ace2k.lanes[3];
  const out = { ...white, insert: false, tag: { state: 'unknown', record: null } };
  const withBays = (b3, b4) => {
    const next = structuredClone(s);
    next.ace2k.lanes[2] = b3;
    next.ace2k.lanes[3] = b4;
    next.ace2k_u1.plan = app.status.ace2k_u1.plan;
    app.status = next;
    keep();
  };
  withBays(s.ace2k.lanes[2], out); // in hand
  assert.deepEqual(app.print.choice, { 0: '4' });
  const same = app.print.choice;
  keep();
  assert.equal(app.print.choice, same, 'nothing moved: the same objects');
  withBays(white, out); // put in bay 3: the pick is the bay, not the spool
  assert.deepEqual(app.print.choice, { 0: '4' });
  const red = { ...white, tag: { state: 'read', record: { brand: 'X', material: 'PLA', color_rgba: 'D32C34FF' } } };
  withBays(white, red); // a red spool in bay 4
  // The pick stays on bay 4, the pin now on the red spool, and one toast says it.
  assert.deepEqual([app.print.choice, app.print.pinned], [{ 0: '4' }, { 0: { uid: null, look: 'pla:#d32c34' } }]);
  assert.equal(toasts.length, 1);
  assert.match(toasts[0], /^4 now holds .+ for color 1$/);
  withBays(white, red); // the same again: no second toast
  assert.equal(toasts.length, 1);
});

test('the pin rules: a bay the user set is the pick whatever it holds; its pin follows what the bay holds', () => {
  const pin = { uid: null, look: 'pla:#ffffff' };
  const choice = { 0: 'a:1' };
  // Edit spool turned a:1 black and a:2 white: the pick stays on a:1, on the black spool now, listed as refilled.
  const lanes = [bayOf('a:1', { material: 'PLA', colour: '#000000' }), bayOf('a:2', WHITE)];
  const got = rekeyPicks(choice, { 0: pin }, lanes, [0]);
  assert.deepEqual(
    [got.choice, got.pinned, got.lost, got.refilled],
    [choice, { 0: { uid: null, look: 'pla:#000000' } }, [], [0]],
  );
  // The same status again: nothing changes.
  const again = rekeyPicks(got.choice, got.pinned, lanes, [0]);
  assert.deepEqual([again.choice === got.choice, again.pinned === got.pinned, again.refilled], [true, true, []]);
  // A pick the page settled (not the user's) still follows its spool.
  assert.deepEqual(rekeyPicks(choice, { 0: pin }, lanes, []).choice, { 0: 'a:2' });
  // Its bay empty and its spool in a:2: the user's pick stays on a:1.
  const empty = rekeyPicks(choice, { 0: pin }, [bayOf('a:1'), bayOf('a:2', WHITE)], [0]);
  assert.equal(empty.choice, choice);
  // A spool whose colour is not known, with no uid: the pin it can no longer be told by goes, the bay stays.
  const vague = rekeyPicks(choice, { 0: pin }, [bayOf('a:1', { material: 'PLA', colour: null })], [0]);
  assert.deepEqual([vague.choice, vague.pinned, vague.refilled], [choice, {}, []]);
  const twice = rekeyPicks(vague.choice, vague.pinned, [bayOf('a:1', { material: 'PLA', colour: null })], [0]);
  assert.equal(twice.pinned, vague.pinned);
  // Its colour known later: pinned again, and listed as refilled, so the page compares it with the colour once.
  const known = rekeyPicks(choice, {}, [bayOf('a:1', { material: 'PLA', colour: '#000000' })], [0]);
  assert.deepEqual([known.pinned, known.refilled], [{ 0: { uid: null, look: 'pla:#000000' } }, [0]]);
  // A tagged spool whose colour becomes known later (the same uid learns a look): refilled as well.
  const tagged = rekeyPicks(choice, { 0: { uid: 'U1', look: null } }, [bayOf('a:1', { ...WHITE, uid: 'U1' })], [0]);
  assert.deepEqual([tagged.pinned, tagged.refilled], [{ 0: { uid: 'U1', look: 'pla:#ffffff' } }, [0]]);
  // A tag read later on the same look (it only learns a uid): not new to the pin.
  assert.deepEqual(rekeyPicks(choice, { 0: pin }, [bayOf('a:1', { ...WHITE, uid: 'U1' })], [0]).refilled, []);
});

test('keepChoice: a re-plan keeps a bay the user set, also one that is empty now, with no "dropped" toast', () => {
  const s = shared();
  const app = appOf(s);
  const { ctx } = live(app);
  ctx.run('pick', { n: '0', key: '4' });
  // The white spool moved to bay 3, and the file was planned again: the new plan's snapshot has bay 4 empty.
  const next = structuredClone(s);
  next.ace2k.lanes[2] = next.ace2k.lanes[3];
  next.ace2k.lanes[3] = { ...next.ace2k.lanes[3], insert: false, tag: { state: 'unknown', record: null } };
  next.ace2k_u1.plan = {
    ...plan,
    mtime: 2,
    lanes: [
      { key: '3', head: 2, colour: '#ffffff', material: 'PLA', empty: false },
      { key: '4', head: 0, colour: null, material: null, empty: true },
    ],
  };
  app.status = next;
  const toasts = [];
  keepChoice({ app, t, toast: (m) => toasts.push(m) });
  assert.deepEqual(
    [app.print.choice, app.print.overrides, app.print.choiceId, toasts],
    [{ 0: '4' }, [0], 'five.gcode\n2', []],
  );
});

test('a plan with no T order: the picker offers every bay at +0, as nothing is placed', () => {
  const s = unknown();
  s.ace2k_u1.plan = { ...plan, order: [] };
  const app = appOf(s);
  assert.equal(planNow(app).place, null);
  const red = buttons(picker(app, 0).nodes).find((b) => textIn(b).startsWith('1'));
  assert.ok(textIn(red).includes(t('print.cost_zero')));
});

test('a pale colour is marked on its swatch, so a white new spool keeps a grey outline in the light theme', () => {
  assert.match(renderHtml(swatch('#ffffff', true)), /class="dot new pale"/);
  assert.match(renderHtml(swatch('#eeeeee')), /class="dot pale"/);
  assert.match(renderHtml(swatch('#000000', true)), /class="dot new"/);
  assert.match(renderHtml(swatch(null)), /class="dot"/);
  const css = readFileSync(new URL('../css/print.css', import.meta.url), 'utf8');
  assert.match(
    css,
    /\.plan \.dot\.new\.pale,\s*\.picker \.dot\.new\.pale \{\s*box-shadow: 0 0 0 1px var\(--pale-edge\);/,
  );
});

test('showStep: the lit bays of the step scroll into view, as little as needed; no element, nothing', () => {
  const calls = [];
  const bay = (key) => ({ scrollIntoView: (o) => calls.push([key, o]) });
  const el = { querySelectorAll: (sel) => (sel === '.dg .bay.r-from, .dg .bay.r-to' ? [bay('from'), bay('to')] : []) };
  showStep(el);
  assert.deepEqual(calls, [
    ['from', { block: 'nearest' }],
    ['to', { block: 'nearest' }],
  ]);
  showStep(null);
  assert.equal(calls.length, 2);
});

// The plan of demo-unknown with the five-colour file (three colours printed), on a desktop or a phone.
const appWithPlan = ({ narrow = false, panel = null } = {}) => {
  const app = appOf(unknown(), {}, panel);
  app.ui.narrow = narrow;
  return app;
};
const usedColourCount = new Set(plan.order).size;
const ctx = { t, run: () => {} };

test('layout A order: head, left column, diagram, timeline, then the print bar', () => {
  const m = renderHtml(html`<${PlanView} app=${appWithPlan()} ctx=${ctx} />`);
  const at = (cls) => m.indexOf(`class="${cls}`);
  assert.ok(at('plan-head') > 0);
  assert.ok(at('plan-head') < at('plan-left'));
  assert.ok(at('plan-left') < at('plan-dia'));
  assert.ok(at('plan-dia') < at('tl'));
  assert.ok(at('tl') < at('pbar'));
  // The left column holds the colours caption, the rows and the guided step; the diagram is outside it.
  const left = m.slice(at('plan-left'), at('plan-dia'));
  assert.match(left, /class="cap"/);
  assert.match(left, /class="crows"/);
  assert.doesNotMatch(left, /class="dg"/);
});

test('the colours are rows, one per printed colour, each opening the picker', () => {
  const m = renderHtml(html`<${PlanView} app=${appWithPlan()} ctx=${ctx} />`);
  assert.match(m, /class="crows"/);
  assert.equal(usedColourCount, 3);
  assert.equal((m.match(/class="crow[ "]/g) ?? []).length, usedColourCount);
  assert.doesNotMatch(m, /class="chips"/);
  assert.equal((m.match(/aria-pressed="false"/g) ?? []).length, usedColourCount);
  const open = renderHtml(html`<${PlanView} app=${appWithPlan({ panel: { kind: 'pick', n: '6' } })} ctx=${ctx} />`);
  assert.match(open, /class="crow open" aria-pressed="true"/);
});

test('scrollTarget: the colours caption on opening, nothing after', () => {
  assert.equal(scrollTarget({ opened: true, narrow: false }), 'colours');
  assert.equal(scrollTarget({ opened: true, narrow: true }), 'colours');
  assert.equal(scrollTarget({ opened: false, narrow: false }), 'step');
  assert.equal(scrollTarget({ opened: false, narrow: true }), null);
});

test('scrollPlan: the caption to the top once per file, then the step on a desktop; a later plan never scrolls', () => {
  const calls = [];
  const cap = { scrollIntoView: (o) => calls.push(['cap', o]) };
  const bay = { scrollIntoView: (o) => calls.push(['bay', o]) };
  const el = {
    querySelector: (sel) => (sel === '.plan-left > .cap' ? cap : null),
    querySelectorAll: (sel) => (sel === '.dg .bay.r-from, .dg .bay.r-to' ? [bay] : []),
  };
  const app = appWithPlan();
  const seen = { current: null };
  // No file yet: nothing.
  scrollPlan(app, el, seen, null);
  assert.deepEqual(calls, []);
  // Opened: the caption, at the top, and the file is marked scrolled.
  scrollPlan(app, el, seen, 'five.gcode');
  assert.deepEqual(calls, [['cap', { block: 'start' }]]);
  assert.equal(app.print.scrolled, 'five.gcode');
  // A step change of the same file: its lit bays, as little as needed.
  scrollPlan(app, el, seen, 'five.gcode');
  assert.deepEqual(calls.at(-1), ['bay', { block: 'nearest' }]);
  // The view mounts again for a file already scrolled to (its plan came after the reading view scrolled): nothing.
  calls.length = 0;
  scrollPlan(app, el, { current: null }, 'five.gcode');
  assert.deepEqual(calls, []);
  // A phone: the opening scroll, then nothing on a step change.
  const phone = appWithPlan({ narrow: true });
  const pseen = { current: null };
  scrollPlan(phone, el, pseen, 'five.gcode');
  scrollPlan(phone, el, pseen, 'five.gcode');
  assert.deepEqual(calls, [['cap', { block: 'start' }]]);
  // No element (the string renderer): the file is still marked, nothing throws.
  const bare = appWithPlan();
  scrollPlan(bare, null, { current: null }, 'five.gcode');
  assert.equal(bare.print.scrolled, 'five.gcode');
  scrollPlan(bare, { querySelector: () => null }, { current: null }, 'other.gcode');
  assert.equal(bare.print.scrolled, 'other.gcode');
});

test('back to the files forgets the scroll and closes the options sheet', () => {
  const app = appWithPlan({ narrow: true, panel: { kind: 'opts' } });
  app.print.scrolled = 'five.gcode';
  const { ctx: live1 } = live(app);
  runAction(live1, 'print_back', {});
  assert.equal(app.print.scrolled, null);
  assert.equal(app.ui.panel, null);
});

test('cmpLine: the comparison in one line, with the time stopped, or why the suggestion does not fit', () => {
  assert.equal(cmpLine(t, null, 1, 'x'), t('print.cmp_line', { as_is: DASH, best: '1 swap', time: fmtHM(SWAP_S) }));
  assert.equal(cmpLine(t, 3, 2, 'x'), `As it is 3 swaps · with the suggestion 2 swaps, ≈ ${fmtHM(2 * SWAP_S)} stopped`);
  assert.equal(cmpLine(t, 0, null, 'why'), 'As it is 0 swaps · with the suggestion: why');
});

test('keepChoice: a bay the user set that the new plan no longer lists (its unit gone) is dropped, with the toast', () => {
  const s0 = shared();
  const app = appOf(s0);
  const { ctx } = live(app);
  ctx.run('pick', { n: '0', key: '4' });
  const next = structuredClone(s0);
  next.ace2k_u1.plan = {
    ...plan,
    mtime: 2,
    lanes: [{ key: '3', head: 2, colour: '#ffffff', material: 'PLA', empty: false }],
  };
  app.status = next;
  const toasts = [];
  keepChoice({ app, t, toast: (m) => toasts.push(m) });
  assert.deepEqual([app.print.choice, app.print.overrides], [{}, []]);
  assert.deepEqual(toasts, [t('print.choice_dropped', { choices: 'color 1: 4' })]);
});

test('printGate: a placement that does not fit still says what each bay the user set lacks', () => {
  const p3 = {
    file: 'three.gcode',
    mtime: 1,
    tools: [
      { colour: '#ffffff', material: 'PLA' },
      { colour: '#000000', material: 'PLA' },
      { colour: '#ff0000', material: 'PLA' },
    ],
    order: [0, 1, 2, 0],
  };
  const lanes = [
    { key: 'a:1', head: 0, material: null, colour: null, empty: true },
    { key: 'a:2', head: 0, material: 'PLA', colour: '#000000', empty: false, materialSet: true },
  ];
  const pick = { 0: 'a:1', 1: 'a:2' };
  const place = /** @type {any} */ ({ fits: false, colours: 3, lanes: 2, lines: [], takeOut: [], choice: {} });
  const gate = printGate({ plan: p3, lanes, pick, user: pick, merges: {}, place, fixed: [0] }, false);
  assert.ok(gate.block?.includes(t('print.no_spool', { tools: 'color 1', key: 'a:1' })), String(gate.block));
  assert.ok(gate.block?.startsWith(noFitWords(place, p3, {})), String(gate.block));
  // A bay that prints its colour adds nothing; a bay gone from the status asks for a spool.
  const more = printGate(
    { plan: p3, lanes, pick: { ...pick, 2: 'z:9' }, user: pick, merges: {}, place, fixed: [1, 2] },
    false,
  );
  assert.equal(more.block, `${noFitWords(place, p3, {})} ${t('print.choose_first', { tools: 'color 3' })}`);
});
