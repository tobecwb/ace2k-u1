import { test } from 'node:test';
import assert from 'node:assert/strict';
import { html } from '../js/ui/html.js';
import { App, Shell, exitTimer } from '../js/ui/App.js';
import { TopBar, tabKey } from '../js/ui/TopBar.js';
import { bannerView, KLIPPY_STATES } from '../js/conn.js';
import { PanelFrame, contentOf } from '../js/ui/SidePanel.js';
import { ErrorBoundary } from '../js/ui/ErrorBoundary.js';
import { ACTIONS, runAction } from '../js/actions.js';
import { renderHtml, textOf } from './ui-helpers.js';
import { loadEnglish, lane, status as fullStatus, appWithPlan } from './helpers.js';
import { t } from '../js/i18n.js';

loadEnglish();
const fine = { ace2k: { version: '0.12.0', link_proven: true, health: { ok: true } } };
const base = {
  status: fine,
  online: true,
  klippy: 'ready',
  theme: 'system',
  ui: { tab: 'units', panel: null, sheet: false },
  print: { files: [] },
};
const ctx = { t, run: () => {}, app: base };

// Every element vnode of a component tree that matches pred. Function components are called with their props.
function vnodes(node, pred, out = []) {
  if (Array.isArray(node)) {
    for (const n of node) vnodes(n, pred, out);
    return out;
  }
  if (!node || typeof node !== 'object') return out;
  if (typeof node.type === 'function' && !node.type.prototype?.render) return vnodes(node.type(node.props), pred, out);
  if (pred(node)) out.push(node);
  vnodes(node.props?.children, pred, out);
  return out;
}

test('the top bar has four tabs and marks the active one', () => {
  const out = renderHtml(html`<${App} app=${base} ctx=${ctx} />`);
  for (const k of ['tab.units', 'tab.print', 'tab.dryer', 'tab.history']) assert.ok(textOf(out).includes(t(k)));
  assert.match(out, /aria-selected="true"[^>]*>[^<]*Units/);
  assert.equal(out.match(/aria-selected="true"/g).length, 1);
  assert.equal(out.match(/role="tab"/g).length, 4);
});

test('the active tab follows app.ui.tab; a tab with no view shows its placeholder', () => {
  const app = { ...base, ui: { ...base.ui, tab: 'print' } };
  const out = renderHtml(html`<${App} app=${app} ctx=${ctx} />`);
  assert.match(out, /aria-selected="true"[^>]*>[^<]*Print/);
  assert.match(
    out,
    /<main class="tab-body" id="tab-body" role="tabpanel" aria-labelledby="tab-print" data-tab="print">/,
  );
  const none = renderHtml(html`<${App} app=${{ ...base, ui: { ...base.ui, tab: 'nope' } }} ctx=${ctx} />`);
  assert.match(none, /data-tab="nope"><p class="placeholder">tab.nope<\/p><\/main>/);
});

test('the History tab shows the chart and the events', () => {
  const app = {
    ...base,
    ui: { ...base.ui, tab: 'history' },
    samples: [],
    events: [],
    hidden: [],
    filter: 'all',
    limit: 200,
    days: [],
    loaded: new Set(),
    chartUnit: null,
    hours: null,
    pending: new Set(),
    folds: {},
  };
  const out = renderHtml(html`<${App} app=${app} ctx=${ctx} />`);
  assert.match(out, /aria-selected="true"[^>]*>[^<]*History/);
  assert.ok(out.includes('<section class="history"'));
});

test('the Dryer tab shows its cards', () => {
  const app = {
    ...base,
    ui: { ...base.ui, tab: 'dryer' },
    presets: [],
    form: { preset: null, temp: 60, hm: '6:00' },
    forms: {},
    folds: {},
    pending: new Set(),
    samples: [],
    dryerUnit: null,
    modeWarn: false,
  };
  const out = renderHtml(html`<${App} app=${app} ctx=${{ ...ctx, app }} />`);
  assert.match(out, /<section class="dcard"/);
});

test('the pill shows the print progress', () => {
  const app = { ...base, status: { print_stats: { state: 'printing' }, virtual_sdcard: { progress: 0.53 } } };
  assert.ok(textOf(renderHtml(html`<${App} app=${app} ctx=${ctx} />`)).includes(t('top.printing', { pct: 53 })));
  assert.equal(t('top.printing', { pct: 53 }), '● Printing 53 %');
});

test('the pill: printing N %, paused with its reason, idle, offline', () => {
  const pill = (st, over = {}) =>
    textOf(renderHtml(html`<${App} app=${{ ...base, status: { ...fine, ...st }, ...over }} ctx=${ctx} />`));
  assert.ok(pill({}).includes(t('top.idle')));
  // No progress reported yet: no %; with Klipper's progress, its share floored.
  assert.ok(pill({ print_stats: { state: 'printing' } }).includes(t('top.printing_bare')));
  assert.ok(!pill({ print_stats: { state: 'printing' } }).includes(t('top.printing', { pct: 0 })));
  assert.ok(
    pill({ print_stats: { state: 'printing', filename: 'p.gcode' }, virtual_sdcard: { progress: 0.429 } }).includes(
      t('top.printing', { pct: 42 }),
    ),
  );
  const why = 'ace2k_u1: head 1: a:1 failed';
  assert.ok(
    pill({ print_stats: { state: 'paused' }, ace2k_u1: { pause_reason: why } }).includes(
      '● Paused — head 1: a:1 failed',
    ),
  );
  assert.ok(pill({ print_stats: { state: 'paused' } }).includes(t('top.paused_only')));
  assert.equal(t('top.paused_only'), '● Paused');
  assert.ok(pill({}, { online: false }).includes(t('top.offline')));
  assert.ok(pill({ print_stats: { state: 'printing' } }, { klippy: 'shutdown' }).includes(t('top.offline')));
});

test('the tabs, the pill, the theme button, the dot and Stop all call their actions through ctx.run', () => {
  const calls = [];
  const spy = { ...ctx, run: (id, data) => calls.push([id, data]) };
  const buttons = vnodes(TopBar({ app: base, ctx: spy }), (n) => n.type === 'button');
  for (const b of buttons) b.props.onClick();
  assert.deepEqual(calls, [
    ['tab', { tab: 'units' }],
    ['tab', { tab: 'print' }],
    ['tab', { tab: 'dryer' }],
    ['tab', { tab: 'history' }],
    ['tab', { tab: 'print' }],
    ['theme', {}],
    ['panel', { kind: 'status' }],
    ['stop_all', {}],
  ]);
});

test('the tabs: ids, aria-controls, only the active one in the Tab order; the arrow keys move between them', () => {
  const out = renderHtml(html`<${App} app=${{ ...base, ui: { ...base.ui, tab: 'print' } }} ctx=${ctx} />`);
  for (const id of ['units', 'print', 'dryer', 'history'])
    assert.match(
      out,
      new RegExp(`role="tab" id="tab-${id}" aria-controls="tab-body"[^>]*tabindex="${id === 'print' ? '0' : '-1'}"`),
    );
  assert.deepEqual(
    ['ArrowRight', 'ArrowLeft', 'Home', 'End', 'a'].map((k) => tabKey('units', k)),
    ['print', 'history', 'units', 'history', null],
  );
  assert.equal(tabKey('history', 'ArrowRight'), 'units');
  const calls = [];
  const focused = [];
  const spy = { ...ctx, run: (id, data) => calls.push([id, data]) };
  const [nav] = vnodes(TopBar({ app: base, ctx: spy }), (n) => n.type === 'nav');
  let prevented = 0;
  const key = (k) => nav.props.onKeyDown({ key: k, preventDefault: () => (prevented += 1) });
  key('x');
  key('ArrowRight');
  globalThis.document = { getElementById: (id) => ({ focus: () => focused.push(id) }) };
  try {
    key('End');
  } finally {
    delete globalThis.document;
  }
  assert.deepEqual(calls, [
    ['tab', { tab: 'print' }],
    ['tab', { tab: 'history' }],
  ]);
  assert.deepEqual([prevented, focused], [2, ['tab-history']]);
});

test('the health dot: green, amber with a failing self-test, red with a link down; its name says so', () => {
  const dot = (ace2k, over = {}) => {
    const out = renderHtml(html`<${TopBar} app=${{ ...base, status: { ace2k }, ...over }} ctx=${ctx} />`);
    const m = out.match(/<button type="button" class="health-dot (\w+)" aria-label="([^"]*)"/);
    return [m[1], m[2]];
  };
  assert.deepEqual(dot(fine.ace2k), ['ok', 'Link ok, Health ok']);
  assert.deepEqual(dot({ link_proven: true, health: { ok: false, failing: ['cutout'] } }), [
    'warn',
    'Link ok, Health: thermal cutout',
  ]);
  assert.deepEqual(dot({ link_proven: false, health: { ok: true } }), ['err', 'Link down, Health ok']);
  assert.equal(dot(fine.ace2k, { online: false })[0], 'err');
  const two = {
    ace2k_units: { units: ['ace2k a', 'ace2k b'] },
    'ace2k a': fine.ace2k,
    'ace2k b': { link_proven: true, health: { ok: false } },
  };
  const out = renderHtml(html`<${TopBar} app=${{ ...base, status: two }} ctx=${ctx} />`);
  assert.match(out, /class="health-dot warn" aria-label="Unit a: Link ok, Health ok; Unit b: Link ok, Health: \?"/);
});

test('Stop all is in every tab; disabled with the note when no lane can stop, or offline', () => {
  const stop = (st, over = {}) => {
    const out = renderHtml(html`<${App} app=${{ ...base, status: st, ...over }} ctx=${ctx} />`);
    return out.match(/<button type="button" class="btn stop"[^>]*>(<span[^>]*>[^<]*<\/span>)?/)[0];
  };
  for (const tab of ['units', 'print', 'dryer', 'history'])
    assert.match(
      stop(fine, { ui: { ...base.ui, tab } }),
      /^<button type="button" class="btn stop" aria-label="Stop all lanes"[^>]*><span class="stop-full">Stop all lanes/,
    );
  const idle = fullStatus({ print: 'printing', lanes: [lane(), lane(), lane(), lane()] });
  assert.doesNotMatch(stop(idle), /disabled/);
  const none = stop({ print_stats: { state: 'printing' } });
  assert.match(none, /disabled/);
  assert.match(none, new RegExp(`title="${t('top.stop_all_print')}"`));
  assert.match(stop(fine, { online: false }), /disabled/);
});

test('the status panel opens on panel kind status and shows the version, the link and the self-test in words', () => {
  assert.doesNotMatch(renderHtml(html`<${App} app=${base} ctx=${ctx} />`), /class="side"/);
  const open = { ...base, ui: { ...base.ui, panel: { kind: 'status' } } };
  const out = textOf(renderHtml(html`<${App} app=${open} ctx=${ctx} />`));
  for (const words of [t('top.status'), 'v0.12.0', t('top.link_ok'), t('top.health_ok')])
    assert.ok(out.includes(words), words);
  const bad = { ace2k: { link_proven: false, health: { ok: false, failing: ['clock'] } } };
  const sick = textOf(
    renderHtml(html`<${PanelFrame} panel=${open.ui.panel} app=${{ ...open, status: bad }} ctx=${ctx} />`),
  );
  assert.ok(sick.includes(t('top.link_down')) && sick.includes('Health: clock') && !sick.includes('v0'));
  const two = {
    ace2k_units: { units: ['ace2k a', 'ace2k b'] },
    'ace2k a': fine.ace2k,
    'ace2k b': fine.ace2k,
  };
  assert.ok(
    textOf(
      renderHtml(html`<${PanelFrame} panel=${open.ui.panel} app=${{ ...open, status: two }} ctx=${ctx} />`),
    ).includes('Unit b'),
  );
  const calls = [];
  const panel = PanelFrame({ panel: open.ui.panel, app: open, ctx: { ...ctx, run: (...a) => calls.push(a) } });
  const [close] = vnodes(panel, (n) => n.type === 'button');
  close.props.onClick();
  assert.deepEqual(calls, [['panel_close', {}]]);
});

test('the theme button shows the icon of the mode in use and is named for it', () => {
  for (const theme of ['system', 'light', 'dark']) {
    const out = renderHtml(html`<${App} app=${{ ...base, theme }} ctx=${ctx} />`);
    assert.match(out, new RegExp(`aria-label="${t(`top.theme_${theme}`)}"`));
    assert.match(out, new RegExp(`<svg [^>]*data-theme-icon="${theme}"`));
  }
});

test('ctx.run calls the action with el.dataset = the data object; unknown ids do nothing', () => {
  const seen = [];
  const real = ACTIONS.theme;
  ACTIONS.theme = (c, el) => seen.push([c, el.dataset, el.value, el.checked]);
  try {
    const data = { a: '1' };
    runAction(ctx, 'theme', data);
    assert.deepEqual(seen, [[ctx, { a: '1' }, '', false]]);
    assert.notEqual(seen[0][1], data, 'a copy, so an action never changes the caller’s object');
    runAction(ctx, 'theme');
    assert.deepEqual(seen[1][1], {});
  } finally {
    ACTIONS.theme = real;
  }
  assert.equal(runAction(ctx, 'no_such_action', {}), undefined);
  assert.equal(runAction(ctx, 'toString', {}), undefined);
});

test('ctx.run reaches a change handler too, with the value and the checked state', () => {
  const app = { ...base, print: { ...base.print } };
  runAction({ ...ctx, app, render: () => {} }, 'print_q', {}, 'a.gcode');
  assert.equal(app.print.q, 'a.gcode');
  const seen = [];
  const real = ACTIONS.theme;
  ACTIONS.theme = (_c, el) => seen.push([el.id, el.value, el.checked, el.blur()]);
  try {
    runAction(ctx, 'theme', {}, 'v', true);
  } finally {
    ACTIONS.theme = real;
  }
  assert.deepEqual(seen, [['theme', 'v', true, undefined]]);
});

test('a broken part shows its error and the rest renders', () => {
  const Bad = () => {
    throw new Error('boom');
  };
  const out = textOf(renderHtml(html`<div><${ErrorBoundary}><${Bad} /></${ErrorBoundary}><p>still</p></div>`));
  assert.ok(out.includes(t('ui.broken', { msg: 'boom' })));
  assert.ok(out.includes('still'));
  assert.equal(t('ui.broken', { msg: 'boom' }), 'This part failed: boom');
});

test('Try again clears the error; the tab body boundary is keyed by its tab', () => {
  const b = new ErrorBoundary({});
  const shown = b.render({ children: 'x' }, { error: new Error('boom') });
  const [retry] = vnodes(shown, (n) => n.type === 'button');
  assert.equal(retry.props.children, t('ui.retry'));
  let next = null;
  b.setState = (st) => {
    next = st;
  };
  retry.props.onClick();
  assert.deepEqual(next, { error: undefined });
  assert.equal(b.render({ children: 'x' }, {}), 'x');
  const [main] = vnodes(
    Shell({ app: { ...base, ui: { ...base.ui, tab: 'nope' } }, ctx, next: { shown: null, leaving: false } }),
    (n) => n.type === 'main',
  );
  assert.equal(main.props.children.key, 'nope');
});

test('ctx.run pins the lane of its data on the shown unit, as a click does', () => {
  const app = { ...base, status: { ace2k: { lanes: [lane(), lane(), lane(), lane()] } }, selected: null };
  const real = ACTIONS.theme;
  ACTIONS.theme = () => {};
  try {
    runAction({ ...ctx, app }, 'theme', { lane: '3' });
  } finally {
    ACTIONS.theme = real;
  }
  assert.deepEqual(app.selected, { unit: null, lane: 2 });
});

test('a part that works renders through its boundary unchanged', () => {
  assert.equal(renderHtml(html`<${ErrorBoundary}><p>fine</p></${ErrorBoundary}>`), '<p>fine</p>');
});

test('bannerView: demo, offline, Klipper not ready, ready', () => {
  assert.deepEqual(bannerView({ demo: true, online: false, klippy: 'demo_missing' }), {
    hidden: false,
    className: 'banner',
    key: 'conn.demo_missing',
    vars: {},
  });
  assert.deepEqual(bannerView({ demo: true, online: false, klippy: null }), {
    hidden: false,
    className: 'banner demo',
    key: 'conn.demo',
    vars: {},
  });
  assert.deepEqual(bannerView({ demo: false, online: false, klippy: 'ready' }), {
    hidden: false,
    className: 'banner',
    key: 'conn.offline',
    vars: {},
  });
  assert.deepEqual(bannerView({ demo: false, online: true, klippy: 'shutdown' }), {
    hidden: false,
    className: 'banner',
    key: 'klippy.state.shutdown',
    vars: {},
  });
  for (const state of KLIPPY_STATES.filter((x) => x !== 'ready'))
    assert.equal(bannerView({ demo: false, online: true, klippy: state }).key, `klippy.state.${state}`);
  assert.deepEqual(bannerView({ demo: false, online: true, klippy: 'weird' }), {
    hidden: false,
    className: 'banner',
    key: 'klippy.unknown',
    vars: { state: 'weird' },
  });
  assert.deepEqual(bannerView({ demo: false, online: true, klippy: 'page_error', message: 'boom' }), {
    hidden: false,
    className: 'banner',
    key: 'conn.page_error',
    vars: { message: 'boom' },
  });
  assert.equal(bannerView({ demo: false, online: true, klippy: 'page_error' }).vars.message, '…');
  assert.deepEqual(bannerView({ demo: false, online: true, klippy: null }), {
    hidden: false,
    className: 'banner',
    key: 'klippy.checking',
    vars: {},
  });
  assert.deepEqual(bannerView({ demo: false, online: true, klippy: 'ready' }), {
    hidden: true,
    className: null,
    key: null,
    vars: {},
  });
});

test('the tab body carries the side it entered from', () => {
  const app = { ...base, ui: { ...base.ui, tab: 'print', enter: 'right' } };
  const markup = renderHtml(html`<${App} app=${app} ctx=${ctx} />`);
  assert.match(markup, /class="tab-body"[^>]*data-enter="right"/);
});

test('an open panel is not leaving', () => {
  const app = { ...base, ui: { ...base.ui, panel: { kind: 'status' } } };
  const markup = renderHtml(html`<${App} app=${app} ctx=${ctx} />`);
  assert.match(markup, /<aside class="side"/);
  assert.doesNotMatch(markup, /leaving/);
});

test("a phone's bottom sheet has a scrim that fades with it and closes it on a tap; a desktop panel has none", () => {
  const runs = [];
  const c = { ...ctx, run: (...a) => runs.push(a) };
  const narrow = { ...base, ui: { ...base.ui, narrow: true, panel: { kind: 'status' } } };
  // The scrim is the shell's own child (not inside the panel's boundary): found without rendering the panel.
  const scrimOf = (app, next) => {
    const kids = [Shell({ app, ctx: c, next }).props.children].flat(Infinity);
    const node = kids.find((k) => k && typeof k.type === 'function' && k.type.name === 'Scrim');
    return node ? node.type(node.props) : null;
  };
  const open = scrimOf(narrow, { shown: { kind: 'status' }, leaving: false });
  assert.equal(open.props.class, 'scrim');
  assert.equal(open.props['aria-hidden'], 'true');
  open.props.onClick();
  assert.deepEqual(runs, [['panel_close', {}]]);
  assert.equal(scrimOf(narrow, { shown: { kind: 'status' }, leaving: true }).props.class, 'scrim leaving');
  assert.equal(scrimOf({ ...narrow, ui: { ...narrow.ui, panel: null } }, { shown: null, leaving: false }), null);
  const wide = { ...base, ui: { ...base.ui, panel: { kind: 'status' } } };
  assert.equal(scrimOf(wide, { shown: { kind: 'status' }, leaving: false }), null);
  assert.match(renderHtml(html`<${App} app=${narrow} ctx=${ctx} />`), /<div class="scrim" aria-hidden="true">/);
});

test('a panel frame that is not leaving has no leaving class and is not inert', () => {
  const markup = renderHtml(
    html`<${PanelFrame} panel=${{ kind: 'status' }} app=${base} ctx=${ctx} leaving=${false} />`,
  );
  assert.match(markup, /class="side"/);
  assert.doesNotMatch(markup, /leaving|inert/);
});

test('a panel frame that is leaving carries the class and is inert', () => {
  const markup = renderHtml(html`<${PanelFrame} panel=${{ kind: 'status' }} app=${base} ctx=${ctx} leaving=${true} />`);
  assert.match(markup, /class="side leaving"/);
  assert.match(markup, /inert/);
});

test('a leaving panel keeps the card it showed last, so its exit never shows "gone" once its data went', () => {
  const app = appWithPlan();
  const c = { ...ctx, app };
  const last = { current: null };
  const open = contentOf({ kind: 'opts' }, app, c, false, last);
  assert.equal(last.current, open, 'an open panel remembers what it shows');
  assert.doesNotMatch(renderHtml(html`<div>${open.body}</div>`), new RegExp(t('panel.gone')));
  // "← Files": the plan's file is gone, and the options panel closes.
  const after = { ...app, print: { ...app.print, file: null } };
  assert.equal(contentOf({ kind: 'opts' }, after, c, true, last), open);
  const leaving = renderHtml(
    html`<${PanelFrame} panel=${{ kind: 'opts' }} app=${after} ctx=${c} leaving=${true} last=${{ current: open }} />`,
  );
  assert.doesNotMatch(leaving, new RegExp(t('panel.gone')));
  // With nothing remembered, the panel shows what it has now; a leaving panel does not overwrite what it remembers.
  const none = { current: null };
  const fresh = contentOf({ kind: 'opts' }, after, c, true, none);
  assert.equal(none.current, null);
  assert.match(renderHtml(html`<div>${fresh.body}</div>`), new RegExp(t('panel.gone')));
});

test('exitTimer runs done after the motion time, can be cancelled, and does nothing unless leaving', async () => {
  assert.equal(
    exitTimer(false, () => assert.fail('no')),
    undefined,
  );
  let done = 0;
  const stop = exitTimer(
    true,
    () => {
      done += 1;
    },
    () => ({ matches: true }),
  );
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(done, 1);
  stop();
  const cancel = exitTimer(
    true,
    () => assert.fail('cancelled'),
    () => ({ matches: false }),
  );
  cancel();
  await new Promise((r) => setTimeout(r, 200));
});
