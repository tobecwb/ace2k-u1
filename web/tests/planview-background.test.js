import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { html } from '../js/ui/html.js';
import { PlanView, barSummary, cmpLine, planTimeWords, swapsWords } from '../js/ui/PlanView.js';
import { ColourPicker, costWords } from '../js/ui/ColourPicker.js';
import { Printing } from '../js/ui/Printing.js';
import { Timeline, titleOf } from '../js/ui/Timeline.js';
import { bands, liveAt, segmentsOf } from '../js/timeline.js';
import { headOfIn, liveLanes, printGate } from '../js/print-plan.js';
import { pickCosts } from '../js/planner.js';
import { SEEDS, changesOf, swapStops } from '../js/swap-time.js';
import { bgHeads, forgetEndedPrint, liveBg, planNow } from '../js/state.js';
import { DASH, fmtHM, fmtMS } from '../js/format.js';
import { renderHtml, textOf } from './ui-helpers.js';
import { loadEnglish } from './helpers.js';
import { t } from '../js/i18n.js';

loadEnglish();

// The Print panel with heads that swap in the background (the adapter's background status): the swaps it shows are
// the stops, and the swaps in the background are a count of their own.

const read = (url) => JSON.parse(readFileSync(new URL(url, import.meta.url), 'utf8'));
const IDLE = { state: 'idle', step: null, lane: null };

// demo-unknown with bays 1 (red PLA) and 4 (a white PLA spool) on head 0, bay 2 (black PETG) on head 1, and no other
// bay on a head.
function shared() {
  const s = read('../dev/demo-unknown.json');
  s.ace2k_u1.heads = [
    [
      { unit: null, lane: 1 },
      { unit: null, lane: 4 },
    ],
    [{ unit: null, lane: 2 }],
    [],
    [],
  ];
  s.ace2k.lanes[3] = {
    ...s.ace2k.lanes[1],
    tag: { state: 'read', record: { brand: 'X', material: 'PLA', color_rgba: 'FFFFFFFF' } },
  };
  return s;
}
// Red PLA, black PETG and white PLA, in the order red black white black, ten times: red and white share head 0, and
// black on head 1 comes between them, so head 0 is parked at each of its 19 swaps.
const plan = {
  file: 'bg.gcode',
  mtime: 1,
  tools: [
    { colour: '#d32c34', material: 'PLA', lane: '1', why: 'closest' },
    { colour: '#000000', material: 'PETG', lane: '2', why: 'closest' },
    { colour: '#ffffff', material: 'PLA', lane: '4', why: 'closest' },
  ],
  order: Array.from({ length: 10 }, () => [0, 1, 2, 1]).flat(),
  raw: [],
  heads: {},
  set: false,
  choice: null,
  lanes: [],
};
/** @param {unknown[] | undefined} background @param {boolean} [narrow] */
const appOf = (background, narrow = false) => ({
  status: { ...shared(), ace2k_u1: { ...shared().ace2k_u1, plan, ...(background ? { background } : {}) } },
  online: true,
  klippy: 'ready',
  selected: null,
  pending: new Set(),
  edit: null,
  print: { open: true, files: [], file: 'bg.gcode', choice: {}, choiceId: null },
  ui: { tab: 'print', panel: null, sheet: false, narrow },
});
const ctxOf = (app) => ({ app, t, render: () => {}, toast: () => {}, run: () => {} });

test('bgHeads: the heads the background status lists, none without one', () => {
  assert.deepEqual(bgHeads({ ace2k_u1: { background: [IDLE, null, IDLE, null] } }), [0, 2]);
  assert.deepEqual(bgHeads({ ace2k_u1: {} }), []);
  assert.deepEqual(bgHeads({}), []);
});

test('the header shows the stops of each side and their swaps in the background', () => {
  const app = appOf([IDLE, null, null, null]);
  const now = planNow(app);
  assert.deepEqual(now?.bg, [0]);
  assert.deepEqual([now?.place?.now, now?.place?.nowFree, now?.place?.best, now?.place?.free], [0, 19, 0, 19]);
  const text = textOf(renderHtml(html`<${PlanView} app=${app} ctx=${ctxOf(app)} />`));
  // No slicer time: each swap in the background costs only its arrival, and the panel says so once.
  const arrival = Math.round(19 * SEEDS.ready);
  assert.deepEqual([now?.place?.nowS, now?.place?.bestS], [arrival, arrival]);
  assert.equal(now?.timeS, null);
  assert.equal(
    textOf(renderHtml(html`<${PlanView} app=${app} ctx=${ctxOf(app)} />`)).split(t('print.no_time')).length,
    2,
  );
  const side = `${swapsWords(t, 0)} ${t('print.stopped', { time: fmtHM(arrival) })} ${t('print.background_n', { n: 19 })}`;
  assert.ok(text.includes(`${t('print.as_is')} ${side}`), text);
  assert.ok(text.includes(`${t('print.with_suggestion')} ${side}`), text);
  // The bar and the timeline say the same.
  assert.ok(text.includes(t('print.bar_bg', { swaps: swapsWords(t, 0), time: fmtHM(arrival), n: 19 })), text);
  assert.ok(text.includes(`${t('timeline.count', { n: 0 })} ${t('print.background_n', { n: 19 })}`), text);
});

test('without a background head the header, the bar and the timeline are as before', () => {
  const app = appOf(undefined);
  const now = planNow(app);
  assert.deepEqual(now?.bg, []);
  const text = textOf(renderHtml(html`<${PlanView} app=${app} ctx=${ctxOf(app)} />`));
  assert.ok(!text.includes(t('print.background_n', { n: 19 })));
  assert.ok(text.includes(t('print.bar', { swaps: swapsWords(t, 19), time: fmtHM(19 * SEEDS.inline) })), text);
  assert.ok(text.includes(t('timeline.count', { n: 19 })), text);
});

test('on a phone the comparison line adds the swaps in the background of both sides', () => {
  const app = appOf([IDLE, null, null, null], true);
  const text = textOf(renderHtml(html`<${PlanView} app=${app} ctx=${ctxOf(app)} />`));
  assert.ok(text.includes(cmpLine(t, 0, 0, '', 19, 19, Math.round(19 * SEEDS.ready))), text);
  assert.ok(text.includes(`${t('print.as_is')} ${t('print.as_is_bg', { swaps: swapsWords(t, 0), n: 19 })}`), text);
  assert.equal(
    cmpLine(t, 3, 1, 'x', 4, null, SEEDS.inline),
    `As it is 3 swaps · with the suggestion 1 swap, ≈ ${fmtHM(SEEDS.inline)} stopped, 4 in the background`,
  );
  assert.equal(
    cmpLine(t, 3, 1, 'x', 4, 2, SEEDS.inline),
    `As it is 3 swaps + 2 in the background · with the suggestion 1 swap, ≈ ${fmtHM(SEEDS.inline)} stopped, 4 in the background`,
  );
  // A dash as it is says nothing of its swaps in the background.
  assert.equal(cmpLine(t, null, 1, 'x', 0, 2), cmpLine(t, null, 1, 'x'));
  // No free swap: the line of before.
  assert.equal(cmpLine(t, 3, 1, 'x', 0), cmpLine(t, 3, 1, 'x'));
});

test('the bar summary: a dash while a colour has no bay; the stops alone when nothing swaps in the background', () => {
  const app = appOf([IDLE, null, null, null]);
  const now = /** @type {import('../js/state.js').PlanNow} */ (planNow(app));
  const gate = printGate(now, false);
  assert.equal(barSummary(t, now, { ...gate, choice: { 0: '1' } }), DASH);
  assert.equal(barSummary(t, { ...now, place: null }, gate), DASH);
  assert.equal(
    barSummary(t, { ...now, bg: [1] }, gate),
    t('print.bar', { swaps: swapsWords(t, 19), time: fmtHM(19 * SEEDS.inline) }),
  );
});

test('the bar summary counts its swaps on the changes its time reads: a frame that drops changes drops both', () => {
  const app = appOf([IDLE, null, null, null]);
  const now = /** @type {import('../js/state.js').PlanNow} */ (planNow(app));
  const gate = printGate(now, false);
  // The first 20 of the 40 changes: head 0 prints red and white 10 times, so 9 swaps, not the file's 19.
  const cut = { ...now, bg: [1], changes: now.changes.slice(0, 20) };
  assert.equal(barSummary(t, cut, gate), t('print.bar', { swaps: swapsWords(t, 9), time: fmtHM(9 * SEEDS.inline) }));
});

test('bands count the free swaps of a parked background head only', () => {
  const lanes = [
    { key: 'a', head: 0 },
    { key: 'b', head: 0 },
    { key: 'c', head: 1 },
  ];
  const headOf = headOfIn(/** @type {any} */ (lanes));
  // Tool 3 has no head: it is skipped, so it neither parks head 0 nor breaks a run of it.
  const segs = segmentsOf({ order: [0, 1, 3, 0, 2, 1] });
  const choice = { 0: 'a', 1: 'b', 2: 'c' };
  const b = bands(segs, choice, headOf, [0]);
  assert.deepEqual(
    b.map((x) => [x.head, x.swaps, x.free]),
    [
      [0, 3, 1],
      [1, 0, 0],
    ],
  );
  assert.deepEqual(
    bands(segs, choice, headOf).map((x) => x.free),
    [0, 0],
  );
});

test('the printing view passes the background heads to its timeline', () => {
  forgetEndedPrint({ print_stats: { state: 'complete' } });
  const s = read('../dev/demo-printing.json');
  const app = {
    status: { ...s, ace2k_u1: { ...s.ace2k_u1, background: [IDLE, IDLE, null, null] } },
    online: true,
    klippy: 'ready',
    selected: null,
    pending: new Set(),
    edit: null,
    print: { open: false, files: [], file: null, choice: {}, choiceId: null },
    ui: { tab: 'print', panel: null, sheet: false },
  };
  const text = textOf(renderHtml(html`<${Printing} app=${app} ctx=${ctxOf(app)} />`));
  // Head 0: 110 swaps, 5 of them in the background; head 1: 92, 8 in the background.
  assert.ok(text.includes(`${t('timeline.count', { n: 105 })} ${t('print.background_n', { n: 5 })}`), text);
  assert.ok(text.includes(`${t('timeline.count', { n: 84 })} ${t('print.background_n', { n: 8 })}`), text);
  const plan2 = s.ace2k_u1.plan;
  const headOf = headOfIn(liveLanes(s, plan2));
  const none = textOf(renderHtml(html`<${Timeline} plan=${plan2} choice=${plan2.choice} headOf=${headOf} t=${t} />`));
  assert.ok(none.includes(t('timeline.count', { n: 110 })));
});

test('bands keep the marks of the free swaps apart from the stops', () => {
  const lanes = [
    { key: 'a', head: 0 },
    { key: 'b', head: 0 },
    { key: 'c', head: 1 },
  ];
  const headOf = headOfIn(/** @type {any} */ (lanes));
  // a b c a: the swap to b stops the print, the swap back to a comes after c (head 0 parked) and is free.
  const segs = segmentsOf({ order: [0, 1, 2, 0] });
  const choice = { 0: 'a', 1: 'b', 2: 'c' };
  const [h0] = bands(segs, choice, headOf, [0]);
  assert.deepEqual([h0.marks, h0.freeMarks, h0.swaps, h0.free], [[0.25], [0.75], 2, 1]);
  const [all] = bands(segs, choice, headOf);
  assert.deepEqual([all.marks, all.freeMarks], [[0.25, 0.75], []]);
});

test('the timeline draws a free swap with its own mark, and its legend line only when the plan has one', () => {
  const lanes = [
    { key: 'a', head: 0 },
    { key: 'b', head: 0 },
    { key: 'c', head: 1 },
  ];
  const headOf = headOfIn(/** @type {any} */ (lanes));
  const p = { tools: [{ colour: '#ff0000' }, { colour: '#00ff00' }, { colour: '#0000ff' }], order: [0, 1, 2, 0] };
  const choice = { 0: 'a', 1: 'b', 2: 'c' };
  const bg = renderHtml(html`<${Timeline} plan=${p} choice=${choice} headOf=${headOf} bg=${[0]} t=${t} />`);
  assert.equal(bg.match(/class="sw-mark"/g).length, 1);
  assert.equal(bg.match(/class="sw-mark free"/g).length, 1);
  const text = textOf(bg);
  assert.ok(text.includes(t('timeline.legend_swap', { time: fmtMS(SEEDS.inline) })), text);
  assert.ok(text.includes(`${t('timeline.mark_bg')} ${t('timeline.legend_bg', { time: fmtMS(SEEDS.ready) })}`), text);
  const none = renderHtml(html`<${Timeline} plan=${p} choice=${choice} headOf=${headOf} t=${t} />`);
  assert.equal(none.match(/class="sw-mark"/g).length, 2);
  assert.ok(!none.includes('sw-mark free'));
  assert.ok(!textOf(none).includes(t('timeline.legend_bg')));
});

test('the legend and the marks round an average the same way', () => {
  const lanes = [
    { key: 'a', head: 0 },
    { key: 'b', head: 0 },
    { key: 'c', head: 1 },
  ];
  const headOf = headOfIn(/** @type {any} */ (lanes));
  const p = { tools: [{ colour: '#ff0000' }, { colour: '#00ff00' }, { colour: '#0000ff' }], order: [0, 1, 2, 0] };
  const choice = { 0: 'a', 1: 'b', 2: 'c' };
  const times = { ...SEEDS, inline: 93.6, ready: 15.6 };
  const out = renderHtml(
    html`<${Timeline} plan=${p} choice=${choice} headOf=${headOf} bg=${[0]} times=${times} t=${t} />`,
  );
  const text = textOf(out);
  // 93.6 s is 1:34 in the legend and in the inline mark's title, 15.6 s is 0:16 in both.
  assert.ok(text.includes(t('timeline.legend_swap', { time: '1:34' })), text);
  assert.ok(out.includes(`title="${t('timeline.stop_title', { time: '1:34' })}"`), out);
  assert.ok(text.includes(t('timeline.legend_bg', { time: '0:16' })), text);
  assert.ok(out.includes(`title="${t('timeline.stop_title', { time: '0:16' })}"`), out);
});

test('a free swap is no swap in progress: the Now card never says the print waits for it', () => {
  const lanes = [
    { key: 'a', head: 0 },
    { key: 'b', head: 0 },
    { key: 'c', head: 1 },
  ];
  const headOf = headOfIn(/** @type {any} */ (lanes));
  const segs = segmentsOf({ order: [0, 1, 2, 0] });
  const choice = { 0: 'a', 1: 'b', 2: 'c' };
  // Just after the swap back to a (free on head 0 in bg), and just after the swap to b (a stop either way).
  assert.equal(liveAt(segs, choice, headOf, 0.76, null, [0]).swapping, null);
  assert.deepEqual(liveAt(segs, choice, headOf, 0.76).swapping, { head: 0, from: 1, to: 0 });
  assert.deepEqual(liveAt(segs, choice, headOf, 0.26, null, [0]).swapping, { head: 0, from: 0, to: 1 });
});

test('liveBg: free leaves out a stopped background swap, waiting is a running one, swapping as the adapter says', () => {
  const stopped = { state: 'stopped', step: 'push', lane: '4' };
  const running = { state: 'running', step: 'unload', lane: '4' };
  const finished = { state: 'finished', step: 'loaded', lane: '4' };
  const status = {
    ace2k_u1: { background: [running, stopped, finished, null], swapping: [0, 2] },
  };
  assert.deepEqual(liveBg(status), { free: [0, 2], waiting: [0], swapping: [0, 2] });
  assert.deepEqual(liveBg({ ace2k_u1: {} }), { free: [], waiting: [], swapping: [] });
});

// a b c a b on heads 0 0 1 0 0: the swap back to a (after c, head 0 parked) is free with head 0 in bg; the swap to b
// after it is a stop.
const LIVE_LANES = [
  { key: 'a', head: 0 },
  { key: 'b', head: 0 },
  { key: 'c', head: 1 },
];
const liveHeadOf = headOfIn(/** @type {any} */ (LIVE_LANES));
const liveChoice = { 0: 'a', 1: 'b', 2: 'c' };

test('a swap is in progress while its background swap still runs, or the adapter swaps its head', () => {
  const segs = segmentsOf({ order: [0, 1, 2, 0] });
  const at = (/** @type {any} */ seen, pos = 0.76) =>
    liveAt(segs, liveChoice, liveHeadOf, pos, null, [0], seen).swapping;
  const swap = { head: 0, from: 1, to: 0 };
  assert.equal(at({ waiting: [], swapping: [] }), null);
  assert.deepEqual(at({ waiting: [0], swapping: [] }), swap);
  assert.deepEqual(at({ waiting: [], swapping: [0] }), swap);
  // Past the swap's window the change prints, whatever the status says.
  assert.equal(at({ waiting: [0], swapping: [0] }, 0.99), null);
  // Another head's run or swap does not make this change wait.
  assert.equal(at({ waiting: [1], swapping: [1] }), null);
  // A stop is in progress within its window either way.
  assert.deepEqual(at({ waiting: [], swapping: [] }, 0.26), { head: 0, from: 0, to: 1 });
});

test('the next swap tells a free swap, and the next stop after it', () => {
  const segs = segmentsOf({ order: [0, 1, 2, 0, 1] });
  const live = liveAt(segs, liveChoice, liveHeadOf, 0.3, null, [0]);
  assert.deepEqual(live.next, { head: 0, from: 1, to: 0, x: 0.6, free: true });
  assert.deepEqual(live.nextStop, { head: 0, from: 0, to: 1, x: 0.8, free: false });
  // Without bg the next swap is the next stop.
  const all = liveAt(segs, liveChoice, liveHeadOf, 0.3);
  assert.equal(all.nextStop, all.next);
  // No stop after the last free swap.
  assert.equal(liveAt(segmentsOf({ order: [0, 1, 2, 0] }), liveChoice, liveHeadOf, 0.3, null, [0]).nextStop, null);
});

// The printing demo at the share pos, with the background status bg and the heads the adapter swaps now.
/** @param {unknown[]} bg @param {number} pos @param {number[]} [swapping] */
function printingAt(bg, pos, swapping = []) {
  forgetEndedPrint({ print_stats: { state: 'complete' } });
  const s = read('../dev/demo-printing.json');
  const plan = s.ace2k_u1.plan;
  s.virtual_sdcard = { ...s.virtual_sdcard, file_position: Math.ceil(pos * plan.size) + 1, progress: pos };
  const app = {
    status: { ...s, ace2k_u1: { ...s.ace2k_u1, swapping, background: bg } },
    online: true,
    klippy: 'ready',
    selected: null,
    pending: new Set(),
    edit: null,
    print: { open: false, files: [], file: null, choice: {}, choiceId: null },
    ui: { tab: 'print', panel: null, sheet: false },
  };
  return { s, plan, text: textOf(renderHtml(html`<${Printing} app=${app} ctx=${ctxOf(app)} />`)) };
}

test('the Now card says the print waits at a free swap only while the live status says so', () => {
  const { s, plan } = printingAt([IDLE, IDLE, null, null], 0);
  const [h0] = bands(segmentsOf(plan), plan.choice, headOfIn(liveLanes(s, plan)), [0, 1]);
  const x = h0.freeMarks[0];
  const seg = segmentsOf(plan).find((g) => g.x === x);
  const key = plan.choice[/** @type {any} */ (seg).tool];
  const RUN = { state: 'running', step: 'push', lane: key };
  const STOP = { state: 'stopped', step: 'push', lane: key };
  const waits = t('printing.waits', { time: fmtHM(SEEDS.inline) });
  // The planned free swap with nothing left to do: the colour prints.
  assert.ok(!printingAt([IDLE, IDLE, null, null], x).text.includes(waits));
  // The T waits for the background swap that still runs; a stopped one goes on at the T; the T ends the swap.
  assert.ok(printingAt([RUN, IDLE, null, null], x).text.includes(waits));
  assert.ok(printingAt([STOP, IDLE, null, null], x).text.includes(waits));
  assert.ok(printingAt([IDLE, IDLE, null, null], x, [0]).text.includes(waits));
});

test('the Next card announces a free swap as a swap in the background, and the next stop under it', () => {
  const { s, plan } = printingAt([IDLE, IDLE, null, null], 0);
  const headOf = headOfIn(liveLanes(s, plan));
  const segs = segmentsOf(plan);
  const x = Math.min(...bands(segs, plan.choice, headOf, [0, 1]).flatMap((b) => b.freeMarks));
  const pos = x - 2 / plan.size;
  const { text } = printingAt([IDLE, IDLE, null, null], pos);
  const live = liveAt(segs, plan.choice, headOf, pos, null, [0, 1]);
  assert.equal(live.next?.free, true);
  assert.ok(text.includes(t('printing.next_bg', { eta: '' }).split('≈')[0]), text);
  assert.ok(!text.includes(t('printing.next_cap')), text);
  assert.ok(text.includes(t('printing.next_stop', { eta: '' }).split('≈')[0]), text);
  // Without a background head the same swap is the next spool swap, with no line for the next stop.
  const none = printingAt([], pos).text;
  assert.ok(
    none.includes(t('printing.next_cap')) && !none.includes(t('printing.next_stop', { eta: '' }).split('≈')[0]),
  );
});

test('the picker shows a change in swaps in the background when the stops stay the same', () => {
  assert.deepEqual(costWords(t, 0, true, 2), { text: t('print.cost_bg_more', { n: 2 }), cls: 'c' });
  assert.deepEqual(costWords(t, 0, true, -3), { text: t('print.cost_bg_less', { n: 3 }), cls: 'c' });
  assert.deepEqual(costWords(t, 1, true, -3), costWords(t, 1));
  assert.deepEqual(costWords(t, 0, true, 0), costWords(t, 0));
  const app = appOf([IDLE, null, null, null]);
  const now = /** @type {import('../js/state.js').PlanNow} */ (planNow(app));
  // Red joins white: head 0 keeps one spool, so its 19 swaps in the background go, and no stop comes or goes.
  const join = { merge: 2 };
  assert.deepEqual(pickCosts(now.plan, now.raw, now.planLanes, now.merges, 0, join, now.fixed, now.bg), {
    d: 0,
    free: -19,
  });
  // The placement given as the base is the one planNow worked out.
  assert.deepEqual(pickCosts(now.plan, now.raw, now.planLanes, now.merges, 0, join, now.fixed, now.bg, now.place), {
    d: 0,
    free: -19,
  });
  const text = textOf(renderHtml(html`<${ColourPicker} app=${app} ctx=${ctxOf(app)} n=${0} />`));
  assert.ok(text.includes(t('print.cost_bg_less', { n: 19 })), text);
  // Without a background head nothing is free.
  assert.equal(pickCosts(now.plan, now.raw, now.planLanes, now.merges, 0, join, now.fixed).free, 0);
  // While the plan does not fit, "fits · n swaps" adds its swaps in the background.
  assert.deepEqual(costWords(t, 2, false, 5), {
    text: t('print.fits_swaps_bg', { swaps: '2 swaps', n: 5 }),
    cls: 'c good',
  });
  assert.deepEqual(costWords(t, 2, false, 0), costWords(t, 2, false));
});

// The same plan with the slicer's bytes and time on it: 100 s of print for each of the 40 changes of the file.
/** @param {number | undefined} time the card's print time, in seconds */
function timedApp(time) {
  const app = appOf([IDLE, null, null, null]);
  const p = app.status.ace2k_u1.plan;
  p.order_at = p.order.map((_, i) => i * 1000);
  p.size = 40000;
  app.print.cards = {
    'bg.gcode': { card: { colours: null, time, thumb: null, modified: 0, gcodeStart: 0, gcodeEnd: 40000 } },
  };
  return app;
}

/** @param {ReturnType<typeof timedApp>} app */
const secondsOf = (app) => {
  const now = /** @type {import('../js/state.js').PlanNow} */ (planNow(app));
  const gate = printGate(now, false);
  const frame = now.frame;
  const changes = changesOf(now.plan, frame, now.timeS);
  return swapStops(changes, gate.choice, headOfIn(now.lanes), now.bg, now.times);
};

test('with the slicer time a parked head is ready: the boxes show the estimated stops, no late mark', () => {
  const app = timedApp(4000);
  const { stops, total } = secondsOf(app);
  assert.ok(stops.some((s) => s?.kind === 'ready'));
  assert.ok(stops.every((s) => s?.kind !== 'late'));
  const now = planNow(app);
  assert.equal(now?.place?.bestS, Math.round(total));
  const out = renderHtml(html`<${PlanView} app=${app} ctx=${ctxOf(app)} />`);
  const text = textOf(out);
  assert.ok(text.includes(t('print.stopped', { time: fmtHM(Math.round(total)) })), text);
  assert.ok(!text.includes(t('print.no_time')));
  assert.ok(!out.includes('sw-mark free late'));
  assert.ok(!text.includes(t('timeline.legend_late')));
  // The row shows the head's own time stopped.
  const head0 = stops.reduce((sum, s) => sum + (s?.s ?? 0), 0);
  assert.ok(head0 < 3600);
  assert.ok(text.includes(t('timeline.head_stopped', { time: fmtMS(Math.round(head0)) })), text);
});

test("the header's time: the slicer's time plus the stops of the swaps Print sends, the slicer's time beside it", () => {
  const app = timedApp(4000);
  const { total } = secondsOf(app);
  assert.ok(total > 0);
  const words = t('print.time_with_swaps', { total: fmtHM(4000 + total), slicer: fmtHM(4000) });
  const now = /** @type {import('../js/state.js').PlanNow} */ (planNow(app));
  assert.equal(planTimeWords(t, now, printGate(now, false)), words);
  assert.ok(textOf(renderHtml(html`<${PlanView} app=${app} ctx=${ctxOf(app)} />`)).includes(words));
  // No card: no time. A time of 0 or not a number: the slicer's line as before, never NaN.
  assert.equal(planTimeWords(t, { ...now, timeS: null }, printGate(now, false)), null);
  for (const time of [0, Number.NaN]) {
    const bad = /** @type {import('../js/state.js').PlanNow} */ (planNow(timedApp(time)));
    assert.equal(planTimeWords(t, bad, printGate(bad, false)), t('print.slicer_time', { time: fmtHM(time) }));
  }
  // Swaps that stop the print for no time: the slicer's time alone, not "with the swaps".
  const free = { inline: 0, ready: 0, dock: 0, resume: 0 };
  assert.equal(
    planTimeWords(t, { ...now, times: free }, printGate(now, false)),
    t('print.slicer_time', { time: '1:07' }),
  );
  // The swaps not known (no placement): the slicer's time alone.
  assert.equal(
    planTimeWords(t, { ...now, place: null }, printGate(now, false)),
    t('print.slicer_time', { time: '1:07' }),
  );
});

test('a print time of 0 or not a number counts as no time: the note shows', () => {
  for (const time of [0, Number.NaN]) {
    const app = timedApp(time);
    const out = renderHtml(html`<${PlanView} app=${app} ctx=${ctxOf(app)} />`);
    const text = textOf(out);
    assert.ok(text.includes(t('print.no_time')), String(time));
    assert.ok(!out.includes('NaN'), text); // no figure, title or style reads NaN
  }
});

test('with a short print time the dock has no time: the late mark shows and the stop grows', () => {
  const ready = secondsOf(timedApp(4000)).total;
  const app = timedApp(1600);
  const { stops, total } = secondsOf(app);
  assert.ok(stops.some((s) => s?.kind === 'late'));
  assert.ok(total > ready);
  const out = renderHtml(html`<${PlanView} app=${app} ctx=${ctxOf(app)} />`);
  assert.ok(out.includes('class="sw-mark free late"'));
  const text = textOf(out);
  assert.ok(text.includes(`${t('timeline.mark_late')} ${t('timeline.legend_late')}`), text);
  assert.ok(text.includes(t('print.stopped', { time: fmtHM(Math.round(total)) })), text);
  const late = Math.round(stops.find((s) => s?.kind === 'late')?.s ?? 0);
  assert.ok(out.includes(`title="${t('timeline.stop_title', { time: fmtMS(late) })}"`));
});

test('every mark carries its own estimate and a title: the stop time, else what the mark is', () => {
  const lanes = [
    { key: 'a', head: 0 },
    { key: 'b', head: 0 },
    { key: 'c', head: 1 },
  ];
  const headOf = headOfIn(/** @type {any} */ (lanes));
  const segs = segmentsOf({ order: [0, 1, 2, 0] });
  const choice = { 0: 'a', 1: 'b', 2: 'c' };
  // No stops given: the marks carry no estimate.
  const [none] = bands(segs, choice, headOf, [0]);
  assert.deepEqual(none.drawn, [
    { x: 0.25, kind: 'stop', s: null },
    { x: 0.75, kind: 'free', s: null },
  ]);
  // With stops (one per segment): the swap to b stops 12 s, the swap back to a is called back early and stops 40 s.
  const stops = [null, { kind: 'inline', s: 12 }, null, { kind: 'late', s: 40 }];
  const [timed] = bands(segs, choice, headOf, [0], /** @type {any} */ (stops));
  assert.deepEqual(timed.drawn, [
    { x: 0.25, kind: 'stop', s: 12 },
    { x: 0.75, kind: 'late', s: 40 },
  ]);
  assert.equal(titleOf(t, timed.drawn[0]), t('timeline.stop_title', { time: fmtMS(12) }));
  assert.equal(titleOf(t, timed.drawn[1]), t('timeline.stop_title', { time: fmtMS(40) }));
  // Without an estimate each kind says what it is: a free swap stops briefly, a late one longer.
  assert.equal(titleOf(t, { kind: 'stop', s: null }), t('timeline.swap_title'));
  assert.equal(titleOf(t, { kind: 'free', s: null }), t('timeline.free_title'));
  assert.equal(titleOf(t, { kind: 'late', s: null }), t('timeline.late_title'));
  assert.ok(t('timeline.free_title').includes('briefly') && t('timeline.late_title').includes('the rest of the swap'));
  // Rendered, every mark has a title.
  const p = { tools: [{ colour: '#ff0000' }, { colour: '#00ff00' }, { colour: '#0000ff' }], order: [0, 1, 2, 0] };
  const out = renderHtml(html`<${Timeline} plan=${p} choice=${choice} headOf=${headOf} bg=${[0]} t=${t} />`);
  assert.ok(!/<u class="sw-mark[^"]*" aria-hidden/.test(out), 'a mark without a title');
});
