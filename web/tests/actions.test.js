import { test } from 'node:test';
import assert from 'node:assert/strict';
import { ACTIONS, CHANGES, dryArgs, LOG_WAIT_MS, laneRefusal, runAction } from '../js/actions.js';
import { CONNECTION_CLOSED } from '../js/conn.js';
import { ingestGcode } from '../js/live.js';
import { editAction, gcodeFor } from '../js/model.js';
import { DEFAULT_OPTIONS } from '../js/print-plan.js';
import { bays, oneUnit, status as fullStatus, twoHeadedUnits, twoUnits } from './helpers.js';

// The texts as t() keys with their values, so a test reads which key and which values were shown.
const t = (key, vars) => (vars ? `${key} ${JSON.stringify(vars)}` : key);
// A head's name as the page writes it, through the same fake t().
const hd = (n) => t('units.head', { n });
const flush = () => new Promise((resolve) => setImmediate(resolve));

// The page state an action reads, and fake effects that record what they were asked to do.
// sendOk is what send and sendScript resolve to. answer is what confirm() returns, or a function of the text.
function harness(appOver = {}, { sendOk = true, answer = true, demo = false, eventCount = 0 } = {}) {
  const calls = {
    send: [],
    sendScript: [],
    toast: [],
    confirm: [],
    render: 0,
    blur: 0,
    loadDays: [],
    openPrint: 0,
    planFile: [],
    startPrint: 0,
    saveHidden: 0,
    saveTheme: 0,
    saveTab: 0,
  };
  const app = {
    status: oneUnit(),
    online: true,
    klippy: 'ready',
    selected: null,
    pending: new Set(),
    edit: null,
    dryerUnit: null,
    chartUnit: null,
    presets: [
      { material: 'PLA', temp: 50, hours: 4 },
      { material: 'PETG', temp: 65, hours: 6 },
    ],
    form: { preset: 1, temp: 60, hm: '6:00' },
    forms: {},
    moveMm: '10',
    hours: null,
    hidden: [],
    theme: 'system',
    filter: 'all',
    limit: 200,
    days: [],
    daysLoaded: 0,
    loaded: new Set(),
    print: { open: false, files: [], file: null, choice: {}, choiceId: null },
    printOptions: { ...DEFAULT_OPTIONS },
    conn: null,
    ...appOver,
  };
  const ctx = {
    app,
    demo,
    t,
    send: (id, n, args, opts = {}) => {
      let script;
      try {
        script = gcodeFor(id, n, args, opts.unit ?? null);
      } catch (e) {
        script = `refused: ${e.message}`;
      }
      calls.send.push({ id, n, args, opts, script });
      return Promise.resolve(sendOk);
    },
    sendScript: (script, n, opts) => {
      calls.sendScript.push({ script, n, opts });
      return Promise.resolve(sendOk);
    },
    toast: (msg) => calls.toast.push(msg),
    confirm: (msg) => {
      calls.confirm.push(msg);
      return typeof answer === 'function' ? answer(msg) : answer;
    },
    render: () => {
      calls.render += 1;
    },
    blur: () => {
      calls.blur += 1;
    },
    // The pause before a check again. It resolves at once unless a test stands in for it.
    sleep: () => Promise.resolve(),
    now: () => 50,
    loadDays: (n) => calls.loadDays.push(n),
    openPrint: () => {
      calls.openPrint += 1;
    },
    planFile: (file) => calls.planFile.push(file),
    startPrint: () => {
      calls.startPrint += 1;
    },
    saveHidden: () => {
      calls.saveHidden += 1;
    },
    saveTheme: () => {
      calls.saveTheme += 1;
    },
    saveTab: () => {
      calls.saveTab += 1;
    },
    eventCount: () => eventCount,
  };
  return { app, ctx, calls };
}

// A button or control as the views render it, with its data-* attributes. Calls to blur() are counted.
// A press of a component's button: ctx.run(id, data), with the action's id in data.action, as the components call it.
const act = (ctx, node) => runAction(ctx, node.dataset.action, { ...node.dataset });

function el(data = {}, extra = {}) {
  const node = { dataset: { ...data }, blurred: 0, ...extra };
  node.blur = () => {
    node.blurred += 1;
  };
  return node;
}

const OFFLINE = 'toast.refused {"msg":"reason.offline"}';

test('the table holds one entry per data-action id the views render', () => {
  assert.deepEqual(Object.keys(ACTIONS).sort(), [
    'card_load',
    'chart_unit',
    'define',
    'define_and_use',
    'dry',
    'dry_clear',
    'dry_log',
    'dry_log_close',
    'dry_mode',
    'dry_mode_cancel',
    'dry_mode_confirm',
    'dry_shortcut',
    'dry_stop',
    'edit_cancel',
    'edit_open',
    'edit_save',
    'eject_all',
    'eject_all_go',
    'eject_all_no',
    'eject_all_stop',
    'fans_off',
    'fans_on',
    'file_pick',
    'filter',
    'flap',
    'hint_keep',
    'lane',
    'list_mode',
    'merge',
    'more',
    'move_mm',
    'panel',
    'panel_close',
    'pick',
    'plan_again',
    'preset',
    'print_back',
    'print_cancel',
    'print_go',
    'print_open',
    'print_pause',
    'print_resume',
    'print_sort',
    'probe_head',
    'series',
    'spool_edit_colour',
    'spool_edit_type',
    'spool_edit_vendor',
    'step_done',
    'step_prev',
    'steps_all',
    'stop_all',
    'tab',
    'theme',
    'unpin',
    'window',
  ]);
  assert.deepEqual(Object.keys(CHANGES).sort(), ['dry_field', 'print_opt', 'print_q']);
});

test('runAction: a lane in the data pins its lane on the shown unit, then runs its action', () => {
  const { app, ctx, calls } = harness();
  act(ctx, el({ action: 'edit_cancel', lane: '3' }));
  assert.deepEqual(app.selected, { unit: null, lane: 2 });
  assert.equal(calls.render, 1);
  const two = harness({ status: twoUnits(), selected: { unit: 'b', lane: 0 } });
  act(two.ctx, el({ action: 'edit_cancel', lane: '4' }));
  assert.deepEqual(two.app.selected, { unit: 'b', lane: 3 });
});

test('runAction: data without a lane leaves the selection; an unknown or inherited id does nothing', () => {
  const { app, ctx, calls } = harness({ selected: { unit: null, lane: 1 } });
  act(ctx, el({ action: 'nothing' }));
  act(ctx, el({ action: 'toString' }));
  act(ctx, el({}));
  assert.deepEqual(app.selected, { unit: null, lane: 1 });
  assert.equal(calls.render, 0);
});

test('edit_open: the form prefilled for the lane and its unit', () => {
  const { app, ctx, calls } = harness({ status: twoUnits(), selected: { unit: 'b', lane: 0 } });
  act(ctx, el({ action: 'edit_open', lane: '2' }));
  assert.deepEqual(app.edit, { lane: 2, head: null, type: 'PLA', vendor: 'Generic', color: '#ffffff', unit: 'b' });
  assert.equal(calls.render, 1);
});

test('edit_open: the unit the data names, the unnamed unit as ""; a lane named by the panel pins it there', () => {
  const two = harness({ status: twoUnits(), selected: { unit: 'b', lane: 0 } });
  act(two.ctx, el({ action: 'edit_open', lane: '3', unit: 'a' }));
  assert.equal(two.app.edit.unit, 'a');
  assert.deepEqual(two.app.selected, { unit: 'a', lane: 2 });
  const one = harness();
  act(one.ctx, el({ action: 'edit_open', lane: '2', unit: '' }));
  assert.deepEqual([one.app.edit.unit, one.app.edit.lane], [null, 2]);
});

test('edit_open with use: the colour picker locks the material and fills the colour', () => {
  const { app, ctx } = harness();
  ACTIONS.edit_open(ctx, el({ lane: '2', unit: '', use: '0', material: 'PETG', colour: '#AA00CC' }));
  assert.deepEqual([app.edit.use, app.edit.type, app.edit.color, app.edit.lock], [0, 'PETG', '#aa00cc', true]);
  // A material the U1 does not list keeps the pick (use) but leaves the material free; a colour that is not
  // #rrggbb keeps the prefill.
  ACTIONS.edit_open(ctx, el({ lane: '2', use: '1', material: 'WOOD', colour: 'red' }));
  assert.deepEqual([app.edit.use, app.edit.type, app.edit.lock], [1, 'PLA', undefined]);
  ACTIONS.spool_edit_type(ctx, el({ type: 'PETG' }));
  assert.equal(app.edit.type, 'PETG');
  ACTIONS.edit_open(ctx, el({ lane: '2', use: '1', material: 'ABS', colour: 'red' }));
  assert.deepEqual([app.edit.use, app.edit.type, app.edit.color], [1, 'ABS', '#ffffff']);
  ACTIONS.edit_open(ctx, el({ lane: '2', use: '1', material: 'ABS' }));
  assert.equal(app.edit.color, '#ffffff');
});

test('spool_edit_*: the open form follows the editor; a locked material and an unknown one stay', () => {
  const { app, ctx, calls } = harness({ edit: form() });
  ACTIONS.spool_edit_type(ctx, el({ type: 'ABS' }));
  ACTIONS.spool_edit_vendor(ctx, el({}, { value: 'Elegoo' }));
  ACTIONS.spool_edit_colour(ctx, el({}, { value: '#ffcc33' }));
  assert.deepEqual([app.edit.type, app.edit.vendor, app.edit.color], ['ABS', 'Elegoo', '#ffcc33']);
  ACTIONS.spool_edit_type(ctx, el({ type: 'WOOD' }));
  ACTIONS.spool_edit_type(ctx, el({}));
  assert.equal(app.edit.type, 'ABS');
  app.edit = form({ use: 2, lock: true });
  ACTIONS.spool_edit_type(ctx, el({ type: 'ABS' }));
  assert.equal(app.edit.type, 'PETG');
  app.edit = null;
  ACTIONS.spool_edit_vendor(ctx, el({}, { value: 'x' }));
  assert.equal(app.edit, null);
  assert.equal(calls.render, 3);
});

test('move_mm: the Advanced length follows the field', () => {
  const { app, ctx, calls } = harness();
  ACTIONS.move_mm(ctx, el({}, { value: '25' }));
  assert.deepEqual([app.moveMm, calls.render], ['25', 1]);
});

test('edit_save with use: the saved spool becomes the pick of that colour, for the plan shown or the bare choice', async () => {
  const bare = harness({ edit: form({ use: 1 }) });
  ACTIONS.edit_save(bare.ctx, el({ lane: '2' }));
  await flush();
  assert.deepEqual(bare.app.print.choice, { 1: '2' });
  const plan = { file: 'x.gcode', mtime: 1, tools: [{ lane: 'a:1' }, { lane: 'a:1' }], order: [], lanes: [] };
  const s = { ...twoUnits(), ace2k_u1: { hooked: true, plan, heads: [[{ unit: 'b', lane: 2 }], null, null, null] } };
  const two = harness({
    status: s,
    edit: form({ head: 0, unit: 'b', use: 0 }),
    print: { open: true, files: [], file: 'x.gcode', choice: {}, choiceId: null },
  });
  ACTIONS.edit_save(two.ctx, el({ lane: '2' }));
  await flush();
  assert.deepEqual(two.app.print.choice, { 0: 'b:2' });
  assert.equal(two.app.print.choiceId, 'x.gcode\n1');
  assert.equal(two.app.edit, null);
});

test('pick, merge and unpin: a pick drops the merge, a merge drops the pick, unpin drops both', () => {
  const plan = { file: 'x.gcode', mtime: 1, tools: [{ lane: '1' }, { lane: '2' }], order: [0, 1], lanes: [] };
  const print = { open: true, files: [], file: 'x.gcode', choice: {}, choiceId: null, merges: { 0: 1 } };
  const { app, ctx, calls } = harness({ status: { ...oneUnit(), ace2k_u1: { hooked: true, plan } }, print });
  ACTIONS.pick(ctx, el({ n: '0', key: '3' }));
  assert.deepEqual([app.print.choice, app.print.merges, app.print.choiceId], [{ 0: '3' }, {}, 'x.gcode\n1']);
  ACTIONS.pick(ctx, el({ n: '1', key: 'new' }));
  assert.deepEqual(app.print.choice, { 0: '3', 1: 'new' });
  ACTIONS.pick(ctx, el({ n: '1' })); // no key: nothing
  ACTIONS.merge(ctx, el({ n: '0', into: '1' }));
  assert.deepEqual([app.print.choice, app.print.merges], [{ 1: 'new' }, { 0: 1 }]);
  ACTIONS.unpin(ctx, el({ n: '0' }));
  ACTIONS.unpin(ctx, el({ n: '1' }));
  assert.deepEqual([app.print.choice, app.print.merges], [{}, {}]);
  assert.equal(calls.render, 5);
});

test('hint_keep: the similar-colour hint is kept apart for the chosen file; with none, nothing changes', () => {
  const { app, ctx } = harness();
  ACTIONS.hint_keep(ctx, el());
  assert.equal(app.print.dismissed, undefined);
  app.print = { ...app.print, file: 'a.gcode' };
  ACTIONS.hint_keep(ctx, el());
  app.print = { ...app.print, file: 'b.gcode' };
  ACTIONS.hint_keep(ctx, el());
  assert.deepEqual(app.print.dismissed, ['a.gcode', 'b.gcode']);
});

test('define_and_use: the bay panel opens on its editor, filled for the colour; a bay that is gone does nothing', () => {
  const two = harness({ status: twoUnits(), ui: { tab: 'print', panel: { kind: 'pick', n: '0' }, sheet: false } });
  ACTIONS.define_and_use(two.ctx, el({ n: '2', key: 'b:3', material: 'PETG', colour: '#00ff00' }));
  assert.deepEqual(two.app.ui.panel, { kind: 'bay', key: 'b:3' });
  assert.deepEqual(
    [two.app.edit.unit, two.app.edit.lane, two.app.edit.use, two.app.edit.type, two.app.edit.lock, two.app.edit.color],
    ['b', 3, 2, 'PETG', true, '#00ff00'],
  );
  const one = harness({ ui: { tab: 'print', panel: null, sheet: false } });
  ACTIONS.define_and_use(one.ctx, el({ n: '0', key: '2' }));
  assert.deepEqual(
    [one.app.edit.unit, one.app.edit.lane, one.app.edit.use, one.app.edit.lock],
    [null, 2, 0, undefined],
  );
  const gone = harness({ ui: { tab: 'print', panel: null, sheet: false } });
  ACTIONS.define_and_use(gone.ctx, el({ n: '0', key: 'z:9' }));
  assert.deepEqual([gone.app.ui.panel, gone.app.edit], [null, null]);
});

test('define while offline: the bay panel opens without its editor, and its Edit spool says offline', () => {
  const status = { ...oneUnit(), print_stats: { state: 'standby' } }; // not printing: the offline branch alone
  const off = harness({ online: false, status, ui: { tab: 'print', panel: null, sheet: false } });
  ACTIONS.define(off.ctx, el({ key: '2' }));
  assert.deepEqual([off.app.ui.panel, off.app.edit], [{ kind: 'bay', key: '2' }, null]);
  assert.equal(editAction(status, 1, false)?.reason, 'offline');
  const down = harness({ klippy: 'shutdown', status, ui: { tab: 'print', panel: null, sheet: false } });
  ACTIONS.define(down.ctx, el({ key: '2' }));
  assert.equal(down.app.edit, null);
  const up = harness({ status, ui: { tab: 'print', panel: null, sheet: false } });
  ACTIONS.define(up.ctx, el({ key: '2' }));
  assert.notEqual(up.app.edit, null); // ready and not printing: the editor opens
});

test('edit_save with use takes the colour out of a merge: a pick and a merge exclude each other', async () => {
  const { app, ctx } = harness({ edit: form({ use: 1 }) });
  app.print = { ...app.print, merges: { 1: 0, 2: 0 } };
  ACTIONS.edit_save(ctx, el({ lane: '2' }));
  await flush();
  assert.deepEqual([app.print.choice, app.print.merges], [{ 1: '2' }, { 2: 0 }]);
  // The spool just defined is the pin, by the material and colour saved: the pick follows it from now on.
  assert.deepEqual(app.print.pinned, { 1: { uid: null, look: 'petg:#12ab34', was: null } });
});

test('edit_save: an empty vendor is saved as Generic', () => {
  const { ctx, calls } = harness({ edit: form({ vendor: '  ' }) });
  ACTIONS.edit_save(ctx, el({ lane: '2' }));
  assert.match(calls.sendScript[0].script, /VENDOR=Generic/);
});

test('edit_cancel: the form dropped', () => {
  const { app, ctx, calls } = harness({ edit: { lane: 1 } });
  ACTIONS.edit_cancel(ctx, el());
  assert.equal(app.edit, null);
  assert.equal(calls.render, 1);
});

const form = (o = {}) => ({ lane: 2, head: 1, type: 'PETG', vendor: 'Acme', color: '#12ab34', unit: null, ...o });

test('edit_save: the spool record and the head setting, then the form closes', async () => {
  const e = form();
  const { app, ctx, calls } = harness({ edit: e });
  ACTIONS.edit_save(ctx, el({ lane: '2' }));
  assert.deepEqual(calls.sendScript, [
    {
      script:
        'ACEK_SPOOL LANE=2 TYPE=PETG COLOR=12ab34 VENDOR=Acme\n' +
        "SET_PRINT_FILAMENT_CONFIG CONFIG_EXTRUDER=1 VENDOR=Acme FILAMENT_TYPE=PETG FILAMENT_SUBTYPE='' FILAMENT_COLOR_RGBA=12AB34FF FORCE=1",
      n: 2,
      opts: { key: 'edit_spool:2', unit: null },
    },
  ]);
  await flush();
  assert.equal(app.edit, null);
  assert.equal(calls.render, 1);
});

test('edit_save: several units key the request by unit and send UNIT=', () => {
  const { ctx, calls } = harness({
    status: { ...twoUnits(), ace2k_u1: { hooked: true, heads: [[{ unit: 'b', lane: 2 }], null, null, null] } },
    edit: form({ head: 0, unit: 'b' }),
  });
  ACTIONS.edit_save(ctx, el({ lane: '2' }));
  assert.equal(calls.sendScript[0].opts.key, 'b|edit_spool:2');
  assert.equal(calls.sendScript[0].opts.unit, 'b');
  assert.match(
    calls.sendScript[0].script,
    /^ACEK_SPOOL LANE=2 TYPE=PETG COLOR=12ab34 VENDOR=Acme UNIT=b\nSET_PRINT_FILAMENT_CONFIG CONFIG_EXTRUDER=0 /,
  );
});

test('edit_save: a failed send, or a form replaced meanwhile, leaves the form', async () => {
  const failed = harness({ edit: form() }, { sendOk: false });
  ACTIONS.edit_save(failed.ctx, el({ lane: '2' }));
  await flush();
  assert.notEqual(failed.app.edit, null);
  assert.equal(failed.calls.render, 0);
  const replaced = harness({ edit: form() });
  ACTIONS.edit_save(replaced.ctx, el({ lane: '2' }));
  const other = form({ type: 'PLA' });
  replaced.app.edit = other;
  await flush();
  assert.equal(replaced.app.edit, other);
});

test('edit_save: nothing without a form, for another lane, or while a save is pending', () => {
  for (const over of [
    { edit: null },
    { edit: form({ lane: 3 }) },
    { edit: form(), pending: new Set(['edit_spool:2']) },
  ]) {
    const { ctx, calls } = harness(over);
    ACTIONS.edit_save(ctx, el({ lane: '2' }));
    assert.deepEqual([calls.sendScript, calls.toast], [[], []]);
  }
});

// A sending Save stays focusable, so Enter can press it again. That second press sends nothing.
// The real sendScript marks the save pending before it awaits. The view then draws Save aria-disabled.
test('edit_save: a second press on a Save that is sending does nothing', () => {
  const { app, ctx, calls } = harness({ edit: form() });
  const send = ctx.sendScript;
  ctx.sendScript = (script, n, opts) => {
    app.pending.add(opts.key);
    return send(script, n, opts);
  };
  ACTIONS.edit_save(ctx, el({ lane: '2' }));
  ACTIONS.edit_save(ctx, el({ lane: '2' }, { 'aria-disabled': 'true' }));
  assert.equal(calls.sendScript.length, 1);
  assert.deepEqual(calls.toast, []);
});

test('edit_save: the lock reason, a head that moved, and a value the G-code refuses each toast', () => {
  const off = harness({ edit: form(), online: false });
  ACTIONS.edit_save(off.ctx, el({ lane: '2' }));
  assert.deepEqual(off.calls.toast, [OFFLINE]);
  const moved = harness({ edit: form({ head: 3 }) });
  ACTIONS.edit_save(moved.ctx, el({ lane: '2' }));
  assert.deepEqual(moved.calls.toast, ['toast.head_changed']);
  const bad = harness({ edit: form({ vendor: 'no spaces' }) });
  ACTIONS.edit_save(bad.ctx, el({ lane: '2' }));
  assert.deepEqual(bad.calls.toast, ['toast.refused {"msg":"refuse.vendor {}"}']);
  assert.deepEqual(
    [off, moved, bad].map((h) => h.calls.sendScript.length),
    [0, 0, 0],
  );
});

test('print_open, print_go: the flows main.js owns; print_go re-checks nothing at the click', () => {
  const { ctx, calls } = harness({ online: false, status: { ...oneUnit(), print_stats: { state: 'printing' } } });
  ACTIONS.print_open(ctx, el());
  ACTIONS.print_go(ctx, el());
  assert.equal(calls.openPrint, 1);
  assert.equal(calls.startPrint, 1);
  assert.deepEqual(calls.toast, []);
});

test('print_resume: RESUME while paused; nothing while a resume runs or no print is paused; refused offline', () => {
  const paused = { ...oneUnit(), print_stats: { state: 'paused' } };
  const ok = harness({ status: paused });
  ACTIONS.print_resume(ok.ctx, el());
  assert.deepEqual(ok.calls.sendScript, [{ script: 'RESUME', n: null, opts: { key: 'print:resume' } }]);
  for (const over of [
    { status: paused, pending: new Set(['print:resume']) },
    { status: { ...paused, ace2k_u1: { hooked: true, resuming: true } } },
    {},
  ]) {
    const h = harness(over);
    ACTIONS.print_resume(h.ctx, el());
    assert.deepEqual([h.calls.sendScript, h.calls.toast], [[], []]);
  }
  const off = harness({ status: paused, klippy: 'shutdown' });
  ACTIONS.print_resume(off.ctx, el());
  assert.deepEqual(off.calls.toast, [OFFLINE]);
  assert.deepEqual(off.calls.sendScript, []);
});

test('print_pause: PAUSE while printing; nothing while paused, a pause is pending or no print runs; refused offline', () => {
  const on = { ...oneUnit(), print_stats: { state: 'printing' } };
  const ok = harness({ status: on });
  ACTIONS.print_pause(ok.ctx, el());
  assert.deepEqual(ok.calls.sendScript, [{ script: 'PAUSE', n: null, opts: { key: 'print:pause' } }]);
  for (const over of [
    { status: on, pending: new Set(['print:pause']) },
    { status: { ...oneUnit(), print_stats: { state: 'paused' } } },
    {},
  ]) {
    const h = harness(over);
    ACTIONS.print_pause(h.ctx, el());
    assert.deepEqual([h.calls.sendScript, h.calls.toast], [[], []]);
  }
  const off = harness({ status: on, klippy: 'shutdown' });
  ACTIONS.print_pause(off.ctx, el());
  assert.deepEqual(off.calls.toast, [OFFLINE]);
  assert.deepEqual(off.calls.sendScript, []);
});

test('print_cancel: CANCEL_PRINT after the confirmation; nothing when declined or the print ended; refused offline', () => {
  const on = { ...oneUnit(), print_stats: { state: 'printing' } };
  const ok = harness({ status: on });
  ACTIONS.print_cancel(ok.ctx, el());
  assert.deepEqual(ok.calls.confirm, ['print.confirm_cancel']);
  assert.deepEqual(ok.calls.sendScript, [{ script: 'CANCEL_PRINT', n: null, opts: { key: 'print:cancel' } }]);
  const declined = harness({ status: on }, { answer: false });
  ACTIONS.print_cancel(declined.ctx, el());
  assert.deepEqual(declined.calls.sendScript, []);
  const ended = harness({ status: on });
  ended.ctx.confirm = () => {
    ended.app.status = oneUnit();
    return true;
  };
  ACTIONS.print_cancel(ended.ctx, el());
  assert.deepEqual(ended.calls.sendScript, []);
  const off = harness({ status: on, online: false });
  ACTIONS.print_cancel(off.ctx, el());
  assert.deepEqual([off.calls.toast, off.calls.confirm], [[OFFLINE], []]);
});

test('chart_unit: the unit shown', () => {
  const { app, ctx, calls } = harness({ status: twoUnits() });
  ACTIONS.chart_unit(ctx, el({ unit: 'b' }));
  assert.equal(app.chartUnit, 'b');
  ACTIONS.chart_unit(ctx, el({ unit: 'gone' }));
  assert.equal(app.chartUnit, 'a');
  assert.equal(calls.render, 2);
});

test('preset: the form takes the preset, the focus is dropped', () => {
  const { app, ctx, calls } = harness();
  ACTIONS.preset(ctx, el({ k: '0' }));
  assert.deepEqual(app.forms[''], { preset: 0, temp: 50, hm: '4:00' });
  assert.deepEqual([calls.blur, calls.render], [1, 1]);
});

const dryOf = (temp, hm, over = {}) => harness({ form: { preset: null, temp, hm }, ...over });

test('dry: the form as ACEK_DRY on the dryer unit', () => {
  const { ctx, calls } = dryOf('55', ' 2:30 ');
  ACTIONS.dry(ctx, el());
  assert.deepEqual(
    calls.send.map(({ id, n, args, opts, script }) => ({ id, n, args, opts, script })),
    [
      {
        id: 'dry',
        n: null,
        args: { temp: 55, minutes: 150 },
        opts: { unit: null },
        script: 'ACEK_DRY TEMP=55 DURATION=150',
      },
    ],
  );
  const two = dryOf(60, '6:00', { status: twoUnits(), dryerUnit: 'b' });
  ACTIONS.dry(two.ctx, el());
  assert.equal(two.calls.send[0].script, 'ACEK_DRY TEMP=60 DURATION=360 UNIT=b');
});

test('dryArgs: the form as temp and minutes, or null', () => {
  assert.deepEqual(dryArgs({ temp: '55', hm: ' 2:30 ' }), { temp: 55, minutes: 150 });
  assert.deepEqual(dryArgs({ temp: 65, hm: '0:59' }), { temp: 65, minutes: 59 });
  for (const hm of ['0:00', '2:60', '24:01', '1', 'x', '']) assert.equal(dryArgs({ temp: 50, hm }), null, hm);
  const a = dryOf(50, '2:60');
  ACTIONS.dry(a.ctx, el());
  assert.deepEqual([a.calls.send, a.calls.toast], [[], ['toast.refused {"msg":"dryer.limits"}']]);
});

test('dry on a card: the unit of the clicked card, not the unit the dryer last named', () => {
  const { ctx, calls } = dryOf(60, '1:00', { status: twoUnits(), dryerUnit: 'a' });
  ACTIONS.dry(ctx, el({ unit: 'b' }));
  assert.equal(calls.send[0].script, 'ACEK_DRY TEMP=60 DURATION=60 UNIT=b');
  const one = dryOf(60, '1:00');
  ACTIONS.dry(one.ctx, el({ unit: '' })); // the unnamed unit
  assert.equal(one.calls.send[0].script, 'ACEK_DRY TEMP=60 DURATION=60');
});

test('each unit keeps its own form: fields and presets on one card leave the others', () => {
  const { app, ctx, calls } = harness({ status: twoUnits() });
  CHANGES.dry_field(ctx, el({ unit: 'a', field: 'hm' }, { value: '2:00' }));
  ACTIONS.preset(ctx, el({ unit: 'b', k: '0' }));
  assert.deepEqual(app.forms, {
    a: { preset: null, temp: 60, hm: '2:00' },
    b: { preset: 0, temp: 50, hm: '4:00' },
  });
  assert.deepEqual(app.form, { preset: 1, temp: 60, hm: '6:00' }, 'the starting values stay');
  ACTIONS.dry(ctx, el({ unit: 'a' }));
  ACTIONS.dry(ctx, el({ unit: 'b' }));
  assert.deepEqual(
    calls.send.map((c) => [c.args, c.opts.unit]),
    [
      [{ temp: 60, minutes: 120 }, 'a'],
      [{ temp: 50, minutes: 240 }, 'b'],
    ],
  );
});

test('dry_field: the form keeps what was typed and drops the preset', () => {
  const { app, ctx, calls } = harness();
  CHANGES.dry_field(ctx, el({ field: 'hm' }, { value: '3:3' }));
  assert.deepEqual(app.forms[''], { preset: null, temp: 60, hm: '3:3' });
  CHANGES.dry_field(ctx, el({ field: 'temp' }, { value: '5' }));
  assert.deepEqual(app.forms[''], { preset: null, temp: '5', hm: '3:3' });
  assert.equal(calls.render, 2);
});

test('dry: out of range, not an integer or empty is refused with the limits; a pending dry sends nothing', () => {
  for (const [temp, hm] of [
    [14, '1:00'],
    [66, '1:00'],
    [50, '0:00'],
    [50, '24:01'],
    ['50.5', '1:00'],
    ['', '1:00'],
    [50, null],
    [50, 'x'],
  ]) {
    const { ctx, calls } = dryOf(temp, hm);
    ACTIONS.dry(ctx, el());
    assert.deepEqual(calls.toast, ['toast.refused {"msg":"dryer.limits"}'], `${temp} ${hm}`);
    assert.deepEqual(calls.send, []);
  }
  const ok = dryOf(65, '24:00');
  ACTIONS.dry(ok.ctx, el());
  assert.equal(ok.calls.send[0].script, 'ACEK_DRY TEMP=65 DURATION=1440');
  const busy = dryOf(50, '1:00', { status: twoUnits(), pending: new Set(['a|dry:']) });
  ACTIONS.dry(busy.ctx, el());
  assert.deepEqual([busy.calls.send, busy.calls.toast], [[], []]);
});

test('dry_stop, dry_clear, dry_log, fans_off, fans_on, flap: their commands on the dryer unit', () => {
  const { ctx, calls } = harness({ status: twoUnits(), dryerUnit: 'b' });
  for (const id of ['dry_stop', 'dry_clear', 'dry_log', 'fans_off', 'fans_on']) ACTIONS[id](ctx, el());
  ACTIONS.flap(ctx, el({ which: 'rear', open: '1' }));
  ACTIONS.flap(ctx, el({ which: 'bottom', open: '0' }));
  assert.deepEqual(
    calls.send.map((c) => [c.id, c.n, c.args, c.opts]),
    [
      ['dry_stop', null, {}, { unit: 'b' }],
      ['dry_clear', null, {}, { unit: 'b' }],
      ['dry_log', null, {}, { unit: 'b' }],
      ['fans_off', null, {}, { unit: 'b' }],
      ['fans_on', null, { seconds: 60 }, { unit: 'b' }],
      ['flap', null, { which: 'rear', open: true }, { unit: 'b' }],
      ['flap', null, { which: 'bottom', open: false }, { unit: 'b' }],
    ],
  );
  assert.deepEqual(
    calls.send.map((c) => c.script),
    [
      'ACEK_DRY_STOP UNIT=b',
      'ACEK_DRY_CLEAR UNIT=b',
      'ACEK_DRYER_LOG UNIT=b',
      'ACEK_FAN ON=0 UNIT=b',
      'ACEK_FAN ON=1 SECONDS=60 UNIT=b',
      'ACEK_FLAP WHICH=rear OPEN=1 UNIT=b',
      'ACEK_FLAP WHICH=bottom OPEN=0 UNIT=b',
    ],
  );
});

test('stop_all: outside a print the plain ACEK_STOP after its confirmation', () => {
  const { ctx, calls } = harness();
  ACTIONS.stop_all(ctx, el());
  assert.deepEqual(calls.confirm, ['top.confirm_stop_all {"unit":""}']);
  assert.deepEqual(calls.sendScript, [{ script: 'ACEK_STOP', n: null, opts: { key: 'stop_all:' } }]);
});

test('stop_all: during a print only the idle lanes of the first unit, named', () => {
  const s = { ...twoUnits(), print_stats: { state: 'printing' } };
  s['ace2k a'] = { lanes: bays({ 1: { mode: 'following' } }) };
  const { ctx, calls } = harness({ status: s });
  ACTIONS.stop_all(ctx, el());
  assert.deepEqual(calls.confirm, ['top.confirm_stop_idle_units {"unit":"a"}']);
  assert.deepEqual(calls.sendScript, [
    {
      script: 'ACEK_STOP LANE=1 UNIT=a\nACEK_STOP LANE=3 UNIT=a\nACEK_STOP LANE=4 UNIT=a',
      n: null,
      opts: { key: 'stop_all:' },
    },
  ]);
});

test('stop_all: nothing to stop, declined, changed meanwhile, offline, or a plan the G-code refuses', () => {
  const busy = {
    ...oneUnit({
      0: { mode: 'following' },
      1: { mode: 'following' },
      2: { mode: 'following' },
      3: { mode: 'following' },
    }),
    print_stats: { state: 'printing' },
  };
  const none = harness({ status: busy });
  ACTIONS.stop_all(none.ctx, el());
  assert.deepEqual([none.calls.toast, none.calls.confirm], [['top.stop_all_print'], []]);
  const declined = harness({}, { answer: false });
  ACTIONS.stop_all(declined.ctx, el());
  assert.deepEqual(declined.calls.sendScript, []);
  const changed = harness();
  changed.ctx.confirm = () => {
    changed.app.status = { ...oneUnit(), print_stats: { state: 'printing' } };
    return true;
  };
  ACTIONS.stop_all(changed.ctx, el());
  assert.deepEqual([changed.calls.toast, changed.calls.sendScript], [['top.stop_all_changed'], []]);
  const off = harness({ online: false });
  ACTIONS.stop_all(off.ctx, el());
  assert.deepEqual([off.calls.toast, off.calls.confirm], [[OFFLINE], []]);
  // A plan that cannot be built is refused with its message. Here the status throws when it is read.
  const broken = harness({
    status: {
      ace2k: { lanes: bays() },
      get print_stats() {
        throw new Error('unreadable');
      },
    },
  });
  ACTIONS.stop_all(broken.ctx, el());
  assert.deepEqual([broken.calls.toast, broken.calls.confirm], [['toast.refused {"msg":"unreadable"}'], []]);
});

const laneBtn = (id, n) => el({ action: 'lane', id, lane: String(n) });

test('lane: a plain action on the lane card unit, its key by default', () => {
  const read = { mode: 'idle', insert: true, rest: true, tag: { state: 'read', record: {} } };
  const s = twoUnits({ 2: read });
  const { ctx, calls } = harness({ status: s, selected: { unit: 'b', lane: 0 } });
  act(ctx, laneBtn('load', 3));
  act(ctx, laneBtn('forget_tag', 3));
  assert.deepEqual(
    calls.send.map((c) => [c.id, c.n, c.args, c.opts, c.script]),
    [
      ['load', 3, {}, { key: undefined, unit: 'b' }, 'ACEK_LOAD LANE=3 WAIT=0 UNIT=b'],
      ['forget_tag', 3, {}, { key: undefined, unit: 'b' }, 'ACEK_TAG_FORGET LANE=3 UNIT=b'],
    ],
  );
  act(ctx, laneBtn('clear', 3));
  assert.deepEqual(calls.toast, ['toast.refused {"msg":"reason.unavailable"}']);
});

test('lane: feed and rollback carry the Advanced length, an empty one as NaN for send to refuse', () => {
  const { ctx, calls } = harness({ moveMm: '25' });
  act(ctx, laneBtn('feed', 1));
  act(ctx, laneBtn('rollback', 2));
  ctx.app.moveMm = '';
  act(ctx, laneBtn('feed', 1));
  assert.deepEqual(
    calls.send.map((c) => c.script),
    [
      'ACEK_FEED LANE=1 LENGTH=25 SPEED=20 WAIT=0',
      'ACEK_ROLLBACK LANE=2 LENGTH=25 SPEED=20 WAIT=0',
      'refused: refuse.length_min',
    ],
  );
  assert.ok(Number.isNaN(calls.send[2].args.length));
});

test('lane: eject asks first with the lane and its head; declined sends nothing', () => {
  const { ctx, calls } = harness();
  act(ctx, laneBtn('eject', 2));
  assert.deepEqual(calls.confirm, [t('action.confirm_eject', { n: 2, head: hd(1) })]);
  assert.equal(calls.send[0].script, 'ACEK_EJECT LANE=2 WAIT=0');
  const declined = harness({}, { answer: false });
  act(declined.ctx, laneBtn('eject', 2));
  assert.deepEqual(declined.calls.send, []);
  const noHead = harness({ status: twoUnits() });
  act(noHead.ctx, laneBtn('eject', 1));
  assert.deepEqual(noHead.calls.confirm, [t('action.confirm_eject', { n: 1, head: '' })]);
});

const probeStatus = (over = {}) => ({
  ace2k_u1: {
    hooked: true,
    heads: [
      null,
      [
        { unit: null, lane: 1 },
        { unit: null, lane: 2 },
      ],
      null,
      null,
    ],
    current: [null, null, null, null],
    ...over,
  },
  ace2k: { lanes: bays() },
  'filament_motion_sensor e1_filament': { filament_detected: true },
});
const headBtn = (head) => ({ dataset: { action: 'probe_head', head: String(head) } });

test('probe_head: the head line asks first and sends the head it names', () => {
  const { ctx, calls } = harness({ status: probeStatus() });
  act(ctx, headBtn(2));
  assert.deepEqual(calls.confirm, [t('action.confirm_probe_head', { head: hd(1) })]);
  assert.deepEqual(
    calls.send.map((c) => [c.args, c.script, c.opts.key]),
    [[{ head: 1 }, 'ACEK_HEAD_PROBE HEAD=2', 'probe_head:2']],
  );
  const declined = harness({ status: probeStatus() }, { answer: false });
  act(declined.ctx, headBtn(2));
  assert.deepEqual(declined.calls.send, []);
});

test('probe_head: refused offline, during a print, with the lane known, or while it is in flight', () => {
  const offline = harness({ status: probeStatus(), online: false });
  act(offline.ctx, headBtn(2));
  assert.deepEqual([offline.calls.confirm, offline.calls.send], [[], []]);
  assert.deepEqual(offline.calls.toast, ['toast.refused {"msg":"reason.offline"}']);
  const printing = harness({ status: { ...probeStatus(), print_stats: { state: 'printing' } } });
  act(printing.ctx, headBtn(2));
  assert.deepEqual(printing.calls.toast, ['toast.refused {"msg":"reason.print_on"}']);
  assert.deepEqual(printing.calls.send, []);
  // The adapter learned the lane after the page was drawn, so there is nothing to probe.
  const known = harness({ status: probeStatus({ current: [null, { unit: null, lane: 1 }, null, null] }) });
  act(known.ctx, headBtn(2));
  assert.deepEqual([known.calls.confirm, known.calls.send], [[], []]);
  assert.deepEqual(known.calls.toast, [t('toast.refused', { msg: t('heads.known', { head: hd(1) }) })]);
  // The line went away for another reason. The adapter unhooked, or the head sensor emptied or went away.
  // The lane is not known, so the refusal is the generic one.
  const unhooked = harness({ status: probeStatus({ hooked: false }) });
  const emptied = harness({
    status: { ...probeStatus(), 'filament_motion_sensor e1_filament': { filament_detected: false } },
  });
  const gone = harness({ status: { ...probeStatus(), 'filament_motion_sensor e1_filament': undefined } });
  for (const h of [unhooked, emptied, gone]) {
    act(h.ctx, headBtn(2));
    assert.deepEqual([h.calls.confirm, h.calls.send], [[], []]);
    assert.deepEqual(h.calls.toast, ['toast.refused {"msg":"reason.unavailable"}']);
  }
  const busy = harness({ status: probeStatus(), pending: new Set(['probe_head:2']) });
  act(busy.ctx, headBtn(2));
  assert.deepEqual([busy.calls.confirm, busy.calls.send], [[], []]);
});

test('probe_head: refused while a lane of the head moves, naming the head', () => {
  const st = probeStatus();
  st.ace2k.lanes[1] = { ...st.ace2k.lanes[1], mode: 'feeding' };
  const moving = harness({ status: st });
  act(moving.ctx, headBtn(2));
  assert.deepEqual([moving.calls.confirm, moving.calls.send], [[], []]);
  assert.deepEqual(moving.calls.toast, [t('toast.refused', { msg: t('reason.head_moving', { head: hd(1) }) })]);
  const hot = harness({ status: probeStatus({ probe_cold: [null, false, null, null], probe_max_temp: 60 }) });
  act(hot.ctx, headBtn(2));
  assert.deepEqual(hot.calls.send, []);
  assert.deepEqual(hot.calls.toast, [t('toast.refused', { msg: t('reason.head_hot', { head: hd(1), limit: 60 }) })]);
});

test('lane: feed-forward flips the lane state under its own key, never twice in flight', () => {
  const on = harness({ status: oneUnit({ 0: { ff_on: true } }) });
  act(on.ctx, laneBtn('ff', 1));
  const off = harness({ status: oneUnit({ 0: { ff_on: false } }) });
  act(off.ctx, laneBtn('ff', 1));
  assert.deepEqual(
    [...on.calls.send, ...off.calls.send].map((c) => [c.id, c.opts, c.script]),
    [
      ['ff_off', { key: 'ff:1', unit: null }, 'ACEK_FEED_FORWARD LANE=1 OFF=1'],
      ['ff_on', { key: 'ff:1', unit: null }, 'ACEK_FEED_FORWARD LANE=1 ON=1'],
    ],
  );
  const busy = harness({ status: twoUnits(), pending: new Set(['a|ff:1']) });
  act(busy.ctx, laneBtn('ff', 1));
  assert.deepEqual(busy.calls.send, []);
});

test('lane: the click-time check refuses with its reason', () => {
  const s = { ...oneUnit({ 0: { mode: 'following' } }), print_stats: { state: 'printing' } };
  const { ctx, calls } = harness({ status: s });
  act(ctx, laneBtn('stop', 1));
  act(ctx, laneBtn('feed', 1));
  ctx.app.online = false;
  act(ctx, laneBtn('clear', 1));
  assert.deepEqual(calls.toast, [
    'toast.refused {"msg":"reason.stop_in_use"}',
    'toast.refused {"msg":"reason.unavailable"}',
    OFFLINE,
  ]);
  assert.deepEqual(calls.send, []);
});

test('lane: Read tag forgets the tag, the toast names the lane after the send', async () => {
  const { ctx, calls } = harness({ status: twoUnits(), selected: { unit: 'b', lane: 0 } });
  act(ctx, laneBtn('read_tag', 2));
  assert.deepEqual(
    calls.send.map((c) => [c.id, c.n, c.args, c.opts, c.script]),
    [['read_tag', 2, { forgotten: false }, { unit: 'b' }, 'ACEK_RFID_FORGET LANE=2 UNIT=b']],
  );
  await flush();
  assert.deepEqual(calls.toast, ['toast.tag_rearmed {"n":"b:2"}']);
  const forgotten = { ...oneUnit(), ace2k_u1: { hooked: true, lanes: { '-:3': { tag_forgotten: true } } } };
  const again = harness({ status: forgotten });
  act(again.ctx, laneBtn('read_tag', 3));
  assert.equal(again.calls.send[0].script, 'ACEK_TAG_FORGET LANE=3 READ=1');
  await flush();
  assert.deepEqual(again.calls.toast, ['toast.tag_rearmed {"n":"3"}']);
});

test('lane: Read tag shows no toast when refused, nor in the demo', async () => {
  const refused = harness({}, { sendOk: false });
  act(refused.ctx, laneBtn('read_tag', 1));
  const demo = harness({}, { demo: true });
  act(demo.ctx, laneBtn('read_tag', 1));
  await flush();
  assert.deepEqual([refused.calls.toast, demo.calls.toast], [[], []]);
  assert.equal(demo.calls.send.length, 1);
});

test('window, series, filter: the chart window, its series and the event filter', () => {
  const { app, ctx, calls } = harness({ limit: 600 });
  ACTIONS.window(ctx, el({ h: '12' }));
  assert.equal(app.hours, 12);
  ACTIONS.series(ctx, el({ key: 'rh' }));
  assert.deepEqual(app.hidden, ['rh']);
  ACTIONS.series(ctx, el({ key: 'ch' }));
  ACTIONS.series(ctx, el({ key: 'rh' }));
  assert.deepEqual(app.hidden, ['ch']);
  assert.equal(calls.saveHidden, 3);
  ACTIONS.filter(ctx, el({ f: 'err' }));
  assert.deepEqual([app.filter, app.limit], ['err', 200]);
  assert.equal(calls.render, 5);
});

test('more: the rows the limit hides first, then the next older day, else only a redraw', () => {
  const rows = harness({ limit: 200 }, { eventCount: 201 });
  ACTIONS.more(rows.ctx, el());
  assert.deepEqual([rows.app.limit, rows.calls.loadDays, rows.calls.render], [400, [], 1]);
  const days = harness({ days: ['d3', 'd2', 'd1'], loaded: new Set(['d3', 'd2']), daysLoaded: 2 }, { eventCount: 200 });
  ACTIONS.more(days.ctx, el());
  assert.deepEqual([days.app.limit, days.calls.loadDays, days.calls.render], [200, [3], 1]);
  const demo = harness({ days: ['d3', 'd2', 'd1'], loaded: new Set() }, { demo: true });
  ACTIONS.more(demo.ctx, el());
  assert.deepEqual([demo.calls.loadDays, demo.calls.render], [[], 1]);
});

const plan = {
  file: 'p.gcode',
  mtime: 1,
  order: [0, 1],
  tools: [
    { lane: '-:1', material: 'PLA' },
    { lane: '-:2', material: 'PLA' },
  ],
  lanes: [
    { key: '-:1', head: 0 },
    { key: '-:2', head: 1 },
    { key: '-:3', head: 1 },
  ],
};

test('print_opt: the options toggled against the heads the plan uses, then a redraw', () => {
  const { app, ctx, calls } = harness();
  CHANGES.print_opt(ctx, el({ opt: 'timeLapse' }, { checked: true }));
  assert.equal(app.printOptions.timeLapse, true);
  CHANGES.print_opt(ctx, el({ opt: 'flow' }, { checked: true }));
  assert.deepEqual([app.printOptions.flow, app.printOptions.flowHeads], [true, []]);
  // The heads are those of the bays Print sends: bays 1 and 2, on heads 0 and 1.
  const live = {
    ...plan,
    tools: [
      { lane: '1', material: 'PLA' },
      { lane: '2', material: 'PLA' },
    ],
  };
  const heads = [1, 2, 3, 4].map((lane) => [{ unit: null, lane }]);
  const plaTag = { tag: { state: 'read', record: { material: 'PLA' } } };
  const withPlan = harness({
    status: { ...oneUnit({ 0: plaTag, 1: plaTag }), ace2k_u1: { hooked: true, plan: live, heads } },
    print: { open: true, files: [], file: 'p.gcode', choice: {}, choiceId: null },
  });
  CHANGES.print_opt(withPlan.ctx, el({ opt: 'flow' }, { checked: true }));
  assert.deepEqual(withPlan.app.printOptions.flowHeads, [0, 1]);
  CHANGES.print_opt(withPlan.ctx, el({ opt: 'flow_head', head: '1' }, { checked: false }));
  assert.deepEqual(withPlan.app.printOptions.flowHeads, [0]);
  assert.deepEqual([calls.render, withPlan.calls.render], [2, 2]);
});

test('tab: switches to the tab named, closes the side panel, stores the choice and redraws', () => {
  const { app, ctx, calls } = harness({ ui: { tab: 'units', panel: { kind: 'bay', key: '1' }, sheet: false } });
  ACTIONS.tab(ctx, el({ tab: 'dryer' }));
  assert.deepEqual(app.ui, { tab: 'dryer', panel: null, sheet: false, enter: 'right' });
  assert.deepEqual([calls.saveTab, calls.render], [1, 1]);
  ACTIONS.tab(ctx, el({ tab: 'nonsense' }));
  assert.equal(app.ui.tab, 'units');
});

test('panel opens the side panel on what its data names; panel_close closes it', () => {
  const { app, ctx, calls } = harness({ ui: { tab: 'units', panel: null, sheet: false } });
  ACTIONS.panel(ctx, el({ kind: 'status' }));
  assert.deepEqual(app.ui, { tab: 'units', panel: { kind: 'status' }, sheet: false });
  app.edit = form();
  ACTIONS.panel(ctx, el({ kind: 'bay', key: 'a:2' }));
  assert.deepEqual([app.ui.panel, app.edit], [{ kind: 'bay', key: 'a:2' }, null]);
  app.edit = form();
  ACTIONS.panel_close(ctx, el());
  assert.deepEqual(app.ui, { tab: 'units', panel: null, sheet: false });
  assert.equal(app.edit, null);
  assert.equal(calls.render, 3);
});

test('theme: each click moves system, light, dark and round, saves the choice and redraws', () => {
  const { app, ctx, calls } = harness();
  const seen = [];
  for (let i = 0; i < 4; i++) {
    ACTIONS.theme(ctx, el());
    seen.push(app.theme);
  }
  assert.deepEqual(seen, ['light', 'dark', 'system', 'light']);
  assert.deepEqual([calls.saveTheme, calls.render], [4, 4]);
});

// Load into head N. sendScript answers each line with the ok of answers[line], else true.
// A string answer is the error the printer or the connection gave. Like sendScript, quiet decides first,
// and else the refusal shows (recorded in shown).
// held records whether the page-wide pending key was held while each line went out.
function loadHarness(appOver = {}, { answer = true, answers = {} } = {}) {
  const h = harness({ status: fullStatus(), events: [], ...appOver }, { answer });
  h.held = [];
  h.shown = [];
  h.ctx.now = () => 1000;
  h.ctx.sendScript = (script, n, opts) => {
    h.calls.sendScript.push({ script, n, opts });
    h.held.push(h.app.pending.has('load_head'));
    const a = answers[script] ?? true;
    if (typeof a === 'string') {
      if (!opts.quiet?.(a)) h.shown.push(a);
      return Promise.resolve(false);
    }
    return a instanceof Promise ? a : Promise.resolve(a);
  };
  return h;
}
const scripts = (h) => h.calls.sendScript.map((c) => c.script);
const START = 'AUTO_FEEDING_BATCH ACTION=START';
const STOP = 'AUTO_FEEDING_BATCH ACTION=STOP';
const DOING0 = 'AUTO_FEEDING_BATCH ACTION=DOING EXTRUDER=0 LOAD=1';
const SPOOL_1 = 'ACEK_HEAD_SPOOL LANE=1';
const PREP_1 = 'ACEK_HEAD_PREPARE LANE=1';

test('load_head: a head of its own heats and loads as the U1 screen does, after a confirmation', async () => {
  const h = loadHarness();
  act(h.ctx, laneBtn('load_head', 4));
  await flush();
  assert.deepEqual(h.calls.confirm, [t('action.confirm_load_head', { lane: '4', head: hd(3) })]);
  assert.deepEqual(scripts(h), [
    'ACEK_HEAD_PREPARE LANE=4',
    'ACEK_HEAD_SPOOL LANE=4',
    START,
    'AUTO_FEEDING_BATCH ACTION=DOING EXTRUDER=3 LOAD=1',
    STOP,
  ]);
  assert.deepEqual(
    h.calls.sendScript.map((c) => c.n),
    [4, 4, 4, 4, 4],
  );
  assert.deepEqual(
    h.calls.sendScript.map((c) => c.opts.key),
    ['load_head:step', 'load_head:spool', 'load_head:step', 'load_head:step', 'load_head:stop'],
  );
  // One pending key for the whole page holds the sequence, until STOP returns.
  assert.deepEqual(h.held, [true, true, true, true, true]);
  assert.equal(h.app.pending.size, 0);
  assert.deepEqual(h.calls.send, []);
});

// Unit a with head 1 shared by a:1 and a:2, the heads' sensors read empty, no lane named in head 1.
function sharedStatus() {
  const s = twoHeadedUnits();
  s['ace2k a'].lanes = bays();
  s.ace2k_u1.current[0] = null;
  for (const n of [0, 1, 2, 3]) s[`filament_motion_sensor e${n}_filament`] = { filament_detected: false };
  s['gcode_macro AUTO_FEEDING_BATCH'] = { doing: false };
  return s;
}

test('load_head: a shared head first makes the lane its own, then loads; a refusal there stops it', async () => {
  const sel = { unit: 'a', lane: 1 };
  const h = loadHarness({ status: sharedStatus(), selected: sel });
  act(h.ctx, laneBtn('load_head', 2));
  await flush();
  assert.deepEqual(h.calls.confirm, [t('action.confirm_load_head_shared', { lane: 'a:2', head: hd(0) })]);
  assert.deepEqual(scripts(h), ['ACEK_LANE_ACTIVE LANE=2 UNIT=a', PREP_A2, SPOOL_A2, START, DOING0, STOP]);
  assert.deepEqual(h.held, [true, true, true, true, true, true]);
  assert.ok(h.calls.sendScript.every((c) => c.opts.unit === 'a'));
  // ACEK_LANE_ACTIVE refused: nothing heats, nothing else is sent. sendScript shows the refusal.
  const refused = loadHarness(
    { status: sharedStatus(), selected: sel },
    { answers: { 'ACEK_LANE_ACTIVE LANE=2 UNIT=a': 'refused' } },
  );
  act(refused.ctx, laneBtn('load_head', 2));
  await flush();
  assert.deepEqual([scripts(refused), refused.shown], [['ACEK_LANE_ACTIVE LANE=2 UNIT=a'], ['refused']]);
  assert.equal(refused.app.pending.size, 0);
});

const SPOOL_A2 = 'ACEK_HEAD_SPOOL LANE=2 UNIT=a';
const PREP_A2 = 'ACEK_HEAD_PREPARE LANE=2 UNIT=a';
const SPOOL_REFUSED = 'ace2k_u1: head 1: spool setting refused: official filament';

test("load_head: the head takes the lane's spool before the load heats; a refused setting loads nothing", async () => {
  const sel = { unit: 'a', lane: 1 };
  const h = loadHarness({ status: sharedStatus(), selected: sel });
  act(h.ctx, laneBtn('load_head', 2));
  await flush();
  // The U1 reads the load temperature from the head's setting when the load starts, so the spool goes first.
  assert.deepEqual(scripts(h), ['ACEK_LANE_ACTIVE LANE=2 UNIT=a', PREP_A2, SPOOL_A2, START, DOING0, STOP]);
  assert.deepEqual(h.calls.sendScript[2].opts, { key: 'load_head:spool', unit: 'a' });
  assert.deepEqual(h.held, [true, true, true, true, true, true]);
  assert.equal(h.app.pending.size, 0);
  // The load failed: STOP, and the head keeps the spool it was given.
  const failed = loadHarness({ status: sharedStatus(), selected: sel }, { answers: { [DOING0]: 'the load failed' } });
  act(failed.ctx, laneBtn('load_head', 2));
  await flush();
  assert.deepEqual(scripts(failed), ['ACEK_LANE_ACTIVE LANE=2 UNIT=a', PREP_A2, SPOOL_A2, START, DOING0, STOP]);
  // A lane with no spool set is no refusal: the adapter says so in a console line, which the events show
  // as a warning (history.test.js), and the page shows nothing as refused.
  assert.deepEqual([h.shown, h.calls.toast], [[], []]);
  // A setting the U1 refuses shows as any refused line: nothing heats, and the key is released.
  const refused = loadHarness({ status: sharedStatus(), selected: sel }, { answers: { [SPOOL_A2]: SPOOL_REFUSED } });
  act(refused.ctx, laneBtn('load_head', 2));
  await flush();
  assert.deepEqual(
    [scripts(refused), refused.shown],
    [['ACEK_LANE_ACTIVE LANE=2 UNIT=a', PREP_A2, SPOOL_A2], [SPOOL_REFUSED]],
  );
  assert.equal(refused.app.pending.size, 0);
});

test('load_head: a lane whose material stays unset is refused at its prepare, and nothing heats', async () => {
  const sel = { unit: 'a', lane: 1 };
  const refused = 'ace2k_u1: lane a:2: no material set — set it with Edit spool: its load refused';
  const h = loadHarness({ status: sharedStatus(), selected: sel }, { answers: { [PREP_A2]: refused } });
  act(h.ctx, laneBtn('load_head', 2));
  await flush();
  assert.deepEqual([scripts(h), h.shown], [['ACEK_LANE_ACTIVE LANE=2 UNIT=a', PREP_A2], [refused]]);
  assert.equal(h.app.pending.size, 0);
});

test('load_head: STOP follows a START that went through, also when the load fails; a refused START ends it', async () => {
  const failed = loadHarness({}, { answers: { [DOING0]: 'the load failed' } });
  act(failed.ctx, laneBtn('load_head', 1));
  await flush();
  assert.deepEqual(scripts(failed), [PREP_1, SPOOL_1, START, DOING0, STOP]);
  // START refused: no batch was opened, so nothing more is sent, and the refusal shows once.
  const noStart = loadHarness({}, { answers: { [START]: 'Klipper is not ready' } });
  act(noStart.ctx, laneBtn('load_head', 1));
  await flush();
  assert.deepEqual([scripts(noStart), noStart.shown], [[PREP_1, SPOOL_1, START], ['Klipper is not ready']]);
  assert.equal(noStart.app.pending.size, 0);
});

test('load_head: a STOP lost to a closed connection says so in an event, and the key is released', async () => {
  const h = loadHarness({}, { answers: { [DOING0]: CONNECTION_CLOSED, [STOP]: CONNECTION_CLOSED } });
  const renders = h.calls.render;
  act(h.ctx, laneBtn('load_head', 1));
  await flush();
  assert.deepEqual(scripts(h), [PREP_1, SPOOL_1, START, DOING0, STOP]);
  // DOING's closed connection shows as any refusal. STOP's says that the heaters turn off by themselves.
  assert.deepEqual(h.shown, [CONNECTION_CLOSED]);
  assert.deepEqual(h.app.events, [{ t: 1000, k: 'e', lvl: 'warn', src: 'unit', msg: 'events.stop_lost' }]);
  assert.equal(h.app.pending.size, 0);
  assert.equal(h.calls.render - renders, 2); // Once when the key is taken, once when it is released.
  // Another STOP refusal shows as any refused G-code, with no event of its own.
  const other = loadHarness({}, { answers: { [STOP]: 'refused' } });
  act(other.ctx, laneBtn('load_head', 1));
  await flush();
  assert.deepEqual([other.shown, other.app.events], [['refused'], []]);
});

test('load_head: one load at a time on the whole page; a second lane cannot start during the first', async () => {
  let finish;
  const h = loadHarness({}, { answers: { [DOING0]: new Promise((r) => (finish = r)) } });
  act(h.ctx, laneBtn('load_head', 1));
  await flush();
  act(h.ctx, laneBtn('load_head', 2));
  await flush();
  assert.equal(h.calls.confirm.length, 1);
  assert.deepEqual(scripts(h), [PREP_1, SPOOL_1, START, DOING0]);
  finish(true);
  await flush();
  assert.deepEqual(scripts(h), [PREP_1, SPOOL_1, START, DOING0, STOP]);
  assert.equal(h.app.pending.size, 0);
});

// A harness whose pause delivers a status change, as the socket's queued messages arrive after the dialog.
// The dialog itself changes nothing: window.confirm blocks, and no message is dispatched while it is open.
function changedDuringPause(change) {
  const h = loadHarness();
  h.pauses = [];
  h.ctx.sleep = (ms) => {
    h.pauses.push(ms);
    change(h.app);
    return Promise.resolve();
  };
  return h;
}

test('load_head: after the dialog the page pauses, then checks the offer again; what changed is refused', async () => {
  // A print started while the dialog was open.
  const printing = changedDuringPause((app) => {
    app.status = fullStatus({ print: 'printing' });
  });
  act(printing.ctx, laneBtn('load_head', 1));
  await flush();
  assert.deepEqual(printing.pauses, [300]);
  assert.deepEqual([scripts(printing), printing.calls.toast], [[], ['toast.refused {"msg":"reason.print_on"}']]);
  assert.equal(printing.app.pending.size, 0);
  // The screen started a head load meanwhile.
  const busy = changedDuringPause((app) => {
    app.status = { ...fullStatus(), 'gcode_macro AUTO_FEEDING_BATCH': { doing: true } };
  });
  act(busy.ctx, laneBtn('load_head', 1));
  await flush();
  assert.deepEqual([scripts(busy), busy.calls.toast], [[], ['toast.refused {"msg":"reason.batch_open"}']]);
  // The lane now shares its head: the lines would differ from what the dialog said.
  const shared = changedDuringPause((app) => {
    app.status.ace2k_u1.heads = [
      [
        { unit: null, lane: 1 },
        { unit: null, lane: 2 },
      ],
      [],
      [],
      [],
    ];
  });
  act(shared.ctx, laneBtn('load_head', 1));
  await flush();
  assert.deepEqual([scripts(shared), shared.calls.toast], [[], ['toast.refused {"msg":"reason.unavailable"}']]);
  // Another click's load took the page-wide key during the pause: this one sends nothing.
  const other = changedDuringPause((app) => {
    app.pending.add('load_head');
  });
  act(other.ctx, laneBtn('load_head', 1));
  await flush();
  assert.deepEqual([scripts(other), other.calls.toast], [[], []]);
});

test('load_head: declined, in flight, refused at the click, or a head the page cannot name: nothing sent', async () => {
  const declined = loadHarness({}, { answer: false });
  act(declined.ctx, laneBtn('load_head', 1));
  const busy = loadHarness({ pending: new Set(['load_head']) });
  act(busy.ctx, laneBtn('load_head', 1));
  // The head's sensor sees filament: the refusal names the head.
  const loaded = loadHarness({ status: fullStatus({ heads: [true, false, false, false] }) });
  act(loaded.ctx, laneBtn('load_head', 1));
  const printing = loadHarness({ status: fullStatus({ print: 'printing' }) });
  act(printing.ctx, laneBtn('load_head', 2));
  // A map with a fifth head: the head is refused before the dialog.
  const fifth = fullStatus();
  fifth.ace2k_u1.heads = [[], [], [], [], [{ unit: null, lane: 1 }]];
  fifth['filament_motion_sensor e4_filament'] = { filament_detected: false };
  const odd = loadHarness({ status: fifth });
  act(odd.ctx, laneBtn('load_head', 1));
  await flush();
  for (const h of [declined, busy, loaded, printing, odd]) assert.deepEqual(scripts(h), []);
  assert.deepEqual([busy.calls.confirm, loaded.calls.confirm, odd.calls.confirm], [[], [], []]);
  assert.deepEqual(loaded.calls.toast, [t('toast.refused', { msg: t('reason.head_loaded', { head: hd(0) }) })]);
  assert.deepEqual(printing.calls.toast, ['toast.refused {"msg":"reason.print_on"}']);
  assert.deepEqual(odd.calls.toast, ['toast.refused {"msg":"refuse.head {}"}']);
});

// Two units, b's printer.cfg allowing the humidity mode.
const humidityOnB = () => {
  const s = twoUnits();
  s['ace2k b'].dryer = { state: 'idle', humidity_allowed: true };
  return s;
};

test('dry_mode: print and manual send at once; humidity opens the warning first', () => {
  const { ctx, calls } = harness({ status: humidityOnB(), dryerUnit: 'b' });
  ACTIONS.dry_mode(ctx, el({ mode: 'print' }));
  assert.deepEqual(
    calls.send.map((c) => c.script),
    ['ACEK_DRY_MODE MODE=print UNIT=b'],
  );
  ACTIONS.dry_mode(ctx, el({ mode: 'humidity' }));
  assert.equal(calls.send.length, 1);
  assert.ok(ctx.app.modeWarn);
  ACTIONS.dry_mode_cancel(ctx, el());
  assert.equal(ctx.app.modeWarn, false);
  assert.equal(calls.send.length, 1);
  ACTIONS.dry_mode(ctx, el({ mode: 'humidity' }));
  ACTIONS.dry_mode_confirm(ctx, el());
  assert.deepEqual(calls.send.map((c) => c.script).at(-1), 'ACEK_DRY_MODE MODE=humidity UNIT=b');
  assert.equal(ctx.app.modeWarn, false);
});

test('dry_mode humidity from a card opens the warning on that card', () => {
  const { ctx } = harness({ status: humidityOnB(), dryerUnit: 'a' });
  ACTIONS.dry_mode(ctx, el({ mode: 'humidity', unit: 'b' }));
  assert.deepEqual([ctx.app.modeWarn, ctx.app.dryerUnit], [true, 'b']);
});

test('dry_mode on the mode already on sends with no question; humidity after failures opens the warning', () => {
  const withMode = (mode, flags = {}) => {
    const s = humidityOnB();
    s['ace2k b'].dryer = { ...s['ace2k b'].dryer, state: 'heating', own: true, mode, ...flags };
    return s;
  };
  for (const mode of ['manual', 'print', 'humidity']) {
    const { ctx, calls } = harness({ status: withMode(mode), dryerUnit: 'b' });
    ACTIONS.dry_mode(ctx, el({ mode }));
    assert.deepEqual(
      [calls.send.map((c) => c.script), calls.confirm, ctx.app.modeWarn ?? false],
      [[`ACEK_DRY_MODE MODE=${mode} UNIT=b`], [], false],
      mode,
    );
  }
  const { ctx, calls } = harness({ status: withMode('manual'), dryerUnit: 'b' });
  ACTIONS.dry_mode(ctx, el({ mode: 'humidity' }));
  assert.deepEqual([calls.send, ctx.app.modeWarn], [[], true]);
  // humidity with failed cycles, running or stopped: choosing it again re-enables it, through the warning
  for (const flags of [{ failures: 2 }, { stopped: true, failures: 3 }]) {
    const again = harness({ status: withMode('humidity', flags), dryerUnit: 'b' });
    ACTIONS.dry_mode(again.ctx, el({ mode: 'humidity' }));
    assert.deepEqual([again.calls.send, again.calls.confirm, again.ctx.app.modeWarn], [[], [], true]);
    ACTIONS.dry_mode_confirm(again.ctx, el());
    assert.deepEqual(
      again.calls.send.map((c) => c.script),
      ['ACEK_DRY_MODE MODE=humidity UNIT=b'],
    );
  }
});

test('dry_mode_confirm sends nothing without the open warning or the humidity mode allowed', () => {
  const closed = harness({ status: humidityOnB(), dryerUnit: 'b' });
  ACTIONS.dry_mode_confirm(closed.ctx, el());
  assert.deepEqual(closed.calls.send, []);
  const barred = harness({ status: twoUnits(), dryerUnit: 'b', modeWarn: true });
  ACTIONS.dry_mode_confirm(barred.ctx, el());
  assert.deepEqual(barred.calls.send, []);
  assert.equal(barred.ctx.app.modeWarn, false);
});

test('dry_stop on an automatic cycle asks first; a manual cycle does not', () => {
  const auto = (own) => {
    const status = oneUnit();
    status.ace2k.dryer = { ...status.ace2k.dryer, state: 'heating', own };
    return status;
  };
  const yes = harness({ status: auto(true) });
  ACTIONS.dry_stop(yes.ctx, el());
  assert.deepEqual(yes.calls.confirm, ['dryer.confirm_stop_auto']);
  assert.equal(yes.calls.send.length, 1);
  const no = harness({ status: auto(true) }, { answer: false });
  ACTIONS.dry_stop(no.ctx, el());
  assert.equal(no.calls.send.length, 0);
  const manual = harness({ status: auto(false) });
  ACTIONS.dry_stop(manual.ctx, el());
  assert.deepEqual([manual.calls.confirm, manual.calls.send.length], [[], 1]);
});

test('dry_mode to manual or print over a running automatic cycle asks first', () => {
  const auto = (mode, flags) => {
    const status = oneUnit();
    status.ace2k.dryer = { ...status.ace2k.dryer, state: 'heating', mode, ...flags };
    return status;
  };
  const own = harness({ status: auto('humidity', { own: true }) });
  ACTIONS.dry_mode(own.ctx, el({ mode: 'manual' }));
  assert.deepEqual(own.calls.confirm, ['dryer.confirm_mode_stops_auto']);
  assert.deepEqual(
    own.calls.send.map((c) => c.script),
    ['ACEK_DRY_MODE MODE=manual'],
  );
  const no = harness({ status: auto('print', { bound: true }) }, { answer: false });
  ACTIONS.dry_mode(no.ctx, el({ mode: 'manual' }));
  assert.deepEqual([no.calls.confirm.length, no.calls.send.length], [1, 0]);
  const bound = harness({ status: auto('print', { bound: true }) });
  ACTIONS.dry_mode(bound.ctx, el({ mode: 'manual' }));
  assert.equal(bound.calls.send.length, 1);
  // the same mode again asks nothing (still sent), and a manual cycle is never stopped by a mode change
  const same = harness({ status: auto('print', { bound: true }) });
  ACTIONS.dry_mode(same.ctx, el({ mode: 'print' }));
  assert.deepEqual([same.calls.confirm, same.calls.send.length], [[], 1]);
  const manual = harness({ status: auto('manual', {}) });
  ACTIONS.dry_mode(manual.ctx, el({ mode: 'print' }));
  assert.deepEqual([manual.calls.confirm, manual.calls.send.length], [[], 1]);
});

test('laneRefusal: the head that holds filament by name, a head it cannot name without one, other reasons plain', () => {
  const { ctx } = harness();
  assert.equal(laneRefusal(ctx, 'head_loaded', 2), t('reason.head_loaded', { head: hd(2) }));
  assert.equal(laneRefusal(ctx, 'head_loaded', null), t('reason.head_loaded_any'));
  assert.equal(laneRefusal(ctx, 'offline', null), t('reason.offline'));
  // Every head reason names its head; head_hot also its limit.
  assert.equal(laneRefusal(ctx, 'head_unknown', 0), t('reason.head_unknown', { head: hd(0) }));
  assert.equal(laneRefusal(ctx, 'head_unknown', null), t('reason.head_unknown'));
});

const LOG_LINES = ['3 cycles started, 2 completed, 0 faults; 0.4 h of heating, 36 Wh at 360 W'];
// A send whose answer the test gives when it wants, and a wait whose end the test gives too.
const held = () => {
  /** @type {(v: boolean) => void} */
  let release = () => {};
  const promise = new Promise((resolve) => {
    release = resolve;
  });
  return { promise, release };
};

test('dry_log: busy from the click; a fake reply during the send or the wait stays for the card', async () => {
  const early = harness({ status: twoUnits(), dryerUnit: 'b' });
  const sent = early.ctx.send;
  const wait = held();
  early.ctx.sleep = (ms) => {
    assert.equal(ms, LOG_WAIT_MS);
    return wait.promise;
  };
  early.ctx.send = (...a) => {
    assert.deepEqual(early.app.dryerLog, { unit: 'b', lines: null, token: early.app.dryerLog.token }); // busy
    early.app.dryerLog = { ...early.app.dryerLog, lines: LOG_LINES }; // as ingestGcode does
    return sent(...a);
  };
  const run = ACTIONS.dry_log(early.ctx, el());
  wait.release(true);
  await run;
  assert.deepEqual([early.app.dryerLog.lines, early.calls.toast], [LOG_LINES, []]);
  assert.equal(early.calls.send[0].script, 'ACEK_DRYER_LOG UNIT=b');
  const late = harness({ status: twoUnits() });
  late.ctx.sleep = () => {
    late.app.dryerLog = { ...late.app.dryerLog, lines: LOG_LINES };
    return Promise.resolve();
  };
  await ACTIONS.dry_log(late.ctx, el({ unit: 'a' }));
  assert.deepEqual([late.app.dryerLog.unit, late.app.dryerLog.lines, late.calls.toast], ['a', LOG_LINES, []]);
});

test('dry_log: the wait runs from the click, so a send that hangs still ends in the toast; a later reply is dropped', async () => {
  const { app, ctx, calls } = harness({ status: twoUnits(), events: [] });
  const answer = held();
  ctx.send = () => answer.promise;
  const run = ACTIONS.dry_log(ctx, el({ unit: 'a' }));
  await flush();
  assert.deepEqual([app.dryerLog, calls.toast], [null, ['dryer.log_none']]);
  ingestGcode(app, '// ace2k a: dryer: 3 cycles started, 2 completed, 0 faults', 1);
  assert.equal(app.dryerLog, null);
  answer.release(true);
  await run;
  assert.deepEqual([app.dryerLog, calls.toast], [null, ['dryer.log_none']]);
});

test("dry_log: an old request's timeout never ends a newer one", async () => {
  const { app, ctx, calls } = harness({ status: twoUnits() });
  const first = held();
  const waits = [first];
  ctx.sleep = () => (waits.length ? waits.shift().promise : new Promise(() => {}));
  ACTIONS.dry_log(ctx, el({ unit: 'a' }));
  await flush();
  ACTIONS.dry_log_close(ctx, el());
  ACTIONS.dry_log(ctx, el({ unit: 'b' }));
  first.release(true);
  await flush();
  assert.deepEqual([app.dryerLog?.unit, app.dryerLog?.lines, calls.toast], ['b', null, []]);
});

test('dry_log: a refusal and the demo leave no log and no timeout toast; one read at a time on the page', async () => {
  const refused = harness({ status: twoUnits() }, { sendOk: false });
  refused.ctx.sleep = () => new Promise(() => {});
  await Promise.race([ACTIONS.dry_log(refused.ctx, el({ unit: 'a' })), flush()]);
  await flush();
  assert.deepEqual([refused.app.dryerLog, refused.calls.toast], [null, []]);
  const demo = harness({}, { demo: true });
  await ACTIONS.dry_log(demo.ctx, el());
  assert.deepEqual([demo.app.dryerLog, demo.calls.toast], [null, []]);
  const { ctx, calls } = harness({ status: twoUnits(), dryerLog: { unit: 'a', lines: null, token: 99 } });
  await ACTIONS.dry_log(ctx, el({ unit: 'b' }));
  assert.deepEqual(calls.send, []);
  ctx.app.dryerLog = { unit: 'a', lines: ['x'], token: 99 };
  ACTIONS.dry_log_close(ctx, el());
  assert.equal(ctx.app.dryerLog, null);
  await ACTIONS.dry_log(ctx, el({ unit: 'b' }));
  assert.equal(calls.send[0].script, 'ACEK_DRYER_LOG UNIT=b');
});

test('a click on the Print tab from another tab plans the chosen file once; on it, or to another tab, nothing', () => {
  const print = { open: true, files: [], file: 'a.gcode', choice: {}, choiceId: null };
  const { app, ctx, calls } = harness({ ui: { tab: 'units', panel: null, sheet: false }, print });
  ACTIONS.tab(ctx, el({ tab: 'dryer' }));
  assert.deepEqual(calls.sendScript, []);
  ACTIONS.tab(ctx, el({ tab: 'print' }));
  assert.deepEqual(
    calls.sendScript.map((c) => c.script ?? c[0]),
    ['ACEK_PLAN FILE="a.gcode"'],
  );
  ACTIONS.tab(ctx, el({ tab: 'print' }));
  assert.equal(calls.sendScript.length, 1);
  assert.equal(app.print.replan, undefined);
});

test('plan_again plans the chosen file again with its reading, keeping the picks, merges and pins', () => {
  const { app, ctx, calls } = harness();
  const picks = { 0: 'a' };
  const merges = { 1: 0 };
  const pinned = { 0: { key: 'a' } };
  app.print = { ...app.print, file: 'mine.gcode', choice: picks, merges, pinned };
  app.status = { ...app.status, ace2k_u1: { plan: { file: 'theirs.gcode' } } };
  ACTIONS.plan_again(ctx, el());
  assert.deepEqual(
    calls.sendScript.map((c) => c.script),
    ['ACEK_PLAN FILE="mine.gcode"'],
  );
  assert.deepEqual(calls.planFile, []);
  assert.deepEqual(app.print.reading, { file: 'mine.gcode', at: 50000 });
  assert.equal(app.print.choice, picks);
  assert.equal(app.print.merges, merges);
  assert.equal(app.print.pinned, pinned);
});

test('a click on the Print tab while the notice shows plans nothing: the notice takes the plan back', () => {
  const print = { open: true, files: [], file: 'mine.gcode', choice: {}, choiceId: null };
  const { app, ctx, calls } = harness({ ui: { tab: 'units', panel: null, sheet: false }, print });
  app.status = { ...app.status, ace2k_u1: { plan: { file: 'theirs.gcode' } } };
  ACTIONS.tab(ctx, el({ tab: 'print' }));
  assert.deepEqual(calls.sendScript, []);
  assert.equal(app.print.reading, undefined);
});
