import { test } from 'node:test';
import assert from 'node:assert/strict';
import { options } from 'preact';
import { html } from '../js/ui/html.js';
import { App } from '../js/ui/App.js';
import {
  ReadingView,
  OtherDevice,
  barWords,
  readingEffects,
  sizeOf,
  startTick,
  READ_TICK_MS,
} from '../js/ui/ReadingView.js';
import { renderHtml, textOf } from './ui-helpers.js';
import { loadEnglish, appWithPlan } from './helpers.js';
import { ACTIONS } from '../js/actions.js';
import { t } from '../js/i18n.js';

loadEnglish();

const card = {
  colours: [
    { colour: '#FFFFFF', material: 'PLA', vendor: null },
    { colour: '#D32C34', material: 'PETG', vendor: null },
  ],
  time: 5760,
  thumb: 'data:x',
  modified: 1,
};
/** A page that chose big.gcode while the host's plan still names p.gcode. */
const appReading = (extra = {}, size = 254594261) => {
  const app = appWithPlan();
  app.print = {
    ...app.print,
    file: 'big.gcode',
    files: [{ path: 'big.gcode', modified: 1, size }],
    cards: { 'big.gcode': { modified: 1, card } },
    reading: { file: 'big.gcode', at: 0, ...extra },
  };
  app.pending = new Set(['print:file']);
  app.status.ace2k_u1.heads = [
    [
      { unit: null, lane: 1 },
      { unit: null, lane: 2 },
    ],
    null,
    null,
    null,
  ];
  return app;
};
const ctx = { t, run: () => {}, app: null };

test('barWords: hidden, seconds left, still reading, elapsed only', () => {
  assert.equal(barWords(t, 254594261, 100), null);
  assert.equal(barWords(t, 254594261, 5000), t('print.reading_left', { time: '0:08' }));
  assert.equal(barWords(t, 254594261, 20000), t('print.still_reading'));
  assert.equal(barWords(t, null, 4200), t('print.reading_elapsed', { time: '0:04' }));
});

test('the reading view shows the name, the card, the colours and the bar', () => {
  const m = renderHtml(html`<${ReadingView} app=${appReading()} ctx=${ctx} nowMs=${5000} />`);
  const text = textOf(m);
  assert.ok(text.includes('big'));
  assert.ok(text.includes(t('print.slicer_time', { time: '1:36' })));
  assert.ok(text.includes(t('print.colour_n', { n: 2 })));
  assert.ok(text.includes('PETG'));
  assert.ok(text.includes(t('print.reading_left', { time: '0:08' })));
  assert.match(m, /class="rbar"/);
  assert.match(m, /width:\d+/);
  assert.match(m, /class="btn go"[^>]*disabled/);
  assert.match(m, /class="plan-dia"/);
});

test('no bar before the show delay; no size gives the time elapsed only, nothing moving; no colours, no caption', () => {
  const early = renderHtml(html`<${ReadingView} app=${appReading()} ctx=${ctx} nowMs=${50} />`);
  assert.doesNotMatch(early, /class="rbar"/);
  assert.match(early, /class="rslot"/, 'the bar has its place before it shows');
  const app = appReading({}, null);
  app.print = { ...app.print, cards: {}, files: [] };
  app.ui = { ...app.ui, narrow: true };
  const m = renderHtml(html`<${ReadingView} app=${app} ctx=${ctx} nowMs=${4200} />`);
  assert.doesNotMatch(m, /class="track"/);
  assert.doesNotMatch(m, /class="run"/);
  assert.ok(textOf(m).includes(t('print.reading_elapsed', { time: '0:04' })));
  assert.doesNotMatch(m, /plan-dia/);
  assert.doesNotMatch(m, /class="crow /);
  assert.doesNotMatch(m, /<p class="cap"/);
  assert.ok(!textOf(m).includes(t('print.colours_cap')));
  assert.match(m, /pbar compact/);
  // A card with an empty colour list: the header and the bar only, too.
  const empty = appReading();
  empty.print.cards['big.gcode'] = { modified: 1, card: { ...card, colours: [] } };
  assert.doesNotMatch(renderHtml(html`<${ReadingView} app=${empty} ctx=${ctx} nowMs=${5000} />`), /<p class="cap"/);
});

test("the size: Moonraker's list, else the card's G-code bytes, else none", () => {
  const app = appReading({}, null);
  assert.equal(sizeOf(app, 'big.gcode'), null);
  app.print.cards['big.gcode'] = { modified: 1, card: { ...card, gcodeStart: 4000, gcodeEnd: 90000000 } };
  assert.equal(sizeOf(app, 'big.gcode'), 90000000);
  const m = renderHtml(html`<${ReadingView} app=${app} ctx=${ctx} nowMs=${2000} />`);
  assert.ok(textOf(m).includes(t('print.reading_left', { time: '0:03' })));
  assert.match(m, /class="track"/);
  app.print.files = [{ path: 'big.gcode', modified: 1, size: 254594261 }];
  assert.equal(sizeOf(app, 'big.gcode'), 254594261);
  app.print.files = [];
  app.print.cards['big.gcode'].card.gcodeEnd = 0;
  assert.equal(sizeOf(app, 'big.gcode'), null);
  app.print.cards = undefined;
  assert.equal(sizeOf(app, 'big.gcode'), null);
});

test('a refusal shows the host message in place of the bar', () => {
  const msg = 'no slicer config block: slice the file with OrcaSlicer';
  const m = renderHtml(html`<${ReadingView} app=${appReading({ error: msg })} ctx=${ctx} nowMs=${5000} />`);
  assert.ok(textOf(m).includes(msg));
  assert.match(m, /plan-hint err/);
  assert.equal(m.split(t('print.back')).length - 1, 1, 'only the header has Files');
  assert.doesNotMatch(m, /class="rbar"/);
  assert.doesNotMatch(m, /class="btn go"/);
});

test('the other-device notice names both files and asks to plan again', () => {
  const m = renderHtml(html`<${OtherDevice} ctx=${ctx} mine="mine.gcode" theirs="theirs.gcode" />`);
  const text = textOf(m);
  assert.ok(text.includes(t('print.other_device', { file: 'theirs' })));
  assert.ok(text.includes(t('print.plan_again', { file: 'mine' })));
  assert.ok(text.includes(t('print.back')));
});

test('the tick starts and stops', (c) => {
  c.mock.timers.enable({ apis: ['setInterval'] });
  let n = 0;
  const stop = startTick(() => n++);
  c.mock.timers.tick(READ_TICK_MS * 3);
  assert.equal(n, 3);
  stop();
  c.mock.timers.tick(READ_TICK_MS * 3);
  assert.equal(n, 3);
});

test('App picks the reading view, then the notice, then the grid', () => {
  const app = appReading();
  const m = renderHtml(html`<${App} app=${app} ctx=${{ ...ctx, app }} />`);
  assert.match(m, /class="plan reading"/);
  const other = appReading();
  other.pending = new Set();
  other.print = { ...other.print, reading: null };
  assert.match(renderHtml(html`<${App} app=${other} ctx=${{ ...ctx, app: other }} />`), /class="plan other"/);
  other.print = { ...other.print, file: null };
  assert.doesNotMatch(
    renderHtml(html`<${App} app=${other} ctx=${{ ...ctx, app: other }} />`),
    /plan other|plan reading/,
  );
});

test('print_back clears the reading', () => {
  const app = appReading();
  const c = { app, redraw: () => {}, render: () => {} };
  ACTIONS.print_back({ ...c, redraw: (_x, fn) => fn(app) }, {});
  assert.equal(app.print.reading, null);
});

/** The buttons of a render, by their text, so a test can press them. */
function pressed(vnode) {
  const buttons = [];
  const prev = options.vnode;
  options.vnode = (v) => {
    if (v.type === 'button') buttons.push(v);
    prev?.(v);
  };
  try {
    renderHtml(vnode);
  } finally {
    options.vnode = prev;
  }
  return buttons;
}

test('the buttons ask for the files and for the plan again', () => {
  const runs = [];
  const c = { t, run: (...a) => runs.push(a) };
  for (const b of pressed(html`<${ReadingView} app=${appReading()} ctx=${c} nowMs=${5000} />`)) b.props.onClick?.();
  for (const b of pressed(html`<${OtherDevice} ctx=${c} mine="a.gcode" theirs="b.gcode" />`)) b.props.onClick();
  assert.deepEqual(
    runs.map((r) => r[0]),
    ['print_back', 'plan_again', 'print_back'],
  );
});

test('the effects: the tick redraws until stopped, none after a refusal; the scroll happens once', (c) => {
  c.mock.timers.enable({ apis: ['setInterval'] });
  const app = appReading();
  const updates = [];
  const seen = { current: null };
  const scrolled = [];
  const root = {
    current: { querySelector: () => ({ scrollIntoView: () => scrolled.push(1) }), querySelectorAll: () => [] },
  };
  const fx = readingEffects(app, root, seen, app.print.reading, (u) => updates.push(u(1)));
  const stop = fx.tick();
  c.mock.timers.tick(READ_TICK_MS);
  assert.deepEqual(updates, [2]);
  stop();
  fx.scroll();
  fx.scroll();
  assert.equal(scrolled.length, 1);
  assert.equal(app.print.scrolled, 'big.gcode');
  assert.equal(readingEffects(app, root, seen, { ...app.print.reading, error: 'no' }, () => {}).tick(), undefined);
  // The scroll goes to the top of the left column, with or without colours on the card: colours that come after the
  // scroll then show below the screen's top edge, never above it.
  const asked = [];
  const find = (sel) => {
    asked.push(sel);
    return { scrollIntoView: () => {} };
  };
  const other = { current: { querySelector: find } };
  const bare = appReading();
  readingEffects(bare, other, { current: null }, bare.print.reading, () => {}).scroll();
  assert.deepEqual(asked, ['.plan-left']);
});

test('the left column starts with the colours caption, or with the bar on a card with no colours (the scroll target)', () => {
  const withColours = renderHtml(html`<${ReadingView} app=${appReading()} ctx=${ctx} nowMs=${5000} />`);
  assert.match(withColours, /<div class="plan-left"><p class="cap">/);
  const app = appReading();
  app.print = { ...app.print, cards: {} };
  const bare = renderHtml(html`<${ReadingView} app=${app} ctx=${ctx} nowMs=${5000} />`);
  assert.match(bare, /<div class="plan-left"><div class="rslot">/);
});
