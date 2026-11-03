import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { options } from 'preact';
import { html } from '../js/ui/html.js';
import { PlanView } from '../js/ui/PlanView.js';
import { GuidedStep, latchTravel, overlayOf, stepBays, stepTravel } from '../js/ui/GuidedStep.js';
import { EjectAll, EjectLine } from '../js/ui/EjectAll.js';
import { Options, PrintBar } from '../js/ui/PrintBar.js';
import { bayRole } from '../js/ui/Diagram.js';
import { runAction } from '../js/actions.js';
import {
  EJECT_WAIT_S,
  dropEjectNote,
  ejectAll,
  leaveEjectRun,
  lostEjectRun,
  planFile,
  startPrint,
} from '../js/printflow.js';
import { fmtHM } from '../js/format.js';
import { autoAdvance, ejectList, guideOf, loadedHead, prevAt, stepId, stepSub, stepText } from '../js/steps.js';
import { initialPrint, planNow, storageOr } from '../js/state.js';
import { renderHtml, textOf } from './ui-helpers.js';
import { loadEnglish } from './helpers.js';
import { t } from '../js/i18n.js';

loadEnglish();

const read = (url) => JSON.parse(readFileSync(new URL(url, import.meta.url), 'utf8'));
const tool = (colour, material, lane = null) => ({
  colour,
  material,
  lane,
  temp: null,
  first_temp: null,
  why: 'closest',
});

// demo-unknown: one unit, bay 1 red PLA (tagged, in error), 2 black PETG (by hand), 3 a spool of no material, 4 empty.
// The five-colour file prints white (T0), red (T1) and black (T6), all PLA: take the unknown spool out of bay 3, put
// a white PLA in bay 3, put a black PLA in bay 4.
const five = read('./fixtures/planner-files.json').five;
const fivePlan = {
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
// demo-four: units a-d, each feeding its own head from bays 1-4 (red, black, white, green PLA); bay 1 of each unit is
// in its head. Two PLA colours that alternate on head 0 and three PETG colours: black PLA moves from a:2 to b:1, and
// the PETG colours go to b:2, c:1 and d:1, which are taken out first.
const fourPlan = {
  file: 'x.gcode',
  mtime: 1,
  raw: [],
  heads: {},
  set: false,
  choice: null,
  lanes: [],
  tools: [
    tool('#dd3333', 'PLA', 'a:1'),
    tool('#111111', 'PLA', 'a:2'),
    tool('#ffffff', 'PETG'),
    tool('#0000ff', 'PETG'),
    tool('#00ff00', 'PETG'),
  ],
  order: [0, 1, 0, 1, 0, 1, 0, 1, 2, 3, 4, 2, 3, 4],
};
// The same units printing the two PLA colours only, both with a bay: one move, and nothing blocks Print.
const movePlan = {
  ...fourPlan,
  file: 'm.gcode',
  tools: fourPlan.tools.slice(0, 2),
  order: [0, 1, 0, 1, 0, 1, 0, 1],
  lanes: [
    { key: 'a:1', head: 0, material: 'PLA', colour: '#dd3333', empty: false },
    { key: 'a:2', head: 0, material: 'PLA', colour: '#111111', empty: false },
  ],
};

function appOf(statusName, plan, print = {}, ui = {}) {
  const s = read(`../dev/demo-${statusName}.json`);
  return {
    status: { ...s, ace2k_u1: { ...s.ace2k_u1, plan }, print_stats: { state: 'standby' } },
    online: true,
    klippy: 'ready',
    selected: null,
    pending: new Set(),
    edit: null,
    events: [],
    printOptions: {},
    print: { open: true, files: [], file: plan.file, choice: {}, choiceId: null, ...print },
    ui: { tab: 'print', panel: null, sheet: false, ...ui },
  };
}

// Empties the bay key ("2" with one unit, "b:1" with several), as the end of its eject does.
const emptyKey = (app, key) => {
  const [unit, n] = key.includes(':') ? key.split(':') : [null, key];
  const lanes = app.status[unit ? `ace2k ${unit}` : 'ace2k'].lanes;
  lanes[Number(n) - 1] = { ...lanes[Number(n) - 1], insert: false, mode: 'idle', tag: null };
  app.status = { ...app.status };
};

// A ctx whose run goes through the real actions, and whose send is fake: refuse names the bays it refuses. Its
// clock moves half a second at each sleep, and an accepted eject empties its bay at the next sleep (drain false
// leaves the bays to the test).
function live(app, { refuse = [], answer = true, drain = true } = {}) {
  const calls = { sent: [], confirm: [], toast: [], renders: [] };
  const ejecting = [];
  let clock = 1000;
  const ctx = {
    app,
    t,
    demo: false,
    toast: (m) => calls.toast.push(m),
    render: () => calls.renders.push(textOf(renderHtml(html`<${PlanView} app=${app} ctx=${ctx} />`))),
    confirm: (q) => {
      calls.confirm.push(q);
      return answer;
    },
    now: () => clock,
    sleep: async () => {
      clock += 0.5;
      if (drain) for (const k of ejecting.splice(0)) emptyKey(app, k);
    },
    send: async (id, n, args, opts) => {
      calls.sent.push([id, n, opts.unit, opts.key]);
      if (!refuse.includes(opts.key.slice('eject:'.length))) {
        assert.deepEqual(args, { wait: true }); // eject all waits for each eject's decision (WAIT=1)
        ejecting.push(opts.key.slice('eject:'.length));
        return true;
      }
      opts.quiet?.('Refused: the lane is busy');
      return false;
    },
    sendScript: async (script, n, opts) => {
      calls.sent.push([script, n, opts.key]);
      return true;
    },
  };
  ctx.run = (id, data = {}, value = '', checked = false) => runAction(ctx, id, data, value, checked);
  ctx.startPrint = () => startPrint(ctx);
  return { ctx, calls };
}
// Go, as the dialog sends it: with the list of bays it shows.
const go = (ctx) => ctx.run('eject_all_go', { keys: ejectList(guideOf(ctx.app).steps).join(',') });
const view = (app, ctx = live(app).ctx) => renderHtml(html`<${PlanView} app=${app} ctx=${ctx} />`);

// Empties bay i (0-based) of the one unit, or puts a tagged spool of that material and colour (RRGGBB) in it.
// A new status, as each status push brings: the page never reads one changed in place.
const empty = (app, i) => {
  app.status.ace2k.lanes[i] = { ...app.status.ace2k.lanes[i], insert: false, tag: null };
  app.status = { ...app.status };
};
const putIn = (app, i, material, rgb) => {
  app.status.ace2k.lanes[i] = {
    mode: 'idle',
    insert: true,
    rest: true,
    tag: { state: 'read', uid: `U${i}`, record: { material, color_rgba: `${rgb}FF` } },
  };
  app.status = { ...app.status };
};

test('the card: step 1 of 3, its sentence and sub-line, ←, Done and the list of steps', () => {
  const app = appOf('unknown', fivePlan);
  const text = textOf(view(app));
  assert.ok(text.includes('PREPARE THE SPOOLS · step 1 of 3'), text);
  assert.ok(text.includes(t('step.out_unknown', { key: '3' })));
  assert.ok(text.includes(t('step.sub.out_unknown')));
  assert.ok(text.includes(`${t('step.prev')} ${t('step.next')} ${t('step.see', { n: 3 })}`));
  // The diagram lights bay 3 ("take out") and dims bays 2 and 4; bay 1 is in error and stays lit.
  const out = view(app);
  assert.equal((out.match(/class="bay dim"/g) ?? []).length, 2);
  assert.match(out, /class="bay r-from"/);
  assert.ok(textOf(out).includes(t('step.take_out')));
  // The list of every step folds open, and shut.
  const { ctx } = live(app);
  ctx.run('steps_all');
  assert.ok(textOf(view(app)).includes(t('step.hide')));
  assert.equal((view(app).match(/<li/g) ?? []).length, 3);
  ctx.run('steps_all');
  assert.equal(app.print.allSteps, false);
});

test('autoAdvance: the bay emptied moves on to step 2; the tagged white PLA put in moves on to step 3', () => {
  const app = appOf('unknown', fivePlan);
  autoAdvance(app);
  assert.deepEqual([app.print.step, app.print.trail], [0, ['o:3', 'i:3', 'i:4']]);
  autoAdvance(app); // Nothing changed: app.print stays the same object.
  const kept = app.print;
  autoAdvance(app);
  assert.equal(app.print, kept);
  empty(app, 2);
  autoAdvance(app);
  assert.equal(app.print.step, 1);
  let text = textOf(view(app));
  assert.ok(text.includes('step 2 of 3'), text);
  assert.ok(text.includes(t('step.put', { key: '3', n: 1, what: 'PLA' })));
  // The sentence and the sub-line carry the colour's dot right after "colour 1".
  assert.match(
    view(app),
    /<p class="gt"[^>]*>Put a new PLA spool for color 1<span class="wdot"><span class="dot[^"]*" style="--fil:#ffffff"><\/span><\/span> in bay 3</,
  );
  assert.match(
    view(app),
    /<p class="note">A new spool for color 1<span class="wdot"><span class="dot[^"]*" style="--fil:#ffffff"><\/span><\/span>\. The page/,
  );
  assert.match(view(app), /class="spool ghost"/);
  // A black PLA does not do: its colour is not the file's white.
  putIn(app, 2, 'PLA', '000000');
  autoAdvance(app);
  assert.equal(app.print.step, 1);
  // The planner now checks the spool in bay 3; the step was shown as a put, so a tagged white PLA does it.
  putIn(app, 2, 'PLA', 'FFFFFF');
  autoAdvance(app);
  text = textOf(view(app));
  assert.ok(text.includes('step 3 of 3'), text);
  assert.ok(text.includes(t('step.put', { key: '4', n: 7, what: 'PLA' })));
  // With no plan there is nothing to advance.
  const none = appOf('unknown', fivePlan, { file: null });
  autoAdvance(none);
  assert.equal(none.print.step, undefined);
});

test('planNow is worked out once per status and Print state: every reader of one render shares it', () => {
  const app = appOf('unknown', fivePlan);
  const now = planNow(app);
  assert.ok(now);
  // The readers of a render (the plan view, the guide, the print bar) get the same object, not a new placement.
  renderHtml(html`<${PlanView} app=${app} ctx=${live(app).ctx} />`);
  assert.equal(guideOf(app).now, now);
  assert.equal(planNow(app), now);
  // A new status, or a pick, works it out again.
  empty(app, 2);
  const next = planNow(app);
  assert.notEqual(next, now);
  assert.equal(planNow(app), next);
  live(app).ctx.run('pick', { n: '0', key: '4' });
  assert.notEqual(planNow(app), next);
});

test('Done and ←: Done passes a step the page cannot confirm; past the last, the card says so; ← goes back', () => {
  const app = appOf('unknown', fivePlan);
  const { ctx } = live(app);
  ctx.run('step_prev'); // Nothing before the first step.
  assert.equal(app.print.step, undefined);
  ctx.run('step_done');
  ctx.run('step_done');
  assert.ok(textOf(view(app)).includes('step 3 of 3'));
  ctx.run('step_done');
  const text = textOf(view(app));
  assert.ok(text.includes(t('step.passed')) && text.includes(t('step.passed_sub')), text);
  assert.ok(!text.includes(t('step.next')));
  ctx.run('step_done'); // Past the end, Done is gone and does nothing.
  assert.equal(app.print.step, 3);
  ctx.run('step_prev');
  assert.ok(textOf(view(app)).includes('step 3 of 3'));
  // A step already done (its bay emptied) is skipped going back.
  empty(app, 2);
  const g = guideOf(app);
  assert.equal(prevAt({ ...g, at: 1 }), null);
  // A pick starts the guide over.
  ctx.run('pick', { n: '0', key: '4' });
  assert.deepEqual([app.print.step, app.print.trail, app.print.shown], [0, [], null]);
});

// demo-unknown printing only the red PLA of bay 1, which is already there: nothing to change.
const redPlan = {
  ...fivePlan,
  file: 'red.gcode',
  tools: [tool('#dd3333', 'PLA', '1')],
  order: [0, 0],
  lanes: [{ key: '1', head: 0, material: 'PLA', colour: '#dd3333', empty: false }],
};

test('the demo: calibration-cube on demo-four is already in place (the visual check shoots it)', () => {
  const cube = read('../dev/demo-files.json').plans['calibration-cube.gcode'];
  const app = appOf('four', cube);
  assert.deepEqual(guideOf(app)?.steps, []);
  const out = view(app);
  assert.ok(textOf(out).includes(t('step.in_place')));
  assert.ok(!/class="btn go" disabled/.test(out));
});

test('no step left: the green card, a diagram with the colour chips and no dimming, "Spools ready"', () => {
  const app = appOf('unknown', redPlan);
  const out = view(app);
  assert.ok(textOf(out).includes(`${t('step.in_place')} ${t('step.in_place_sub')}`));
  assert.ok(!/class="bay dim"/.test(out));
  assert.ok(textOf(out).includes(t('print.colour_n', { n: 1 })));
  // More colours than bays: no placement, so no guide at all (the header says it does not fit).
  const many = appOf('unknown', read('../dev/demo-files.json').plans['benchy-5col.gcode']);
  assert.equal(guideOf(many), null);
  assert.ok(!textOf(view(many)).includes(t('step.in_place')));
});

test('a phone: the step as two rows (take out, ↓, put in) in the card, and no diagram', () => {
  const app = appOf('four', fourPlan, {}, { narrow: true });
  const { ctx } = live(app);
  let out = view(app);
  assert.ok(!/<svg/.test(out));
  assert.match(out, /class="srow from"/);
  assert.ok(!/class="srow to"/.test(out));
  // The fifth step: black PLA from a:2 to b:1 (b:1 was taken out first): both rows and the arrow.
  for (let i = 0; i < 4; i += 1) ctx.run('step_done');
  out = view(app);
  assert.match(out, /class="srow from".*class="sdown".*class="srow to"/s);
  assert.ok(textOf(out).includes(`${t('step.ghost', { material: 'PLA' })} ${t('step.put_in')}`));
  // A row opens its bay's panel.
  ctx.run('step_done');
  out = view(app);
  assert.match(out, /class="srow to"/);
  assert.ok(!/class="srow from"/.test(out));
});

test('eject all: the confirmation lists the bays, where a moved spool goes, and the heads that heat', () => {
  const app = appOf('four', fourPlan);
  const { ctx } = live(app);
  assert.ok(textOf(view(app)).includes(t('eject_all.button', { n: 5 })));
  ctx.run('eject_all');
  const text = textOf(view(app));
  assert.ok(text.includes(t('eject_all.title')));
  for (const k of ['b:1', 'b:2', 'c:1', 'd:1', 'a:2']) assert.ok(text.includes(k), k);
  assert.ok(text.includes(t('eject_all.goes_to', { key: 'b:1' })));
  assert.ok(text.includes(t('eject_all.unused')));
  assert.ok(text.includes(t('eject_all.heat', { key: 'c:1', head: 'T2' })));
  assert.ok(text.includes(t('eject_all.heat', { key: 'd:1', head: 'T3' })));
  // b:1 is taken out for the black PLA that moves in; it is in T1.
  assert.ok(text.includes(t('eject_all.heat', { key: 'b:1', head: 'T1' })));
  assert.ok(!text.includes(t('eject_all.heat', { key: 'b:2', head: 'T1' })));
  assert.deepEqual([loadedHead(app, 'a:1'), loadedHead(app, 'a:2'), loadedHead(app, 'zz')], [0, null, null]);
  ctx.run('eject_all_no');
  assert.equal(app.print.eject, null);
});

test('eject all: the bays with no material are left out and listed', () => {
  const app = appOf('unknown', fivePlan);
  const { ctx } = live(app);
  const out = renderHtml(html`<${EjectAll} app=${app} ctx=${ctx} g=${guideOf(app)} />`);
  assert.ok(textOf(out).includes(t('eject_all.skipped', { key: '3' })));
  // No bay to eject: no button in the card.
  assert.ok(!textOf(view(app)).includes('⏏'));
});

test('eject all: a refusal of the second bay stops the run there; the third bay is never sent', async () => {
  const app = appOf('four', fourPlan);
  const { ctx, calls } = live(app, { refuse: ['b:2'] });
  await ejectAll(ctx); // Not asked first: nothing.
  assert.deepEqual(calls.sent, []);
  ctx.run('eject_all');
  await go(ctx);
  assert.deepEqual(calls.sent, [
    ['eject', 1, 'b', 'eject:b:1'],
    ['eject', 2, 'b', 'eject:b:2'],
  ]);
  assert.deepEqual(app.print.eject, { phase: 'failed', key: 'b:2', msg: 'Refused: the lane is busy' });
  // While it ran, the card said which bay, and the diagram marked it.
  const during = calls.renders.find((r) => r.includes(t('eject_all.running', { key: 'b:1' })));
  assert.ok(during?.includes(t('step.ejecting')));
  const text = textOf(view(app));
  assert.ok(text.includes(t('eject_all.failed', { key: 'b:2', msg: 'Refused: the lane is busy' })));
  ctx.run('eject_all_no');
  assert.equal(app.print.eject, null);
});

test('eject all: a refusal with no message stops with the generic reason', async () => {
  const app = appOf('four', fourPlan);
  const { ctx } = live(app);
  ctx.send = async () => false;
  ctx.run('eject_all');
  await go(ctx);
  assert.deepEqual(app.print.eject, { phase: 'failed', key: 'b:1', msg: t('reason.unavailable') });
});

test('eject all: every bay ejected one at a time, and the moved spool is held', async () => {
  const app = appOf('four', fourPlan);
  const { ctx, calls } = live(app);
  ctx.run('eject_all');
  const run = go(ctx);
  // A second click while it runs neither opens the confirmation nor runs again.
  ctx.run('eject_all');
  ctx.run('eject_all_no');
  assert.equal(app.print.eject.phase, 'run');
  await run;
  assert.deepEqual(
    calls.sent.map((c) => c[3]),
    ['eject:b:1', 'eject:b:2', 'eject:c:1', 'eject:d:1', 'eject:a:2'],
  );
  assert.equal(app.print.eject, null);
  assert.deepEqual(
    app.print.held.map((h) => [h.from, h.material, h.colour]),
    [['a:2', 'PLA', '#111111']],
  );
});

test('eject all: eject 2 starts only once bay 1 reads empty, and the card shows bay 1 the whole time', async () => {
  const app = appOf('four', fourPlan);
  const { ctx, calls } = live(app, { drain: false });
  let polls = 0;
  const sentBefore = [];
  ctx.sleep = async () => {
    polls += 1;
    // While b:1 still holds its spool, the run sends nothing more and the card names b:1.
    sentBefore.push(calls.sent.length);
    assert.deepEqual(app.print.eject, { phase: 'run', key: 'b:1' });
    assert.ok(textOf(view(app, ctx)).includes(t('eject_all.running', { key: 'b:1' })));
    if (polls === 3) {
      // The unload runs: the lane is busy, the bay still full.
      const lane = app.status['ace2k b'].lanes[0];
      app.status['ace2k b'].lanes[0] = { ...lane, mode: 'unloading' };
    }
    if (polls === 4) {
      emptyKey(app, 'b:1');
      ctx.send = async () => false; // b:2 refused: the run stops there.
    }
  };
  ctx.run('eject_all');
  await go(ctx);
  assert.deepEqual(sentBefore, [1, 1, 1, 1]);
  assert.deepEqual(app.print.eject, { phase: 'failed', key: 'b:2', msg: t('reason.unavailable') });
});

test('eject all: a lane that ends in error stops the run and names the bay; a short error does not', async () => {
  const app = appOf('four', fourPlan);
  const { ctx, calls } = live(app, { drain: false });
  let polls = 0;
  const base = ctx.sleep;
  ctx.sleep = async () => {
    polls += 1;
    await base();
    const lanes = app.status['ace2k b'].lanes;
    // Polls 1-2: an error the host clears by itself (an unload that stopped short, run again).
    if (polls === 1) lanes[0] = { ...lanes[0], mode: 'error', error: 'unload_incomplete' };
    if (polls === 3) lanes[0] = { ...lanes[0], mode: 'unloading', error: null };
    // From poll 5: stuck, and it stays.
    if (polls === 5) lanes[0] = { ...lanes[0], mode: 'error', error: 'stuck' };
  };
  ctx.run('eject_all');
  await go(ctx);
  assert.equal(calls.sent.length, 1);
  assert.deepEqual(app.print.eject, { phase: 'failed', key: 'b:1', msg: t('lane.error.stuck') });
  assert.ok(polls >= 5 + 5 / 0.5 - 1);
  assert.ok(textOf(view(app, ctx)).includes(t('eject_all.failed', { key: 'b:1', msg: t('lane.error.stuck') })));
});

test("eject all: the host's failed line stops the run with its reason; no end within the wait stops it too", async () => {
  const app = appOf('four', fourPlan);
  const { ctx } = live(app, { drain: false });
  const base = ctx.sleep;
  ctx.sleep = async () => {
    await base();
    // An older line of another bay is no failure of b:1.
    app.events = [
      { t: 1, k: 'e', lvl: 'err', src: 'unit', msg: 'ace2k_u1: lane b:1 eject failed: old' },
      { t: ctx.now(), k: 'e', lvl: 'err', src: 'unit', msg: 'ace2k_u1: lane c:1 eject failed: other bay' },
      {
        t: ctx.now(),
        k: 'e',
        lvl: 'err',
        src: 'unit',
        msg: "ace2k_u1: lane b:1 eject failed: the U1's unload of e1 — cold",
      },
    ];
  };
  ctx.run('eject_all');
  await go(ctx);
  assert.deepEqual(app.print.eject, { phase: 'failed', key: 'b:1', msg: "the U1's unload of e1 — cold" });

  const slow = appOf('four', fourPlan);
  const s = live(slow, { drain: false });
  const sleep = s.ctx.sleep;
  s.ctx.sleep = async () => {
    for (let i = 0; i < 240; i += 1) await sleep(); // two minutes a poll
  };
  s.ctx.run('eject_all');
  await go(s.ctx);
  assert.deepEqual(slow.print.eject, {
    phase: 'failed',
    key: 'b:1',
    msg: t('eject_all.timeout', { time: fmtHM(EJECT_WAIT_S) }),
  });
  assert.equal(s.calls.sent.length, 1);
});

test('eject all: a failure the host says before WAIT=1 returns, or an eject not started, stops the run', async () => {
  for (const [line, msg] of [
    ['ace2k_u1: lane b:1 eject failed: the probe not started — busy', 'the probe not started — busy'],
    [
      'ace2k_u1: lane b:1 eject not started — lane busy — ACEK_CLEAR LANE=1 UNIT=b',
      'lane busy — ACEK_CLEAR LANE=1 UNIT=b',
    ],
  ]) {
    const app = appOf('four', fourPlan);
    const { ctx, calls } = live(app, { drain: false });
    let clock = 1000;
    ctx.now = () => clock;
    const send = ctx.send;
    ctx.send = async (...args) => {
      // The line comes while the command runs; the command returns a second later.
      app.events = [...app.events, { t: clock, k: 'e', lvl: 'err', src: 'unit', msg: line }];
      clock += 1;
      return send(...args);
    };
    ctx.sleep = async () => assert.fail('the failure is seen at once');
    ctx.run('eject_all');
    await go(ctx);
    assert.equal(calls.sent.length, 1);
    assert.deepEqual(app.print.eject, { phase: 'failed', key: 'b:1', msg });
  }
});

test('eject all: a failed line the page had before the send, in the same second, is no failure of this eject', async () => {
  const app = appOf('four', fourPlan);
  const { ctx } = live(app, { drain: false });
  let clock = 1000.4;
  ctx.now = () => clock;
  // The previous eject of b:1 failed in the second of this send, just before it.
  app.events = [{ t: 1000, k: 'e', lvl: 'err', src: 'unit', msg: 'ace2k_u1: lane b:1 eject failed: stale' }];
  let polls = 0;
  const base = ctx.sleep;
  ctx.sleep = async () => {
    polls += 1;
    await base();
    clock += 0.5;
    // This eject's own failure comes later, with another reason.
    if (polls === 2)
      app.events = [
        ...app.events,
        { t: Math.floor(clock), k: 'e', lvl: 'err', src: 'unit', msg: 'ace2k_u1: lane b:1 eject failed: fresh' },
      ];
  };
  ctx.run('eject_all');
  await go(ctx);
  assert.equal(polls, 2, 'the stale line did not stop the run at once');
  assert.deepEqual(app.print.eject, { phase: 'failed', key: 'b:1', msg: 'fresh' });
});

test('eject all: a tail left at the bay asks to take it out by hand, and the run waits past the time limit', async () => {
  const app = appOf('four', fourPlan);
  const { ctx, calls } = live(app, { drain: false });
  let polls = 0;
  const base = ctx.sleep;
  const lines = [];
  ctx.sleep = async () => {
    polls += 1;
    for (let i = 0; i < 240; i += 1) await base(); // two minutes a poll
    if (polls === 1)
      app.events = [
        // Another unit's lane 1 is no word on b:1.
        {
          t: ctx.now(),
          k: 'e',
          lvl: 'info',
          src: 'unit',
          msg: "ace2k_u1: lane a:1 ejected (5 mm back): its tail is past the unit's drive gear, at its bay: take it out by hand",
        },
        {
          t: ctx.now(),
          k: 'e',
          lvl: 'info',
          src: 'unit',
          msg: "ace2k_u1: lane b:1 ejected (300 mm back): its tail is past the unit's drive gear, at its bay: take it out by hand",
        },
      ];
    lines.push(textOf(view(app, ctx)));
    if (polls === 8) {
      emptyKey(app, 'b:1'); // 16 minutes later the operator pulls the tail out
      ctx.send = async () => false;
    }
  };
  ctx.run('eject_all');
  await go(ctx);
  assert.equal(calls.sent.length, 1);
  assert.ok(lines[0].includes(t('eject_all.running', { key: 'b:1' })));
  assert.ok(lines[1].includes(t('eject_all.tail', { key: 'b:1' })));
  assert.ok(lines[7].includes(t('eject_all.tail', { key: 'b:1' })));
  // b:1 counted as ejected; the run went on to b:2 (refused here).
  assert.deepEqual(app.print.eject, { phase: 'failed', key: 'b:2', msg: t('reason.unavailable') });
});

test("eject all: Stop the run during a bay's eject or the take-out prompt sends nothing more", async () => {
  for (const tail of [false, true]) {
    const app = appOf('four', fourPlan);
    const { ctx, calls } = live(app, { drain: false });
    let polls = 0;
    const base = ctx.sleep;
    ctx.sleep = async () => {
      polls += 1;
      await base();
      if (polls === 1 && tail)
        app.events = [
          {
            t: ctx.now(),
            k: 'e',
            lvl: 'info',
            src: 'unit',
            msg: "ace2k_u1: lane b:1: nothing came back: its tail is already past the unit's drive gear, at its bay: take it out by hand",
          },
        ];
      if (polls === 3) {
        assert.ok(textOf(view(app, ctx)).includes(t('eject_all.stop')));
        if (tail) assert.equal(app.print.eject.tail, true);
        ctx.run('eject_all_stop');
        emptyKey(app, 'b:1'); // the unit's own eject of b:1 ends after the stop
      }
    };
    ctx.run('eject_all');
    await go(ctx);
    assert.equal(calls.sent.length, 1, 'no bay after b:1');
    assert.ok(
      calls.sent.every((c) => c[0] === 'eject'),
      'no stop command to the unit',
    );
    assert.deepEqual(app.print.eject, { phase: 'failed', key: 'b:1', msg: t('eject_all.stopped') });
    assert.ok(textOf(view(app, ctx)).includes(t('eject_all.failed', { key: 'b:1', msg: t('eject_all.stopped') })));
    ctx.run('eject_all_stop'); // nothing runs: nothing changes
    assert.equal(app.print.eject.phase, 'failed');
  }
});

test('eject all: a stop keeps the spools already held and the bays already ejected', async () => {
  const app = appOf('four', fourPlan);
  const { ctx, calls } = live(app, { drain: false });
  const kept = { uid: null, material: 'PLA', colour: '#123456', tag: false, from: 'x:1' };
  app.print = { ...app.print, held: [kept] };
  ctx.sleep = async () => {
    const key = app.print.eject?.key;
    if (key === 'c:1') ctx.run('eject_all_stop');
    else emptyKey(app, key); // b:1 and b:2 end
  };
  ctx.run('eject_all');
  await go(ctx);
  assert.deepEqual(
    calls.sent.map((c) => c[3]),
    ['eject:b:1', 'eject:b:2', 'eject:c:1'],
  );
  assert.deepEqual(app.print.eject, { phase: 'failed', key: 'c:1', msg: t('eject_all.stopped') });
  assert.deepEqual(app.print.held, [kept]);
});

test('eject all: a bay gone while it ejects stops the run as unavailable', async () => {
  const app = appOf('four', fourPlan);
  const { ctx } = live(app, { drain: false });
  const base = ctx.sleep;
  ctx.sleep = async () => {
    await base();
    // Unit b leaves the printer's units.
    app.status = { ...app.status, ace2k_units: { units: ['ace2k a', 'ace2k c', 'ace2k d'] } };
  };
  ctx.run('eject_all');
  await go(ctx);
  assert.deepEqual(app.print.eject, { phase: 'failed', key: 'b:1', msg: t('reason.unavailable') });
});

test('eject all: Go sends only the list shown, never during a print; the demo does not wait', async () => {
  const app = appOf('four', fourPlan);
  const { ctx, calls } = live(app);
  ctx.run('eject_all');
  await ctx.run('eject_all_go', { keys: 'b:1' }); // a list the bays no longer give
  assert.deepEqual(calls.sent, []);
  assert.deepEqual(calls.toast, [t('eject_all.changed')]);
  assert.deepEqual(app.print.eject, { phase: 'ask' });
  app.status = { ...app.status, print_stats: { state: 'printing' } };
  await go(ctx);
  assert.deepEqual(calls.sent, []);
  assert.equal(calls.toast[1], t('eject_all.printing'));
  assert.equal(app.print.eject, null);

  const demo = appOf('four', fourPlan);
  const d = live(demo, { drain: false });
  d.ctx.demo = true;
  d.ctx.sleep = async () => assert.fail('the demo does not wait');
  d.ctx.run('eject_all');
  await go(d.ctx);
  assert.equal(d.calls.sent.length, 5);
  assert.equal(demo.print.eject, null);
});

// A browser tab's session storage: it outlives a reload of the page, not the tab.
function session() {
  const m = new Map();
  return {
    m,
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => m.set(k, String(v)),
    removeItem: (k) => m.delete(k),
  };
}

test('eject all: a double click on Go starts one run, and it goes through every bay', async () => {
  const app = appOf('four', fourPlan);
  const { ctx, calls } = live(app);
  ctx.run('eject_all');
  const first = go(ctx);
  const second = go(ctx);
  await Promise.all([first, second]);
  assert.deepEqual(
    calls.sent.map((c) => c[3]),
    ['eject:b:1', 'eject:b:2', 'eject:c:1', 'eject:d:1', 'eject:a:2'],
  );
  assert.equal(app.print.eject, null);
});

// A fresh page in the same tab, as main.js starts it: the Print panel as initialPrint gives it, no file.
function freshPage(store, clock) {
  const fresh = appOf('four', fourPlan);
  fresh.print = initialPrint();
  const page = live(fresh);
  page.ctx.session = store;
  page.ctx.now = () => clock;
  return { fresh, ...page };
}

// Waits until the run is on bay key.
async function onBay(app, key) {
  for (let i = 0; i < 50 && app.print.eject?.key !== key; i += 1) await Promise.resolve();
  assert.equal(app.print.eject.key, key);
}

test('eject all: a reload of the tab during the run says at which bay it stopped, once, on the fresh page', async () => {
  const app = appOf('four', fourPlan);
  const { ctx } = live(app, { drain: false });
  const store = session();
  ctx.session = store;
  let release;
  const gate = new Promise((r) => {
    release = r;
  });
  let polls = 0;
  ctx.sleep = async () => {
    polls += 1;
    if (polls === 1) emptyKey(app, 'b:1');
    else await gate; // b:2 still ejects when the page goes away
  };
  ctx.run('eject_all');
  const run = go(ctx);
  await onBay(app, 'b:2');
  assert.equal(store.m.size, 0, 'nothing is noted while the page lives: a duplicated tab copies nothing');
  leaveEjectRun(ctx); // pagehide of the reload
  const { fresh, ctx: next, calls } = freshPage(store, ctx.now() + 2);
  lostEjectRun(next);
  const said = t('eject_all.failed', { key: 'b:2', msg: t('eject_all.reloaded') });
  assert.deepEqual(calls.toast, [said]);
  assert.equal(fresh.events.at(-1).msg, said);
  // Said once: the next reload shows nothing.
  const again = freshPage(store, ctx.now() + 3);
  lostEjectRun(again.ctx);
  assert.deepEqual(again.calls.toast, []);
  emptyKey(app, 'b:2');
  release();
  ctx.run('eject_all_stop');
  await run;
});

test('eject all: no note from a stopped or ended run, the demo, an old note, or a page back from the cache', async () => {
  const app = appOf('four', fourPlan);
  const { ctx } = live(app, { drain: false });
  const store = session();
  ctx.session = store;
  let release;
  const gate = new Promise((r) => {
    release = r;
  });
  ctx.sleep = () => gate;
  ctx.run('eject_all');
  const run = go(ctx);
  await onBay(app, 'b:1');
  ctx.run('eject_all_stop'); // Stop the run: the page going away now notes nothing
  leaveEjectRun(ctx);
  assert.equal(store.m.size, 0);
  emptyKey(app, 'b:1');
  release();
  await run;
  leaveEjectRun(ctx); // the run is over
  assert.equal(store.m.size, 0);

  app.print = { ...app.print, eject: { phase: 'run', key: 'c:1' } };
  ctx.demo = true;
  leaveEjectRun(ctx); // the demo writes no note
  assert.equal(store.m.size, 0);
  ctx.demo = false;
  leaveEjectRun(ctx);
  const demo = freshPage(store, ctx.now() + 1);
  demo.ctx.demo = true;
  lostEjectRun(demo.ctx); // the demo only drops the note
  assert.deepEqual(demo.calls.toast, []);
  assert.equal(store.m.size, 0);

  leaveEjectRun(ctx);
  const late = freshPage(store, ctx.now() + 31); // a note older than a reload's
  lostEjectRun(late.ctx);
  assert.deepEqual(late.calls.toast, []);

  leaveEjectRun(ctx);
  dropEjectNote(ctx); // pageshow from the cache: the run still goes on there
  const back = freshPage(store, ctx.now() + 1);
  lostEjectRun(back.ctx);
  assert.deepEqual(back.calls.toast, []);
});

test('eject all: a browser that blocks storage still runs every bay, and a new page shows nothing', async () => {
  const app = appOf('four', fourPlan);
  const { ctx, calls } = live(app);
  const blocked = () => {
    throw new Error('blocked');
  };
  ctx.session = { getItem: blocked, setItem: blocked, removeItem: blocked };
  ctx.run('eject_all');
  await go(ctx);
  assert.equal(calls.sent.length, 5);
  assert.equal(app.print.eject, null);
  app.print = { ...app.print, eject: { phase: 'run', key: 'b:1' } };
  leaveEjectRun(ctx);
  dropEjectNote(ctx);
  lostEjectRun(ctx);
  assert.deepEqual(calls.toast, []);
  // main.js reads both storages through storageOr: a blocked one is null.
  assert.equal(storageOr(blocked), null);
  const store = session();
  assert.equal(
    storageOr(() => store),
    store,
  );
});

test('eject all: a run ended by another file says so in a toast', async () => {
  const app = appOf('four', fourPlan);
  const { ctx, calls } = live(app, { drain: false });
  let release;
  const gate = new Promise((r) => {
    release = r;
  });
  ctx.sleep = () => gate;
  ctx.run('eject_all');
  const run = go(ctx);
  await Promise.resolve();
  planFile(ctx, 'other.gcode');
  emptyKey(app, 'b:1');
  release();
  await run;
  assert.equal(calls.toast.at(-1), t('eject_all.left', { key: 'b:1' }));
  assert.ok(calls.toast.at(-1).startsWith('Eject all stopped at b:1'));
});

test('eject all: another file chosen during the run ends it; it sends no more and writes nothing', async () => {
  const app = appOf('four', fourPlan);
  const { ctx, calls } = live(app, { drain: false });
  let release;
  const gate = new Promise((r) => {
    release = r;
  });
  ctx.sleep = () => gate;
  ctx.run('eject_all');
  const run = go(ctx);
  await Promise.resolve();
  assert.equal(calls.sent.length, 1);
  // The operator picks another file, then the same one again, and asks for eject all on it.
  planFile(ctx, 'other.gcode');
  planFile(ctx, fourPlan.file);
  assert.equal(app.print.eject, null);
  ctx.run('eject_all');
  await go(ctx); // The first run still waits on b:1: this one does not start.
  assert.equal(calls.toast.at(-1), t('eject_all.busy'));
  assert.deepEqual(app.print.eject, { phase: 'ask' });
  emptyKey(app, 'b:1');
  release();
  await run;
  // The first run sent nothing more, held nothing and left the new confirmation as it was.
  assert.equal(calls.sent.filter((c) => c[0] === 'eject').length, 1);
  assert.deepEqual(app.print.eject, { phase: 'ask' });
  assert.deepEqual(app.print.held, []);
});

// A colour with no bay as it is, which the planner gives d:1, whose PLA spool's colour is not known: a check only.
const checkPlan = { ...fourPlan, file: 'k.gcode', tools: [tool('#123456', 'PLA')], order: [0, 0] };
const checkApp = () => {
  const app = appOf('four', checkPlan);
  const d = app.status['ace2k d'].lanes;
  d[0] = { ...d[0], tag: { ...d[0].tag, record: { ...d[0].tag.record, color_rgba: null } } };
  app.status = { ...app.status };
  return app;
};

test('Print with steps pending asks first and lists them; cancel sends nothing, OK starts', async () => {
  // The colour has no bay as it is, so Print sends the suggestion, whose check of d:1 is pending: not a block (the
  // spool there is of a known material), a question.
  const app = checkApp();
  assert.deepEqual(
    guideOf(app)?.steps.map((s) => [s.kind, s.key]),
    [['check', 'd:1']],
  );
  const no = live(app, { answer: false });
  await no.ctx.run('print_go');
  assert.equal(no.calls.confirm.length, 1);
  const g = guideOf(app);
  const list = g.steps.map((s) => stepText(t, s, g.now)).join('; ');
  assert.equal(no.calls.confirm[0], t('print.pending', { list }));
  assert.deepEqual(no.calls.sent, []);
  const yes = live(app);
  await yes.ctx.run('print_go');
  assert.equal(yes.calls.sent.length, 1);
  assert.match(yes.calls.sent[0][0], /^ACEK_PLAN_SET/);
  // Every colour has a bay as it is: Print sends that, and asks only about the step still pending for black.
  const moveApp = appOf('four', movePlan);
  const asIs = live(moveApp);
  const mg = guideOf(moveApp); // Read first: a started print clears the chosen file.
  await asIs.ctx.run('print_go');
  const step = mg.steps.find((s) => s.kind === 'move');
  assert.deepEqual(asIs.calls.confirm, [t('print.as_is_check', { list: stepText(t, step, mg.now) })]);
  assert.match(asIs.calls.sent[0][0], /^ACEK_PLAN_SET FILE="m\.gcode" T0=a:1 T1=a:2\n/);
  // No step pending: no question.
  const done = live(appOf('unknown', redPlan));
  await done.ctx.run('print_go');
  assert.deepEqual(done.calls.confirm, []);
});

// A bay of demo-four: emptied, or holding a tagged PLA of that colour (RRGGBB).
const unitLanes = (app, key) => app.status[`ace2k ${key.split(':')[0]}`].lanes;
const emptyAt = (app, key) => {
  const lanes = unitLanes(app, key);
  const i = Number(key.split(':')[1]) - 1;
  lanes[i] = { ...lanes[i], insert: false, tag: null };
  app.status = { ...app.status };
};
const plaAt = (app, key, rgb) => {
  const lanes = unitLanes(app, key);
  const i = Number(key.split(':')[1]) - 1;
  lanes[i] = {
    mode: 'idle',
    insert: true,
    rest: true,
    tag: { state: 'read', uid: key, record: { material: 'PLA', color_rgba: `${rgb}FF` } },
  };
  app.status = { ...app.status };
};
const PRINT_M = /^ACEK_PLAN_SET FILE="m\.gcode" T0=a:1 T1=d:1\n/;

test('eject all, then the spool put back: the put back step, then the green "in place", and Print sends the bays', async () => {
  const app = appOf('four', movePlan);
  const { ctx, calls } = live(app);
  // The black PLA moves from a:2 to d:1, whose green PLA no colour uses: it comes out first.
  assert.deepEqual(
    guideOf(app)?.steps.map((s) => [s.kind, s.key]),
    [
      ['out', 'd:1'],
      ['move', 'd:1'],
    ],
  );
  ctx.run('eject_all');
  await go(ctx);
  assert.deepEqual(
    calls.sent.map((c) => c[3]),
    ['eject:d:1', 'eject:a:2'],
  );
  emptyAt(app, 'd:1');
  emptyAt(app, 'a:2');
  autoAdvance(app);
  // a:2 is empty and its spool in hand: the planner still counts it, so the step is to put it back.
  const g = guideOf(app);
  assert.deepEqual(
    g?.steps.map((s) => s.kind),
    ['putBack'],
  );
  assert.ok(textOf(view(app)).includes(t('step.put_back', { key: 'd:1', what: 'PLA' })));
  plaAt(app, 'd:1', '111111');
  autoAdvance(app);
  assert.deepEqual([guideOf(app)?.steps, app.print.held], [[], []]);
  assert.deepEqual([app.print.choice, app.print.pinned?.[1]?.uid], [{ 1: 'd:1' }, 'd:1']);
  const out = view(app);
  assert.ok(textOf(out).includes(t('step.in_place')));
  assert.ok(!/class="btn go" disabled/.test(out));
  calls.sent.length = 0;
  await ctx.run('print_go');
  assert.deepEqual(calls.confirm, []);
  assert.match(calls.sent[0][0], PRINT_M);
});

test('a move by hand, without eject all: each step done, then "in place", and Print sends the bays', async () => {
  const app = appOf('four', movePlan);
  const { ctx, calls } = live(app);
  autoAdvance(app);
  emptyAt(app, 'd:1');
  autoAdvance(app);
  assert.equal(guideOf(app)?.cur?.kind, 'move');
  // The spool leaves a:2 (in hand) and goes into d:1.
  emptyAt(app, 'a:2');
  autoAdvance(app);
  plaAt(app, 'd:1', '111111');
  autoAdvance(app);
  assert.deepEqual(guideOf(app)?.steps, []);
  assert.ok(textOf(view(app)).includes(t('step.in_place')));
  await ctx.run('print_go');
  assert.deepEqual(calls.confirm, []);
  assert.match(calls.sent[0][0], PRINT_M);
  // "Done, next" on a move the page cannot see also settles it: the colour prints from that bay.
  const hand = appOf('four', movePlan);
  const h = live(hand);
  h.ctx.run('step_done');
  h.ctx.run('step_done');
  // The pin is the black PLA that moves in; the red one d:1 still shows is the one it replaces.
  assert.deepEqual(hand.print.choice, { 1: 'd:1' });
  assert.deepEqual(hand.print.pinned?.[1], { uid: null, look: 'pla:#111111', was: { uid: null, look: 'pla:#dd3333' } });
  assert.deepEqual(guideOf(hand)?.steps, []);
});

test("Print sends the bays as they are, the steps being a suggestion; a merge prints from its root's bay", async () => {
  // Before any step: both colours have a bay (a:1 and a:2, on T0). Print sends those, more swaps and all; it asks
  // about the step still pending, never forces the suggestion (black to d:1).
  const app = appOf('four', movePlan);
  const { ctx, calls } = live(app);
  assert.ok(!/class="btn go" disabled/.test(view(app)));
  // The timeline shows the same bays: both colours on T0, which swaps, and nothing on T3.
  const timeline = textOf(view(app).slice(view(app).indexOf(t('timeline.cap'))));
  assert.match(timeline, new RegExp(`T0 ${t('timeline.count', { n: 7 })}`));
  assert.ok(!timeline.includes('T3'));
  // The bar's summary counts the same swaps: 7.
  assert.ok(textOf(view(app)).includes(t('print.bar', { swaps: t('print.swaps_n', { n: 7 }), time: fmtHM(7 * 90) })));
  // The bar says the bays go as they are, not "1 step left".
  assert.ok(textOf(view(app)).includes(t('print.as_is_note')));
  assert.ok(!textOf(view(app)).includes(t('print.step_left')));
  await ctx.run('print_go');
  assert.equal(calls.confirm.length, 1);
  assert.match(calls.sent[0][0], /^ACEK_PLAN_SET FILE="m\.gcode" T0=a:1 T1=a:2\n/);
  // PETG colours go to bays whose PLA the steps take out first: they have no spool yet, so Print waits and says so.
  const petg = view(appOf('four', fourPlan));
  assert.match(petg, /class="btn go" disabled/);
  assert.ok(textOf(petg).includes(t('print.put_pending', { tools: 'color 3', key: 'b:2' })), textOf(petg));
  // Black joins red: both colours print from a:1, nothing is left to change, and the start names black printing
  // with the red spool (a confirm, never a block).
  const merged = appOf('four', movePlan, { merges: { 1: 0 } });
  const m = live(merged);
  await m.ctx.run('print_go');
  const red = t('print.as_is_colour_hex', { head: 'T0', n: 2, what: 'PLA', key: 'a:1', colour: '#dd3333' });
  assert.deepEqual(m.calls.confirm, [t('print.as_is_check', { list: red })]);
  assert.match(m.calls.sent[0][0], /^ACEK_PLAN_SET FILE="m\.gcode" T0=a:1 T1=a:1\n/);
});

test('the print bar: summary, steps left, options as switches, and Print disabled with its reason', () => {
  const app = appOf('four', movePlan);
  const { ctx } = live(app);
  const now = planNow(app);
  const bar = (left, summary = 'S') =>
    textOf(renderHtml(html`<${PrintBar} app=${app} ctx=${ctx} now=${now} summary=${summary} left=${left} />`));
  assert.ok(bar(1).includes(t('print.step_left')));
  assert.ok(bar(2).includes(t('print.steps_left', { n: 2 })));
  assert.ok(bar(0).includes(`S ${t('print.ready')}`));
  assert.ok(textOf(view(app)).includes(t('print.go_big')));
  // A switch runs print_opt with its state; flow on lists the heads the print uses.
  ctx.run('print_opt', { opt: 'flow' }, '', true);
  assert.ok(bar(0).includes(t('panel.head', { head: 'T0' })));
  // The heads are those of the bays Print sends: a:1 and a:2 as they are, both on T0; not d:1 (T3) of the suggestion.
  assert.ok(!bar(0).includes(t('panel.head', { head: 'T3' })));
  ctx.run('print_opt', { opt: 'flow_head', head: '0' }, '', false);
  assert.equal(app.printOptions.flow, false);
  // While a start is in flight: "Starting…" and its note.
  app.pending.add('print:go');
  assert.ok(bar(0).includes(t('print.starting')) && bar(0).includes(t('print.starting_note')));
  app.pending.delete('print:go');
  // A check pending does not block Print: it is asked about at the start. A put still pending does (white into
  // bay 3 of demo-unknown, which holds a spool of no material): color 1 has no spool yet.
  assert.ok(!/class="btn go" disabled/.test(view(checkApp())));
  assert.ok(textOf(view(appOf('unknown', fivePlan))).includes(t('print.put_pending', { tools: 'color 1', key: '3' })));
  // More colours than bays blocks Print and says so.
  const many = appOf('unknown', read('../dev/demo-files.json').plans['benchy-5col.gcode']);
  const out = view(many);
  assert.ok(textOf(out).includes(t('print.no_fit_n', { n: 5, m: 4 })));
  assert.match(out, /class="btn go" disabled/);
});

test('a phone: a compact print bar (summary, Options ▾ and Print), no options in the flow', () => {
  const app = appOf('four', movePlan, {}, { narrow: true });
  const out = view(app);
  assert.ok(!out.includes('class="tgl"'));
  const bar = out.slice(out.indexOf('<section class="pbar compact"'));
  assert.ok(bar.includes(t('print.options_btn')) && bar.includes(t('print.go_big')));
  assert.ok(view(appOf('four', movePlan)).includes('class="tgl"'));
});

test('the overlay: a bay in error, a bay of no material and the open bay are never dimmed', () => {
  const lane = (key, extra = {}) => ({
    key,
    n: 1,
    head: 0,
    empty: false,
    spool: { colour: '#fff', material: 'PLA', tag: true },
    current: false,
    ...extra,
  });
  const o = { from: 'a', to: 'b' };
  assert.deepEqual(bayRole(lane('a'), o, null), { role: 'from', dim: false });
  assert.deepEqual(bayRole(lane('b'), o, null), { role: 'to', dim: false });
  assert.deepEqual(bayRole(lane('c'), o, null), { role: null, dim: true });
  assert.deepEqual(bayRole(lane('c'), o, 'c'), { role: null, dim: false });
  assert.deepEqual(bayRole(lane('c', { error: 'Stuck' }), o, null), { role: null, dim: false });
  assert.deepEqual(bayRole(lane('c', { spool: { colour: null, material: null, tag: false } }), o, null).dim, false);
  assert.deepEqual(bayRole(lane('c', { spool: null }), { ejecting: 'c' }, null), { role: 'ej', dim: false });
  assert.deepEqual(bayRole(lane('c'), null, null), { role: null, dim: false });
  assert.deepEqual(bayRole(lane('c'), { chips: {} }, null), { role: null, dim: false });
});

// A hand-made plan view for the sentences: tool 0 white PLA, tool 1 of no material, bay a:1 white PETG named
// "white", a:2 empty.
const now = {
  plan: { tools: [tool('#ffffff', 'PLA'), tool(null, null), tool('zz', 'PLA'), tool('#ffffff', 'PETG')] },
  merges: {},
  lanes: [
    { key: 'a:1', colour: '#ffffff', material: 'PETG', empty: false, head: 0, colour_name: 'white' },
    { key: 'a:2', colour: null, material: null, empty: true, head: 0 },
    { key: 'a:3', colour: '#e72f1d', material: 'PLA', empty: false, head: 0, colour_name: null },
    { key: 'a:4', colour: '#fafafa', material: 'PLA', empty: false, head: 0, colour_name: null },
    { key: 'a:5', colour: null, material: null, empty: false, head: 0, unknown: true },
    { key: 'a:6', colour: '#ffffff', material: null, empty: false, head: 0 },
    { key: 'a:7', colour: '#ffffff', material: 'ABS', empty: false, head: 0 },
    { key: 'a:8', colour: '#ffffff', material: 'PLA', empty: false, head: 0 },
  ],
  place: { lines: [] },
};
const white = { id: 'w', colour: '#ffffff', material: 'PETG', tag: true };

test('stepText and stepSub: each kind of step in words', () => {
  const cases = [
    [{ kind: 'out', key: 'a:1', spool: white }, t('step.out', { key: 'a:1', what: 'white PETG' }), t('step.sub.out')],
    [{ kind: 'out', key: 'zz', spool: null }, t('step.out', { key: 'zz', what: 'spool' }), t('step.sub.out')],
    [
      { kind: 'move', from: 'a:1', key: 'a:2', spool: white, tools: [0] },
      t('step.move', { key: 'a:2', from: 'a:1', what: 'white PETG' }),
      t('step.sub.move_empty'),
    ],
    [
      { kind: 'move', from: 'a:2', key: 'a:1', spool: null, tools: [0] },
      t('step.move', { key: 'a:1', from: 'a:2', what: 'spool' }),
      '',
    ],
    [
      { kind: 'putBack', key: 'a:2', spool: white, tools: [0] },
      t('step.put_back', { key: 'a:2', what: 'PETG' }),
      t('step.sub.put_back'),
    ],
    [
      { kind: 'put', key: 'a:2', tools: [1] },
      t('step.put', { key: 'a:2', n: 2, what: 'spool' }),
      t('step.sub.put', { n: 2 }),
    ],
    // A check of a white spool whose material is not known (a:6): the page cannot compare it with every color, never
    // "looks like"; of one whose colour is not known: cannot tell.
    [
      { kind: 'check', key: 'a:6', tools: [0] },
      t('step.check', { key: 'a:6', n: 1 }),
      t('step.sub.check_unsure', { n: 1 }),
    ],
    [{ kind: 'check', key: 'a:2', tools: [0] }, t('step.check', { key: 'a:2', n: 1 }), t('step.sub.check')],
    // A put over a spool says why as the planner decided it: another material, no material, another colour (named
    // by the host, else by its colour); a put over a spool that may be the colour's (a new spool asked) says so.
    [
      { kind: 'put', key: 'a:1', tools: [0] },
      t('step.put', { key: 'a:1', n: 1, what: 'PLA' }),
      t('step.sub.put_over_material', { key: 'a:1', what: 'PETG', want: 'PLA', n: 1 }),
    ],
    [
      { kind: 'put', key: 'a:5', tools: [0] },
      t('step.put', { key: 'a:5', n: 1, what: 'PLA' }),
      t('step.sub.put_over_unknown', { key: 'a:5', n: 1 }),
    ],
    [
      { kind: 'put', key: 'a:4', tools: [0] },
      t('step.put', { key: 'a:4', n: 1, what: 'PLA' }),
      t('step.sub.put_fresh', { key: 'a:4', n: 1 }),
    ],
    // A file colour the page cannot name is never written raw.
    [
      { kind: 'put', key: 'a:3', tools: [2] },
      t('step.put', { key: 'a:3', n: 3, what: 'PLA' }),
      t('step.sub.put_over_any', { key: 'a:3', what: 'red PLA', n: 3 }),
    ],
    // A group joined across materials (white PLA and white PETG) over an ABS spool: the materials it is not, named.
    [
      { kind: 'put', key: 'a:7', tools: [0, 3] },
      t('step.put', { key: 'a:7', n: 1, what: 'PLA' }),
      t('step.sub.put_over_material', { key: 'a:7', what: 'ABS', want: 'PLA/PETG', n: 1 }),
    ],
    // The same group over a PLA spool: one spool cannot print both materials, the conflict named before any colour.
    [
      { kind: 'put', key: 'a:8', tools: [0, 3] },
      t('step.put', { key: 'a:8', n: 1, what: 'PLA' }),
      t('step.sub.put_over_mixed', { key: 'a:8', what: 'PLA', other: 'PETG' }),
    ],
    // A check whose verdict is a mismatch (a hand-made one: the planner puts there) takes the put's words; a match
    // needs none.
    [
      { kind: 'check', key: 'a:3', tools: [0] },
      t('step.check', { key: 'a:3', n: 1 }),
      t('step.sub.put_over', { key: 'a:3', what: 'red PLA', n: 1, want: 'white' }),
    ],
    [{ kind: 'check', key: 'a:4', tools: [0] }, t('step.check', { key: 'a:4', n: 1 }), ''],
    // A check for a colour the file gives no colour: the page cannot compare.
    [
      { kind: 'check', key: 'a:4', tools: [1] },
      t('step.check', { key: 'a:4', n: 2 }),
      t('step.sub.check_no_file_colour', { n: 2 }),
    ],
    [
      { kind: 'put', key: 'a:3', tools: [0] },
      t('step.put', { key: 'a:3', n: 1, what: 'PLA' }),
      t('step.sub.put_over', { key: 'a:3', what: 'red PLA', n: 1, want: 'white' }),
    ],
  ];
  for (const [s, text, sub] of cases) {
    assert.equal(stepText(t, s, now), text);
    assert.equal(stepSub(t, s, now), sub);
  }
  assert.deepEqual([stepId(cases[0][0]), stepId(cases[2][0])], ['o:a:1', 'i:a:2']);
});

test('the card and the rows from a hand-made guide: a check, a move from an empty bay, a tool gone', () => {
  const app = appOf('unknown', fivePlan, {}, { narrow: true });
  const { ctx } = live(app);
  const guide = (s) => ({ now, steps: [s], trail: [stepId(s)], at: 0, cur: s, live: new Set([stepId(s)]) });
  const card = (s) => renderHtml(html`<${GuidedStep} app=${app} ctx=${ctx} g=${guide(s)} />`);
  // A check lights its bay with what is there, no ghost.
  let out = card({ kind: 'check', key: 'a:1', tools: [0] });
  assert.match(out, /class="srow to"/);
  assert.ok(textOf(out).includes(`PETG ${t('step.put_in')}`));
  assert.ok(textOf(out).includes(t('step.see_one')));
  // A check of an empty bay, and a move out of an empty bay.
  out = card({ kind: 'check', key: 'a:2', tools: [0] });
  assert.ok(textOf(out).includes(`${t('units.empty')} ${t('step.put_in')}`));
  out = card({ kind: 'move', from: 'a:2', key: 'a:1', spool: null, tools: [0] });
  assert.ok(textOf(out).includes(`${t('units.empty')} ${t('step.take_out')}`));
  assert.ok(textOf(out).includes(t('step.ghost', { material: t('step.spool') })));
  // A tool beyond the plan's list: no colour, no material.
  const gone = { kind: 'put', key: 'a:2', tools: [5] };
  assert.deepEqual(stepBays(guide(gone), gone).ghost, { colour: null, material: null });
  assert.deepEqual(stepBays(guide(gone), null), { from: null, to: null, ghost: null });
  // The overlay names the bay ejecting while a run is on, and nothing else.
  app.print = { ...app.print, eject: { phase: 'run', key: 'a:1' } };
  assert.equal(overlayOf(app, guide(gone)).ejecting, 'a:1');
  app.print = { ...app.print, eject: { phase: 'ask' } };
  assert.equal(overlayOf(app, guide(gone)).ejecting, null);
  assert.equal(renderHtml(html`<${EjectLine} app=${app} ctx=${ctx} />`), '');
  const noLines = { ...guide(gone), now: { ...now, place: null } };
  assert.deepEqual(overlayOf(app, noLines).chips, {});
});

test('a new file starts its guide over', () => {
  const app = appOf('unknown', fivePlan, {
    step: 2,
    trail: ['x'],
    held: [white],
    eject: { phase: 'ask' },
    allSteps: true,
  });
  const { ctx } = live(app);
  planFile(ctx, 'five.gcode');
  assert.deepEqual(
    [app.print.step, app.print.trail, app.print.shown, app.print.held, app.print.eject, app.print.allSteps],
    [0, [], null, [], null, false],
  );
});

// Every element vnode a render makes, so a test can press its buttons.
function pressable(vnode) {
  const nodes = [];
  const prev = options.vnode;
  options.vnode = (v) => {
    if (typeof v.type === 'string') nodes.push(v);
    prev?.(v);
  };
  try {
    renderHtml(vnode);
  } finally {
    options.vnode = prev;
  }
  return nodes.filter((n) => n.props.onClick || n.props.onChange);
}

test('every button of the card, the confirmation, the run line and the rows runs its action', () => {
  const app = appOf('four', fourPlan, { allSteps: false }, { narrow: true });
  const runs = [];
  const ctx = { app, t, run: (id, data) => runs.push([id, data]) };
  const g = guideOf(app);
  for (const n of pressable(html`<${GuidedStep} app=${app} ctx=${ctx} g=${g} />`)) n.props.onClick();
  app.print = { ...app.print, eject: { phase: 'ask' } };
  for (const n of pressable(html`<${EjectAll} app=${app} ctx=${ctx} g=${g} />`)) n.props.onClick();
  app.print = { ...app.print, eject: { phase: 'run', key: 'c:1' } };
  for (const n of pressable(html`<${EjectLine} app=${app} ctx=${ctx} />`)) n.props.onClick();
  app.print = { ...app.print, eject: { phase: 'failed', key: 'c:1', msg: 'no' } };
  for (const n of pressable(html`<${EjectLine} app=${app} ctx=${ctx} />`)) n.props.onClick();
  // On a phone the compact bar opens the options in a sheet; the switches there run print_opt.
  const plan = planNow(app);
  for (const vnode of [
    html`<${PlanView} app=${app} ctx=${ctx} />`,
    html`<${Options} app=${app} ctx=${ctx} now=${plan} />`,
  ])
    for (const n of pressable(vnode))
      n.props.onClick ? n.props.onClick() : n.props.onChange?.({ currentTarget: { checked: false } });
  assert.deepEqual([...new Set(runs.map(([id]) => id))].sort(), [
    'eject_all',
    'eject_all_go',
    'eject_all_no',
    'eject_all_stop',
    'panel',
    'print_back',
    'print_go',
    'print_opt',
    'step_done',
    'step_prev',
    'steps_all',
  ]);
  // The diagram's step bays open their panel as every bay does.
  const wide = appOf('four', fourPlan);
  const seen = [];
  const c2 = { app: wide, t, run: (id, data) => seen.push([id, data]) };
  for (const n of pressable(html`<${PlanView} app=${wide} ctx=${c2} />`)) n.props.onClick?.();
  assert.ok(seen.some(([id, d]) => id === 'panel' && d.key === 'b:2'));
});

test('stepTravel and latchTravel: Done slides right, Back left, a reset and the done card none, a re-render keeps it', () => {
  const trail = ['a', 'b', 'c'];
  assert.equal(stepTravel(null, 'a', trail), null);
  assert.equal(stepTravel('a', 'b', trail), 'right');
  assert.equal(stepTravel('c', 'b', trail), 'left');
  assert.equal(stepTravel('b', 'passed', trail), null);
  assert.equal(stepTravel('passed', 'a', trail), null);
  // A reset from a later index back to the first: the previous step is not in the new trail.
  assert.equal(stepTravel('c', 'x', ['x', 'y']), null);
  const ref = { current: null };
  assert.equal(latchTravel(ref, 'a', trail), null);
  assert.equal(latchTravel(ref, 'b', trail), 'right');
  assert.equal(latchTravel(ref, 'b', trail), 'right', 'the same step keeps its direction on a re-render');
  assert.equal(latchTravel(ref, 'a', trail), 'left');
  assert.equal(latchTravel(ref, 'a', trail), 'left');
  assert.equal(latchTravel(ref, 'x', ['x', 'y']), null, 'a reset never slides');
});

test('one step text at a time, keyed by the step', () => {
  const app = appOf('unknown', fivePlan, {}, { narrow: true });
  const { ctx } = live(app);
  const at = (s) => {
    const g = { now, steps: [s], trail: [stepId(s)], at: 0, cur: s, live: new Set([stepId(s)]) };
    const out = renderHtml(html`<${GuidedStep} app=${app} ctx=${ctx} g=${g} />`);
    assert.equal((out.match(/class="gtext"/g) ?? []).length, 1);
    return out.match(/data-step="([^"]*)"/)?.[1];
  };
  const a = at({ kind: 'check', key: 'a:1', tools: [0] });
  const b = at({ kind: 'check', key: 'a:2', tools: [0] });
  assert.ok(a && b && a !== b);
});
