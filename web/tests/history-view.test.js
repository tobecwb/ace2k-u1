import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { options } from 'preact';
import { html } from '../js/ui/html.js';
import {
  HistoryView,
  WINDOWS,
  chartEvents,
  defaultWindow,
  eventTone,
  hideOnOutsideTap,
  keyedRows,
  sourceClass,
  tipFor,
  tipHandlers,
  watchWidth,
} from '../js/ui/HistoryView.js';
import { eventCount, parseJsonl } from '../js/history.js';
import { sourceOf } from '../js/log-text.js';
import { shiftDay } from '../js/live.js';
import { App } from '../js/ui/App.js';
import { renderHtml, textOf } from './ui-helpers.js';
import { elements, loadEnglish } from './helpers.js';
import { t } from '../js/i18n.js';

loadEnglish();

const read = (name) => readFileSync(new URL(`../${name}`, import.meta.url), 'utf8');
const nowS = () => Date.now() / 1000;
const demoDay = () => shiftDay(parseJsonl(read('dev/demo-history.jsonl')), nowS());

// A page state with the demo's history, shifted to end now.
const appOf = (over = {}) => {
  const day = demoDay();
  return {
    status: JSON.parse(read('dev/demo-status.json')),
    online: true,
    klippy: 'ready',
    samples: day.samples,
    events: day.events,
    hidden: [],
    filter: 'all',
    limit: 200,
    hours: null,
    chartUnit: null,
    days: [],
    loaded: new Set(),
    conn: null,
    folds: {},
    pending: new Set(),
    ui: { tab: 'history', panel: null },
    ...over,
  };
};

// The markup of a component and every element vnode it made, so a test can press its buttons.
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
const textIn = (v) => {
  const c = v.props.children;
  if (Array.isArray(c)) return c.map((x) => (x && typeof x === 'object' ? textIn(x) : (x ?? ''))).join('');
  return c && typeof c === 'object' ? textIn(c) : String(c ?? '');
};
const view = (over = {}) => {
  const runs = [];
  const app = appOf(over);
  const ctx = { app, t, run: (id, data = {}) => runs.push([id, data]) };
  return { ...rendered(html`<${HistoryView} app=${app} ctx=${ctx} />`), runs, app };
};
const press = (nodes, words) => nodes.find((n) => n.type === 'button' && textIn(n).trim() === words).props.onClick();

test('defaultWindow: the shortest window that holds the running cycle', () => {
  const now = 100000;
  assert.deepEqual(WINDOWS, [1, 6, 24]);
  assert.equal(defaultWindow(null, now), 6);
  assert.equal(defaultWindow({ start: now - 1800 }, now), 1);
  assert.equal(defaultWindow({ start: now - 3600 }, now), 1);
  assert.equal(defaultWindow({ start: now - 4 * 3600 }, now), 6);
  assert.equal(defaultWindow({ start: now - 10 * 3600 }, now), 24);
  assert.equal(defaultWindow({ start: now - 30 * 3600 }, now), 24);
});

test('the chart is on top, the event table below, each in its own box', () => {
  const { out } = view();
  const boxes = elements(out, 'section.history div.hbox');
  assert.equal(boxes.length, 2);
  assert.ok(boxes[0].text.includes(t('chart.title')));
  assert.ok(boxes[1].text.includes(t('events.title')));
  assert.ok(out.indexOf('chart-wrap') < out.indexOf('evtable'));
  assert.match(out, /<div class="evscroll" role="region" tabindex="0" aria-label="Events">/);
});

test('the demo history draws its events as dots on the time axis, one circle each', () => {
  const { out, app } = view();
  const inWindow = app.events.filter((e) => e.t >= nowS() - 6 * 3600);
  assert.ok(inWindow.length >= 3);
  assert.equal(elements(out, 'svg circle.ev').length, inWindow.length);
  assert.ok(elements(out, 'svg circle.ev-err').length >= 1, 'the error is red');
  assert.ok(elements(out, 'svg circle.ev-dryer').length >= 1, 'the dryer event has its colour');
  // The filter picks the events of the chart too.
  assert.equal(elements(view({ filter: 'errors' }).out, 'svg circle.ev').length, 1);
});

test('a replaced sample array of the same length is drawn anew', () => {
  const day = demoDay();
  const same = day.samples.map((x) => ({ ...x, ch: 40 + (x.ch ?? 0) / 100 }));
  assert.equal(same.length, day.samples.length);
  const a = elements(view({ samples: day.samples }).out, 'polyline[data-series=ch]')[0].attrs.points;
  const b = elements(view({ samples: same }).out, 'polyline[data-series=ch]')[0].attrs.points;
  assert.notEqual(a, b);
});

test('the table has three columns: time, source, text; the newest event first', () => {
  const { out, app } = view();
  assert.deepEqual(
    elements(out, 'table.evtable th').map((h) => h.text),
    [t('history.col_time'), t('history.col_source'), t('history.col_event')],
  );
  const rows = elements(out, 'table.evtable tbody tr');
  assert.equal(rows.length, app.events.length);
  assert.equal(elements(out, 'tbody td').length, 3 * rows.length);
  assert.equal(elements(out, 'td.tm time').length, rows.length);
  const newest = [...app.events].sort((a, b) => b.t - a.t)[0];
  assert.equal(elements(out, 'td.tx')[0].text, newest.msg.replace(/^ace2k: /, ''));
  assert.deepEqual(
    rows.map((r) => r.attrs.class),
    [...app.events].sort((a, b) => b.t - a.t).map((e) => `lvl-${e.lvl}`),
  );
});

test('an event without a message shows an empty cell; the console prefix is dropped', () => {
  const { out } = view({
    events: [
      { t: nowS() - 2, lvl: 'warn', src: 'dryer', msg: null },
      { t: nowS() - 3, lvl: 'info', src: 'unit', msg: 'ace2k_u1: head 1 ok' },
    ],
  });
  assert.deepEqual(
    elements(out, 'td.tx').map((x) => x.text),
    ['', 'head 1 ok'],
  );
  assert.deepEqual(
    elements(out, 'tbody tr').map((r) => r.attrs.class),
    ['lvl-warn', 'lvl-info'],
  );
});

test('spool inserted and removed events read in the catalogue words', () => {
  const events = [
    { t: nowS() - 20, lvl: 'info', src: 'lane1', msg: 'lane 1 spool inserted' },
    { t: nowS() - 10, lvl: 'info', src: 'lane2', msg: 'ace2k b: lane 2 spool removed' },
  ];
  const { out } = view({ events });
  assert.deepEqual(
    elements(out, 'td.tx').map((x) => x.text),
    [
      `${t('units.name', { name: 'b' })}: ${t('events.lane_spool_removed', { n: 2 })}`,
      t('events.lane_spool_inserted', { n: 1 }),
    ],
  );
});

test('the source: a coloured dot and ACE <unit>, or U1', () => {
  const events = [
    { t: 5, lvl: 'info', src: 'unit', msg: 'ace2k_u1: head 1 ready' },
    { t: 4, lvl: 'info', src: 'lane3', msg: 'lane c:3 spool inserted' },
    { t: 3, lvl: 'info', src: 'unit', msg: 'ace2k b: dryer idle → heating' },
    { t: 2, lvl: 'info', src: 'dryer', msg: 'ace2k: dryer reached 55 °C' },
    { t: 1, lvl: 'info', src: 'lane1', msg: 'lane 1 stuck' },
  ];
  const { out } = view({ events });
  assert.deepEqual(
    elements(out, 'span.src').map((s) => s.text),
    ['U1', 'ACE c', 'ACE b', 'ACE', 'ACE'],
  );
  assert.equal(elements(out, 'span.src i.hdot').length, 5);
  assert.deepEqual(sourceOf(events[0]), { u1: true, name: null });
  assert.deepEqual(sourceOf(events[1]), { u1: false, name: 'c' });
  assert.deepEqual(sourceOf(events[3]), { u1: false, name: null });
  assert.deepEqual(sourceOf({ src: 'unit', msg: null }), { u1: true, name: null });
  assert.deepEqual(sourceOf({ src: 'dryer', msg: null }), { u1: false, name: null });
  assert.deepEqual(sourceOf({ src: undefined, msg: 'x' }), { u1: true, name: null });
  assert.equal(sourceClass({ u1: true, name: null }, ['a']), 'src-u1');
  assert.equal(sourceClass({ u1: false, name: 'b' }, ['a', 'b']), 'src-1');
  assert.equal(sourceClass({ u1: false, name: null }, [null]), 'src-0');
  assert.equal(sourceClass({ u1: false, name: 'zz' }, ['a']), 'src-0');
  assert.equal(sourceClass({ u1: false, name: 'e' }, ['a', 'b', 'c', 'd', 'e']), 'src-0', 'the colours repeat');
});

test("a dot's colour: red for an error, the dryer's own, else neutral", () => {
  assert.equal(eventTone({ lvl: 'err', src: 'dryer' }), 'err');
  assert.equal(eventTone({ lvl: 'info', src: 'dryer' }), 'dryer');
  assert.equal(eventTone({ lvl: 'warn', src: 'lane1' }), 'info');
  assert.deepEqual(chartEvents([{ t: 7, lvl: 'err', src: 'lane1' }]), [{ t: 7, tone: 'err' }]);
});

test('keyedRows: rows with the same time and text get distinct keys', () => {
  const e = { t: 1, msg: 'a' };
  assert.deepEqual(
    keyedRows([e, e, { t: 2, msg: 'a' }]).map((r) => r.key),
    ['1|a|0', '1|a|1', '2|a|0'],
  );
});

test('an empty list says so and offers no more; a long one offers Show more, which presses more', () => {
  const none = view({ events: [] });
  assert.deepEqual(
    elements(none.out, 'section.history p.note').map((n) => n.text),
    [t('events.empty')],
  );
  assert.equal(elements(none.out, 'table').length, 0);
  assert.equal(elements(none.out, 'button.more').length, 0);
  const events = [1, 2, 3].map((i) => ({ t: nowS() - i, lvl: 'info', src: 'unit', msg: `ace2k: n${i}` }));
  const some = view({ events, limit: 2 });
  assert.equal(elements(some.out, 'tbody tr').length, 2, 'the limit cuts the table');
  assert.deepEqual(
    elements(some.out, 'button.more').map((b) => b.text),
    [t('events.more')],
  );
  press(some.nodes, t('events.more'));
  assert.deepEqual(some.runs, [['more', {}]]);
});

test('Show more counts the filtered events: a filter that holds few events offers no more', () => {
  const events = [
    ...[1, 2, 3].map((i) => ({ t: nowS() - i, lvl: 'info', src: 'unit', msg: `ace2k: n${i}` })),
    { t: nowS() - 9, lvl: 'err', src: 'lane1', msg: 'ace2k: lane 1 stuck' },
  ];
  const a = appOf({ events, limit: 2, filter: 'errors' });
  assert.equal(eventCount(a), 1);
  assert.equal(elements(view({ events, limit: 2, filter: 'errors' }).out, 'button.more').length, 0);
  assert.equal(eventCount({ events, filter: 'all' }), 4);
});

test('the filters, windows and series press their actions', () => {
  const v = view();
  press(v.nodes, t('events.filter.errors'));
  press(v.nodes, t('events.filter.dryer'));
  press(v.nodes, '2');
  press(v.nodes, '24 h');
  press(v.nodes, t('chart.humidity'));
  assert.deepEqual(v.runs, [
    ['filter', { f: 'errors' }],
    ['filter', { f: 'dryer' }],
    ['filter', { f: 'lane2' }],
    ['window', { h: '24' }],
    ['series', { key: 'rh' }],
  ]);
  assert.deepEqual(
    elements(v.out, '[aria-label="Lane"] button').map((b) => b.attrs['aria-label']),
    [1, 2, 3, 4].map((n) => t('events.filter.lane', { n })),
  );
});

test('the window follows the running cycle; a pressed window is marked', () => {
  const pressed = (out) => elements(out, 'div.head span.seg button').filter((b) => b.attrs['aria-pressed'] === 'true');
  assert.deepEqual(
    pressed(view({ hours: 24 }).out).map((b) => b.text),
    ['24 h'],
  );
  assert.deepEqual(
    pressed(view({ hours: 1 }).out).map((b) => b.text),
    ['1 h'],
  );
});

test('the legend: swatches, hidden series, the dashed target, and the reading while the dryer heats', () => {
  const v = view({ hidden: ['hl'] });
  const legend = elements(v.out, 'div.hlegend button');
  assert.equal(legend.length, 5);
  assert.deepEqual(
    legend.map((b) => b.attrs['aria-pressed']),
    ['true', 'false', 'true', 'true', 'true'],
  );
  const [dashed] = elements(v.out, 'div.hlegend button i.dashed');
  assert.equal(dashed.attrs.style, 'color:var(--line-ch)');
  assert.ok(elements(v.out, 'div.hlegend').length === 1 && v.out.includes(t('history.legend_events')));
  const status = JSON.parse(read('dev/demo-status.json'));
  status.ace2k.dryer = { ...status.ace2k.dryer, state: 'heating', target: 55, remaining: 120 };
  const day = demoDay();
  const heating = view({ status, samples: day.samples.map((s) => ({ ...s, ch: 45.2, dry: 'heating', tgt: 55 })) });
  assert.ok(textOf(heating.out).includes('Chamber 45.2 / 55.0 °C'));
  assert.ok(elements(heating.out, 'svg polyline[data-series=tgt]').length >= 1, 'the live target is drawn');
  assert.ok(!textOf(v.out).includes('/ 55'));
});

test('a chart with no samples in its window says so and draws no plot; the table still lists the events', () => {
  const none = view({ samples: [] });
  assert.deepEqual(
    elements(none.out, 'div.chart-wrap div.note').map((n) => n.text),
    [t('chart.empty')],
  );
  assert.equal(elements(none.out, 'svg').length, 0);
  assert.ok(elements(none.out, 'tbody tr').length > 0);
  const one = view({ samples: [{ t: Math.floor(nowS()) - 5, k: 's', ch: 20 }] });
  assert.equal(elements(one.out, 'svg').length, 0, 'a single point draws no line');
});

test('several units: a unit selector for the chart and a Unit group of filters', () => {
  const status = { ace2k_units: { units: ['ace2k a', 'ace2k b'] }, 'ace2k a': { lanes: [] }, 'ace2k b': { lanes: [] } };
  const events = [{ t: nowS() - 5, lvl: 'info', src: 'unit', msg: 'ace2k b: dryer idle → heating' }];
  const v = view({ status, events, chartUnit: 'b' });
  const group = elements(v.out, 'span.seg[role=group]').filter((g) => g.attrs['aria-label'] === t('chart.unit'));
  assert.equal(group.length, 1);
  assert.deepEqual(
    elements(v.out, 'div.head span.seg[aria-label=Unit] button').map((b) => [b.attrs['aria-pressed'], b.text]),
    [
      ['false', 'Unit a'],
      ['true', 'Unit b'],
    ],
  );
  press(v.nodes, 'Unit a');
  assert.deepEqual(v.runs, [['chart_unit', { unit: 'a' }]]);
  const units = elements(v.out, 'span.lanes[aria-label=Unit] button');
  assert.deepEqual(
    units.map((b) => b.attrs['aria-label']),
    ['Unit a', 'Unit b'],
  );
  assert.equal(elements(v.out, 'span.src').map((s) => s.text)[0], 'ACE b');
  assert.equal(elements(view().out, 'span.lanes[aria-label=Unit]').length, 0, 'one unit: no group');
});

test('the history error shows above the chart', () => {
  const status = { ...JSON.parse(read('dev/demo-status.json')), ace2k_u1_history: { error: 'disk full' } };
  const { out } = view({ status });
  const [alert] = elements(out, 'p.history-error');
  assert.equal(alert.text, t('top.history_error', { e: 'disk full' }));
  assert.ok(out.indexOf('history-error') < out.indexOf('hbox'));
  assert.equal(elements(view().out, 'p.history-error').length, 0);
});

test('the History tab is what App draws for the tab, inside its own boundary', () => {
  const app = appOf();
  const ctx = { app, t, run: () => {} };
  const out = renderHtml(html`<${App} app=${app} ctx=${ctx} />`);
  assert.equal(elements(out, 'main[data-tab=history] section.history').length, 1);
});

// ---- the tooltip ----
const FMT = { clock: (x) => `c${x}`, temp: (v) => `${v}C`, pct: (v) => `${v}%`, t: (k) => k };
const WIN = { w: 400, l: 32, r: 362, t0: 0, t1: 330 };
const SAMPLES = [
  { t: 0, ch: 20, hl: 21, hr: 22, rh: 50 },
  { t: 330, ch: 30, hl: 31, hr: 32, rh: 40, tgt: 50 },
];
test('tipFor: the sample nearest the time under the pointer, placed beside it; null without samples', () => {
  const left = tipFor({ x: 40, y: 10 }, 400, 100, { win: WIN, samples: SAMPLES, fmt: FMT });
  assert.ok(left?.text.startsWith('c0 ·'));
  assert.deepEqual([left.left, left.top], [48, 18]);
  const right = tipFor({ x: 360, y: 10 }, 400, 100, { win: WIN, samples: SAMPLES, fmt: FMT });
  assert.ok(right?.text.includes('chart.target 50C'));
  assert.equal(right.left, 300, 'it stays inside the box');
  assert.equal(tipFor({ x: 40, y: 10 }, 400, 100, { win: WIN, samples: [], fmt: FMT }), null);
});

// Fakes for the plot's elements.
const fakeTip = () => ({ hidden: true, textContent: '', offsetWidth: 100, style: {} });
const fakeWrap = (svg = { getBoundingClientRect: () => ({ left: 10, top: 20, width: 400 }) }) => ({
  querySelector: () => svg,
  contains: (n) => n === 'inside',
});
const ptr = (over = {}) => ({ clientX: 50, clientY: 30, pointerType: 'mouse', ...over });

test('tipHandlers: move and down show the tooltip; a mouse leaving hides it; a tap does not', () => {
  const tip = fakeTip();
  const refs = { wrap: { current: fakeWrap() }, tip: { current: tip } };
  const h = tipHandlers(refs, { win: WIN, samples: SAMPLES, fmt: FMT });
  h.move(ptr());
  assert.equal(tip.hidden, false);
  assert.ok(tip.textContent.startsWith('c0'));
  assert.deepEqual(tip.style, { left: '48px', top: '18px' });
  h.leave(ptr({ pointerType: 'touch' }));
  assert.equal(tip.hidden, false);
  h.leave(ptr());
  assert.equal(tip.hidden, true);
  h.down(ptr());
  assert.equal(tip.hidden, false);
  const bare = tipHandlers(refs, { win: WIN, samples: [], fmt: FMT });
  bare.move(ptr());
  assert.equal(tip.hidden, true, 'no sample: hidden');
});

test('tipHandlers: nothing to do without the tooltip element or the drawing', () => {
  const tip = fakeTip();
  const view = { win: WIN, samples: SAMPLES, fmt: FMT };
  tipHandlers({ wrap: { current: fakeWrap() }, tip: { current: null } }, view).move(ptr());
  tipHandlers({ wrap: { current: fakeWrap(null) }, tip: { current: tip } }, view).move(ptr());
  tipHandlers({ wrap: { current: null }, tip: { current: tip } }, view).move(ptr());
  assert.equal(tip.hidden, true);
  tipHandlers({ wrap: { current: null }, tip: { current: null } }, view).leave(ptr());
});

test('hideOnOutsideTap: a tap outside the plot hides the tooltip; one inside does not; the cleanup stops it', () => {
  const tip = fakeTip();
  tip.hidden = false;
  const listeners = new Map();
  const doc = {
    addEventListener: (type, fn) => listeners.set(type, fn),
    removeEventListener: (type, fn) => listeners.get(type) === fn && listeners.delete(type),
  };
  const refs = { wrap: { current: fakeWrap() }, tip: { current: tip } };
  const stop = hideOnOutsideTap(doc, refs);
  listeners.get('pointerdown')({ target: 'inside' });
  assert.equal(tip.hidden, false);
  listeners.get('pointerdown')({ target: 'outside' });
  assert.equal(tip.hidden, true);
  tip.hidden = false;
  refs.wrap.current = null;
  listeners.get('pointerdown')({ target: 'outside' });
  assert.equal(tip.hidden, true, 'no plot: any tap hides it');
  refs.tip.current = null;
  listeners.get('pointerdown')({ target: 'outside' });
  stop();
  assert.equal(listeners.size, 0);
});

test('watchWidth: tells the plot width now and on every change; stops on cleanup', () => {
  assert.equal(
    watchWidth(null, () => {}, class {}),
    undefined,
  );
  const seen = [];
  let callback;
  const log = [];
  class Observer {
    constructor(fn) {
      callback = fn;
    }
    observe(el) {
      log.push(['observe', el.id]);
    }
    disconnect() {
      log.push(['disconnect']);
    }
  }
  const el = { id: 'plot', clientWidth: 700 };
  const stop = watchWidth(el, (w) => seen.push(w), Observer);
  callback();
  el.clientWidth = 320;
  callback();
  stop();
  assert.deepEqual(seen, [700, 320]);
  assert.deepEqual(log, [['observe', 'plot'], ['disconnect']]);
});
