import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { options } from 'preact';
import { html } from '../js/ui/html.js';
import { MobileLists, listMode } from '../js/ui/MobileLists.js';
import { App } from '../js/ui/App.js';
import { PlanView } from '../js/ui/PlanView.js';
import { ACTIONS } from '../js/actions.js';
import { renderHtml, textOf } from './ui-helpers.js';
import { appWithPlan, loadEnglish } from './helpers.js';
import { t } from '../js/i18n.js';

loadEnglish();

const demo = (name) => JSON.parse(readFileSync(new URL(`../dev/demo-${name}.json`, import.meta.url), 'utf8'));
const appOf = (status, ui = {}) => ({
  status,
  online: true,
  klippy: 'ready',
  ui: { tab: 'units', panel: null, sheet: false, narrow: true, ...ui },
});
const draw = (app, run = () => {}) => renderHtml(html`<${MobileLists} app=${app} ctx=${{ app, t, run }} />`);

// Every element vnode of a component tree that matches pred. Function components are called with their props.
function vnodes(node, pred, out = []) {
  if (Array.isArray(node)) {
    for (const n of node) vnodes(n, pred, out);
    return out;
  }
  if (!node || typeof node !== 'object') return out;
  if (typeof node.type === 'function') return vnodes(node.type(node.props), pred, out);
  if (pred(node)) out.push(node);
  vnodes(node.props?.children, pred, out);
  return out;
}

test('on a phone the Units tab is the lists, not the diagram', () => {
  const app = appOf(demo('mine'));
  const out = renderHtml(html`<${App} app=${app} ctx=${{ app, t, run: () => {} }} />`);
  assert.match(out, /class="mlists"/);
  assert.doesNotMatch(out, /class="dg"/);
  const wide = appOf(demo('mine'), { narrow: false });
  const out2 = renderHtml(html`<${App} app=${wide} ctx=${{ app: wide, t, run: () => {} }} />`);
  assert.match(out2, /class="dg"/);
  assert.doesNotMatch(out2, /class="mlists"/);
});

test('by head: T0 has the U1 spool, T1 the lanes a:1-a:4 with the one in the head in blue', () => {
  const out = draw(appOf(demo('mine')));
  const text = textOf(out);
  for (const h of ['T0', 'T1', 'T2', 'T3']) assert.ok(text.includes(h), h);
  assert.ok(text.includes(t('units.u1_spool')));
  assert.ok(text.includes(t('list.lanes', { n: 4 })));
  for (const n of [1, 2, 3, 4]) assert.ok(out.includes(`<span class="mut">a</span>${n}`), `a:${n}`);
  assert.equal(out.match(/class="mrow on/g).length, 3);
  assert.ok(text.includes(t('panel.in_head')));
  assert.ok(text.includes(t('list.waiting')));
  assert.ok(!text.includes('→ T'));
});

test('by unit: an amber block per unit with the lanes and their "→ T1" chips', () => {
  const out = draw(appOf(demo('mine'), { listMode: 'unit' }));
  const text = textOf(out);
  assert.equal(out.match(/class="mblock unit"/g).length, 3);
  assert.ok(text.includes(t('units.name', { name: 'a' })));
  assert.ok(text.includes('→ T1'));
  assert.ok(text.includes('→ T2'));
  assert.ok(text.includes(t('units.dry')));
});

test('a lane in error shows a red mark and the short error word on its row', () => {
  // demo-unknown: lane 1 is stuck.
  const out = draw(appOf(demo('unknown')));
  assert.match(out, /class="mrow[^"]* err"/);
  assert.match(out, /<span class="merr"><b class="merr-mark">!<\/b><span class="merr-text">Stuck<\/span><\/span>/);
  const odd = demo('unknown');
  odd.ace2k.lanes[0].error = 'brand_new';
  assert.match(draw(appOf(odd)), /<\/b><span class="merr-text">Error<\/span>/);
  const calm = demo('unknown');
  calm.ace2k.lanes[0] = { ...calm.ace2k.lanes[0], mode: 'feeding', error: null };
  assert.ok(!draw(appOf(calm)).includes('merr'));
});

test('a one-unit set-up has plain lane numbers, no unit tag, and "1 lane" for a head with one', () => {
  const out = draw(appOf(demo('one')));
  assert.ok(!out.includes('class="mut"'));
  assert.ok(textOf(out).includes(t('list.lane_one')) || textOf(out).includes(t('list.lanes', { n: 4 })));
});

test('the toggle and the rows run the page actions', () => {
  const seen = [];
  const app = appOf(demo('mine'));
  const tree = html`<${MobileLists} app=${app} ctx=${{ app, t, run: (id, data) => seen.push([id, data]) }} />`;
  const buttons = vnodes(tree, (n) => n.type === 'button');
  for (const b of buttons) b.props.onClick();
  assert.deepEqual(seen.slice(0, 2), [
    ['list_mode', { mode: 'head' }],
    ['list_mode', { mode: 'unit' }],
  ]);
  assert.ok(seen.some(([id, d]) => id === 'panel' && d.kind === 'head'));
  assert.ok(seen.some(([id, d]) => id === 'panel' && d.kind === 'bay' && d.key === 'a:1'));
  const unit = appOf(demo('mine'), { listMode: 'unit' });
  const seen2 = [];
  for (const b of vnodes(
    html`<${MobileLists} app=${unit} ctx=${{ app: unit, t, run: (id, data) => seen2.push([id, data]) }} />`,
    (n) => n.type === 'button',
  ))
    b.props.onClick();
  assert.ok(seen2.some(([id, d]) => id === 'panel' && d.kind === 'unit' && d.id === 'a'));
  assert.ok(seen2.some(([id, d]) => id === 'dry_shortcut' && d.unit === 'a'));
});

test('list_mode keeps head or unit and redraws', () => {
  const app = appOf({});
  let drawn = 0;
  const ctx = { app, render: () => (drawn += 1) };
  ACTIONS.list_mode(ctx, { dataset: { mode: 'unit' } });
  assert.equal(app.ui.listMode, 'unit');
  assert.equal(listMode(app), 'unit');
  ACTIONS.list_mode(ctx, { dataset: { mode: 'x' } });
  assert.equal(listMode(app), 'head');
  assert.equal(drawn, 2);
  assert.equal(listMode({ ui: {} }), 'head');
});

test('the phone rules: the tabs are a bottom bar, the lists show, and nothing is wider than the screen', () => {
  const css = readFileSync(new URL('../css/mobile.css', import.meta.url), 'utf8');
  assert.match(css, /@media \(max-width: 700px\)/);
  assert.match(css, /\.top \.tabs \{[^}]*position: fixed;[^}]*bottom: 0;/);
  assert.match(css, /\.mlists \{\s*display: none;/);
});

test('the phone stacking: a toast clears the tab bar and rides above a sheet; the notch and the home indicator are cleared', () => {
  const css = readFileSync(new URL('../css/mobile.css', import.meta.url), 'utf8');
  const z = (sel) => Number(css.match(new RegExp(`${sel.replace('.', '\\.')} \\{[^}]*z-index: (\\d+)`))[1]);
  assert.ok(z('.toast') > z('.side.sheet') && z('.side.sheet') > z('.top .tabs'));
  assert.match(
    css,
    /\.toast \{\s*bottom: calc\(var\(--control-h-touch\) \+ 24px \+ env\(safe-area-inset-bottom, 0px\)\);/,
  );
  assert.match(css, /\.top \.tabs \{[^}]*env\(safe-area-inset-bottom, 0px\)/);
  assert.match(
    readFileSync(new URL('../index.html', import.meta.url), 'utf8'),
    /<meta name="viewport"[^>]*viewport-fit=cover/,
  );
  assert.match(css, /\.mmat \{[^}]*text-overflow: ellipsis/);
});

test('Stop all has a short word for the phone and keeps its full label as its name', () => {
  const app = appOf(demo('mine'));
  const out = renderHtml(html`<${App} app=${app} ctx=${{ app, t, run: () => {} }} />`);
  assert.equal(out.match(/class="btn stop"/g).length, 1);
  assert.match(out, /class="btn stop"[^>]*aria-label="Stop every unit(&#39;|')s lanes"/);
  assert.match(out, /<span class="stop-short" aria-hidden="true">Stop<\/span>/);
  const css = readFileSync(new URL('../css/mobile.css', import.meta.url), 'utf8');
  const at = css.indexOf('@media (max-width: 700px)');
  const [base, phone] = [css.slice(0, at), css.slice(at)];
  assert.match(base, /\.stop-short \{\s*display: none;\s*\}/);
  assert.doesNotMatch(base, /\.stop-full/);
  assert.match(phone, /\.stop-full \{\s*display: none;/);
  assert.match(phone, /\.stop-short \{\s*display: inline;/);
});

test('the insets hold in any orientation: the base rules of the shell, banner and side panel carry them', () => {
  const css = readFileSync(new URL('../css/mobile.css', import.meta.url), 'utf8');
  const base = css.slice(0, css.indexOf('@media (max-width: 700px)'));
  for (const sel of ['.shell', '.banner', '.side'])
    for (const edge of ['top', 'right'])
      assert.match(
        base,
        new RegExp(`\\${sel} \\{[^}]*max\\(\\d+px, env\\(safe-area-inset-${edge}, 0px\\)\\)`),
        `${sel} ${edge}`,
      );
  assert.match(base, /\.shell \{[^}]*env\(safe-area-inset-bottom[^}]*env\(safe-area-inset-left/);
  assert.match(base, /\.banner \{[^}]*env\(safe-area-inset-left/);
});

test('an overlong error word and head chip are cut with an ellipsis, and the event table scrolls by keyboard', () => {
  const css = readFileSync(new URL('../css/mobile.css', import.meta.url), 'utf8');
  assert.match(css, /\.mhd \{[^}]*white-space: nowrap/s);
  assert.match(css, /\.merr-text \{[^}]*text-overflow: ellipsis/s);
});

test('on a phone, the head row whose sheet is open is marked, and only it', () => {
  const out = draw(appOf(demo('mine'), { panel: { kind: 'head', id: '1' } }));
  assert.equal((out.match(/class="mheadrow sel"/g) ?? []).length, 1);
  assert.match(out, /class="mheadrow sel" aria-current="true" aria-label="T1/);
  assert.ok(!draw(appOf(demo('mine'))).includes('mheadrow sel'));
  const css = readFileSync(new URL('../css/mobile.css', import.meta.url), 'utf8');
  assert.match(css, /\.mrow\.sel,\n {2}\.mheadrow\.sel \{/);
});

test('phone: the comparison on one line, Options in the bar, no options in the flow', () => {
  const runs = [];
  const app = appWithPlan({ narrow: true });
  const ctx = { app, t, run: (id, data) => runs.push([id, data]) };
  const m = renderHtml(html`<${PlanView} app=${app} ctx=${ctx} />`);
  assert.match(m, /class="cmp line"/);
  assert.doesNotMatch(m, /class="cmp-side/);
  assert.doesNotMatch(m, /class="opts/);
  assert.doesNotMatch(m, /class="tgl"/);
  assert.match(m, new RegExp(t('print.options_btn')));
  // The button sits in the compact bar and opens the options sheet.
  const bar = m.slice(m.indexOf('<section class="pbar compact"'));
  assert.ok(bar.includes(t('print.options_btn')));
  const seen = [];
  const prev = options.vnode;
  options.vnode = (v) => {
    if (v.type === 'button' && v.props['aria-haspopup'] === 'dialog') seen.push(v);
    prev?.(v);
  };
  try {
    renderHtml(html`<${PlanView} app=${app} ctx=${ctx} />`);
  } finally {
    options.vnode = prev;
  }
  seen[0].props.onClick();
  assert.deepEqual(runs, [['panel', { kind: 'opts' }]]);
  // A desktop keeps the two boxes and the options in the bar.
  const wide = renderHtml(html`<${PlanView} app=${appWithPlan()} ctx=${ctx} />`);
  assert.doesNotMatch(wide, /class="cmp line"/);
  assert.match(wide, /class="cmp-side/);
  assert.match(wide, /<div class="opts" role="group"/);
  assert.ok(!wide.includes(t('print.options_btn')));
});

test('the phone stylesheet sets the one-line comparison', () => {
  const css = readFileSync(new URL('../css/mobile.css', import.meta.url), 'utf8');
  assert.match(css, /\.cmp\.line \{\s*margin: 0;\s*font-size: var\(--fs-md\);\s*color: var\(--ink-2\);/);
});
