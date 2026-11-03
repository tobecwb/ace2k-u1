import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { options } from 'preact';
import { html } from '../js/ui/html.js';
import { App } from '../js/ui/App.js';
import { Diagram, pillWidth } from '../js/ui/Diagram.js';
import { MobileLists } from '../js/ui/MobileLists.js';
import { PlanView } from '../js/ui/PlanView.js';
import { Printing, printJob, marksOf, cardEffect, colourWords, spoolWords } from '../js/ui/Printing.js';
import { Timeline, rowsOf, span, stripDrag, stripPointer, zoomActions } from '../js/ui/Timeline.js';
import { WHOLE, axisLabels, moveTo, pickView, resize, zoomIn, zoomOut } from '../js/zoom.js';
import { headOfIn, liveLanes } from '../js/print-plan.js';
import { liveAt, segmentsOf } from '../js/timeline.js';
import { fmtHM } from '../js/format.js';
import { totalOf } from '../js/clock.js';
import { fileFrame, forgetEndedPrint } from '../js/state.js';
import { ingestStatus } from '../js/live.js';
import { SWAP_S } from '../js/planner.js';
import { renderHtml, textOf } from './ui-helpers.js';
import { loadEnglish } from './helpers.js';
import { t } from '../js/i18n.js';

loadEnglish();

// demo-printing: two units, a five-colour plan of 250 changes over 6 117 340 bytes, 40 % printed
// (file_position and progress), 2304 s printed. T0 holds a:1/a:2, T1 a:3/a:4, T2 b:1.
// Each test starts a print afresh: the clock forgets the share it showed for the last one.
const demo = () => {
  forgetEndedPrint({ print_stats: { state: 'complete' } });
  return JSON.parse(readFileSync(new URL('../dev/demo-printing.json', import.meta.url), 'utf8'));
};
// The running file's card: the slicer's 1:36, and its G-code over the whole file (so a share is the file position
// over the plan's size).
const FILE = 'objeto_5_cores_PLA_1h36m.gcode';
const CARD = { colours: null, time: 5760, thumb: null, modified: 0, gcodeStart: 0, gcodeEnd: 6117340 };
const appOf = (status, over = {}) => ({
  status,
  online: true,
  klippy: 'ready',
  selected: null,
  pending: new Set(),
  edit: null,
  print: {
    open: false,
    files: [],
    file: null,
    choice: {},
    choiceId: null,
    cards: { [FILE]: { modified: 0, card: CARD } },
  },
  ui: { tab: 'print', panel: null, sheet: false },
  ...over,
});
// The share printed, as the clock counts it over CARD's frame.
const pOf = (s) => s.virtual_sdcard.file_position / CARD.gcodeEnd;

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
  if (Array.isArray(v)) return v.map(textIn).join('');
  if (!v || typeof v !== 'object') return String(v ?? '');
  return textIn(v.props.children);
};
const button = (nodes, words) =>
  nodes.filter((n) => n.type === 'button').find((n) => textIn(n).replace(/\s+/g, ' ').trim() === words);
const view = (app) => {
  const runs = [];
  const ctx = { app, t, run: (id, data = {}) => runs.push([id, data]) };
  return { ...rendered(html`<${Printing} app=${app} ctx=${ctx} />`), runs };
};
// The plan's live picture at pos, as the view works it out.
const liveOf = (s, pos) => {
  const plan = s.ace2k_u1.plan;
  const headOf = headOfIn(liveLanes(s, plan));
  return liveAt(segmentsOf(plan), plan.choice, headOf, pos);
};

test('the job: the plan of the running file with its choice, else no plan', () => {
  const s = demo();
  const job = printJob(appOf(s));
  assert.equal(job.file, 'objeto_5_cores_PLA_1h36m.gcode');
  assert.equal(job.plan, s.ace2k_u1.plan);
  assert.equal(job.clock.done, 2304);
  // 40 % in 2304 s: the file's rate and the slicer's 1:36 agree on 3456 s left.
  assert.deepEqual([job.clock.phase, job.clock.left, totalOf(job.clock)], ['running', 3456, 5760]);
  const other = demo();
  other.ace2k_u1.plan = { ...other.ace2k_u1.plan, file: 'other.gcode' };
  assert.equal(printJob(appOf(other)).plan, null);
  const unset = demo();
  unset.ace2k_u1.plan = { ...unset.ace2k_u1.plan, choice: null };
  assert.equal(printJob(appOf(unset)).plan, null);
  const empty = demo();
  empty.ace2k_u1.plan = { ...empty.ace2k_u1.plan, order: [] };
  assert.equal(printJob(appOf(empty)).plan, null);
  // Before the file's card comes: Klipper's whole-file progress, so a share shows at once (40 %).
  const early = printJob(appOf(demo(), { print: { files: [], cards: {} } }));
  assert.deepEqual([early.clock.p, early.clock.phase], [0.4, 'running']);
  const start = demo();
  start.virtual_sdcard = { file_position: 0, progress: 0 };
  start.print_stats.print_duration = 0;
  assert.equal(totalOf(printJob(appOf(start)).clock), 5760);
  assert.equal(printJob(appOf({ print_stats: { state: 'printing' } })).file, '');
});

test('the printing view: name, %, times, progress bar, Pause and Stop', () => {
  const { out, nodes, runs } = view(appOf(demo()));
  const text = textOf(out);
  assert.ok(text.includes('objeto_5_cores_PLA_1h36m'));
  assert.ok(text.includes(t('printing.pct', { pct: 40 })));
  assert.ok(text.includes(t('printing.time', { done: '0:38', total: '1:36', left: '0:58' })));
  assert.match(out, /<div class="prog"><i style="width:40%"><\/i><\/div>/);
  button(nodes, t('printing.pause')).props.onClick();
  button(nodes, t('printing.stop')).props.onClick();
  assert.deepEqual(runs, [
    ['print_pause', {}],
    ['print_cancel', {}],
  ]);
});

test('the next swap reads "Next spool swap · in ≈ h:mm" with its head, colours and bays', () => {
  const s = demo();
  const lv = liveOf(s, pOf(s));
  assert.ok(lv.next);
  const { out } = view(appOf(s));
  const text = textOf(out);
  assert.match(text, /Next spool swap · in ≈ \d+:\d\d/);
  // On the clock: (x − p) / (1 − p) × left, with 3456 s left at 40 %.
  const eta = fmtHM(Math.max(60, Math.round(((lv.next.x - pOf(s)) / (1 - pOf(s))) * 3456)));
  assert.ok(text.includes(t('printing.next', { eta })));
  const choice = s.ace2k_u1.plan.choice;
  assert.ok(
    text.includes(
      t('printing.change', { head: t('units.head', { n: lv.next.head }), from: lv.next.from + 1, to: lv.next.to + 1 }),
    ),
  );
  assert.ok(text.includes(t('printing.bays', { from: choice[lv.next.from], to: choice[lv.next.to] })));
});

test('the now card names the head, the colour and the bay', () => {
  const s = demo();
  const lv = liveOf(s, pOf(s));
  const text = textOf(view(appOf(s)).out);
  assert.ok(text.includes(t('printing.now_cap')));
  assert.match(text, new RegExp(`from bay ${lv.now.key}`));
  assert.ok(text.includes(`${t('units.head', { n: lv.now.head })} prints color ${lv.now.tool + 1}`));
});

test('during a swap the now card turns orange and says the print waits', () => {
  const s = demo();
  const plan = s.ace2k_u1.plan;
  // The first change after 40 % that loads another lane on its head, a hair after it starts.
  const next = liveOf(s, pOf(s)).next;
  s.virtual_sdcard = { file_position: Math.ceil(next.x * plan.size) + 1, progress: next.x };
  const { out } = view(appOf(s));
  const text = textOf(out);
  assert.ok(out.includes('class="nowc swap"'));
  assert.ok(text.includes(t('printing.swapping')));
  assert.ok(
    text.includes(
      t('printing.change', { head: t('units.head', { n: next.head }), from: next.from + 1, to: next.to + 1 }),
    ),
  );
  assert.ok(text.includes(t('printing.waits', { time: fmtHM(SWAP_S) })));
});

test('a swap that is over shows the Now card, though its part of the file is long', () => {
  const s = demo();
  const plan = s.ace2k_u1.plan;
  const next = liveOf(s, pOf(s)).next;
  // The change's part runs to the middle of the rest of the file: a long part.
  const at = Math.round(next.x * plan.size);
  const end = Math.round((next.x + 1) * 0.5 * plan.size);
  plan.order_at = plan.order_at.map((v) => (v > at ? Math.max(v, end) : v));
  // Well past one swap of print time after the change, still early in its part.
  const rate = pOf(s) / s.print_stats.print_duration; // share of the file per second
  const pos = next.x + rate * SWAP_S * 3;
  s.virtual_sdcard = { file_position: Math.ceil(pos * plan.size), progress: pos };
  s.print_stats.print_duration = pos / rate;
  const { out } = view(appOf(s));
  assert.ok(!out.includes('class="nowc swap"'));
  assert.ok(textOf(out).includes(t('printing.now_cap')));
});

test('at the end of the file the now card says finished and there is no next swap', () => {
  const s = demo();
  s.virtual_sdcard = { file_position: s.ace2k_u1.plan.size, progress: 1 };
  const text = textOf(view(appOf(s)).out);
  assert.ok(text.includes(t('printing.finished')));
  assert.ok(!text.includes(t('printing.no_change')));
  assert.ok(text.includes(t('printing.no_next')));
  assert.ok(text.includes(t('printing.next_cap')));
});

test('before the first change the now card says no colour yet; with no total the next swap has no ETA', () => {
  const s = demo();
  s.virtual_sdcard = { file_position: 10, progress: 0 };
  s.print_stats.print_duration = 0;
  // No slicer time in the card: nothing to count the time left from yet.
  const cards = { [FILE]: { modified: 0, card: { ...CARD, time: null } } };
  const text = textOf(view(appOf(s, { print: { files: [], cards } })).out);
  assert.ok(text.includes(t('printing.no_change')));
  assert.ok(text.includes(t('printing.next_cap')));
  assert.ok(!/in ≈/.test(text));
  assert.ok(text.includes(t('printing.elapsed', { done: '0:00' })));
});

test('a print with no plan shows progress and Pause/Stop only: no now, no next, no timeline (Review Focus 4)', () => {
  const s = demo();
  delete s.ace2k_u1.plan;
  const { out, nodes } = view(appOf(s));
  const text = textOf(out);
  assert.ok(text.includes(t('printing.pct', { pct: 40 })));
  assert.ok(button(nodes, t('printing.pause')) && button(nodes, t('printing.stop')));
  for (const k of ['printing.now_cap', 'printing.next_cap', 'timeline.cap']) assert.ok(!text.includes(t(k)), k);
  assert.ok(!out.includes('class="tl"') && !out.includes('class="dg"'));
});

test('paused: Resume instead of Pause; a pause or resume running disables the button', () => {
  const s = demo();
  s.print_stats.state = 'paused';
  const { nodes, runs } = view(appOf(s));
  button(nodes, t('printing.resume')).props.onClick();
  assert.deepEqual(runs, [['print_resume', {}]]);
  assert.equal(button(nodes, t('printing.pause')), undefined);
  s.ace2k_u1.resuming = true;
  assert.equal(button(view(appOf(s)).nodes, t('printing.resume')).props.disabled, true);
  const p = demo();
  assert.equal(
    button(view(appOf(p, { pending: new Set(['print:pause']) })).nodes, t('printing.pause')).props.disabled,
    true,
  );
});

test('the thumbnail of the running file shows once its card has loaded', () => {
  const s = demo();
  const card = { [s.print_stats.filename]: { modified: 0, card: { time: null, thumb: '/th.png', colours: null } } };
  const out = view(appOf(s, { print: { files: [], cards: card } })).out;
  assert.ok(out.includes('src="/th.png"'));
});

test('the view asks for the card of the running file (its thumbnail and slicer time)', () => {
  const runs = [];
  cardEffect({ run: (id, data) => runs.push([id, data]) }, 'x.gcode')();
  assert.deepEqual(runs, [['card_load', { file: 'x.gcode' }]]);
});

test('the Print tab shows the printing view while a print runs, the grid or plan otherwise', () => {
  const s = demo();
  const app = appOf(s);
  const out = renderHtml(html`<${App} app=${app} ctx=${{ app, t, run: () => {} }} />`);
  assert.ok(out.includes('<section class="printing"'));
  assert.ok(out.includes('class="tl"'));
  s.print_stats.state = 'standby';
  const idle = renderHtml(html`<${App} app=${app} ctx=${{ app, t, run: () => {} }} />`);
  assert.ok(!idle.includes('<section class="printing"'));
});

test('the units: the printing bay outlined green, bays loaded in other heads blue', () => {
  const s = demo();
  const app = appOf(s);
  const lv = liveOf(s, pOf(s));
  const marks = marksOf(app, lv.now);
  assert.equal(marks[lv.now.key], 'printing');
  // a:2 (T0), a:3 (T1) and b:1 (T2) are loaded; the head that prints keeps only its printing bay.
  const inHead = Object.keys(marks).filter((k) => marks[k] === 'in_head');
  assert.ok(inHead.length >= 1 && !inHead.includes(lv.now.key));
  assert.deepEqual(marksOf(app, null), Object.fromEntries(['a:2', 'a:3', 'b:1'].map((k) => [k, 'in_head'])));
  const out = textOf(view(app).out);
  assert.ok(out.includes(t('printing.mark_printing')) && out.includes(t('printing.mark_in_head')));
  const svg = renderHtml(
    html`<${Diagram} app=${app} ctx=${{ app, t, run: () => {} }} marks=${{ 'a:2': 'printing', 'b:1': 'in_head' }} />`,
  );
  assert.equal(svg.match(/class="mk printing"/g).length, 1);
  assert.equal(svg.match(/class="mk in_head"/g).length, 1);
  assert.equal(
    renderHtml(html`<${Diagram} app=${app} ctx=${{ app, t, run: () => {} }} />`).includes('class="mk '),
    false,
  );
});

test('on a phone the units are the lists, with the same marks', () => {
  const s = demo();
  const app = appOf(s, { ui: { tab: 'print', panel: null, sheet: false, narrow: true } });
  const out = view(app).out;
  assert.ok(out.includes('class="mlists"') && !out.includes('class="dg"'));
  const lists = renderHtml(
    html`<${MobileLists} app=${app} ctx=${{ app, t, run: () => {} }} marks=${{ 'a:1': 'printing', 'a:3': 'in_head' }} />`,
  );
  assert.equal(lists.match(/mrow[^"]*mk-printing/g).length, 1);
  assert.equal(lists.match(/mrow[^"]*mk-in_head/g).length, 1);
  assert.ok(textOf(lists).includes(t('printing.mark_printing')));
});

test('the timeline: a row per used head with its swaps, thick and thin parts, ▼ marks, axis and legend', () => {
  const s = demo();
  const plan = s.ace2k_u1.plan;
  const headOf = headOfIn(liveLanes(s, plan));
  const out = renderHtml(html`<${Timeline} plan=${plan} choice=${plan.choice} headOf=${headOf} t=${t} />`);
  const text = textOf(out);
  assert.equal(out.match(/class="tlr"/g).length, 3); // T0, T1, T2; T3 prints nothing
  for (const h of [0, 1, 2]) assert.ok(text.includes(t('units.head', { n: h })));
  assert.ok(text.includes(t('timeline.count', { n: 0 })));
  const marks = out.match(/class="sw-mark"/g).length;
  assert.ok(marks > 100);
  assert.ok(out.includes('class="act"') && out.includes('class="idle"'));
  for (const k of ['timeline.cap', 'timeline.legend_active', 'timeline.legend_idle']) assert.ok(text.includes(t(k)), k);
  assert.ok(text.includes(t('timeline.legend_swap', { time: fmtHM(SWAP_S) })));
  assert.ok(!out.includes('class="cur"') && !out.includes('class="past"'));
  const live = renderHtml(html`<${Timeline} plan=${plan} choice=${plan.choice} headOf=${headOf} pos=${0.4} t=${t} />`);
  assert.equal(live.match(/class="cur"/g).length, 3);
  assert.equal(live.match(/class="past" style="left:0%;width:40%"/g).length, 3);
  assert.ok(textOf(live).includes(t('timeline.now', { pct: 40 })));
});

test('the timeline rows are built once per plan, frame and heads: a render with fresh but equal inputs reuses them', () => {
  const s = demo();
  const plan = s.ace2k_u1.plan;
  const lanes = liveLanes(s, plan);
  // Each render hands a fresh choice, head lookup and frame, as PlanView does.
  const first = rowsOf(plan, { ...plan.choice }, headOfIn(lanes), { start: 10, end: 900 });
  assert.equal(rowsOf(plan, { ...plan.choice }, headOfIn(lanes), { start: 10, end: 900 }), first);
  // What the rows show changes: they are built again.
  const moved = rowsOf(plan, { ...plan.choice }, headOfIn(lanes), { start: 0, end: 900 });
  assert.notEqual(moved, first);
  const other = rowsOf(plan, { ...plan.choice }, () => 0, { start: 0, end: 900 });
  assert.notEqual(other, moved);
  assert.equal(
    rowsOf(plan, { ...plan.choice }, () => 0, null),
    rowsOf(plan, { ...plan.choice }, () => 0, null),
  );
  // The zoom buttons' glyphs come from the catalogue.
  const out = renderHtml(html`<${Timeline} plan=${plan} choice=${plan.choice} headOf=${headOfIn(lanes)} t=${t} />`);
  assert.ok(
    out.includes(`>${t('timeline.zoom_out_mark')}</button>`) && out.includes(`>${t('timeline.zoom_in_mark')}</button>`),
  );
});

test('the timeline: nothing to draw with no change on any head', () => {
  const s = demo();
  const plan = { ...s.ace2k_u1.plan, order: [] };
  assert.equal(renderHtml(html`<${Timeline} plan=${plan} choice=${{}} headOf=${() => 0} t=${t} />`), '');
  const none = { ...s.ace2k_u1.plan };
  assert.equal(renderHtml(html`<${Timeline} plan=${none} choice=${none.choice} headOf=${() => null} t=${t} />`), '');
});

test('the colour in words: the host name, else the bay code, else the file code, else nothing', () => {
  const tool = { colour: '#a1b2c3', material: 'PLA' };
  assert.equal(colourWords({ key: '1', colour: '#ff0000', colour_name: 'red', head: 0 }, tool), 'red');
  assert.equal(colourWords({ key: '1', colour: '#ff0000', head: 0 }, tool), '#FF0000');
  assert.equal(colourWords({ key: '1', colour: null, head: 0 }, tool), '#A1B2C3');
  assert.equal(colourWords(undefined, undefined), '');
});

test('a change with no colour or material in the file still draws and reads', () => {
  const s = demo();
  const plan = s.ace2k_u1.plan;
  plan.tools = plan.tools.map(() => ({ colour: null, material: null }));
  const text = textOf(view(appOf(s)).out);
  assert.match(text, /prints color \d+ · \S+ from bay/);
  assert.ok(!/ {2}from bay/.test(view(appOf(s)).out), 'no double space where the material would be');
  const out = renderHtml(
    html`<${Timeline} plan=${plan} choice=${plan.choice} headOf=${headOfIn(liveLanes(s, plan))} t=${t} />`,
  );
  assert.ok(!out.includes('--fil'));
});

test('the spool in words: colour and material, or the colour alone', () => {
  assert.equal(spoolWords('red', 'PLA', t), 'red PLA');
  assert.equal(spoolWords('red', null, t), 'red');
  assert.equal(spoolWords('red', '', t), 'red');
});

test('a mark pill is as wide as its word', () => {
  assert.equal(pillWidth('in head'), 62);
  assert.ok(pillWidth('a much longer word') > pillWidth('printing'));
});

test('with marks, a lane its head still reports as loaded reads "waiting", never "in the head"', () => {
  const s = demo(); // T0 holds a:2
  const app = appOf(s, { ui: { tab: 'print', panel: null, sheet: false, narrow: true } });
  const ctx = { app, t, run: () => {} };
  const plain = textOf(renderHtml(html`<${MobileLists} app=${app} ctx=${ctx} />`));
  assert.ok(plain.includes(t('panel.in_head')));
  const marked = renderHtml(
    html`<${MobileLists} app=${app} ctx=${ctx} marks=${{ 'a:1': 'printing', 'a:3': 'in_head', 'b:1': 'in_head' }} />`,
  );
  assert.ok(!textOf(marked).includes(t('panel.in_head')));
  assert.ok(!/class="mrow on/.test(marked));
  assert.match(textOf(marked), /a 2 PLA waiting/);
});

test('span clamps odd offsets into the band', () => {
  assert.deepEqual(span(0.25, 0.5), { left: 25, width: 25 });
  assert.deepEqual(span(-0.5, 0.2), { left: 0, width: 20 });
  assert.deepEqual(span(0.8, 1.7), { left: 80, width: 20 });
  assert.deepEqual(span(0.6, 0.4), { left: 60, width: 0 });
  assert.deepEqual(span(Number.NaN, 2), { left: 0, width: 100 });
  assert.deepEqual(span(0.2, Number.NaN), { left: 20, width: 80 });
});

test('the plan view ends with the timeline of the current pick', () => {
  const s = demo();
  s.print_stats.state = 'standby';
  const plan = { ...s.ace2k_u1.plan, set: false, choice: null };
  s.ace2k_u1.plan = plan;
  const app = appOf(s, { print: { open: true, files: [], file: plan.file, choice: {}, choiceId: null } });
  const out = renderHtml(html`<${PlanView} app=${app} ctx=${{ app, t, run: () => {} }} />`);
  assert.ok(out.includes('class="tl"'));
  assert.ok(!out.includes('class="cur"'));
});

test('the progress counts the G-code bytes only: a 3 % header does not shift it', () => {
  const s = demo();
  // The G-code runs from byte 183 520 (a 3 % header) to the end; the print is half way through it.
  const card = { ...CARD, gcodeStart: 183520 };
  s.virtual_sdcard = { ...s.virtual_sdcard, file_position: Math.round(183520 + (6117340 - 183520) / 2) };
  const job = printJob(appOf(s, { print: { files: [], cards: { [FILE]: { modified: 0, card } } } }));
  assert.ok(Math.abs(/** @type {number} */ (job.clock.p) - 0.5) < 1e-6);
  assert.deepEqual(job.frame, { start: 183520, end: 6117340 });
});

test('the header at the end and when stopped: "finishing", never 0:00 while it runs; nothing extrapolated', () => {
  const s = demo();
  s.virtual_sdcard = { ...s.virtual_sdcard, file_position: 6117000 };
  const end = textOf(view(appOf(s)).out);
  assert.ok(end.includes(t('printing.finishing', { done: '0:38' })), end);
  assert.ok(!end.includes('0:00 left'));
  const stopped = demo();
  stopped.print_stats.state = 'cancelled';
  assert.equal(printJob(appOf(stopped)).clock.left, null);
});

test('the shown share never goes back and is kept for one print; the status ingest forgets it once the print ends', () => {
  const s = demo();
  const app = appOf(s, { print: { files: [], cards: {} } });
  // Before the card: Klipper's whole-file progress, 40 %.
  assert.equal(printJob(app).clock.p, 0.4);
  // The card comes with a 3 % header: the frame share (38 %) replaces the pre-card 40 %, which was never kept.
  const card = { ...CARD, gcodeStart: 183520 };
  app.print = { ...app.print, cards: { [FILE]: { modified: 0, card } } };
  const framed = /** @type {number} */ (printJob(app).clock.p);
  assert.ok(framed < 0.4 && framed > 0.37, String(framed));
  const before = { ...app.print };
  // Rendering leaves the page state alone.
  assert.deepEqual(app.print, before);
  // A status without a position or progress: the share counted over the frame holds.
  app.status = { ...s, virtual_sdcard: {} };
  assert.equal(printJob(app).clock.p, framed);
  // A new print of the same file (its time printed starts again): the old share is not used.
  app.status = { ...s, virtual_sdcard: {}, print_stats: { ...s.print_stats, print_duration: 10 } };
  assert.equal(printJob(app).clock.p, null);
  // The ingest of an ended print forgets it: a later status of the same file starts afresh.
  app.status = s;
  printJob(app);
  ingestStatus({ ...app, snaps: {}, samples: [], events: [] }, { print_stats: { state: 'cancelled' } }, 0);
  app.status = { ...s, virtual_sdcard: {} };
  assert.equal(printJob(app).clock.p, null);
});

test('while no share is reported, the Now card says it is not known yet; the pill uses the same clock', () => {
  const s = demo();
  s.virtual_sdcard = {};
  const text = textOf(view(appOf(s)).out);
  assert.ok(text.includes(t('printing.now_unknown')), text);
  assert.ok(!text.includes(t('printing.no_change')));
  // The next swap says nothing while no share is known.
  assert.ok(!text.includes(t('printing.next_cap')) && !text.includes(t('printing.no_next')));
  // The pill: the clock's share over the G-code bytes (a 3 % header), not Klipper's whole-file progress.
  const header = demo();
  const card = { ...CARD, gcodeStart: 183520 };
  header.virtual_sdcard = { file_position: Math.round(183520 + (6117340 - 183520) / 2), progress: 0.515 };
  const app = appOf(header, { print: { files: [], cards: { [FILE]: { modified: 0, card } } } });
  const top = textOf(renderHtml(html`<${App} app=${app} ctx=${{ app, t, run: () => {} }} />`));
  assert.ok(top.includes(t('top.printing', { pct: 50 })), top);
});

test("the plan view's timeline counts over the same frame as the print view (fileFrame)", () => {
  const s = demo();
  const app = appOf(s, {
    print: { files: [], cards: { [FILE]: { modified: 0, card: { ...CARD, gcodeStart: 53045 } } } },
  });
  assert.deepEqual(fileFrame(app, FILE), { start: 53045, end: 6117340 });
  // Without a card: the running file's size, else its plan's size.
  const bare = appOf(s, { print: { files: [], cards: {} } });
  assert.deepEqual(fileFrame(bare, FILE), { start: 0, end: 6117340 });
  // The first change sits at the frame's start: share 0.
  assert.equal(segmentsOf(s.ace2k_u1.plan, fileFrame(app, FILE))[0].x, 0);
});

test('a share before the first card is not kept: 0 at the start, not ~2 %; a re-read keeps the share', () => {
  const s = demo();
  s.virtual_sdcard = { file_position: 183520, progress: 0.03 };
  const app = appOf(s, { print: { files: [], cards: {} } });
  assert.equal(printJob(app).clock.p, 0.03);
  app.print = { ...app.print, cards: { [FILE]: { modified: 0, card: { ...CARD, gcodeStart: 183520 } } } };
  assert.equal(printJob(app).clock.p, 0);
  // Half way; then the card is invalidated and read again: the whole-file 51.5 % is not shown over the 50 %.
  s.virtual_sdcard = { file_position: Math.round(183520 + (6117340 - 183520) / 2), progress: 0.515 };
  const half = /** @type {number} */ (printJob(app).clock.p);
  assert.ok(Math.abs(half - 0.5) < 1e-6);
  app.print = { ...app.print, cards: { [FILE]: { modified: 1, card: null } } };
  assert.equal(printJob(app).clock.p, half);
});

test('a card without the bytes keeps no share; the header-free share wins when they arrive; complete shows 1', () => {
  forgetEndedPrint({ print_stats: { state: 'complete' } }); // Nothing kept from a test before.
  const s = demo();
  s.virtual_sdcard = { file_position: 183520, progress: 0.03 };
  const bare = { ...CARD, gcodeStart: null, gcodeEnd: null };
  const app = appOf(s, { print: { files: [], cards: { [FILE]: { modified: 0, card: bare } } } });
  assert.ok(Math.abs(/** @type {number} */ (printJob(app).clock.p) - 0.03) < 1e-3);
  app.print = { ...app.print, cards: { [FILE]: { modified: 0, card: { ...CARD, gcodeStart: 183520 } } } };
  assert.equal(printJob(app).clock.p, 0);
  // Complete: 1, not the share kept.
  app.status = { ...s, print_stats: { ...s.print_stats, state: 'complete' }, virtual_sdcard: { file_position: 0 } };
  assert.equal(printJob(app).clock.p, 1);
});

const tl = (extra) => {
  const s = demo();
  const plan = s.ace2k_u1.plan;
  const headOf = headOfIn(liveLanes(s, plan));
  const f = (props) =>
    renderHtml(html`<${Timeline} plan=${plan} choice=${plan.choice} headOf=${headOf} t=${t} ...${props} />`);
  return f(extra);
};

test('the bars draw the stretch only, and the count stays the whole print', () => {
  const whole = tl({});
  const zoomed = tl({ view: { a: 0, b: 0.1 } });
  const counts = (m) => [...m.matchAll(/class="lb">.*?<span>(.*?)<\/span>/g)].map((x) => x[1]);
  assert.deepEqual(counts(zoomed), counts(whole));
  assert.ok((zoomed.match(/class="act"/g) ?? []).length < (whole.match(/class="act"/g) ?? []).length);
  assert.ok((zoomed.match(/class="sw-mark"/g) ?? []).length < (whole.match(/class="sw-mark"/g) ?? []).length);
});

test('the now line and the printed part are drawn only where they fall in the stretch', () => {
  assert.equal(tl({ pos: 0.4, view: { a: 0.5, b: 0.9 } }).match(/class="(cur|past)"/g), null);
  const past = tl({ pos: 0.95, view: { a: 0.5, b: 0.9 } });
  assert.equal(past.match(/class="past" style="left:0%;width:100%"/g).length, 3);
  assert.equal(past.match(/class="cur"/g), null);
  assert.equal(tl({ pos: 0.7, view: { a: 0.5, b: 0.9 } }).match(/class="cur" style="left:50%"/g).length, 3);
});

test('the strip, its window and the zoom buttons', () => {
  const m = tl({ view: { a: 0.2, b: 0.4 } });
  assert.match(m, /class="zstrip"/);
  assert.match(m, /class="zwin" style="left:20%;width:20%"/);
  assert.ok(m.includes(t('timeline.whole')));
  assert.ok(!/disabled/.test(m));
  assert.match(tl({}), /aria-label="Zoom out" disabled/);
  assert.ok(!/aria-label="Zoom in" disabled/.test(tl({})));
  assert.match(tl({ view: { a: 0.5, b: 0.51 } }), /aria-label="Zoom in" disabled/);
});

test('the axis says where the stretch is, in the slicer time', () => {
  const v = { a: 0, b: 0.5 };
  const m = tl({ timeS: 7200, view: v });
  for (const label of axisLabels(t, v, 7200)) assert.ok(m.includes(label), label);
});

test('stripDrag: move, resize and pick map to the zoom functions', () => {
  const v = { a: 0.2, b: 0.4 };
  assert.deepEqual(stripDrag('move', v, 0.3, 0.5), moveTo(v, 0.4));
  assert.deepEqual(stripDrag('a', v, 0.2, 0.1), resize(v, 'a', 0.1));
  assert.deepEqual(stripDrag('b', v, 0.4, 0.9), resize(v, 'b', 0.9));
  assert.deepEqual(stripDrag('pick', v, 0.7, 0.3), pickView(0.7, 0.3));
});

test('the strip: a drag on the window, an edge and the bare strip', () => {
  const calls = [];
  const el = {
    getBoundingClientRect: () => ({ left: 100, width: 200 }),
    setPointerCapture: (id) => calls.push(id),
  };
  const drag = { current: null };
  const sets = [];
  const v = { a: 0.2, b: 0.4 };
  const h = stripPointer({ current: el }, drag, v, (x) => sets.push(x));
  const ev = (cls, x, o = {}) => ({
    target: { classList: { contains: (c) => c === cls } },
    clientX: 100 + x * 200,
    pointerId: 7,
    button: 0,
    buttons: 1,
    ...o,
  });
  h.move(ev('', 0.5)); // no drag yet: nothing
  h.down(ev('zwin', 0.3, { button: 2 })); // not the primary button: no drag
  assert.equal(drag.current, null);
  h.down(ev('zwin', 0.3));
  assert.deepEqual(drag.current, { kind: 'move', x0: 0.3, v, id: 7 });
  h.move(ev('', 0.5, { pointerId: 8 })); // another pointer: ignored
  h.up(ev('', 0.5, { pointerId: 8 }));
  assert.ok(drag.current);
  h.move(ev('', 0.5));
  h.down(ev('zl', 0.2));
  assert.equal(drag.current.kind, 'a');
  h.down(ev('zr', 0.4));
  assert.equal(drag.current.kind, 'b');
  h.down(ev('', -1)); // outside the strip: clamped to 0
  assert.deepEqual(drag.current, { kind: 'pick', x0: 0, v, id: 7 });
  h.move(ev('', 2)); // clamped to 1
  assert.deepEqual(sets, [moveTo(v, 0.4), pickView(0, 1)]);
  h.move(ev('', 0.5, { buttons: 0 })); // released out of sight: the drag ends, nothing set
  assert.equal(drag.current, null);
  assert.equal(sets.length, 2);
  h.down(ev('', 0.1));
  h.up(ev('', 0.1));
  assert.equal(drag.current, null);
  assert.equal(calls.length, 5);
});

test('the zoom buttons change the stretch', () => {
  const v = { a: 0.2, b: 0.4 };
  const sets = [];
  const act = zoomActions(v, (x) => sets.push(x));
  act.out();
  act.in();
  act.whole();
  assert.deepEqual(sets, [zoomOut(v), zoomIn(v), WHOLE]);
  const seen = [];
  const prev = options.vnode;
  options.vnode = (n) => {
    seen.push(n);
    prev?.(n);
  };
  tl({ view: v });
  options.vnode = prev;
  assert.equal(seen.filter((n) => n.type === 'button').length, 3);
});
