import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  openPrint,
  replanChosen,
  planFile,
  startPrint,
  keepChoice,
  loadCard,
  ensureRunningCard,
  RUNNING_RETRY_MS,
  CARD_SLOTS,
} from '../js/printflow.js';
import { demoOtherPlan, DEMO_OTHER_MARGIN_MS, forgetDemoFiles } from '../js/demo.js';
import { DEFAULT_OPTIONS, asIsChoice, carriedChoice, chosen } from '../js/print-plan.js';
import { ACTIONS } from '../js/actions.js';
import { loadEnglish } from './helpers.js';
import { CONNECTION_CLOSED } from '../js/conn.js';
import { ingestStatus } from '../js/live.js';
import { otherDevice, planNow, readingOf } from '../js/state.js';

loadEnglish();

const t = (key, vars) => (vars ? `${key} ${JSON.stringify(vars)}` : key);
const plan = {
  file: 'p.gcode',
  mtime: 1,
  order: [0, 1],
  tools: [
    { lane: '1', material: 'PLA' },
    { lane: '2', material: 'PLA' },
  ],
  lanes: [
    { key: '1', head: 0, material: 'PLA' },
    { key: '2', head: 1, material: 'PLA' },
  ],
};
const SET = { '-:1': { material_set: true }, '-:2': { material_set: true } };
const closed = { open: false, files: [], file: null, choice: {}, choiceId: null };
// The unnamed unit: bays 1 and 2 hold a tagged PLA spool, each on a head of its own; bays 3 and 4 are empty.
const pla = (uid) => ({
  mode: 'idle',
  insert: true,
  rest: true,
  tag: { state: 'read', uid, record: { material: 'PLA' } },
});
const BAYS = [pla('U1'), pla('U2'), { insert: false }, { insert: false }];
const HEADS = [
  [{ unit: null, lane: 1 }],
  [{ unit: null, lane: 2 }],
  [{ unit: null, lane: 3 }],
  [{ unit: null, lane: 4 }],
];

// sendResult is what sendScript resolves to. onSend(script, opts) runs inside it, as when quiet handles a refusal.
function harness({
  demo = false,
  online = true,
  planned = plan,
  print = closed,
  status = {},
  files = [{ path: 'a.gcode', modified: 1 }],
  filesError = null,
  sendResult = true,
  onSend = null,
  answer = true,
  sleep = async () => {},
} = {}) {
  const calls = { toast: [], render: 0, sendScript: [], confirm: [] };
  const app = {
    online,
    klippy: 'ready',
    ui: { tab: 'print', panel: null, sheet: false },
    pending: new Set(),
    print: { ...print },
    printOptions: { ...DEFAULT_OPTIONS, flow: true },
    // The adapter has the material of the two tagged spools set.
    status: { ace2k_u1: { hooked: true, plan: planned, heads: HEADS, lanes: SET }, ace2k: { lanes: BAYS }, ...status },
    conn: {
      files: async () => {
        if (filesError) throw new Error(filesError);
        return files;
      },
    },
  };
  const ctx = {
    app,
    demo,
    t,
    now: () => 50,
    sleep,
    toast: (m) => calls.toast.push(m),
    render: () => {
      calls.render += 1;
    },
    // The question Print asks while steps are pending, answered with answer.
    confirm: (q) => {
      calls.confirm.push(q);
      return answer;
    },
    sendScript: async (script, n, opts) => {
      calls.sendScript.push([script, n, opts.key, opts.once ?? false]);
      onSend?.(script, opts);
      return sendResult;
    },
  };
  return { app, ctx, calls };
}

test('openPrint: the first open shows the grid and sends no ACEK_PLAN, though the host has a live plan', async () => {
  const { app, ctx, calls } = harness();
  await openPrint(ctx);
  assert.deepEqual([app.print.open, app.print.file, calls.sendScript.length], [true, null, 0]);
  // A file this page chose stays open on a later open; "← Files" clears it and the grid stays.
  app.print = { ...app.print, file: 'mine.gcode' };
  await openPrint(ctx);
  assert.deepEqual([app.print.file, calls.sendScript.length], ['mine.gcode', 0]);
  app.print = { ...app.print, file: null };
  await openPrint(ctx);
  assert.deepEqual([app.print.file, calls.sendScript.length], [null, 0]);
});

test('openPrint: the tab opens at once, the chosen file stays and nothing is planned, the files are listed afresh', async () => {
  const { app, ctx, calls } = harness({ print: { ...closed, file: 'p.gcode' } });
  await openPrint(ctx);
  assert.deepEqual(
    [app.print.open, app.print.file, app.print.files],
    [true, 'p.gcode', [{ path: 'a.gcode', modified: 1 }]],
  );
  assert.deepEqual(calls.sendScript, []);
  assert.equal(calls.render, 2);
});

// The demo files' data, read once by the page and so shared by the tests that use it.
const demoFetch = async () => ({
  json: async () => ({
    files: [{ path: 'd.gcode', modified: 5 }],
    cards: { 'd.gcode': { colours: null } },
    plans: {
      'd.gcode': { file: 'd.gcode', mtime: 5, tools: [], order: [] },
      'big.gcode': { file: 'big.gcode', mtime: 5, tools: [], order: [], delayMs: 14000 },
    },
  }),
});

test('planFile in the demo: a plan with delayMs reads first, with print:file pending, then arrives', async () => {
  let release = () => {};
  const gate = new Promise((r) => (release = r));
  let wait = null;
  const demo = harness({
    demo: true,
    print: { ...closed, file: 'big.gcode' },
    sleep: (ms) => {
      wait = ms;
      return gate;
    },
  });
  forgetDemoFiles();
  const real = globalThis.fetch;
  globalThis.fetch = demoFetch;
  try {
    planFile(demo.ctx, 'big.gcode');
    await new Promise((r) => setImmediate(r));
    assert.equal(wait, 14000);
    assert.equal(demo.app.pending.has('print:file'), true);
    assert.equal(demo.app.print.reading.file, 'big.gcode');
    assert.equal(demo.app.status.ace2k_u1.plan.file, 'p.gcode', 'the old plan stays while it reads');
    release();
    await new Promise((r) => setImmediate(r));
    assert.equal(demo.app.pending.has('print:file'), false);
    assert.equal(demo.app.status.ace2k_u1.plan.file, 'big.gcode');
    assert.equal('delayMs' in demo.app.status.ace2k_u1.plan, false);
    assert.equal(demo.app.print.reading, null, 'the reading ends with its plan');
    // Another file chosen meanwhile: the late plan is dropped.
    demo.app.print = { ...demo.app.print, file: null };
    demo.app.status.ace2k_u1.plan = { file: 'x' };
    planFile(demo.ctx, 'big.gcode');
    demo.app.print = { ...demo.app.print, file: 'other.gcode' };
    await new Promise((r) => setImmediate(r));
    assert.equal(demo.app.status.ace2k_u1.plan.file, 'x');
  } finally {
    globalThis.fetch = real;
    forgetDemoFiles();
  }
});

test('openPrint: offline nothing is planned, a print in progress keeps its plan, the demo only opens', async () => {
  const off = harness({ online: false });
  await openPrint(off.ctx);
  assert.deepEqual(off.calls.sendScript, []);
  const busy = harness({ status: { print_stats: { state: 'printing' } } });
  await openPrint(busy.ctx);
  assert.deepEqual(busy.calls.sendScript, []);
  forgetDemoFiles(); // This test counts the reads, so it starts from none.
  const demo = harness({ demo: true });
  const real = globalThis.fetch;
  let reads = 0;
  globalThis.fetch = async () => {
    reads += 1;
    return demoFetch();
  };
  try {
    await openPrint(demo.ctx);
    assert.deepEqual(
      [demo.app.print.open, demo.app.print.files, demo.calls.render],
      [true, [{ path: 'd.gcode', modified: 5 }], 2],
    );
    await loadCard(demo.ctx, 'd.gcode'); // The demo's card comes from the same file, read once.
    await loadCard(demo.ctx, 'missing.gcode'); // No card for it: an empty one.
    assert.deepEqual(demo.app.print.cards['d.gcode'].card, { colours: null });
    assert.equal(demo.app.print.cards['missing.gcode'].card.colours, null);
    // A card picked in the demo gets the demo's plan where the adapter's goes, and nothing is sent.
    demo.app.print.merges = { 1: 0 };
    planFile(demo.ctx, 'd.gcode');
    assert.deepEqual(demo.app.print.merges, {});
    await new Promise((r) => setImmediate(r));
    assert.equal(demo.app.status.ace2k_u1.plan.file, 'd.gcode');
    assert.equal(demo.app.status.ace2k_u1.hooked, true);
    planFile(demo.ctx, 'missing.gcode'); // No plan for it: the status keeps the last one.
    await new Promise((r) => setImmediate(r));
    assert.equal(demo.app.status.ace2k_u1.plan.file, 'd.gcode');
    assert.deepEqual(demo.calls.sendScript, []);
    assert.equal(reads, 1);
  } finally {
    globalThis.fetch = real;
    forgetDemoFiles();
  }
});

test('loadCard: a path is fetched once per modified time, a failure leaves an empty card, a stale answer is dropped', async () => {
  const { app, ctx, calls } = harness();
  const asked = [];
  app.conn.fileCard = async (path) => {
    asked.push(path);
    if (path === 'bad.gcode') throw new Error('x');
    return { colours: [], time: 60, thumb: null, modified: 1 };
  };
  await loadCard(ctx, 'a.gcode');
  await loadCard(ctx, 'a.gcode');
  assert.deepEqual(asked, ['a.gcode']);
  assert.equal(app.print.cards['a.gcode'].card.time, 60);
  app.print = { ...app.print, files: [{ path: 'a.gcode', modified: 2 }] };
  await loadCard(ctx, 'a.gcode');
  assert.deepEqual(asked, ['a.gcode', 'a.gcode']);
  assert.equal(app.print.cards['a.gcode'].modified, 2);
  await loadCard(ctx, 'bad.gcode');
  assert.deepEqual(app.print.cards['bad.gcode'], {
    modified: 0,
    card: { colours: null, time: null, thumb: null, modified: null },
  });
  // A newer request for the same path replaces the entry while this one waits.
  /** @type {() => void} */
  let release = () => {};
  app.conn.fileCard = () => new Promise((r) => (release = () => r({ colours: [], time: 1, thumb: null, modified: 1 })));
  app.print = { ...app.print, files: [{ path: 'w.gcode', modified: 1 }] };
  const slow = loadCard(ctx, 'w.gcode');
  app.print = { ...app.print, cards: { ...app.print.cards, 'w.gcode': { modified: 9, card: null } } };
  release();
  await slow;
  assert.equal(app.print.cards['w.gcode'].modified, 9);
  assert.ok(calls.render > 0);
});

test('loadCard: at most CARD_SLOTS cards load at once; the others wait in order, a failure frees its slot', async () => {
  const { app, ctx } = harness();
  const paths = ['1', '2', '3', '4', '5', '6', '7'].map((n) => `${n}.gcode`);
  app.print = { ...app.print, files: paths.map((path) => ({ path, modified: 1 })) };
  const open = new Map();
  const started = [];
  let most = 0;
  app.conn.fileCard = (path) =>
    new Promise((ok, no) => {
      started.push(path);
      open.set(
        path,
        path === '2.gcode' ? () => no(new Error('x')) : () => ok({ colours: [], time: 1, thumb: null, modified: 1 }),
      );
      most = Math.max(most, open.size);
    });
  const all = paths.map((p) => loadCard(ctx, p));
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(CARD_SLOTS, 4);
  assert.deepEqual(started, paths.slice(0, 4));
  const finish = async (p) => {
    const done = open.get(p);
    open.delete(p);
    done();
    await new Promise((r) => setTimeout(r, 0));
  };
  await finish('2.gcode'); // a failure frees its slot too
  assert.deepEqual(started, paths.slice(0, 5));
  await finish('1.gcode');
  await finish('3.gcode');
  assert.deepEqual(started, paths);
  for (const p of [...open.keys()]) await finish(p);
  await Promise.all(all);
  assert.equal(most, CARD_SLOTS);
  assert.equal(app.print.cards['7.gcode'].card.time, 1);
  // With the queue empty, a new card starts at once.
  app.print = { ...app.print, files: [...app.print.files, { path: '8.gcode', modified: 1 }] };
  const eighth = loadCard(ctx, '8.gcode');
  assert.equal(started.at(-1), '8.gcode');
  await finish('8.gcode');
  await eighth;
});

test('openPrint: a failed listing is toasted', async () => {
  const { ctx, calls } = harness({ filesError: 'no files' });
  await openPrint(ctx);
  assert.deepEqual(calls.toast, ['toast.print_files {"msg":"no files"}']);
  assert.equal(calls.render, 2);
});

test('openPrint: a file chosen while the listing is in flight keeps the fresh list (the list lands on the new app.print)', async () => {
  const { app, ctx } = harness();
  /** @type {(files: string[]) => void} */
  let release = () => {};
  app.conn.files = () =>
    new Promise((resolve) => {
      release = resolve;
    });
  const opened = openPrint(ctx);
  planFile(ctx, 'b.gcode'); // app.print is replaced while the listing waits
  release([{ path: 'new.gcode', modified: 2 }]);
  await opened;
  assert.deepEqual([app.print.file, app.print.files], ['b.gcode', [{ path: 'new.gcode', modified: 2 }]]);
});

test('planFile: the file is chosen with the default options and planned; none or a bad name does nothing useful', () => {
  const { app, ctx, calls } = harness({ print: { ...closed, choice: { 0: 'x' }, choiceId: 'old' } });
  planFile(ctx, '');
  assert.deepEqual(calls.sendScript, []);
  planFile(ctx, 'b.gcode');
  assert.deepEqual(
    [app.print.file, app.print.choice, app.print.choiceId, app.print.choiceTools, app.printOptions],
    ['b.gcode', {}, null, null, DEFAULT_OPTIONS],
  );
  assert.deepEqual(calls.sendScript, [['ACEK_PLAN FILE="b.gcode"', null, 'print:file', false]]);
  planFile(ctx, 'a#b.gcode');
  assert.equal(calls.sendScript.length, 1);
  assert.match(calls.toast[0], /^toast\.refused/);
});

const withFile = { ...closed, open: true, file: 'p.gcode' };

test("startPrint: sends the plan's script once under print:go, then closes the panel", async () => {
  const { app, ctx, calls } = harness({ print: withFile });
  await startPrint(ctx);
  assert.equal(calls.sendScript.length, 1);
  assert.deepEqual(calls.sendScript[0].slice(1), [null, 'print:go', true]);
  assert.match(calls.sendScript[0][0], /^ACEK_PLAN_SET FILE="p\.gcode" T0=1 T1=2\nSDCARD_PRINT_FILE_WITH_PARAMETERS/);
  assert.deepEqual(
    [app.print.open, app.print.file, app.print.choice, app.print.choiceTools, calls.render],
    [false, null, {}, null, 1],
  );
});

test("after a print starts, Units -> Print plans nothing and another device's plan shows no notice", async () => {
  const { app, ctx, calls } = harness({ print: withFile });
  await startPrint(ctx);
  const sent = calls.sendScript.length;
  app.print = { ...app.print, open: true }; // the grid mounting opens the tab again
  app.status = { ...app.status, ace2k_u1: { ...app.status.ace2k_u1, plan: { ...plan, file: 'theirs.gcode' } } };
  replanChosen(ctx);
  assert.equal(calls.sendScript.length, sent); // no file chosen: no re-plan
  assert.equal(otherDevice(app), null); // and no false notice
});

test('startPrint closes an open picker or Options panel, and keeps a file chosen while Print was in flight', async () => {
  for (const kind of ['pick', 'opts']) {
    const { app, ctx } = harness({ print: withFile });
    app.ui = { ...app.ui, panel: { kind } };
    await startPrint(ctx);
    assert.equal(app.ui.panel, null, kind);
  }
  const { app, ctx } = harness({ print: withFile });
  const sending = startPrint(ctx);
  app.print = { ...app.print, file: 'later.gcode' }; // chosen while the script was in flight
  await sending;
  assert.deepEqual([app.print.open, app.print.file], [false, 'later.gcode']);
});

test('startPrint: the demo and a refused start leave the panel open', async () => {
  const demo = harness({ print: withFile, demo: true });
  await startPrint(demo.ctx);
  assert.equal(demo.app.print.open, true);
  const refused = harness({ print: withFile, sendResult: false });
  await startPrint(refused.ctx);
  assert.deepEqual([refused.app.print.open, refused.calls.render], [true, 0]);
});

test('startPrint: nothing without a plan, while Print is pending, when the colours do not fit the bays or past the tool limit', async () => {
  const none = harness({ planned: null, print: withFile });
  await startPrint(none.ctx);
  const pending = harness({ print: withFile });
  pending.app.pending.add('print:go');
  await startPrint(pending.ctx);
  // Five colours for four bays: the placement does not fit.
  const five = Array.from({ length: 5 }, () => ({ lane: null, material: 'PLA' }));
  const nofit = harness({ print: withFile, planned: { ...plan, order: [0, 1, 2, 3, 4], tools: five } });
  await startPrint(nofit.ctx);
  const high = harness({
    print: withFile,
    planned: { ...plan, order: [0, 32], tools: Array.from({ length: 33 }, () => ({ lane: '1', material: 'PLA' })) },
  });
  await startPrint(high.ctx);
  for (const [name, h] of Object.entries({ none, pending, nofit, high }))
    assert.deepEqual(h.calls.sendScript, [], name);
});

test('startPrint: nothing while offline or while a print is on, whatever the button showed', async () => {
  const off = harness({ print: withFile, online: false });
  await startPrint(off.ctx);
  const busy = harness({ print: withFile, status: { print_stats: { state: 'printing' } } });
  await startPrint(busy.ctx);
  for (const [name, h] of Object.entries({ off, busy })) {
    assert.deepEqual(h.calls.sendScript, [], name);
    assert.equal(h.app.print.open, true, name);
  }
});

test('startPrint: a script that cannot be built is toasted and nothing is sent', async () => {
  const { ctx, calls } = harness({
    print: { ...withFile, file: 'a#b.gcode' },
    planned: { ...plan, file: 'a#b.gcode' },
  });
  await startPrint(ctx);
  assert.match(calls.toast[0], /^toast\.refused/);
  assert.deepEqual(calls.sendScript, []);
});

test('startPrint: a file changed since its plan is planned again, with its toast, and the panel stays open', async () => {
  const { app, ctx, calls } = harness({
    print: { ...withFile, choice: { 0: '2' }, choiceId: 'p.gcode\n1' },
    sendResult: false,
    onSend: (script, opts) => {
      if (opts.key === 'print:go') opts.quiet('the file changed since its plan');
    },
  });
  await startPrint(ctx);
  assert.deepEqual(
    calls.sendScript.map((c) => c[2]),
    ['print:go', 'print:file'],
  );
  assert.equal(calls.sendScript[1][0], 'ACEK_PLAN FILE="p.gcode"');
  assert.equal(calls.toast.length, 1);
  // the choice stays for the new plan to carry over
  assert.deepEqual([app.print.open, app.print.choice, app.print.choiceId], [true, { 0: '2' }, 'p.gcode\n1']);
});

// The user picks a:3 for every tool, while the suggestion for T3 is a:2 (head 1).
const benchPlan = (mtime) => ({
  file: 'p.gcode',
  mtime,
  order: [0, 1, 2, 3],
  tools: [
    { lane: 'a:3', why: 'closest', material: 'PLA' },
    { lane: 'a:3', why: 'closest', material: 'PLA' },
    { lane: 'a:3', why: 'closest', material: 'PLA' },
    { lane: 'a:2', why: 'closest', material: 'PLA' },
  ],
  lanes: [
    { key: 'a:1', head: 0, material: 'PLA' },
    { key: 'a:2', head: 0, material: 'PLA' },
    { key: 'a:3', head: 1, material: 'PLA' },
  ],
});
const pickEl = (n, key) => ({ dataset: { n: String(n), key } });

test('the lanes the operator chose survive the plan being made again for the same file (T3=a:3, not a:2)', () => {
  const { app, ctx } = harness({ planned: benchPlan(1), print: withFile });
  for (const n of [0, 1, 2, 3]) ACTIONS.pick(ctx, pickEl(n, 'a:3'));
  app.status = { ...app.status, ace2k_u1: { hooked: true, plan: benchPlan(2) } }; // Print… planned it again
  // One bay holds one spool: the colours that pick a bay another colour prints from join it, so all four print
  // from a:3, and that carries over.
  const kept = /** @type {NonNullable<ReturnType<typeof carriedChoice>>} */ (carriedChoice(app.print, benchPlan(2)));
  const sent = asIsChoice(benchPlan(2), chosen(benchPlan(2), kept.choice), kept.merges);
  assert.deepEqual(sent, { 0: 'a:3', 1: 'a:3', 2: 'a:3', 3: 'a:3' });
});

test('keepChoice: a plan made again for the same file carries the choice over; a lane no longer offered is dropped, in one toast', () => {
  const print = {
    ...withFile,
    choice: { 0: 'a:3', 1: 'a:1', 3: 'a:3', 5: 'a:2' },
    choiceId: 'p.gcode\n1',
    choiceTools: benchPlan(1).tools,
  };
  const again = benchPlan(2);
  again.lanes = [again.lanes[0], { ...again.lanes[1], empty: true }, again.lanes[2]];
  const { app, ctx, calls } = harness({ planned: again, print });
  keepChoice(ctx);
  assert.deepEqual(
    [app.print.choice, app.print.choiceId, app.print.choiceTools],
    [{ 0: 'a:3', 1: 'a:1', 3: 'a:3' }, 'p.gcode\n2', again.tools],
  );
  assert.deepEqual(calls.toast, ['print.choice_dropped {"choices":"print.colour_n {\\"n\\":6}: a:2"}']); // T5 is gone
  keepChoice(ctx); // the same plan, so nothing more
  assert.equal(calls.toast.length, 1);
  const empty = harness({ planned: again, print: { ...print, choice: { 2: 'a:2' } } });
  keepChoice(empty.ctx); // a:2's bay is empty now
  assert.deepEqual(
    [empty.app.print.choice, empty.calls.toast],
    [{}, ['print.choice_dropped {"choices":"print.colour_n {\\"n\\":3}: a:2"}']],
  );
});

test('keepChoice: every lane still offered moves silently; the same plan, another file, no choice or no plan do nothing', () => {
  const print = { ...withFile, choice: { 0: 'a:1', 1: null }, choiceId: 'p.gcode\n1', choiceTools: benchPlan(1).tools };
  const kept = harness({ planned: benchPlan(2), print });
  keepChoice(kept.ctx);
  assert.deepEqual(
    [kept.app.print.choice, kept.app.print.choiceId, kept.calls.toast],
    [{ 0: 'a:1' }, 'p.gcode\n2', []],
  );
  for (const [planned, p] of [
    [benchPlan(1), print],
    [
      { ...benchPlan(2), file: 'q.gcode' },
      { ...print, file: null },
    ],
    [benchPlan(2), { ...print, choiceId: null }],
    [null, print],
  ]) {
    const h = harness({ planned, print: p });
    keepChoice(h.ctx);
    assert.equal(h.app.print.choice, p.choice);
    assert.deepEqual([h.app.print.choiceId, h.calls.toast], [p.choiceId, []]);
  }
});

test('keepChoice: a tool whose colour changed in the new plan loses its lane, named in the same toast', () => {
  const print = {
    ...withFile,
    choice: { 0: 'a:3', 3: 'a:3' },
    choiceId: 'p.gcode\n1',
    choiceTools: benchPlan(1).tools,
  };
  const resliced = benchPlan(2);
  resliced.tools[3] = { ...resliced.tools[3], colour: '#00ff00' };
  const { app, ctx, calls } = harness({ planned: resliced, print });
  keepChoice(ctx);
  assert.deepEqual(app.print.choice, { 0: 'a:3' });
  assert.deepEqual(calls.toast, ['print.choice_dropped {"choices":"print.colour_n {\\"n\\":4}: a:3"}']);
});

// --- the file's thumbnail ------------------------------------------------------------------------

test("planFile and openPrint fetch the file's thumbnail; it shows for that file only", async () => {
  const { app, ctx } = harness({ print: { ...closed, file: 'p.gcode' } });
  const asked = [];
  app.conn.thumbnail = async (file) => {
    asked.push(file);
    return `/t/${file}.png`;
  };
  await openPrint(ctx);
  await new Promise((r) => setImmediate(r));
  assert.deepEqual(app.print.thumb, { file: 'p.gcode', url: '/t/p.gcode.png' });
  planFile(ctx, 'a.gcode');
  await new Promise((r) => setImmediate(r));
  assert.deepEqual(asked, ['p.gcode', 'a.gcode']);
  assert.deepEqual(app.print.thumb, { file: 'a.gcode', url: '/t/a.gcode.png' });
});

test('a thumbnail that arrives after another file was chosen, none, or a failure, shows nothing', async () => {
  const { app, ctx, calls } = harness();
  let answer = null;
  app.conn.thumbnail = () =>
    new Promise((resolve) => {
      answer = resolve;
    });
  planFile(ctx, 'a.gcode');
  app.print = { ...app.print, file: 'b.gcode' }; // chosen meanwhile
  answer('/t/a.png');
  await new Promise((r) => setImmediate(r));
  assert.equal(app.print.thumb, undefined);
  const renders = calls.render;
  app.conn.thumbnail = async () => null;
  planFile(ctx, 'c.gcode');
  await new Promise((r) => setImmediate(r));
  app.conn.thumbnail = async () => {
    throw new Error('HTTP 500');
  };
  planFile(ctx, 'd.gcode');
  await new Promise((r) => setImmediate(r));
  assert.equal(app.print.thumb, undefined); // nothing kept, so the panel shows none
  assert.equal(calls.render, renders); // and nothing drawn again
});

test('the demo and a panel with no file fetch no thumbnail', async () => {
  const demo = harness({ demo: true });
  let asked = 0;
  demo.app.conn.thumbnail = async () => {
    asked += 1;
    return '/t.png';
  };
  await openPrint(demo.ctx);
  planFile(demo.ctx, 'a.gcode');
  const none = harness({ planned: null });
  none.app.conn.thumbnail = demo.app.conn.thumbnail;
  await openPrint(none.ctx);
  await new Promise((r) => setImmediate(r));
  assert.equal(asked, 0);
});

test("the page's own settled picks go with the picks: a new file and Print drop them, a re-plan keeps those carried", async () => {
  const fresh = harness({ print: { ...closed, choice: { 0: '1' }, choiceId: 'old', auto: [0] } });
  planFile(fresh.ctx, 'b.gcode');
  assert.deepEqual(fresh.app.print.auto, []);
  // A print started: the choice, merges, pins and the page's own picks go.
  const go = harness({ print: { ...withFile, choice: { 0: '1', 1: '2' }, choiceId: 'p.gcode\n1', auto: [0] } });
  await startPrint(go.ctx);
  assert.deepEqual([go.app.print.choice, go.app.print.auto], [{}, []]);
  // A plan made again carries the picks it keeps, and the page's own among them.
  const again = benchPlan(2);
  const print = {
    ...withFile,
    choice: { 0: 'a:3', 5: 'a:2' },
    choiceId: 'p.gcode\n1',
    choiceTools: benchPlan(1).tools,
    auto: [0, 5],
  };
  const carried = harness({ planned: again, print });
  keepChoice(carried.ctx);
  assert.deepEqual([carried.app.print.choice, carried.app.print.auto], [{ 0: 'a:3' }, [0]]);
});

function timersHarness(status) {
  const h = harness({ status: { print_stats: status } });
  const timers = [];
  const real = globalThis.setTimeout;
  globalThis.setTimeout = (fn, ms) => timers.push([fn, ms]);
  return { ...h, timers, restore: () => (globalThis.setTimeout = real) };
}
const BARE = { colours: null, time: null, thumb: null, modified: 7 };
const flush = () => new Promise((r) => setImmediate(r));

test('ensureRunningCard: once per file, keyed by its own time; later reads take the metadata only, a few times', async () => {
  const { app, ctx, timers, restore } = timersHarness({ state: 'printing', filename: 'r.gcode' });
  try {
    let cards = 0;
    let metas = 0;
    app.conn.fileCard = async () => {
      cards += 1;
      return BARE;
    };
    app.conn.fileBytes = async () => {
      metas += 1;
      return metas < 2
        ? { gcodeStart: null, gcodeEnd: null, modified: 7 }
        : { gcodeStart: 100, gcodeEnd: 900, modified: 7 };
    };
    ensureRunningCard(ctx);
    ensureRunningCard(ctx); // Once per file: no second read.
    await flush();
    assert.equal(cards, 1);
    assert.equal(app.print.cards['r.gcode'].modified, 7);
    // The list opened later knows the same time: the card stays, nothing is read.
    app.print = { ...app.print, files: [{ path: 'r.gcode', modified: 7 }] };
    await loadCard(ctx, 'r.gcode');
    assert.equal(cards, 1);
    // No bytes: the first wait, then a read of the metadata alone (no range of the file).
    assert.deepEqual(
      timers.map((t) => t[1]),
      [RUNNING_RETRY_MS[0]],
    );
    ensureRunningCard(ctx); // A status while it waits starts nothing.
    await timers[0][0]();
    await flush();
    assert.equal(metas, 1);
    assert.deepEqual(
      timers.map((t) => t[1]),
      RUNNING_RETRY_MS.slice(0, 2),
    );
    await timers[1][0]();
    await flush();
    assert.equal(metas, 2);
    assert.equal(cards, 1);
    assert.equal(app.print.cards['r.gcode'].card.gcodeEnd, 900);
    assert.equal(timers.length, 2);
    // The print ended: nothing more is read, and a read already waiting is dropped.
    app.status = { ...app.status, print_stats: { state: 'complete', filename: 'r.gcode' } };
    ensureRunningCard(ctx);
    app.print = { ...app.print, cards: {} };
    ensureRunningCard(ctx);
    await flush();
    assert.equal(cards + metas, 3);
  } finally {
    restore();
  }
});

test('ensureRunningCard: a failed read stores nothing; a read that comes after the print ended is dropped', async () => {
  const { app, ctx, timers, restore } = timersHarness({ state: 'paused', filename: 'f.gcode' });
  try {
    app.conn.fileCard = async () => {
      throw new Error('x');
    };
    ensureRunningCard(ctx);
    await flush();
    assert.equal(app.print.cards?.['f.gcode'], undefined);
    assert.equal(timers.length, 1);
    let metas = 0;
    app.conn.fileBytes = async () => {
      metas += 1;
      return { gcodeStart: 1, gcodeEnd: 2, modified: 1 };
    };
    app.status = { ...app.status, print_stats: { state: 'standby', filename: 'f.gcode' } };
    ensureRunningCard(ctx);
    await timers[0][0](); // Its job is gone: it reads nothing.
    assert.equal(metas, 0);
    assert.equal(app.print.cards?.['f.gcode'], undefined);
  } finally {
    restore();
  }
});

test('a grid read never replaces the running card that has the bytes; a card that loses them is read again', async () => {
  const { app, ctx, timers, restore } = timersHarness({ state: 'printing', filename: 'r.gcode' });
  try {
    const bytes = { ...BARE, gcodeStart: 100, gcodeEnd: 900 };
    app.conn.fileCard = async () => bytes;
    ensureRunningCard(ctx);
    await flush();
    assert.equal(app.print.cards['r.gcode'].card.gcodeEnd, 900);
    // A grid read in flight whose list says another time, and fails: the bytes stay.
    app.print = { ...app.print, files: [{ path: 'r.gcode', modified: 8 }] };
    app.conn.fileCard = async () => {
      throw new Error('x');
    };
    await loadCard(ctx, 'r.gcode');
    assert.equal(app.print.cards['r.gcode'].card.gcodeEnd, 900);
    // Gone anyway (the entry is dropped): read again from the metadata, bounded.
    let metas = 0;
    app.conn.fileBytes = async () => {
      metas += 1;
      return { gcodeStart: 100, gcodeEnd: 900, modified: 8 };
    };
    for (let i = 0; i < 4; i += 1) {
      app.print = { ...app.print, cards: {} };
      ensureRunningCard(ctx);
      await flush();
    }
    // Three losses are read again; the fourth is not.
    assert.equal(metas, 3);
    assert.equal(app.print.cards['r.gcode'], undefined);
    assert.equal(timers.length, 0);
  } finally {
    restore();
  }
});

test('ensureRunningCard: a card the grid already read with the bytes is not read again', async () => {
  const { app, ctx, restore } = timersHarness({ state: 'printing', filename: 'g.gcode' });
  try {
    let asked = 0;
    app.conn.fileCard = async () => {
      asked += 1;
      return BARE;
    };
    app.print = { ...app.print, cards: { 'g.gcode': { modified: 1, card: { ...BARE, gcodeStart: 1, gcodeEnd: 2 } } } };
    ensureRunningCard(ctx);
    await flush();
    assert.equal(asked, 0);
  } finally {
    restore();
  }
});

test('planFile records the reading; a refusal ends it with the host message, no toast', () => {
  const { app, ctx, calls } = harness({
    onSend: (_script, opts) => assert.equal(opts.quiet('no slicer config block: slice it again'), true),
    sendResult: false,
  });
  planFile(ctx, 'odd.gcode');
  assert.deepEqual(app.print.reading, {
    file: 'odd.gcode',
    at: 50000,
    error: 'no slicer config block: slice it again',
  });
  assert.deepEqual(calls.toast, []);
  // A refusal of an older file does not touch the reading of the file now chosen.
  const late = harness();
  late.ctx.sendScript = async (_script, _n, opts) => {
    late.app.print = { ...late.app.print, file: 'next.gcode', reading: { file: 'next.gcode', at: 1 } };
    opts.quiet('late');
    return false;
  };
  planFile(late.ctx, 'odd.gcode');
  assert.deepEqual(late.app.print.reading, { file: 'next.gcode', at: 1 });
  // A send that goes through leaves the reading as it was set.
  const ok = harness();
  planFile(ok.ctx, 'fine.gcode');
  assert.deepEqual(ok.app.print.reading, { file: 'fine.gcode', at: 50000 });
});

test('a refusal whose reading is not on screen keeps its toast: a re-plan or a card click of the live file, an older file', () => {
  const live = { ...plan, file: 'mine.gcode' };
  // The Print-tab click on the live file: the reading is hidden (the live plan names the file), so quiet says no.
  const tab = harness({
    planned: live,
    print: { ...closed, open: true, file: 'mine.gcode' },
    onSend: (_s, opts) => assert.equal(opts.quiet('file busy'), false),
  });
  replanChosen(tab.ctx);
  assert.equal(tab.calls.sendScript.length, 1);
  assert.equal(tab.app.print.reading.error, 'file busy', 'the error is still kept on its reading');
  // A click on the live file's card: the same.
  const card = harness({ planned: live, onSend: (_s, opts) => assert.equal(opts.quiet('file busy'), false) });
  planFile(card.ctx, 'mine.gcode');
  assert.equal(card.calls.sendScript.length, 1);
  // A refusal of an older file, another file chosen since: its toast shows.
  const old = harness();
  old.ctx.sendScript = async (_s, _n, opts) => {
    old.app.print = { ...old.app.print, file: 'next.gcode', reading: { file: 'next.gcode', at: 1 } };
    assert.equal(opts.quiet('late'), false);
    return false;
  };
  planFile(old.ctx, 'odd.gcode');
  // The reading on screen keeps the refusal in place of the bar, no toast.
  const shown = harness({ onSend: (_s, opts) => assert.equal(opts.quiet('no slicer config block'), true) });
  planFile(shown.ctx, 'odd.gcode');
  assert.equal(shown.app.print.reading.error, 'no slicer config block');
});

test('replanChosen sends no second plan while this page reads the file: its request in flight, or its plan not come yet', () => {
  const live = { ...plan, file: 'mine.gcode' };
  // The request of the live file is still in flight (left Print and came back): nothing more goes.
  const inFlight = harness({ planned: live, print: { ...closed, open: true, file: 'mine.gcode' } });
  inFlight.app.pending.add('print:file');
  replanChosen(inFlight.ctx);
  assert.deepEqual(inFlight.calls.sendScript, []);
  // Another file chosen, its plan not come yet: nothing more goes.
  const reading = harness({
    planned: { ...plan, file: 'old.gcode' },
    print: { ...closed, open: true, file: 'mine.gcode', reading: { file: 'mine.gcode', at: 1 } },
  });
  replanChosen(reading.ctx);
  assert.deepEqual(reading.calls.sendScript, []);
  // A reading that ended in a refusal is not in flight: the click plans again.
  const refused = harness({
    planned: { ...plan, file: 'old.gcode' },
    print: { ...closed, open: true, file: 'mine.gcode', reading: { file: 'mine.gcode', at: 1, error: 'x' } },
  });
  replanChosen(refused.ctx);
  assert.equal(refused.calls.sendScript.length, 1);
});

test('openPrint after the first open plans nothing, even after a Print-tab click', async () => {
  const mine = { ...plan, file: 'mine.gcode' };
  const { app, ctx, calls } = harness({ planned: mine, print: { ...closed, open: true, file: 'mine.gcode' } });
  replanChosen(ctx);
  assert.equal(calls.sendScript.length, 1);
  app.status = { ...app.status, ace2k_u1: { ...app.status.ace2k_u1, plan: { ...plan, file: 'theirs.gcode' } } };
  await openPrint(ctx); // the grid mounts because another device planned another file
  assert.equal(calls.sendScript.length, 1);
});

test('replanChosen plans the chosen file again with its reading, keeping the picks; nothing without a file, a print, a link, the demo, or while another device planned', () => {
  const picks = { 0: 'a' };
  const mine = { ...plan, file: 'mine.gcode' };
  const { app, ctx, calls } = harness({
    planned: mine,
    print: { ...closed, open: true, file: 'mine.gcode', choice: picks },
  });
  replanChosen(ctx);
  assert.deepEqual(calls.sendScript, [['ACEK_PLAN FILE="mine.gcode"', null, 'print:file', false]]);
  assert.equal(app.print.choice, picks);
  assert.deepEqual(app.print.reading, { file: 'mine.gcode', at: 50000 });
  // The live plan already names the file: the plan view stays while it is read again (no grid between).
  assert.equal(readingOf(app), null);
  assert.equal(otherDevice(app), null);
  for (const over of [
    { print: { ...closed, open: true, file: null } },
    { print: { ...closed, file: 'mine.gcode' } },
    { print: { ...closed, open: true, file: 'mine.gcode' }, status: { print_stats: { state: 'printing' } } },
    { print: { ...closed, open: true, file: 'mine.gcode' }, online: false },
    { print: { ...closed, open: true, file: 'mine.gcode' }, demo: true },
    { planned: mine, print: { ...closed, open: true, file: 'a;b.gcode' } },
    // Another device's plan is live: the notice's "Plan … again" is the one way back.
    { print: { ...closed, open: true, file: 'mine.gcode' } },
  ]) {
    const h = harness(over);
    replanChosen(h.ctx);
    assert.deepEqual(h.calls.sendScript, []);
    assert.equal(h.app.print.reading, undefined);
  }
});

test('a reading ends when its plan comes, so another device planning later shows the notice', () => {
  const { app, ctx } = harness({ planned: { ...plan, file: 'old.gcode' } });
  app.snaps = {};
  app.samples = [];
  app.events = [];
  planFile(ctx, 'mine.gcode'); // the click
  assert.deepEqual(readingOf(app), { file: 'mine.gcode', at: 50000 });
  assert.equal(otherDevice(app), null);
  const at = (file) => ({ ...app.status, ace2k_u1: { ...app.status.ace2k_u1, plan: { ...plan, file } } });
  ingestStatus(app, at('mine.gcode'), 1); // the plan arrives
  assert.equal(app.print.reading, null);
  assert.equal(readingOf(app), null);
  assert.equal(otherDevice(app), null);
  assert.equal(planNow(app)?.plan.file, 'mine.gcode', 'the plan view shows the plan that came');
  ingestStatus(app, at('theirs.gcode'), 2); // another device plans its file
  assert.equal(readingOf(app), null);
  assert.equal(otherDevice(app), 'theirs.gcode');
  // A status before the plan comes keeps the reading.
  planFile(ctx, 'next.gcode');
  ingestStatus(app, at('theirs.gcode'), 3);
  assert.deepEqual(readingOf(app), { file: 'next.gcode', at: 50000 });
});

test('planFile: the reading is set only when a script is sent; a lost connection keeps its toast and its reading, with the message', () => {
  const bad = harness();
  planFile(bad.ctx, 'a;b.gcode');
  assert.equal(bad.app.print.reading, undefined);
  assert.notEqual(bad.app.print.file, 'a;b.gcode', 'a file with no script is not switched to');
  const lost = harness({ onSend: (_s, opts) => assert.equal(opts.quiet(CONNECTION_CLOSED), false) });
  planFile(lost.ctx, 'x.gcode');
  assert.equal(lost.app.print.reading.error, CONNECTION_CLOSED);
  const other = harness();
  other.ctx.sendScript = async (_s, _n, opts) => {
    other.app.print = { ...other.app.print, reading: { file: 'y.gcode', at: 1 } };
    assert.equal(opts.quiet(CONNECTION_CLOSED), false);
    return false;
  };
  planFile(other.ctx, 'x.gcode');
  assert.deepEqual(other.app.print.reading, { file: 'y.gcode', at: 1 });
});

test('planFile in the demo: the reading starts at the click, before the demo data is read; a file with no plan ends it', async () => {
  forgetDemoFiles();
  const real = globalThis.fetch;
  globalThis.fetch = demoFetch;
  try {
    const demo = harness({ demo: true });
    planFile(demo.ctx, 'nope.gcode');
    assert.deepEqual(demo.app.print.reading, { file: 'nope.gcode', at: 50000 });
    await new Promise((r) => setImmediate(r));
    assert.equal(demo.app.print.reading, null);
    // Another file chosen before the data came keeps its own reading.
    planFile(demo.ctx, 'nope.gcode');
    demo.app.print = { ...demo.app.print, file: 'big.gcode', reading: { file: 'big.gcode', at: 1 } };
    await new Promise((r) => setImmediate(r));
    assert.deepEqual(demo.app.print.reading, { file: 'big.gcode', at: 1 });
    // "Plan … again" in the demo keeps the picks and takes the demo's plan.
    const again = harness({ demo: true, print: { ...closed, open: true, file: 'd.gcode', choice: { 0: 'a' } } });
    ACTIONS.plan_again(again.ctx, { dataset: {} });
    await new Promise((r) => setImmediate(r));
    assert.equal(again.app.status.ace2k_u1.plan.file, 'd.gcode');
    assert.deepEqual(again.app.print.choice, { 0: 'a' });
    assert.equal(again.app.print.reading, null);
    assert.deepEqual(again.calls.sendScript, []);
  } finally {
    globalThis.fetch = real;
    forgetDemoFiles();
  }
});

test('demoOtherPlan: waits for the first file, as long as its delay, then puts the other plan without delayMs', async () => {
  forgetDemoFiles();
  const real = globalThis.fetch;
  globalThis.fetch = demoFetch;
  try {
    // The first file's plan arrives after 3 sleeps: the other plan follows it.
    const h = harness({ demo: true });
    let naps = 0;
    h.ctx.sleep = async (ms) => {
      assert.equal(ms, 50);
      if (++naps === 3) h.app.status.ace2k_u1.plan = { file: 'd.gcode' };
    };
    await demoOtherPlan(h.ctx, 'd.gcode', 'big.gcode');
    assert.equal(naps, 3);
    assert.equal(h.app.status.ace2k_u1.plan.file, 'big.gcode');
    assert.equal('delayMs' in h.app.status.ace2k_u1.plan, false);
    // The first file never gets its plan: it gives up after delayMs + the margin, and changes nothing.
    const g = harness({ demo: true });
    let total = 0;
    g.ctx.sleep = async (ms) => {
      total += ms;
    };
    await demoOtherPlan(g.ctx, 'big.gcode', 'd.gcode');
    assert.equal(total, 14000 + DEMO_OTHER_MARGIN_MS);
    assert.equal(g.app.status.ace2k_u1.plan.file, 'p.gcode');
    // No plan for the other file: nothing changes.
    h.app.status.ace2k_u1.plan = { file: 'd.gcode' };
    await demoOtherPlan(h.ctx, 'd.gcode', 'missing.gcode');
    assert.equal(h.app.status.ace2k_u1.plan.file, 'd.gcode');
  } finally {
    globalThis.fetch = real;
    forgetDemoFiles();
  }
});
