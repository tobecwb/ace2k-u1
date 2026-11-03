import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { options } from 'preact';
import { html } from '../js/ui/html.js';
import { App } from '../js/ui/App.js';
import { SidePanel, PanelFrame, panelKey, onEscape, panelEffects } from '../js/ui/SidePanel.js';
import { focusIn, focusBack, focusEditor } from '../js/ui/focus.js';
import { BayCard, busyKey } from '../js/ui/BayCard.js';
import { diagramModel } from '../js/diagram-model.js';
import { SpoolEditor } from '../js/ui/SpoolEditor.js';
import { UnitCard, dryerLine, dryerButton } from '../js/ui/UnitCard.js';
import { HeadCard, headLanesOf } from '../js/ui/HeadCard.js';
import {
  autoPanel,
  encoderSpeed,
  escapeCloses,
  eventText,
  keptEdit,
  laneAt,
  tagText,
  unitAt,
  unitId,
} from '../js/lane-state.js';
import { ACTIONS } from '../js/actions.js';
import { FILAMENT_TYPES, laneErrs, SWATCHES } from '../js/model.js';
import { dryerView } from '../js/dryer-form.js';
import { renderHtml, textOf } from './ui-helpers.js';
import { appWithPlan, elements, lane, loadEnglish } from './helpers.js';
import { t } from '../js/i18n.js';

loadEnglish();

const demo = (name) => JSON.parse(readFileSync(new URL(`../dev/demo-${name}.json`, import.meta.url), 'utf8'));
const appOf = (status, over = {}) => ({
  status,
  online: true,
  klippy: 'ready',
  selected: null,
  pending: new Set(),
  folds: {},
  moveMm: '10',
  edit: null,
  form: { preset: 1, temp: 60, hm: '6:00' },
  forms: {},
  print: { open: false, files: [], file: null, choice: {}, choiceId: null },
  ui: { tab: 'units', panel: null, sheet: false },
  ...over,
});

// The markup of a component, and every element vnode it made, so a test can press its buttons.
function rendered(vnode) {
  const nodes = [];
  const prev = options.vnode;
  options.vnode = (v) => {
    if (typeof v.type === 'string') nodes.push(v);
    prev?.(v);
  };
  try {
    return { out: renderHtml(vnode), nodes };
  } finally {
    options.vnode = prev;
  }
}
// The text of a vnode's children.
const textIn = (v) => {
  const c = v.props.children;
  if (Array.isArray(c)) return c.map((x) => (x && typeof x === 'object' ? textIn(x) : (x ?? ''))).join('');
  return c && typeof c === 'object' ? textIn(c) : String(c ?? '');
};
const button = (nodes, words) => nodes.find((n) => n.type === 'button' && textIn(n).trim() === words);

// The side panel of an app with this panel open; runs records the actions it calls.
function panel(status, open, over = {}) {
  const runs = [];
  const app = appOf(status, { ui: { tab: 'units', panel: open, sheet: false }, ...over });
  const ctx = { app, t, run: (id, data = {}, value) => runs.push([id, data, value].filter((x) => x !== undefined)) };
  return { app, runs, ...rendered(html`<${SidePanel} app=${app} ctx=${ctx} />`) };
}

test('panel opens on a bay, keyed as the diagram writes it; panel_close clears it', () => {
  const app = appOf(demo('mine'));
  const ctx = { app, t, render: () => {} };
  ACTIONS.panel(ctx, { dataset: { kind: 'bay', key: 'a:2' } });
  assert.deepEqual(app.ui.panel, { kind: 'bay', key: 'a:2' });
  ACTIONS.panel_close(ctx, { dataset: {} });
  assert.equal(app.ui.panel, null);
});

test('Escape closes the panel; any other key does not; the listener goes when the panel closes', () => {
  const listeners = new Map();
  const doc = {
    activeElement: null,
    addEventListener: (type, fn) => listeners.set(type, fn),
    removeEventListener: (type) => listeners.delete(type),
  };
  let closed = 0;
  const stop = onEscape(doc, () => {
    closed += 1;
  });
  listeners.get('keydown')({ key: 'Enter' });
  listeners.get('keydown')({ key: 'Escape' });
  assert.equal(closed, 1);
  stop();
  assert.equal(listeners.size, 0);
});

test('the focus moves into the panel (to its autofocus mark) and back to what opened it', () => {
  const focused = [];
  const el = (name, inside = false) => ({ name, focus: () => focused.push(name), isConnected: true, inside });
  const opener = el('bay');
  const mark = el('dry');
  const box = {
    ...el('panel'),
    contains: (x) => Boolean(x?.inside),
    querySelector: () => null,
  };
  const memo = { opener: null };
  focusIn(box, { activeElement: opener }, memo);
  assert.deepEqual([memo.opener, focused], [opener, ['panel']]);
  // A change inside the panel keeps the first opener, and an autofocus mark takes the focus.
  focusIn({ ...box, querySelector: () => mark }, { activeElement: el('x', true) }, memo);
  focusIn(box, { activeElement: null }, memo);
  assert.deepEqual([memo.opener, focused], [opener, ['panel', 'dry', 'panel']]);
  focusIn(null, { activeElement: opener }, memo); // no panel drawn: nothing
  focusBack(memo);
  assert.deepEqual(focused.at(-1), 'bay');
  // An opener that left the page, or none, gets nothing.
  focusBack({ opener: { ...opener, isConnected: false, focus: () => assert.fail('gone') } });
  focusBack({ opener: null });
  assert.equal(panelKey({ kind: 'unit', id: 'a', focus: 'dryer' }), 'unit||a||dryer|');
  assert.equal(panelKey({ kind: 'pick', n: '2' }), 'pick|||2||');
  assert.equal(panelKey({ kind: 'bay', key: '2', auto: '1' }), 'bay|2||||1');
  // A panel that opened by itself, and a field being typed in, keep the focus where it is.
  const before = focused.length;
  const quiet = { opener: null };
  focusIn(box, { activeElement: opener }, quiet, true);
  for (const tagName of ['INPUT', 'SELECT', 'TEXTAREA'])
    focusIn(box, { activeElement: { ...el('field'), tagName } }, quiet);
  assert.deepEqual([focused.length, quiet.opener], [before, null]);
});

test('the side panel is the fixed panel, a bottom sheet on a phone, and makes room in the page', () => {
  const open = appOf(demo('unknown'), { ui: { tab: 'units', panel: { kind: 'bay', key: '2' }, sheet: false } });
  const out = renderHtml(html`<${App} app=${open} ctx=${{ app: open, t, run: () => {} }} />`);
  assert.match(out, /<div class="shell has-panel">/);
  assert.match(out, /<aside class="side" aria-label="Bay 2" tabindex="-1" data-kind="bay">/);
  const phone = { ...open, ui: { ...open.ui, narrow: true } };
  assert.match(renderHtml(html`<${App} app=${phone} ctx=${{ app: phone, t, run: () => {} }} />`), /class="side sheet"/);
  const shut = appOf(demo('unknown'));
  assert.match(renderHtml(html`<${App} app=${shut} ctx=${{ app: shut, t, run: () => {} }} />`), /<div class="shell">/);
});

test('an untagged bay shows Edit spool; a tagged one says "from the tag" and has no Edit', () => {
  const untagged = panel(demo('unknown'), { kind: 'bay', key: '2' });
  const text = textOf(untagged.out);
  assert.ok(button(untagged.nodes, t('action.edit_spool')), 'Edit spool');
  assert.ok(text.includes('PETG') && text.includes(t('lane.by_hand')) && text.includes('Bambu · #111111'));
  assert.ok(text.includes(t('panel.route', { lane: '2', head: 'T1' })));
  const tagged = panel(demo('unknown'), { kind: 'bay', key: '1' });
  assert.ok(textOf(tagged.out).includes(t('lane.from_tag')));
  assert.ok(!button(tagged.nodes, t('action.edit_spool')));
  assert.ok(textOf(tagged.out).includes('Bambu · #DD3333'));
});

test('the bay card: buffer gauge, speed, feed-forward, last move, the rows and the Advanced fold', () => {
  const s = demo('unknown');
  s.ace2k.lanes[1].last_event = { kind: 'loaded', mode: 'loading', filament_mm: 812.5 };
  const { out, nodes, runs } = panel(s, { kind: 'bay', key: '2' }, { folds: { adv: true } });
  assert.deepEqual(
    elements(out, 'div.gauge span').map((e) => [e.text, e.attrs.class]),
    [
      [t('buffer.taut'), 'z'],
      [t('buffer.at_rest'), 'z on'],
      [t('buffer.pushed'), 'z'],
    ],
  );
  assert.ok(textOf(out).includes(t('panel.speed_value', { v: '0.0' })), 'an idle lane is still');
  assert.ok(textOf(out).includes(t('panel.last_move', { what: 'loaded (loading) at 812.5 mm' })));
  const ff = nodes.find((n) => n.props.role === 'switch');
  assert.equal(ff.props['aria-checked'], 'true');
  ff.props.onClick();
  assert.match(out, /<details id="adv" class="adv" open>/);
  for (const words of [
    t('action.eject'),
    t('action.read_tag'),
    t('action.feed'),
    t('action.load_head', { head: 'T1' }),
  ])
    assert.ok(button(nodes, words), words);
  button(nodes, t('action.eject')).props.onClick();
  button(nodes, t('action.edit_spool')).props.onClick();
  nodes.find((n) => n.type === 'input' && n.props.type === 'number').props.onInput({ currentTarget: { value: '25' } });
  assert.deepEqual(runs, [
    ['lane', { lane: '2', unit: '', id: 'ff' }],
    ['lane', { lane: '2', unit: '', id: 'eject' }],
    ['edit_open', { lane: '2', unit: '' }],
    ['move_mm', {}, '25'],
  ]);
  // The tag and the encoder sit in the Advanced fold.
  assert.ok(textOf(out).includes(t('tag.no_tag')) && textOf(out).includes('0.0 mm'));
});

test('during a print, Eject is disabled and says why; Stop locked by the print has its own words', () => {
  const s = { ...demo('unknown'), print_stats: { state: 'printing' } };
  const { out, nodes } = panel(s, { kind: 'bay', key: '2' });
  const eject = button(nodes, t('action.eject'));
  assert.equal(eject.props.disabled, true);
  assert.equal(eject.props.title, t('reason.in_use'));
  assert.ok(textOf(out).includes(t('reason.in_use')));
  assert.match(out, /<button type="button" class="btn pri" data-id="eject" disabled/);
  const moving = structuredClone(s);
  moving.ace2k.lanes[1].mode = 'following';
  assert.ok(textOf(panel(moving, { kind: 'bay', key: '2' }).out).includes(t('reason.stop_in_use')));
});

test('a bay of one of several units: "b:3 → T2", its unit in the actions', () => {
  const { out, nodes, runs } = panel(demo('mine'), { kind: 'bay', key: 'b:3', auto: '1' });
  assert.ok(textOf(out).includes('b:3 → T2'));
  assert.ok(textOf(out).includes(t('panel.shared_with', { lanes: 'b:1, b:2, b:4' })));
  button(nodes, t('action.edit_spool')).props.onClick();
  assert.deepEqual(runs, [['edit_open', { lane: '3', unit: 'b' }]]);
});

test('an empty bay says so with its hint; a spool with no material says it is not identified', () => {
  const empty = panel(demo('unknown'), { kind: 'bay', key: '4' });
  assert.ok(textOf(empty.out).includes(t('lane.empty_hint', { n: '4' })));
  const s = demo('unknown');
  s.print_task_config.filament_exist[2] = false;
  const unknown = textOf(panel(s, { kind: 'bay', key: '3' }).out);
  assert.ok(unknown.includes(t('lane.spool_unknown')));
  assert.ok(!unknown.includes(t('lane.by_hand')) && !unknown.includes(t('lane.from_tag')));
});

test('a lane in error names its error; one with no head routes nowhere; busy buttons say sending', () => {
  const s = demo('mine');
  s['ace2k a'].lanes[1] = { ...s['ace2k a'].lanes[1], mode: 'error', error: 'tangled' };
  s.ace2k_u1.heads[1] = s.ace2k_u1.heads[1].filter((x) => x.lane !== 2);
  const { out } = panel(s, { kind: 'bay', key: 'a:2' }, { pending: new Set(['a|ff:2', 'a|clear:2']) });
  assert.ok(textOf(out).includes(t('panel.route_none', { lane: 'a:2' })));
  assert.deepEqual(
    elements(out, 'span.state.s-error').map((e) => e.text),
    [t('lane.error.tangled')],
  );
  assert.ok(textOf(out).includes(t('action.sending')));
  assert.match(out, /role="switch" aria-checked="true"[^>]*disabled/);
  assert.equal(busyKey('ff', 1), 'ff:2');
  assert.equal(busyKey('load_head', 1), 'load_head');
  assert.equal(busyKey('eject', 1), 'eject:2');
});

test('the open spool editor sits in its bay card, and only in its own', () => {
  const s = demo('mine');
  const edit = { lane: 3, head: 2, type: 'PETG', vendor: 'Generic', color: '#111111', unit: 'b' };
  const own = panel(s, { kind: 'bay', key: 'b:3' }, { edit });
  assert.ok(own.out.includes('class="spool-editor"'));
  assert.ok(!button(own.nodes, t('action.edit_spool')), 'the button gives way to the form');
  assert.ok(!panel(s, { kind: 'bay', key: 'a:3' }, { edit }).out.includes('spool-editor'));
});

// The spool editor of lane key in status, with the form edit.
function editor(status, key, edit, over = {}) {
  const runs = [];
  const app = appOf(status, { edit, ...over });
  const ctx = { app, t, run: (id, data = {}, value) => runs.push([id, data, value].filter((x) => x !== undefined)) };
  return { runs, ...rendered(html`<${SpoolEditor} app=${app} ctx=${ctx} at=${laneAt(app, key)} edit=${edit} />`) };
}

test('the spool editor: material buttons, swatches and a free colour, an optional vendor, Save', () => {
  const edit = { lane: 2, head: 1, type: 'PETG', vendor: 'Generic', color: '#111111', unit: null };
  const { out, nodes, runs } = editor(demo('unknown'), '2', edit);
  const mats = nodes.filter((n) => n.type === 'button' && n.props.class?.startsWith('mat'));
  assert.deepEqual(
    mats.map((n) => textIn(n)),
    FILAMENT_TYPES,
  );
  assert.deepEqual(
    mats.filter((n) => n.props['aria-pressed'] === 'true').map((n) => textIn(n)),
    ['PETG'],
  );
  assert.equal(nodes.filter((n) => n.props.class?.startsWith('sw')).length, SWATCHES.length);
  assert.ok(textOf(out).includes(t('lane.edit_note')) && textOf(out).includes(t('spool.vendor')));
  mats[0].props.onClick();
  nodes.find((n) => n.props.class === 'sw').props.onClick();
  nodes.find((n) => n.props.type === 'color').props.onInput({ currentTarget: { value: '#123456' } });
  nodes.find((n) => n.props.type === 'text').props.onInput({ currentTarget: { value: 'Elegoo' } });
  nodes.find((n) => n.type === 'form').props.onSubmit({ preventDefault: () => {} });
  button(nodes, t('action.cancel')).props.onClick();
  assert.deepEqual(runs, [
    ['spool_edit_type', { type: 'ABS' }],
    ['spool_edit_colour', {}, '#ffffff'],
    ['spool_edit_colour', {}, '#123456'],
    ['spool_edit_vendor', {}, 'Elegoo'],
    ['edit_save', { lane: '2' }],
    ['edit_cancel', {}],
  ]);
  assert.ok(button(nodes, t('action.save')));
});

test('the spool editor opened for a colour: the material locked, "Save and use for colour 1"', () => {
  const edit = { lane: 2, head: 1, type: 'PLA', vendor: 'Generic', color: '#D32C34', unit: null, use: 0, lock: true };
  const { nodes } = editor(demo('unknown'), '2', edit);
  const mats = nodes.filter((n) => n.type === 'button' && n.props.class?.startsWith('mat'));
  assert.equal(mats.length, 1);
  assert.deepEqual(
    [textIn(mats[0]), mats[0].props['aria-pressed'], mats[0].props.disabled],
    ['PLA', 'true', undefined],
  );
  const save = button(nodes, t('spool.save_use', { n: 1 }));
  assert.ok(save);
  assert.equal(save.props.disabled, false);
  // The colour given is pressed among the swatches when it is one of them.
  assert.equal(
    nodes.filter((n) => n.props['aria-pressed'] === 'true' && n.props.class?.startsWith('sw')).length,
    SWATCHES.includes('#d32c34') ? 1 : 0,
  );
});

test('the spool editor: a sending Save says so and sends nothing; a locked one says why', () => {
  const edit = { lane: 2, head: 1, type: 'PETG', vendor: 'Generic', color: '#111111', unit: null };
  const busy = editor(demo('unknown'), '2', edit, { pending: new Set(['edit_spool:2']) });
  const save = button(busy.nodes, t('action.sending'));
  assert.equal(save.props['aria-disabled'], 'true');
  busy.nodes.find((n) => n.type === 'form').props.onSubmit({ preventDefault: () => {} });
  assert.deepEqual(busy.runs, []);
  const off = editor(demo('unknown'), '2', edit, { online: false });
  assert.deepEqual(
    [button(off.nodes, t('action.save')).props.disabled, button(off.nodes, t('action.save')).props.title],
    [true, t('reason.offline')],
  );
  // A lane whose tag speaks offers no Edit spool at all.
  const tagged = editor(demo('unknown'), '1', { ...edit, lane: 1 });
  assert.equal(button(tagged.nodes, t('action.save')).props.title, t('reason.unavailable'));
});

test('the unit card: four small spools, the dryer ring with Dry, the firmware and the link', () => {
  const { out, nodes, runs } = panel(demo('unknown'), { kind: 'unit', id: '' });
  assert.equal(elements(out, 'span.mini').length, 4);
  assert.equal(elements(out, 'span.mini.empty').length, 1);
  assert.equal(elements(out, 'span.mini.unknown').length, 2, 'the empty bay and the spool of no known colour');
  assert.ok(textOf(out).includes(t('panel.firmware', { v: '0.11.0', link: t('top.link_ok') })));
  assert.ok(textOf(out).includes(t('panel.dry_plan', { temp: 60, time: '6:00' })));
  button(nodes, t('panel.dry')).props.onClick();
  assert.deepEqual(runs, [['dry', { unit: '' }]]);
  assert.ok(!out.includes('data-autofocus'));
  const shortcut = panel(demo('mine'), { kind: 'unit', id: 'b', focus: 'dryer' });
  assert.match(shortcut.out, /data-autofocus="true"/);
  assert.ok(textOf(shortcut.out).startsWith(t('units.name', { name: 'b' })));
});

test("the unit card's dryer: heating with Stop, cooling with no button, a fault with Clear", () => {
  const view = (dryer) => dryerView({ ace2k: { dryer } });
  const heat = view({ state: 'heating', target: 55, remaining: 161 });
  assert.equal(dryerLine(heat, appOf({}), null, t), t('panel.dry_left', { target: '55.0 °C', left: '2:41' }));
  assert.deepEqual(dryerButton(heat, t), { action: 'dry_stop', label: t('dryer.stop') });
  assert.equal(
    dryerLine(view({ state: 'starting', target: 55 }), appOf({}), null, t),
    t('panel.dry_left', { target: '55.0 °C', left: '0:00' }),
  );
  const cool = view({ state: 'cooldown' });
  assert.deepEqual([dryerLine(cool, appOf({}), null, t), dryerButton(cool, t)], [t('dryer.cooldown_rule'), null]);
  const fault = view({ state: 'fault', fault: 'fans' });
  assert.deepEqual(
    [dryerLine(fault, appOf({}), null, t), dryerButton(fault, t)],
    [t('dryer.fault.fans'), { action: 'dry_clear', label: t('dryer.clear') }],
  );
  // A unit with no reading and no version shows dashes and its link only.
  const bare = panel({ ace2k: { lanes: [] } }, { kind: 'unit', id: '' }, { online: false });
  assert.ok(textOf(bare.out).includes(`— ${'—'}`) || textOf(bare.out).includes('—'));
  assert.ok(textOf(bare.out).includes(t('top.link_down')));
});

test('the head card: its lanes with Load into T1, the lane in it, Find the lane in T1 while unknown', () => {
  const s = demo('mine');
  s['filament_motion_sensor e1_filament'] = { filament_detected: false, enabled: true };
  const { out, nodes, runs } = panel(s, { kind: 'head', id: '1' });
  assert.ok(textOf(out).startsWith('Head T1'));
  assert.equal(elements(out, 'li.row.on').length, 1);
  assert.ok(textOf(out).includes(t('panel.in_head')));
  const loads = nodes.filter((n) => n.type === 'button' && textIn(n) === t('action.load_head', { head: 'T1' }));
  assert.equal(loads.length, 3);
  loads[0].props.onClick();
  assert.deepEqual(runs, [['lane', { id: 'load_head', lane: '2', unit: 'a' }]]);
  assert.ok(!button(nodes, t('action.probe_head', { head: 'T1' })));
  assert.deepEqual(
    headLanesOf(appOf(s), 1).map((l) => [l.key, l.current]),
    [
      ['a:1', true],
      ['a:2', false],
      ['a:3', false],
      ['a:4', false],
    ],
  );
  // The head holds filament and no lane is known in it: the probe with its note, and the loads locked until it
  // names the lane.
  const unknown = demo('mine');
  unknown.ace2k_u1.current[1] = null;
  const u = panel(unknown, { kind: 'head', id: '1' }, { pending: new Set() });
  assert.ok(textOf(u.out).includes('T1 holds a filament: no lane known'));
  const probe = button(u.nodes, t('action.probe_head', { head: 'T1' }));
  probe.props.onClick();
  assert.deepEqual(u.runs, [['probe_head', { head: '2' }]]);
  assert.equal(probe.props.disabled, false);
  assert.ok(textOf(u.out).includes("Moves T1's lanes slowly, one at a time; T1 must be cold."));
  const locked = u.nodes.find((n) => n.type === 'button' && textIn(n) === t('action.load_head', { head: 'T1' }));
  assert.deepEqual(
    [locked.props.disabled, locked.props.title],
    [true, 'T1 holds a filament the page cannot name: Find the lane in T1 first'],
  );
  // While printing, and while a lane of the head moves, the probe is disabled with its reason.
  const printingNow = demo('mine');
  printingNow.ace2k_u1.current[1] = null;
  printingNow.print_stats = { ...printingNow.print_stats, state: 'printing' };
  const p = panel(printingNow, { kind: 'head', id: '1' });
  const pb = button(p.nodes, t('action.probe_head', { head: 'T1' }));
  assert.deepEqual([pb.props.disabled, pb.props.title], [true, t('reason.print_on')]);
  const moving = demo('mine');
  moving.ace2k_u1.current[1] = null;
  moving['ace2k a'].lanes[2] = { ...moving['ace2k a'].lanes[2], mode: 'rolling_back' };
  const m = panel(moving, { kind: 'head', id: '1' });
  const mb = button(m.nodes, t('action.probe_head', { head: 'T1' }));
  assert.deepEqual([mb.props.disabled, mb.props.title], [true, "one of T1's lanes is moving: wait for it to end"]);
  assert.ok(!textOf(m.out).includes('must be cold'));
  // The probe's own moves are not a reason while it runs: it says sending, with no note.
  const running = panel(moving, { kind: 'head', id: '1' }, { pending: new Set(['a|probe_head:2']) });
  const rb = button(running.nodes, t('action.sending'));
  assert.deepEqual([rb.props.disabled, rb.props.title], [true, undefined]);
  assert.deepEqual(
    elements(running.out, 'p.note.why').map((e) => e.text),
    [t('reason.head_unknown', { head: 'T1' })],
  );
  // A hot head: disabled with the adapter's limit.
  const hot = demo('mine');
  hot.ace2k_u1.current[1] = null;
  hot.ace2k_u1.probe_cold = [null, false, null, null];
  hot.ace2k_u1.probe_max_temp = 60;
  const hb = button(panel(hot, { kind: 'head', id: '1' }).nodes, t('action.probe_head', { head: 'T1' }));
  assert.deepEqual([hb.props.disabled, hb.props.title], [true, 'T1 is hot: let it cool below 60 °C']);
  const off = panel(unknown, { kind: 'head', id: '1' }, { online: false });
  assert.equal(button(off.nodes, t('action.probe_head', { head: 'T1' })).props.title, t('reason.offline'));
  // A head load in progress keeps every Load busy.
  const busy = panel(s, { kind: 'head', id: '1' }, { pending: new Set(['load_head']) });
  assert.ok(button(busy.nodes, t('action.sending')).props.disabled);
});

test("the head card of a head no lane feeds: the U1's own spool; a head with one lane", () => {
  const own = panel(demo('mine'), { kind: 'head', id: '0' });
  assert.ok(textOf(own.out).includes(t('units.u1_spool')) && textOf(own.out).includes(t('units.no_ace')));
  const s = demo('mine');
  s.print_task_config.filament_exist[0] = false;
  assert.ok(textOf(panel(s, { kind: 'head', id: '0' }).out).includes(t('units.u1_spool')));
  const one = demo('unknown');
  one.ace2k.lanes[2].insert = true;
  const single = textOf(panel(one, { kind: 'head', id: '2' }).out);
  assert.ok(single.includes(t('units.lanes_one', { list: '3' })));
  assert.ok(single.includes(t('panel.head_now', { lane: '3', material: 'PLA' })) || single.includes('now 3'));
  // A lane with no spool reads unknown, and a lane in the head with no material reads it too.
  const bare = demo('unknown');
  bare.ace2k.lanes[0].insert = false;
  assert.ok(textOf(panel(bare, { kind: 'head', id: '0' }).out).includes(t('units.no_material')));
});

test('the status card stays a panel kind; a bay, unit or head that is gone says so', () => {
  const status = panel(demo('unknown'), { kind: 'status' });
  assert.ok(textOf(status.out).includes(t('top.link_ok')));
  for (const open of [
    { kind: 'bay', key: 'z:9' },
    { kind: 'unit', id: 'zz' },
    { kind: 'head', id: '7' },
    { kind: 'head', id: 'x' },
  ])
    assert.ok(textOf(panel(demo('mine'), open).out).includes(t('panel.gone')), JSON.stringify(open));
  const app = appOf(demo('mine'));
  const frame = PanelFrame({ panel: { kind: 'unit', id: 'a' }, app, ctx: { app, t, run: () => {} } });
  assert.equal(frame.props['aria-label'], t('units.name', { name: 'a' }));
});

test('lane keys resolve for one unit ("2", unit null) and several ("a:2", unit "a")', () => {
  const one = laneAt(appOf(demo('unknown')), '2');
  assert.deepEqual([one.unit, one.i, one.key], [null, 1, '2']);
  const several = laneAt(appOf(demo('mine')), 'c:4');
  assert.deepEqual([several.unit, several.i], ['c', 3]);
  assert.equal(laneAt(appOf(demo('mine')), '2'), null);
  assert.equal(laneAt(appOf(demo('unknown')), 'a:2'), null);
  assert.equal(unitAt(appOf(demo('unknown')), ''), null);
  assert.equal(unitAt(appOf(demo('mine')), 'b'), 'b');
  assert.equal(unitAt(appOf(demo('mine')), ''), undefined);
  assert.deepEqual([unitId(null), unitId('a')], ['', 'a']);
});

test('autoPanel: a lane in error at load or newly in error opens its bay; never over an open panel', () => {
  const err = (status, unit, i, error = 'stuck') => {
    const s = structuredClone(status);
    const lanes = unit ? s[`ace2k ${unit}`].lanes : s.ace2k.lanes;
    lanes[i] = { ...lanes[i], mode: 'error', error };
    return s;
  };
  // At load (the empty status before the first frame): the first lane in error.
  const calm = demo('unknown');
  calm.ace2k.lanes[0] = { ...calm.ace2k.lanes[0], mode: 'idle', error: null };
  const one = err(calm, null, 1);
  // The demo itself starts with bay 1 stuck, so it opens on bay 1.
  assert.deepEqual(autoPanel(appOf(demo('unknown')), {}), { kind: 'bay', key: '1', auto: '1' });
  assert.deepEqual(autoPanel(appOf(one), {}), { kind: 'bay', key: '2', auto: '1' });
  assert.deepEqual(autoPanel(appOf(one), null), { kind: 'bay', key: '2', auto: '1' });
  // Another tab is left alone.
  for (const tab of ['print', 'dryer', 'history'])
    assert.equal(autoPanel(appOf(one, { ui: { tab, panel: null, sheet: false } }), {}), null, tab);
  const several = err(err(demo('mine'), 'b', 2), 'c', 0);
  assert.deepEqual(autoPanel(appOf(several), {}), { kind: 'bay', key: 'b:3', auto: '1' });
  // A lane newly in error while one already was: the new one.
  assert.deepEqual(autoPanel(appOf(several), err(demo('mine'), 'b', 2)), { kind: 'bay', key: 'c:1', auto: '1' });
  // Nothing new: nothing opens, so a card the operator closed stays closed.
  assert.equal(autoPanel(appOf(several), several), null);
  assert.equal(autoPanel(appOf(demo('mine')), {}), null);
  // An open panel is never taken over.
  const busy = appOf(several, { ui: { tab: 'units', panel: { kind: 'head', id: '1' }, sheet: false } });
  assert.equal(autoPanel(busy, {}), null);
  // A known error with the mode not yet error counts; a mode error with no error word counts too.
  assert.equal(laneErrs(lane({ error: 'tangled' })), true);
  assert.equal(laneErrs(lane({ mode: 'error', error: null })), true);
  assert.equal(laneErrs(lane()), false);
  assert.equal(laneErrs(undefined), false);
});

test('encoderSpeed: from two encoder changes, 0 for an idle or a still lane, none before it can tell', () => {
  assert.equal(encoderSpeed(null, null, 0, false), null);
  assert.deepEqual(encoderSpeed(null, 5, 1000, true), { mm: 5, at: 1000, speed: 0 });
  const first = encoderSpeed(null, 10, 1000, false);
  assert.equal(first.speed, null);
  const next = encoderSpeed(first, 12, 2000, false);
  assert.equal(next.speed, 2);
  assert.equal(encoderSpeed(next, 12, 3000, false), next, 'unchanged within the still time');
  assert.equal(encoderSpeed(next, 12, 5000, false).speed, 0);
  assert.equal(encoderSpeed(next, 8, 2000, false).speed, 2, 'a change at the same instant keeps the last speed');
  assert.equal(encoderSpeed(next, 8, 4000, false).speed, 2);
});

test('tagText and eventText: the words of a tag state and of the last move', () => {
  const tag = (state, record = null, insert = true) => tagText(lane({ insert, tag: { state, record } }), false);
  assert.equal(tag('pending'), t('tag.pending'));
  assert.equal(tag('searching'), t('tag.reading'));
  assert.equal(tag('reading'), t('tag.reading'));
  assert.equal(tag('read', { brand: 'Bambu', material: 'PLA' }), t('tag.read_who', { who: 'Bambu PLA' }));
  assert.equal(tag('read', {}), t('tag.read'));
  assert.equal(tag('read'), t('tag.read'));
  assert.equal(tag('no_tag'), t('tag.no_tag'));
  assert.equal(tag('unknown'), t('lane.no_spool'));
  assert.equal(tagText(lane({ tag: { state: 'read' } }), true), t('tag.forgotten'));
  assert.equal(tagText(lane({ insert: false, tag: { state: 'no_tag' } }), true), t('tag.no_tag'));
  assert.equal(tagText(null, false), t('lane.no_spool'));
  assert.equal(tagText({ insert: true }, false), t('lane.no_spool'));
  assert.equal(eventText(null), t('lane.none'));
  assert.equal(
    eventText({ kind: 'nope', mode: null }),
    t('lane.event_text', { kind: t('lane.kind.unknown'), mode: t('lane.mode.unknown'), mm: '—' }),
  );
});

test('BayCard renders on its own for the colour picker too', () => {
  const app = appOf(demo('unknown'));
  const out = renderHtml(html`<${BayCard} app=${app} ctx=${{ app, t, run: () => {} }} at=${laneAt(app, '2')} />`);
  assert.ok(out.includes('class="card bay-card"'));
  const unit = renderHtml(html`<${UnitCard} app=${app} ctx=${{ app, t, run: () => {} }} unit=${null} />`);
  assert.ok(unit.includes('class="card unit-card"'));
  const head = renderHtml(html`<${HeadCard} app=${app} ctx=${{ app, t, run: () => {} }} head=${1} />`);
  assert.ok(head.includes('class="card head-card-panel"'));
});

test("the panel's effects: focus in, focus back, and Escape through panel_close", () => {
  const focused = [];
  const listeners = new Map();
  const opener = { focus: () => focused.push('opener'), isConnected: true };
  const doc = {
    activeElement: opener,
    addEventListener: (type, fn) => listeners.set(type, fn),
    removeEventListener: (type) => listeners.delete(type),
  };
  const box = { focus: () => focused.push('panel'), contains: () => false, querySelector: () => null };
  const other = { id: 'other' };
  const runs = [];
  const app = appOf({});
  const fx = panelEffects({ current: box }, { opener: null }, { app, run: (...a) => runs.push(a) }, false, doc);
  fx.focus();
  const stop = fx.escape();
  listeners.get('keydown')({ key: 'Escape' });
  // While the dryer's warning or the bottom sheet is open, Escape is theirs.
  app.modeWarn = true;
  listeners.get('keydown')({ key: 'Escape' });
  app.modeWarn = false;
  app.ui = { ...app.ui, sheet: true };
  listeners.get('keydown')({ key: 'Escape' });
  fx.restore(false); // not leaving: nothing
  doc.activeElement = other;
  fx.restore(true); // focus moved elsewhere since: kept
  assert.deepEqual(focused, ['panel']);
  doc.activeElement = null;
  fx.restore(true); // lost: back to the opener
  doc.activeElement = { id: 'inside' };
  box.contains = () => true;
  fx.restore(true); // still in the closing panel: back to the opener
  box.contains = () => false;
  doc.body = { id: 'body' };
  doc.activeElement = doc.body;
  fx.restore(true); // on the body: back to the opener
  stop();
  assert.deepEqual(focused, ['panel', 'opener', 'opener', 'opener']);
  assert.deepEqual(runs, [['panel_close', {}]]);
  assert.equal(listeners.size, 0);
  // An auto-opened panel does not take the focus.
  const auto = panelEffects({ current: box }, { opener: null }, { app, run: () => {} }, true, doc);
  auto.focus();
  assert.deepEqual(focused, ['panel', 'opener', 'opener', 'opener']);
});

test('the bay card: feed-forward off and locked with its reason; a lane the unit does not report', () => {
  const s = demo('unknown');
  s.ace2k.lanes[1].ff_on = false;
  const off = panel(s, { kind: 'bay', key: '2' }, { online: false });
  const ff = off.nodes.find((n) => n.props.role === 'switch');
  assert.deepEqual(
    [ff.props.class, ff.props['aria-checked'], ff.props.disabled, ff.props.title],
    ['tog', 'false', true, t('reason.offline')],
  );
  const bare = panel({ ace2k: { lanes: [] } }, { kind: 'bay', key: '1' });
  assert.ok(textOf(bare.out).includes(t('lane.no_spool_in_bay')));
});

test('the head card: a Load sending while it is offered; the unit card cooling down and on a fault', () => {
  const s = demo('mine');
  s['filament_motion_sensor e1_filament'] = { filament_detected: false, enabled: true };
  const busy = panel(s, { kind: 'head', id: '1' }, { pending: new Set(['load_head']) });
  assert.ok(
    busy.nodes.filter((n) => n.type === 'button' && textIn(n) === t('action.sending')).every((n) => n.props.disabled),
  );
  // Without the U1's head load macro in the status, no lane offers Load.
  const noMacro = demo('mine');
  delete noMacro['gcode_macro AUTO_FEEDING_BATCH'];
  const plain = panel(noMacro, { kind: 'head', id: '1' });
  assert.equal(plain.nodes.filter((n) => n.type === 'button' && textIn(n).startsWith('Load into')).length, 0);
  assert.equal(elements(plain.out, 'li.row').length, 4);
  const cool = demo('unknown');
  cool.ace2k.dryer.state = 'cooldown';
  const c = panel(cool, { kind: 'unit', id: '' });
  assert.ok(!button(c.nodes, t('panel.dry')) && textOf(c.out).includes(t('dryer.cooldown_rule')));
  const fault = demo('unknown');
  fault.ace2k.dryer = { ...fault.ace2k.dryer, state: 'fault', fault: 'fans' };
  const f = panel(fault, { kind: 'unit', id: '' });
  assert.equal(button(f.nodes, t('dryer.clear')).props.class, 'btn');
});

test('heads have one name everywhere: the bay note and the head card say T1, never "head 2"', () => {
  const bay = textOf(panel(demo('unknown'), { kind: 'bay', key: '2' }).out);
  assert.ok(bay.includes('T1 already holds filament: unload it first'), bay);
  assert.ok(!/head \d already/.test(bay));
  const unknown = demo('mine');
  unknown.ace2k_u1.current[1] = null;
  const head = panel(unknown, { kind: 'head', id: '1' });
  const text = textOf(head.out);
  assert.ok(text.includes('T1 holds a filament the page cannot name: Find the lane in T1 first'));
  assert.ok(!/head \d (already|holds)/.test(text));
  // Each reason once, as a note.
  assert.deepEqual(
    elements(head.out, 'p.note.why').map((e) => e.text),
    [t('reason.head_unknown', { head: 'T1' })],
  );
  const load = head.nodes.find((n) => n.type === 'button' && textIn(n) === t('action.load_head', { head: 'T1' }));
  assert.ok(load, 'Load into T1 is on the head card');
  assert.deepEqual([load.props.disabled, load.props.title], [true, t('reason.head_unknown', { head: 'T1' })]);
  // The probe's own reason is a note too, and a probe in flight says sending.
  const off = panel(unknown, { kind: 'head', id: '1' }, { online: false });
  const offNote = elements(off.out, 'p.note.why').find((e) => e.text === t('reason.offline', { head: 'T1' }));
  assert.ok(offNote, "the probe's reason is a note");
  const probe = button(off.nodes, t('action.probe_head', { head: 'T1' }));
  assert.deepEqual([probe.props.disabled, probe.props.title], [true, offNote.text]);
  const busy = panel(unknown, { kind: 'head', id: '1' }, { pending: new Set(['a|probe_head:2']) });
  const sending = busy.nodes.find((n) => n.type === 'button' && textIn(n) === t('action.sending'));
  assert.equal(sending.props.disabled, true);
});

test('the bay card of a lane whose head holds an unnamed filament links to the head card', () => {
  const unknown = demo('mine');
  unknown.ace2k_u1.current[1] = null;
  const bay = panel(unknown, { kind: 'bay', key: 'a:2' });
  const link = button(bay.nodes, 'No lane known in T1 — open T1');
  assert.ok(link, textOf(bay.out));
  assert.equal(link.props.class, 'link');
  link.props.onClick();
  assert.deepEqual(bay.runs, [['panel', { kind: 'head', id: '1' }]]);
  // One note only: the link is head_unknown's note.
  const notes = elements(bay.out, 'p.note.why').map((e) => e.text);
  assert.deepEqual(
    notes.filter((x) => x === 'No lane known in T1 — open T1'),
    ['No lane known in T1 — open T1'],
  );
  assert.ok(!textOf(bay.out).includes(t('reason.head_unknown', { head: 'T1' })));
  const load = bay.nodes.find((n) => n.type === 'button' && n.props['data-id'] === 'load_head');
  assert.ok(load, 'Load into T1 is on the bay card');
  assert.deepEqual([load.props.disabled, load.props.title], [true, t('reason.head_unknown', { head: 'T1' })]);
  // The head card it opens says so at the top, above Find the lane in T1, apart from an empty head's words.
  const head = textOf(panel(unknown, { kind: 'head', id: '1' }).out);
  const top = 'T1 holds a filament: no lane known';
  assert.ok(head.indexOf(top) >= 0 && head.indexOf(top) < head.indexOf(t('action.probe_head', { head: 'T1' })), head);
  assert.notEqual(top, t('panel.head_none'));
  // The link shows whenever no lane is known in the head, also when another reason disables Load into T1 first.
  const batch = demo('mine');
  batch.ace2k_u1.current[1] = null;
  batch['gcode_macro AUTO_FEEDING_BATCH'] = { doing: true };
  const busy = panel(batch, { kind: 'bay', key: 'a:2' });
  assert.ok(button(busy.nodes, 'No lane known in T1 — open T1'));
  // With the lane known, no shortcut.
  const known = panel(demo('mine'), { kind: 'bay', key: 'a:2' });
  assert.ok(!textOf(known.out).includes('No lane known'));
});

test('the bay card: the subtype beside the material, Clear error in the red of a stop', () => {
  const s = demo('unknown');
  s.print_task_config.filament_sub_type[1] = 'Matte';
  s.ace2k.lanes[1] = { ...s.ace2k.lanes[1], mode: 'error', error: 'stuck' };
  const { out, nodes } = panel(s, { kind: 'bay', key: '2' });
  assert.ok(textOf(out).includes('PETG Matte'));
  assert.match(button(nodes, t('action.clear')).props.class, /\bstop\b/);
});

test("the spool editor closes when its lane stops offering Edit spool, as the old lane card's did", () => {
  const edit = { lane: 2, head: 1, type: 'PETG', vendor: 'Generic', color: '#111111', unit: null };
  const app = appOf(demo('unknown'), { edit });
  assert.equal(keptEdit(app), edit);
  // A tag that now speaks for the lane: the form goes, from the card and from the state.
  const tagged = demo('unknown');
  tagged.ace2k.lanes[1].tag = { state: 'read', record: { material: 'PLA' } };
  assert.equal(keptEdit(appOf(tagged, { edit })), null);
  assert.ok(!panel(tagged, { kind: 'bay', key: '2' }, { edit }).out.includes('spool-editor'));
  // An emptied bay, or a unit that is gone, drop it too; no form keeps nothing.
  const empty = demo('unknown');
  empty.ace2k.lanes[1].insert = false;
  assert.equal(keptEdit(appOf(empty, { edit })), null);
  assert.equal(keptEdit(appOf(demo('mine'), { edit: { ...edit, unit: 'zz' } })), null);
  assert.equal(keptEdit(appOf(demo('unknown'))), null);
  // Offline it stays: Save says why instead.
  assert.equal(keptEdit(appOf(demo('unknown'), { edit, online: false })), edit);
});

test('the spool editor: a disabled Save says why in a note; a pick with a free material', () => {
  const edit = { lane: 2, head: 1, type: 'PETG', vendor: 'Generic', color: '#111111', unit: null };
  const off = editor(demo('unknown'), '2', edit, { online: false });
  const save = button(off.nodes, t('action.save'));
  assert.deepEqual([save.props.disabled, save.props.title], [true, t('reason.offline')]);
  assert.deepEqual(
    elements(off.out, 'p.note.why').map((e) => e.text),
    [t('reason.offline')],
  );
  const on = editor(demo('unknown'), '2', edit);
  assert.equal(elements(on.out, 'p#spool-why').length, 0);
  // Opened for a colour whose material the U1 does not list: every material, and Save still makes the pick.
  const free = editor(demo('unknown'), '2', { ...edit, use: 2 });
  assert.equal(
    free.nodes.filter((n) => n.type === 'button' && n.props.class?.startsWith('mat')).length,
    FILAMENT_TYPES.length,
  );
  assert.ok(button(free.nodes, t('spool.save_use', { n: 3 })));
});

test('Escape belongs to the side panel only while nothing else is open over the page', () => {
  assert.equal(escapeCloses(appOf({})), true);
  assert.equal(escapeCloses(appOf({}, { modeWarn: true })), false);
  assert.equal(escapeCloses(appOf({}, { ui: { tab: 'units', panel: null, sheet: true } })), false);
  // The panel's own phone form is a bottom sheet too, but it is the panel: Escape still closes it.
  assert.equal(
    escapeCloses(appOf({}, { ui: { tab: 'units', panel: { kind: 'bay', key: '1' }, sheet: false, narrow: true } })),
    true,
  );
});

test('one rule for a lane in error: its tile, its row and its card mark it and it opens by itself, or none of that', () => {
  const calm = demo('unknown');
  calm.ace2k.lanes[0] = { ...calm.ace2k.lanes[0], mode: 'idle', error: null };
  /** @param {object} over */
  const with2 = (over) => {
    const s = structuredClone(calm);
    s.ace2k.lanes[1] = { ...s.ace2k.lanes[1], ...over };
    return s;
  };
  for (const [over, marked] of [
    [{ mode: 'idle', error: 'stuck' }, true], // a known error the mode does not say
    [{ mode: 'error', error: 'something_new' }, true], // the mode says it, with an error the page does not know
    [{ mode: 'idle', error: 'something_new' }, false],
    [{ mode: 'idle', error: null }, false],
  ]) {
    const app = appOf(with2(over));
    const tile = diagramModel(app).units[0].lanes[1];
    assert.equal(tile.error !== null, marked, JSON.stringify(over));
    assert.equal(autoPanel(app, {}) !== null, marked, JSON.stringify(over));
    const card = renderHtml(html`<${BayCard} app=${app} ctx=${{ app, t, run: () => {} }} at=${laneAt(app, '2')} />`);
    assert.equal(card.includes('state s-error'), marked, JSON.stringify(over));
  }
});

test('the spool editor marks its chosen material to take the focus', () => {
  const app = appOf(demo('unknown'));
  const mark = (type, lock) => {
    const edit = { lane: 2, head: null, type, vendor: '', color: '#ff0000', unit: null, lock };
    const out = renderHtml(
      html`<${SpoolEditor} app=${app} ctx=${{ app, t, run: () => {} }} at=${laneAt(app, '2')} edit=${edit} />`,
    );
    return [...out.matchAll(/<button[^>]*data-autofocus="true"[^>]*>([^<]*)</g)].map((m) => m[1]);
  };
  const types = FILAMENT_TYPES.map(String);
  assert.ok(types.length > 2);
  assert.deepEqual(mark(types[2]), [types[2]]); // not the first one
  assert.deepEqual(mark('nothing-listed'), [types[0]]); // none chosen: the first
  assert.deepEqual(mark('PLA-locked-elsewhere', true), ['PLA-locked-elsewhere']); // locked: its one button
});

test('the spool editor keeps the focus: in the form on open, on Edit spool when it closes', () => {
  const focused = [];
  const el = (name, tagName = 'BUTTON') => ({ tagName, focus: () => focused.push(name) });
  const inside = el('inside');
  const field = el('field', 'INPUT');
  const panel = el('panel');
  const found = { '.spool-editor [data-autofocus]': el('form'), '[data-id="edit_spool"]:not(:disabled)': el('edit') };
  const root = {
    contains: (x) => x === inside || x === field,
    querySelector: (q) => found[q] ?? null,
    closest: () => panel,
  };
  const body = el('body', 'BODY');
  const was = { current: false };
  const on = (open, active, r = root) => focusEditor(r, open, was, { activeElement: active, body });
  on(false, body); // no change: nothing
  on(true, el('edit')); // opens: into the form
  on(true, body); // a re-render: nothing
  on(false, body); // Cancel or Save done, the focus lost: Edit spool
  on(true, body);
  on(false, inside); // a live button of the card keeps it
  on(true, body);
  on(false, field); // so does a field of the card
  on(true, body);
  on(false, null); // lost with no element at all: Edit spool
  on(true, body);
  on(false, body, { ...root, querySelector: () => null }); // no Edit spool button: the panel
  const first = { ...root, querySelector: (q) => (q === '.spool-editor button' ? el('first') : null) };
  on(true, body, first); // no mark: the form's first button
  on(false, body, { ...root, querySelector: () => null });
  on(true, body, { ...root, querySelector: () => null }); // no form at all: the panel
  assert.deepEqual(focused, [
    'form',
    'edit',
    'form',
    'form',
    'form',
    'edit',
    'form',
    'panel',
    'first',
    'panel',
    'panel',
  ]);
  // A card that mounts with the form open does not move the focus (the panel's focusIn does).
  const mounted = { current: true };
  focusEditor(root, true, mounted, { activeElement: body, body });
  focusEditor(null, false, mounted);
  assert.equal(focused.length, 11);
});

test('the opts panel holds the start options', () => {
  const app = appWithPlan({ narrow: true, panel: { kind: 'opts' } });
  const ctx = { app, t, run: () => {} };
  const m = renderHtml(html`<${SidePanel} app=${app} ctx=${ctx} />`);
  assert.match(m, /class="side sheet/);
  assert.match(m, /data-kind="opts"/);
  assert.ok(m.includes(`<h2>${t('print.options')}</h2>`));
  assert.match(m, new RegExp(t('print.opt.bed_level')));
  assert.match(m, /<div class="opts" role="group"/);
  // With no plan open (the file was closed), the sheet says what it showed is gone.
  app.print = { ...app.print, file: null };
  const gone = textOf(renderHtml(html`<${SidePanel} app=${app} ctx=${ctx} />`));
  assert.ok(gone.includes(t('panel.gone')));
  assert.ok(!gone.includes(t('print.opt.bed_level')));
});
