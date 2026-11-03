import { test } from 'node:test';
import assert from 'node:assert/strict';
import { html } from '../js/ui/html.js';
import { readFileSync } from 'node:fs';
import { FileGrid, gridView, observeCards, watchCards, cardsKey, openLater } from '../js/ui/FileGrid.js';
import { App } from '../js/ui/App.js';
import { openPrint } from '../js/printflow.js';
import { sortFiles, filterFiles, fitOf } from '../js/filegrid.js';
import { CLOSE, colourDistance, withinClose } from '../js/print-plan.js';
import { stepDone } from '../js/steps.js';
import { runAction } from '../js/actions.js';
import { renderHtml, textOf } from './ui-helpers.js';
import { loadEnglish } from './helpers.js';
import { t } from '../js/i18n.js';

loadEnglish();

const files = [
  { path: 'b-old.gcode', modified: 10 },
  { path: 'A-new.gcode', modified: 30 },
  { path: 'c-mid.gcode', modified: 20 },
];
const lanes = [
  { key: '1', colour: '#ff0000', material: 'PLA', empty: false, head: 0 },
  { key: '2', colour: '#0000ff', material: 'PETG', empty: false, head: 1 },
  { key: '3', colour: '#00ff00', material: null, empty: false, head: 2, unknown: true },
  { key: '4', colour: null, material: null, empty: true, head: 3 },
];

test('sortFiles: newest first, or by name without the case; the input is not changed', () => {
  assert.deepEqual(
    sortFiles(files, 'new').map((f) => f.path),
    ['A-new.gcode', 'c-mid.gcode', 'b-old.gcode'],
  );
  assert.deepEqual(
    sortFiles(files, 'name').map((f) => f.path),
    ['A-new.gcode', 'b-old.gcode', 'c-mid.gcode'],
  );
  assert.equal(files[0].path, 'b-old.gcode');
});

test('filterFiles: case-insensitive on the name; empty text keeps all', () => {
  assert.deepEqual(
    filterFiles(files, ' NEW ').map((f) => f.path),
    ['A-new.gcode'],
  );
  assert.equal(filterFiles(files, ''), files);
});

test('fitOf: counts the file colours with no same-material spool close by', () => {
  const cols = [
    { colour: '#ee1111', material: 'PLA', vendor: null },
    { colour: '#0000ff', material: 'PLA', vendor: null }, // blue is PETG in the units
  ];
  assert.equal(fitOf(cols, lanes), 1);
  assert.equal(fitOf([{ colour: '#00ff00', material: 'PLA', vendor: null }], lanes), 1); // unknown spool
  assert.equal(fitOf([{ colour: '#ff0000', material: null, vendor: null }], lanes), 0);
  assert.equal(fitOf([], lanes), 0);
});

const card = (colours, time) => ({ modified: 1, card: { colours, time, thumb: null, modified: 1 } });
const status = {
  ace2k: {
    lanes: [
      {
        insert: true,
        mode: 'idle',
        tag: { state: 'read', uid: '04AB', record: { material: 'PLA', color_rgba: 'FF0000FF' } },
      },
    ],
  },
};

/** @param {object} print */
const view = (print, calls = []) => {
  const app = { status, print: { files, ...print } };
  const ctx = { t, run: (...a) => calls.push(a), app };
  return renderHtml(html`<${FileGrid} app=${app} ctx=${ctx} />`);
};

test('the grid lists the cards newest first with count, search and sort chips', () => {
  const out = view({});
  assert.ok(textOf(out).includes('Files: 3'));
  assert.ok(out.indexOf('A-new') < out.indexOf('c-mid') && out.indexOf('c-mid') < out.indexOf('b-old'));
  assert.match(out, /class="chip on"[^>]*aria-pressed="true"[^>]*>Newest/);
  assert.ok(out.includes('placeholder="Search files"'));
  const named = view({ sort: 'name', q: 'c-' });
  assert.ok(textOf(named).includes('Files: 1') && named.includes('c-mid') && !named.includes('A-new'));
  assert.ok(textOf(view({ files: [] })).includes('No G-code files.'));
});

test('a loaded card shows the time, the colours, the dots and the fit line; an unknown one shows none', () => {
  const cols = [
    { colour: '#ff0000', material: 'PLA', vendor: null },
    { colour: '#123456', material: 'PLA', vendor: null },
  ];
  const out = view({
    files: [files[1], files[0], files[2]],
    cards: {
      'A-new.gcode': card(cols, 5760),
      'b-old.gcode': card(null, 60),
      'c-mid.gcode': card([cols[0]], 120),
    },
  });
  assert.ok(textOf(out).includes('1:36 · 2 colors'));
  assert.ok(textOf(out).includes('1 color'));
  assert.ok(out.includes('title="PLA #123456"'));
  assert.equal(out.match(/class="fdots"/g).length, 2);
  assert.equal(out.match(/ffit/g).length, 2);
  assert.ok(textOf(out).includes('All colors are in the units'));
});

test('a card with a thumbnail shows it lazily; a card with no colour material has a plain title', () => {
  const out = view({
    files: [files[0]],
    cards: {
      'b-old.gcode': {
        modified: 1,
        card: {
          colours: [{ colour: '#ffffff', material: null, vendor: null }],
          time: null,
          thumb: '/t.png',
          modified: 1,
        },
      },
    },
  });
  assert.match(out, /<img loading="lazy" src="\/t.png"/);
  assert.ok(out.includes('title="#FFFFFF"'));
});

test('clicking a card plans the file; the chips and the search call their actions', () => {
  const calls = [];
  const app = { status, print: { files: [files[0]] } };
  const ctx = { t, run: (...a) => calls.push(a), app };
  const walk = (n, out = []) => {
    if (Array.isArray(n)) for (const x of n) walk(x, out);
    else if (n && typeof n === 'object') {
      if (typeof n.type === 'function') walk(n.type(n.props), out);
      else out.push(n);
      walk(n.props?.children, out);
    }
    return out;
  };
  const all = walk(gridView(app, ctx, null));
  all.find((n) => n.type === 'button' && n.props['data-file']).props.onClick();
  all.filter((n) => n.props?.class?.startsWith('chip'))[1].props.onClick();
  all.find((n) => n.type === 'input').props.onInput({ target: { value: 'x' } });
  assert.deepEqual(calls, [
    ['file_pick', { file: 'b-old.gcode' }],
    ['print_sort', { sort: 'name' }],
    ['print_q', {}, 'x'],
  ]);
});

test('observeCards: a card is reported once, when it enters the viewport', () => {
  let cb;
  const seen = [];
  const watched = [];
  class Fake {
    constructor(fn) {
      cb = fn;
    }
    observe(el) {
      watched.push(el);
    }
    unobserve(el) {
      watched.splice(watched.indexOf(el), 1);
    }
    disconnect() {
      watched.length = 0;
    }
  }
  const els = [{ dataset: { file: 'a.gcode' } }, { dataset: { file: 'b.gcode' } }, { dataset: {} }];
  const stop = observeCards({ querySelectorAll: () => els }, (p) => seen.push(p), Fake);
  assert.equal(watched.length, 3);
  const obs = { unobserve: (el) => watched.splice(watched.indexOf(el), 1) };
  cb(
    [
      { isIntersecting: false, target: els[0] },
      { isIntersecting: true, target: els[1] },
      { isIntersecting: true, target: els[2] },
    ],
    obs,
  );
  assert.deepEqual(seen, ['b.gcode']);
  assert.equal(watched.length, 1);
  stop();
  assert.equal(watched.length, 0);
});

test('the actions: pick plans, card_load loads, sort and search change the state', async () => {
  const calls = [];
  const app = { print: { files: [] } };
  const ctx = {
    app,
    render: () => calls.push('render'),
    planFile: (f) => calls.push(['plan', f]),
    loadCard: async (f) => calls.push(['card', f]),
  };
  runAction(ctx, 'file_pick', { file: 'a.gcode' });
  await runAction(ctx, 'card_load', { file: 'a.gcode' });
  assert.equal(runAction(ctx, 'card_load', {}), undefined);
  runAction(ctx, 'print_sort', { sort: 'name' });
  assert.equal(app.print.sort, 'name');
  runAction(ctx, 'print_sort', { sort: 'bogus' });
  assert.equal(app.print.sort, 'new');
  runAction(ctx, 'print_q', {}, 'zz');
  assert.equal(app.print.q, 'zz');
  assert.deepEqual(calls.slice(0, 2), [
    ['plan', 'a.gcode'],
    ['card', 'a.gcode'],
  ]);
});

test('watchCards: nothing without the grid element; with it, a card in view runs card_load', () => {
  const calls = [];
  const ctx = { run: (...a) => calls.push(a) };
  assert.equal(watchCards(null, ctx, class {}), undefined);
  let cb;
  class Fake {
    constructor(fn) {
      cb = fn;
    }
    observe() {}
    disconnect() {}
  }
  const el = { dataset: { file: 'q.gcode' } };
  watchCards({ querySelectorAll: () => [el] }, ctx, Fake);
  cb([{ isIntersecting: true, target: el }], { unobserve() {} });
  assert.deepEqual(calls, [['card_load', { file: 'q.gcode' }]]);
});

test('cardsKey: a file whose time changed gets a new key, so its card is watched and loaded again', () => {
  assert.notEqual(cardsKey([{ path: 'a', modified: 1 }]), cardsKey([{ path: 'a', modified: 2 }]));
  assert.equal(
    cardsKey([
      { path: 'a', modified: 1 },
      { path: 'b', modified: 2 },
    ]),
    'a@1\nb@2',
  );
});

test('openLater: the tab opens after the running effects, never inside them', () => {
  const runs = [];
  const queued = [];
  openLater({ t, run: (...a) => runs.push(a) }, (fn) => queued.push(fn));
  // The draw that opening makes, inside Preact's effect flush, re-runs the grid's effects.
  assert.deepEqual(runs, []);
  queued[0]();
  assert.deepEqual(runs, [['print_open']]);
});

test('the Print tab during a print: the printing view draws before and after the plan of the file printing opens', async () => {
  const printing = JSON.parse(readFileSync(new URL('../dev/demo-printing.json', import.meta.url), 'utf8'));
  const app = {
    status: printing,
    online: true,
    klippy: 'ready',
    selected: null,
    pending: new Set(),
    edit: null,
    print: { open: false, files: [], file: null, choice: {}, choiceId: null },
    printOptions: {},
    ui: { tab: 'print', panel: null, sheet: false },
  };
  const ctx = { app, t, demo: true, run: () => {}, render: () => {}, toast: () => {} };
  assert.match(renderHtml(html`<${App} app=${app} ctx=${ctx} />`), /class="printing"/);
  globalThis.fetch = async () => ({ json: async () => ({ files: [], cards: {} }) });
  await openPrint(ctx);
  assert.equal(app.print.file, null); // the grid is the page's start, though the printing view shows the print
  assert.match(renderHtml(html`<${App} app=${app} ctx=${ctx} />`), /class="printing"/);
});

test('fitOf and the guided steps use one comparison: below CLOSE matches, CLOSE itself does not', () => {
  assert.equal(CLOSE, 25);
  assert.equal(withinClose(25), false);
  assert.equal(withinClose(24.999), true);
  // A spool just inside and one just outside: the file card and the done test agree on both.
  for (const [rgb, inside] of [
    ['#bababa', true],
    ['#b8b8b8', false],
  ]) {
    assert.equal(colourDistance('#ffffff', rgb) < CLOSE, inside);
    const lane = { key: '1', colour: rgb, material: 'PLA', empty: false, head: 0, spool: { tag: 'T' } };
    const fit = fitOf([{ colour: '#ffffff', material: 'PLA', vendor: null }], [lane]);
    const done = stepDone({ kind: 'put', key: '1', tools: [0] }, [lane], () => ({
      colour: '#ffffff',
      material: 'PLA',
    }));
    assert.deepEqual([fit === 0, done], [inside, inside], rgb);
  }
});
