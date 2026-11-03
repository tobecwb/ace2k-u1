// The actions of the page. A component calls ctx.run(id, data), and runAction (at the end) finds the id in
// ACTIONS (a click) or CHANGES (a control whose change matters). Each function takes ctx and a stand-in element
// whose dataset holds data, with the control's value and checked state. ctx holds the page state and the effects.
// The actions get their effects from ctx, so they run under node.
// send(id, n, args, {key, unit}) builds the G-code and sends it.
// sendScript(script, n, {key, quiet, unit, once}) sends a script as it is.
// Both resolve to true when the command was handed over.
import {
  allowedNow,
  CONFIRM,
  printingNow,
  cancelScript,
  DRYER_MODES,
  editPrefill,
  editSave,
  FILAMENT_TYPES,
  editScript,
  headOf,
  laneOf,
  laneText,
  loadHeadPlan,
  resumeScript,
  pauseScript,
  resuming,
  sharedHeadsView,
  sliceFor,
  stopAllConfirmed,
  stopAllPlan,
  tagForgotten,
} from './model.js';
import {
  known,
  LOAD_HEAD_KEY,
  mergesOf,
  nextTheme,
  olderDays,
  pkey,
  planNow,
  ready,
  selection,
  shownPlan,
  tabOf,
  unitNames,
} from './state.js';
import { CONNECTION_CLOSED } from './conn.js';
import { travel } from './motion.js';
import { FAN_SECONDS, parseHM, formOf, autoCycle, dryerView } from './dryer-form.js';
import { fmtHM } from './format.js';
import { ffKey, laneAt, unitId } from './lane-state.js';
import { built } from './send.js';
import { appendEvent } from './history.js';
import {
  normalColour,
  bayOwner,
  bayRoots,
  choiceFor,
  chosen,
  mergeRoot,
  laneByKey,
  liveLanes,
  lookOf,
  nextOptions,
  pinOf,
  planId,
  NEW,
  printGate,
  usedHeads,
  without,
} from './print-plan.js';
import { stepNext, stepPrev } from './steps.js';
import { ejectAll, planAgain, replanChosen, toGrid } from './printflow.js';
import { headName, headVars } from './words.js';

/** @typedef {import('./types.js').ActionCtx} ActionCtx */
/** @typedef {import('./types.js').ChangeEl} ChangeEl */
/** @typedef {import('./types.js').AppState} AppState */
/** @typedef {import('./model.js').GcodeArgs} GcodeArgs */
/** @typedef {(ctx: ActionCtx, el: HTMLElement) => unknown} ActionFn  The action of a click. */
/** @typedef {(ctx: ActionCtx, el: ChangeEl) => unknown} ChangeFn  The action of a change event. */

// Returns the entry of the table for key. An inherited key such as 'toString' never matches.
/** @template T @param {Record<string, T>} table @param {string | null | undefined} key @returns {T | null} */
const entry = (table, key) => (key != null && Object.hasOwn(table, key) ? table[key] : null);

// Returns true when the adapter is not reachable or Klipper is not ready. It shows the refusal toast first.
/** @param {ActionCtx} ctx */
function refusedOffline(ctx) {
  if (ready(ctx.app)) return false;
  ctx.toast(ctx.t('toast.refused', { msg: ctx.t('reason.offline') }));
  return true;
}

/** @param {ActionCtx} ctx @param {(app: AppState) => void} change */
const redraw = (ctx, change) => {
  change(ctx.app);
  ctx.render();
};

// The dryer's commands go to the unit of the card that was clicked (data-unit, '' for the unnamed unit),
// else to the unit the dryer last named.
/** @param {ActionCtx} ctx @param {HTMLElement} [el] */
const dryerUnit = (ctx, el) =>
  known(ctx.app, el && el.dataset.unit !== undefined ? el.dataset.unit || null : ctx.app.dryerUnit);
// The dryer of that unit, as its card shows it.
/** @param {ActionCtx} ctx @param {HTMLElement} [el] */
const dryerNow = (ctx, el) => dryerView(sliceFor(ctx.app.status, dryerUnit(ctx, el)));
// Builds a dryer action for the ACTIONS table. args reads the clicked element when the command needs it.
/** @param {string} id @param {(ctx: ActionCtx, el: HTMLElement) => GcodeArgs} args @returns {ActionFn} */
const dryerSend = (id, args) => (ctx, el) => ctx.send(id, null, args(ctx, el), { unit: dryerUnit(ctx, el) });

// How long the page waits for the reply of ACEK_DRYER_LOG, from the click.
export const LOG_WAIT_MS = 4000;
let logToken = 0;

// Asks the unit for its dryer log. The reply comes as console lines, which ingestGcode puts in app.dryerLog,
// and the card shows it. One read at a time on the whole page. The wait starts with the click: when no reply
// comes in LOG_WAIT_MS, the read ends with a toast, and a reply that comes later is dropped.
/** @param {ActionCtx} ctx @param {HTMLElement} el */
function dryLog(ctx, el) {
  const { app } = ctx;
  if (app.dryerLog?.lines === null) return;
  const unit = dryerUnit(ctx, el);
  logToken += 1;
  const token = logToken;
  app.dryerLog = { unit, lines: null, token };
  ctx.render();
  // Ends this read while it still waits. A reply that came, or a newer read, is left as it is.
  /** @param {string | null} msg */
  const end = (msg) => {
    const log = app.dryerLog;
    if (log?.token !== token || log.lines !== null) return;
    app.dryerLog = null;
    if (msg) ctx.toast(msg);
    ctx.render();
  };
  const waited = ctx.demo ? null : ctx.sleep(LOG_WAIT_MS).then(() => end(ctx.t('dryer.log_none')));
  const sent = ctx.send('dry_log', null, {}, { unit }).then((ok) => {
    if (!ok || ctx.demo) end(null); // a refusal has shown its toast; the demo answers nothing
  });
  return Promise.all([sent, waited]);
}

// Saves the Edit spool form. It always saves the lane's spool record.
// It also saves the head's setting when the lane has a head of its own, or is the head's current lane.
// The form closes once the request is handed over.
/** @param {ActionCtx} ctx @param {HTMLElement} el */
function editSaveAction(ctx, el) {
  const { app } = ctx;
  const n = Number(el.dataset.lane);
  const e = app.edit;
  const key = pkey(app, e?.unit, `edit_spool:${n}`);
  if (!e || e.lane !== n || app.pending.has(key)) return;
  const save = editSave(sliceFor(app.status, e.unit), e, ready(app));
  if (!save.ok) {
    ctx.toast(
      save.reason === 'head_changed'
        ? ctx.t('toast.head_changed')
        : ctx.t('toast.refused', { msg: ctx.t(`reason.${save.reason}`) }),
    );
    return;
  }
  const script = built(ctx, () => editScript(save, n, e.unit));
  if (script === null) return;
  ctx.sendScript(script, n, { key, unit: e.unit }).then((ok) => {
    if (!ok) return;
    // Opened from the colour picker, the saved spool becomes the pick of that file colour.
    if (e.use !== undefined)
      pickTool(app, e.use, laneText(sliceFor(app.status, e.unit), n - 1), { type: e.type, color: e.color });
    if (app.edit === e) app.edit = null;
    ctx.render();
  });
}

// A keystroke in a field of the dryer card: the form keeps the text as typed.
/** @param {ActionCtx} ctx @param {ChangeEl} el */
function dryField(ctx, el) {
  const unit = dryerUnit(ctx, /** @type {HTMLElement} */ (/** @type {unknown} */ (el)));
  redraw(ctx, (app) => {
    app.forms[unit ?? ''] = {
      ...formOf(app, unit),
      preset: null,
      [el.dataset.field === 'hm' ? 'hm' : 'temp']: el.value,
    };
  });
}

// Sets tool n's pick (a lane key or NEW), its merge (the tool it joins) and its pin (the spool picked), each null
// for none, for the plan shown. The change is stamped with that plan, so a re-plan of the same file carries it
// (carriedChoice). A choice made for an earlier plan is dropped.
/**
 * @param {AppState} app @param {number} n
 * @param {{ key?: string | null, into?: number | null, pin?: import('./types.js').Pin | null }} set
 */
function setTool(app, n, { key = null, into = null, pin = null }) {
  const plan = shownPlan(app);
  const base = plan ? choiceFor(app.print, plan) : app.print.choice;
  const merges = without(mergesOf(app.print), n);
  const pinned = without(app.print.pinned ?? {}, n);
  if (into !== null) merges[n] = into;
  if (pin !== null) pinned[n] = pin;
  app.print = {
    ...app.print,
    choice: key !== null ? { ...base, [n]: key } : without(base, n),
    merges,
    pinned,
    // The user's own choice now: no longer one the page settled itself.
    auto: (app.print.auto ?? []).filter((x) => x !== n),
    // The tools at the time of the choice. A new plan is compared against them.
    ...(plan ? { choiceId: planId(plan), choiceTools: plan.tools } : {}),
    // Another pick makes other steps: the guide starts over. A spool already in hand stays held.
    step: 0,
    trail: [],
    shown: null,
  };
}

// Makes lane key (or NEW, a new spool) the pick of tool n, as the colour picker does, pinned on the spool in that
// bay, so the pick follows the spool when it moves. saved is the spool "Save and use" just defined there: the pin is
// its look, and remembers the spool the status may still show (was). A bay whose spool says nothing (no tag, no
// material and colour) pins nothing. The tool leaves any merge: a pick and a merge exclude each other.
/**
 * @param {AppState} app @param {number} n @param {string} key
 * @param {{ type: string, color: string } | null} [saved]
 */
function pickTool(app, n, key, saved = null) {
  const plan = shownPlan(app);
  if (plan && key !== NEW) {
    // The join context of the plan shown: the picks and merges the picker draws.
    const pick = chosen(plan, choiceFor(app.print, plan));
    const merges = mergesOf(app.print);
    const root = mergeRoot(plan, merges, n);
    // The bay the colour already prints from, through a join: no change.
    if (root !== n && pick[root] === key) return;
    // A bay that another colour prints from holds one spool: the pick joins that colour (bayOwner), also after
    // "Save and use" defined that spool.
    const owner = bayOwner(plan, bayRoots(plan, pick, merges), merges, n, key);
    if (owner !== null) {
      setTool(app, n, { into: owner });
      return;
    }
  }
  const before = pinOf(plan ? laneByKey(liveLanes(app.status, plan), key) : undefined);
  const pin = saved ? { uid: null, look: lookOf(saved.type, saved.color), was: before } : before;
  setTool(app, n, { key, pin: pin && (pin.uid || pin.look) ? pin : null });
}

// Opens bay key's panel with its spool editor already open. extra is what editOpen reads besides the lane: from the
// colour picker ("? define and use") the colour's index (use), material and colour, so Save also makes the bay that
// colour's pick; from the plan's "Define a:1", nothing. A bay that is gone does nothing.
/** @param {ActionCtx} ctx @param {string | undefined} key @param {Record<string, string>} [extra] */
function openBayEditor(ctx, key, extra = {}) {
  const at = laneAt(ctx.app, key);
  if (!at) return;
  ctx.app.ui = { ...ctx.app.ui, panel: { kind: 'bay', key: at.key } };
  // While the page is offline or the print is printing, the bay's panel opens without its editor: its Edit spool
  // says why, offline first.
  if (!ready(ctx.app) || printingNow(ctx.app.status)) {
    ctx.app.edit = null;
    ctx.render();
    return;
  }
  const data = { lane: String(at.i + 1), unit: unitId(at.unit), ...extra };
  editOpen(ctx, /** @type {HTMLElement} */ (/** @type {unknown} */ ({ dataset: data })));
}

// "? define and use" in the colour picker: the bay's panel opens with its spool editor, filled with the file
// colour's material and colour; Save then makes the bay that colour's pick.
/** @param {ActionCtx} ctx @param {HTMLElement} el */
function defineAndUse(ctx, el) {
  const d = el.dataset;
  openBayEditor(ctx, d.key, { use: d.n ?? '', material: d.material ?? '', colour: d.colour ?? '' });
}

// The unit that an action's data names: its name, '' for the unnamed [ace2k]. Without one, the lane shown.
/** @param {AppState} app @param {DOMStringMap} data */
const dataUnit = (app, data) => (data.unit !== undefined ? known(app, data.unit || null) : selection(app).unit);

// Opens the spool editor of a lane. Opened from the colour picker (data.use, the tool 0-based), Save also makes the
// lane that colour's pick. The colour fills the form, and a material of the U1's table fills it and is locked;
// a material outside the table leaves the choice to the operator.
/** @param {ActionCtx} ctx @param {HTMLElement} el */
function editOpen(ctx, el) {
  const { app } = ctx;
  const d = el.dataset;
  const unit = dataUnit(app, d);
  const form = { ...editPrefill(sliceFor(app.status, unit), Number(d.lane) - 1), unit };
  if (d.use !== undefined) {
    form.use = Number(d.use);
    if (d.material && FILAMENT_TYPES.includes(d.material)) {
      form.type = d.material;
      form.lock = true;
    }
    const colour = normalColour(d.colour);
    if (colour) form.color = colour;
  }
  app.edit = form;
  ctx.render();
}

// A change in the open spool editor. A locked material does not change.
/** @param {'type' | 'vendor' | 'color'} field @returns {ActionFn} */
const editField = (field) => (ctx, el) => {
  const e = ctx.app.edit;
  const value = field === 'type' ? (el.dataset.type ?? '') : /** @type {HTMLInputElement} */ (el).value;
  if (!e || (field === 'type' && (e.lock || !FILAMENT_TYPES.includes(value)))) return;
  ctx.app.edit = { ...e, [field]: value };
  ctx.render();
};

/** @param {ActionCtx} ctx */
function printResume(ctx) {
  if (ctx.app.pending.has('print:resume') || resuming(ctx.app.status)) return; // A RESUME is still running.
  if (refusedOffline(ctx)) return;
  // The print may have resumed or ended since the render. Then there is nothing to resume.
  const script = resumeScript(ctx.app.status);
  if (script) ctx.sendScript(script, null, { key: 'print:resume' });
}

// The printing view's Pause: the U1's own PAUSE, once.
/** @param {ActionCtx} ctx */
function printPause(ctx) {
  if (ctx.app.pending.has('print:pause')) return;
  if (refusedOffline(ctx)) return;
  // The print may have paused or ended since the render. Then there is nothing to pause.
  const script = pauseScript(ctx.app.status);
  if (script) ctx.sendScript(script, null, { key: 'print:pause' });
}

/** @param {ActionCtx} ctx */
function printCancel(ctx) {
  if (refusedOffline(ctx)) return;
  if (!ctx.confirm(ctx.t('print.confirm_cancel'))) return;
  // The print may have ended while the dialog was open. Then there is nothing to cancel.
  const script = cancelScript(ctx.app.status);
  if (script) ctx.sendScript(script, null, { key: 'print:cancel' });
}

// Returns the dry form as the cycle's temperature and minutes, or null when it is out of the limits.
// A unit's form keeps the raw strings. So an emptied input is refused here, and never read as 0.
// The time is h:mm, 0:01 to 24:00 (parseHM).
/** @param {import('./types.js').DryForm} form @returns {{ temp: number, minutes: number } | null} */
export function dryArgs(form) {
  const raw = String(form.temp ?? '').trim();
  const temp = raw === '' ? NaN : Number(raw);
  const minutes = parseHM(form.hm);
  return Number.isInteger(temp) && temp >= 15 && temp <= 65 && minutes !== null ? { temp, minutes } : null;
}

/** @param {ActionCtx} ctx @param {HTMLElement} el */
function dry(ctx, el) {
  const { app } = ctx;
  const du = dryerUnit(ctx, el);
  if (app.pending.has(pkey(app, du, 'dry:'))) return;
  const args = dryArgs(formOf(app, du));
  if (!args) {
    ctx.toast(ctx.t('toast.refused', { msg: ctx.t('dryer.limits') }));
    return;
  }
  ctx.send('dry', null, args, { unit: du });
}

// Stop all is decided before the dialog. During a print it stops only the lanes not in use,
// because a followed lane must keep its follow. The dialog says what is stopped.
// After the dialog, exactly those lines are sent.
/** @param {ActionCtx} ctx */
function stopAll(ctx) {
  const { app } = ctx;
  if (refusedOffline(ctx)) return;
  const plan = built(ctx, () => stopAllPlan(sliceFor(app.status, null), unitNames(app)[0]));
  if (plan === null) return;
  if (!plan.ok) {
    ctx.toast(ctx.t(plan.toast));
    return;
  }
  if (!ctx.confirm(ctx.t(plan.words.confirm, plan.words.vars))) return;
  // While the dialog was open, the print may have started or ended, or a planned lane may have gone into use.
  // Then nothing is sent. The page never stops a lane that the print now uses.
  // It never sends lines that the dialog did not promise.
  const now = stopAllConfirmed(plan, sliceFor(app.status, null));
  if (!now.ok) {
    ctx.toast(ctx.t(now.toast));
    return;
  }
  ctx.sendScript(now.script, null, { key: 'stop_all:' });
}

// Finds the lane in a head, from the line of that head. The click is checked against the live status first,
// because by now the adapter may know the lane, or a print may have started. Then the user confirms it.
/** @param {ActionCtx} ctx @param {HTMLElement} el */
function probeHead(ctx, el) {
  const { app } = ctx;
  const head = Number(el.dataset.head);
  const line = sharedHeadsView(app.status, ready(app)).find((h) => h.head === head - 1);
  const unit = known(app, line?.unit ?? null);
  if (app.pending.has(pkey(app, unit, `probe_head:${head}`))) return;
  if (!line?.probe?.enabled) {
    // When the line names a lane, there is nothing to find.
    // When the line is gone, the generic refusal says so.
    // The line goes when the adapter unhooks, or when the head sensor empties or goes away.
    const msg = line?.probe
      ? laneRefusal(ctx, line.probe.reason, head - 1)
      : line?.current
        ? ctx.t('heads.known', { head: headName(ctx.t, head - 1) })
        : ctx.t('reason.unavailable');
    ctx.toast(ctx.t('toast.refused', { msg }));
    return;
  }
  if (!ctx.confirm(ctx.t('action.confirm_probe_head', { head: headName(ctx.t, head - 1) }))) return;
  ctx.send('probe_head', null, { head: head - 1 }, { key: `probe_head:${head}`, unit });
}

// Read tag forgets the tag. The lane goes back to pending, and the unit reads the tag the next time the spool turns.
/** @param {ActionCtx} ctx @param {number} n @param {string | null} unit */
async function readTag(ctx, n, unit) {
  const forgotten = tagForgotten(sliceFor(ctx.app.status, unit), n - 1);
  if ((await ctx.send('read_tag', n, { forgotten }, { unit })) && !ctx.demo) {
    ctx.toast(ctx.t('toast.tag_rearmed', { n: laneText(sliceFor(ctx.app.status, unit), n - 1) }));
  }
}

// Why a lane action is refused, in words. Every head_* refusal names its head (headVars); a head the page cannot
// name (null) gives the words of head_loaded without a head.
/** @param {ActionCtx} ctx @param {string | null} reason @param {number | null} head */
export const laneRefusal = (ctx, reason, head) => {
  if (!String(reason).startsWith('head_')) return ctx.t(`reason.${reason}`);
  if (head === null) return reason === 'head_loaded' ? ctx.t('reason.head_loaded_any') : ctx.t(`reason.${reason}`);
  return ctx.t(`reason.${reason}`, headVars(ctx.t, ctx.app.status, reason, head));
};

const STOP_KEY = 'load_head:stop';
// The pause after Load into head N's dialog, before the offer is checked again. A pause of 0 ms can run
// before the socket messages that queued up while the dialog was open.
const RECHECK_PAUSE_MS = 300;

// The STOP of a head load, as a send option. A STOP lost to a closed connection is not sent again:
// after a reconnect the page cannot tell its own batch from one the screen opened since.
// The U1 turns its heaters off by itself after 5 idle minutes (its idle_timeout), so the page says that
// in an event instead of the plain refusal.
/** @param {ActionCtx} ctx @param {string | null} unit */
const stopOpts = (ctx, unit) => ({
  key: STOP_KEY,
  unit,
  /** @param {string} msg */
  quiet: (msg) => {
    if (msg !== CONNECTION_CLOSED) return false;
    ctx.app.events = appendEvent(ctx.app.events, {
      t: ctx.now(),
      k: 'e',
      lvl: 'warn',
      src: 'unit',
      msg: ctx.t('events.stop_lost'),
    });
    return true;
  },
});

// Load into head N, checked against the live status at the click and again after the dialog,
// because a print or another head load may start while the dialog is open.
// It returns the lines to send, or null after showing why not. plan is the one that the dialog
// described, when there is one: lines that now differ from it are not sent.
/**
 * @param {ActionCtx} ctx
 * @param {number} n
 * @param {string | null} unit
 * @param {ReturnType<typeof loadHeadPlan> | null} plan
 */
function loadHeadNow(ctx, n, unit, plan) {
  const slice = sliceFor(ctx.app.status, unit);
  const now = allowedNow(slice, n - 1, 'load_head', ready(ctx.app));
  if (!now.ok) {
    ctx.toast(ctx.t('toast.refused', { msg: laneRefusal(ctx, now.reason, headOf(slice, n - 1)) }));
    return null;
  }
  const next = built(ctx, () => loadHeadPlan(slice, n - 1, unit));
  if (next && plan && JSON.stringify(next) !== JSON.stringify(plan)) {
    ctx.toast(ctx.t('toast.refused', { msg: ctx.t('reason.unavailable') }));
    return null;
  }
  return next;
}

// Load into head N: the U1 screen's own head load, after a confirmation that says the head heats.
// On a shared head, ACEK_LANE_ACTIVE first makes the lane the head's lane. When it is refused, nothing else is sent.
// Then ACEK_HEAD_PREPARE: a lane whose material is not set yet is pushed to the head's sensor, its tag read on
// the way. When its material stays unset, it is rolled back and refused, and nothing else is sent.
// Then ACEK_HEAD_SPOOL gives the head the lane's spool, as a swap does. It goes before the load, because the U1
// reads the load temperature from the head's setting when the load starts. When it is refused, nothing heats.
// A lane with no spool set is no refusal: the adapter says so in a console line, which the events show as a warning.
// If the load fails after it, the head keeps the spool it was given.
// Then START. A refused START opened no batch, so nothing more is sent.
// Then DOING, which returns when the load ends, about 90 s later, and STOP, whatever DOING returned.
// Without STOP the macro stays in its batch, and the heads stay at the load temperature.
// Outside a print STOP only turns the heads' heaters off, as the screen does.
// One pending key for the whole page holds the sequence, because the macro has one batch for the printer.
// So every lane's button stays busy until STOP returns.
/** @param {ActionCtx} ctx @param {number} n @param {string | null} unit */
async function loadHead(ctx, n, unit) {
  const { app } = ctx;
  if (app.pending.has(LOAD_HEAD_KEY)) return;
  const plan = loadHeadNow(ctx, n, unit, null);
  if (plan === null) return;
  const vars = { lane: laneText(sliceFor(app.status, unit), n - 1), head: headName(ctx.t, plan.head) };
  const question = plan.select
    ? ctx.t('action.confirm_load_head_shared', vars)
    : ctx.t('action.confirm_load_head', vars);
  if (!ctx.confirm(question)) return;
  // window.confirm blocks, and the status messages that came meanwhile are dispatched only after this task.
  // So the page pauses before it checks again. Another click's load may have started during the pause.
  await ctx.sleep(RECHECK_PAUSE_MS);
  if (app.pending.has(LOAD_HEAD_KEY) || loadHeadNow(ctx, n, unit, plan) === null) return;
  const step = { key: `${LOAD_HEAD_KEY}:step`, unit };
  app.pending.add(LOAD_HEAD_KEY);
  ctx.render();
  try {
    if (plan.select && !(await ctx.sendScript(plan.select, n, step))) return;
    if (!(await ctx.sendScript(plan.prepare, n, step))) return;
    if (!(await ctx.sendScript(plan.spool, n, { key: `${LOAD_HEAD_KEY}:spool`, unit }))) return;
    if (!(await ctx.sendScript(plan.start, n, step))) return;
    await ctx.sendScript(plan.doing, n, step);
    await ctx.sendScript(plan.stop, n, stopOpts(ctx, unit));
  } finally {
    app.pending.delete(LOAD_HEAD_KEY);
    ctx.render();
  }
}

// A lane action's arguments besides the lane. Feed and rollback send the Advanced length.
// An empty or invalid length becomes NaN. send refuses NaN and shows the allowed range in the toast.
/** @param {AppState} app @param {string} action @returns {GcodeArgs} */
function laneArgs(app, action) {
  if (action === 'feed' || action === 'rollback') return { length: app.moveMm === '' ? NaN : Number(app.moveMm) };
  return {};
}

// The lane actions that run their own steps instead of one G-code: Read tag shows a toast after it,
// and Load into head N sends its sequence.
/** @type {Record<string, (ctx: ActionCtx, n: number, unit: string | null) => unknown>} */
const OWN_STEPS = { read_tag: readTag, load_head: loadHead };

// A button on a lane card. It acts on the card's unit, and is checked against the live status at the click.
/** @param {ActionCtx} ctx @param {HTMLElement} el */
function laneAction(ctx, el) {
  const { app } = ctx;
  const unit = selection(app).unit;
  const n = Number(el.dataset.lane);
  const id = el.dataset.id ?? '';
  const slice = sliceFor(app.status, unit);
  const now = allowedNow(slice, n - 1, id, ready(app));
  const head = headOf(slice, n - 1);
  if (!now.ok) {
    ctx.toast(ctx.t('toast.refused', { msg: laneRefusal(ctx, now.reason, head) }));
    return;
  }
  const own = entry(OWN_STEPS, id);
  if (own) {
    own(ctx, n, unit);
    return;
  }
  const ff = id === 'ff';
  if (ff && app.pending.has(pkey(app, unit, ffKey(n)))) return;
  const action = ff ? (laneOf(slice, n - 1)?.ff_on ? 'ff_off' : 'ff_on') : id;
  if (
    CONFIRM.includes(action) &&
    !ctx.confirm(ctx.t(`action.confirm_${action}`, { n, head: head === null ? '' : headName(ctx.t, head) }))
  )
    return;
  ctx.send(action, n, laneArgs(app, action), { key: ff ? ffKey(n) : undefined, unit });
}

/** @param {ActionCtx} ctx @param {HTMLElement} el */
function series(ctx, el) {
  const { app } = ctx;
  const k = el.dataset.key ?? '';
  app.hidden = app.hidden.includes(k) ? app.hidden.filter((x) => x !== k) : [...app.hidden, k];
  ctx.saveHidden();
  ctx.render();
}

// Shows the rows that the limit hides. When there are none, it fetches the next older day.
/** @param {ActionCtx} ctx */
function more(ctx) {
  const { app } = ctx;
  if (ctx.eventCount() > app.limit) app.limit += 200;
  else if (olderDays(app, ctx.demo)) ctx.loadDays(app.daysLoaded + 1);
  ctx.render();
}

/** @type {Record<string, ActionFn>} */
export const ACTIONS = {
  edit_open: editOpen,
  define: (ctx, el) => openBayEditor(ctx, el.dataset.key),
  spool_edit_type: editField('type'),
  spool_edit_vendor: editField('vendor'),
  spool_edit_colour: editField('color'),
  // The Advanced length of the lane card's feed and rollback.
  move_mm: (ctx, el) =>
    redraw(ctx, (app) => {
      app.moveMm = /** @type {HTMLInputElement} */ (el).value;
    }),
  edit_cancel: (ctx) =>
    redraw(ctx, (app) => {
      app.edit = null;
    }),
  edit_save: editSaveAction,
  probe_head: probeHead,
  print_open: (ctx) => ctx.openPrint(),
  print_go: (ctx) => ctx.startPrint(),
  // The file grid: a card is chosen, a card scrolls into view, the sort chips.
  file_pick: (ctx, el) => ctx.planFile(el.dataset.file),
  card_load: (ctx, el) => {
    if (el.dataset.file) return ctx.loadCard(el.dataset.file);
    return undefined;
  },
  print_sort: (ctx, el) =>
    redraw(ctx, (app) => {
      app.print = { ...app.print, sort: el.dataset.sort === 'name' ? 'name' : 'new' };
    }),
  // The plan view: back to the grid, the colour picker's choices and the similar-colour hint.
  print_back: (ctx) => redraw(ctx, (app) => toGrid(app)),
  // The other-device notice: plan this page's file again, keeping the picks, merges and pins.
  plan_again: (ctx) => planAgain(ctx),
  pick: (ctx, el) => {
    const key = el.dataset.key;
    if (key) redraw(ctx, (app) => pickTool(app, Number(el.dataset.n), key));
  },
  // A merge drops the tool's own pick and pin; unpin drops all three.
  merge: (ctx, el) => redraw(ctx, (app) => setTool(app, Number(el.dataset.n), { into: Number(el.dataset.into) })),
  unpin: (ctx, el) => redraw(ctx, (app) => setTool(app, Number(el.dataset.n), {})),
  hint_keep: (ctx) =>
    redraw(ctx, (app) => {
      const file = app.print.file;
      const kept = app.print.dismissed ?? [];
      if (file && !kept.includes(file)) app.print = { ...app.print, dismissed: [...kept, file] };
    }),
  define_and_use: defineAndUse,
  // The guided steps: back, "Done, next →", the list of every step, and eject all (its confirmation, the run,
  // and closing the line that says where it stopped).
  step_prev: (ctx) => redraw(ctx, stepPrev),
  step_done: (ctx) => redraw(ctx, stepNext),
  steps_all: (ctx) =>
    redraw(ctx, (app) => {
      app.print = { ...app.print, allSteps: !app.print.allSteps };
    }),
  eject_all: (ctx) =>
    redraw(ctx, (app) => {
      if (app.print.eject?.phase !== 'run') app.print = { ...app.print, eject: { phase: 'ask' } };
    }),
  eject_all_go: (ctx, el) => ejectAll(ctx, el.dataset.keys),
  // Stop the run: no more bays are sent. The bay being ejected finishes its eject on the unit (nothing is sent to it),
  // the spools already held stay, and the card says where the run stopped. The run sees the phase and ends.
  eject_all_stop: (ctx) =>
    redraw(ctx, (app) => {
      const run = app.print.eject;
      if (run?.phase === 'run')
        app.print = { ...app.print, eject: { phase: 'failed', key: run.key, msg: ctx.t('eject_all.stopped') } };
    }),
  eject_all_no: (ctx) =>
    redraw(ctx, (app) => {
      if (app.print.eject?.phase !== 'run') app.print = { ...app.print, eject: null };
    }),
  print_resume: printResume,
  print_pause: printPause,
  print_cancel: printCancel,
  chart_unit: (ctx, el) =>
    redraw(ctx, (app) => {
      app.chartUnit = known(app, el.dataset.unit ?? null);
    }),
  preset: (ctx, el) => {
    const k = Number(el.dataset.k);
    const p = ctx.app.presets[k];
    ctx.app.forms[dryerUnit(ctx, el) ?? ''] = { preset: k, temp: p.temp, hm: fmtHM(p.hours * 3600) };
    ctx.blur();
    ctx.render();
  },
  dry,
  dry_mode: (ctx, el) => {
    const mode = el.dataset.mode ?? '';
    if (!DRYER_MODES.includes(mode)) return;
    const d = dryerNow(ctx, el);
    // Humidity again after failed cycles turns it back on and clears the count: that asks, through the warning.
    const rearm = mode === 'humidity' && (d.stopped || d.failures > 0);
    // The mode the page shows as on asks nothing, but is still sent: the unit ignores a repeated mode, and a
    // stale status must not swallow a real change.
    if (mode === d.mode && !rearm) return dryerSend('dry_mode', () => ({ mode }))(ctx, el);
    if (mode === 'humidity') {
      const unit = dryerUnit(ctx, el);
      return redraw(ctx, (app) => {
        app.dryerUnit = unit; // The warning stands on the card that was clicked.
        app.modeWarn = true;
      });
    }
    // A change of mode stops the automatic cycle that runs: ask first, as Stop does.
    const stops = autoCycle(d);
    if (stops && !ctx.confirm(ctx.t('dryer.confirm_mode_stops_auto'))) return;
    return dryerSend('dry_mode', () => ({ mode }))(ctx, el);
  },
  dry_mode_confirm: (ctx, el) => {
    // Only from the open warning, and only where printer.cfg allows the humidity mode.
    const open = ctx.app.modeWarn;
    ctx.app.modeWarn = false;
    ctx.render();
    if (!open || !dryerNow(ctx, el).humidityAllowed) return;
    return dryerSend('dry_mode', () => ({ mode: 'humidity' }))(ctx, el);
  },
  dry_mode_cancel: (ctx) => redraw(ctx, (app) => (app.modeWarn = false)),
  dry_stop: (ctx, el) => {
    const d = dryerNow(ctx, el);
    if (autoCycle(d) && !ctx.confirm(ctx.t('dryer.confirm_stop_auto'))) return;
    return dryerSend('dry_stop', () => ({}))(ctx, el);
  },
  dry_clear: dryerSend('dry_clear', () => ({})),
  dry_log: dryLog,
  dry_log_close: (ctx) => redraw(ctx, (app) => (app.dryerLog = null)),
  fans_off: dryerSend('fans_off', () => ({})),
  fans_on: dryerSend('fans_on', () => ({ seconds: FAN_SECONDS })),
  flap: dryerSend('flap', (_ctx, el) => ({ which: el.dataset.which, open: el.dataset.open === '1' })),
  stop_all: stopAll,
  lane: laneAction,
  window: (ctx, el) =>
    redraw(ctx, (app) => {
      app.hours = Number(el.dataset.h);
    }),
  series,
  // A tab of the top bar. The side panel belongs to the tab it was opened on, so it closes. A click on the Print
  // tab plans its file again (printflow.js replanChosen), so a file uploaded again under the same name gets a fresh plan.
  tab: (ctx, el) =>
    redraw(ctx, (app) => {
      const tab = tabOf(el.dataset.tab);
      const from = app.ui.tab;
      app.ui = { ...app.ui, tab, panel: null, enter: travel(from, tab) };
      if (tab === 'print' && from !== 'print') replanChosen(ctx);
      ctx.saveTab();
    }),
  // The Units tab on a phone groups its lists by head or by unit.
  list_mode: (ctx, el) =>
    redraw(ctx, (app) => {
      app.ui = { ...app.ui, listMode: el.dataset.mode === 'unit' ? 'unit' : 'head' };
    }),
  // The side panel: it shows what the data names ({ kind: 'status' }, …), and closes.
  // An open spool editor belongs to the panel it was opened in, so it closes.
  panel: (ctx, el) =>
    redraw(ctx, (app) => {
      app.ui = { ...app.ui, panel: /** @type {Record<string, string>} */ ({ ...el.dataset }) };
      app.edit = null;
    }),
  // The unit footer's "♨ dry": the unit's panel, opened on its dryer.
  dry_shortcut: (ctx, el) =>
    redraw(ctx, (app) => {
      app.ui = { ...app.ui, panel: { kind: 'unit', id: el.dataset.unit ?? '', focus: 'dryer' } };
    }),
  panel_close: (ctx) =>
    redraw(ctx, (app) => {
      app.ui = { ...app.ui, panel: null };
      app.edit = null;
    }),
  theme: (ctx) => {
    ctx.app.theme = nextTheme(ctx.app.theme);
    ctx.saveTheme();
    ctx.render();
  },
  filter: (ctx, el) =>
    redraw(ctx, (app) => {
      app.filter = el.dataset.f ?? 'all';
      app.limit = 200;
    }),
  more,
};

// The handlers of the controls that carry a value or a checked state.
/** @type {Record<string, ChangeFn>} */
export const CHANGES = {
  print_q: (ctx, el) =>
    redraw(ctx, (app) => {
      app.print = { ...app.print, q: el.value };
    }),
  dry_field: dryField,
  print_opt: (ctx, el) =>
    redraw(ctx, (app) => {
      // The heads the print uses, from the bays Print sends (the print bar's gate).
      const now = planNow(app);
      const used = now ? usedHeads(now.plan.order, printGate(now, false).choice, now.lanes) : [];
      app.printOptions = nextOptions(
        app.printOptions,
        { opt: el.dataset.opt, on: Boolean(el.checked), head: el.dataset.head },
        used,
      );
    }),
};

// A lane button pins its lane (1-based in data.lane) on the unit its data names, else on the shown unit.
/** @param {AppState} app @param {DOMStringMap} data */
function pinLane(app, data) {
  if (data.lane) app.selected = { unit: dataUnit(app, data), lane: Number(data.lane) - 1 };
}

// The bridge from the components to the actions: ctx.run(id, data). A component names the action and hands
// the values that the old markup carried in data-*. The action gets them as el.dataset, with the control's
// value and checked state. An id that is in neither table does nothing.
// A lane in the data pins its lane first, so a lane that goes into error elsewhere does not take the card.
/**
 * @param {ActionCtx} ctx
 * @param {string} id
 * @param {Record<string, string>} [data]
 * @param {string} [value]
 * @param {boolean} [checked]
 */
export function runAction(ctx, id, data = {}, value = '', checked = false) {
  const fn = entry(ACTIONS, id) ?? entry(CHANGES, id);
  if (!fn) return undefined;
  const el = { id, value, checked, dataset: { ...data }, blur: () => {} };
  pinLane(ctx.app, el.dataset);
  return fn(ctx, /** @type {HTMLElement & ChangeEl} */ (/** @type {unknown} */ (el)));
}
