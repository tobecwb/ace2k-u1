// The flows of the Print panel: open it, plan a file, and start the print.
// ctx holds the page state and the effects, as in js/send.js.
import { laneOf, printing } from './model.js';
import { ready, shownPlan, planNow, otherDevice, readingOf } from './state.js';
import { built } from './send.js';
import { CONNECTION_CLOSED } from './conn.js';
import { appendEvent } from './history.js';
import { asIsChecks, guideOf, ejectList, runEjectAll, stepText } from './steps.js';
import { chipText, laneAt } from './lane-state.js';
import { laneErrs } from './model.js';
import { fmtHM } from './format.js';
import { hasBytes } from './clock.js';
import { demoFiles, demoPlan } from './demo.js';
import {
  printScript,
  printGate,
  DEFAULT_OPTIONS,
  planScript,
  carriedChoice,
  planId,
  refusalAction,
  rekeyPicks,
  userBays,
  laneByKey,
  materialMatch,
  colourDiffers,
  laneColourName,
  liveLanes,
} from './print-plan.js';

/** @typedef {import('./types.js').Ctx} Ctx */
/** @typedef {import('./types.js').ActionCtx} ActionCtx */
/** @typedef {{ toast: string, script: string }} Replan */

// Back to the grid: no file chosen, no reading, and the colour picker or Options panel closed with it.
/** @param {import('./types.js').AppState} app */
export function toGrid(app) {
  app.print = { ...app.print, file: null, reading: null, scrolled: null };
  if (app.ui.panel?.kind === 'pick' || app.ui.panel?.kind === 'opts') app.ui = { ...app.ui, panel: null };
}

// Opening the Print tab lists the G-code files again. It plans nothing and does not take the file from the live plan:
// a page opens on the grid (a reload, another device). A file this page chose stays open, and a click on the Print
// tab from another tab plans it again (replanChosen).
/** @param {Ctx} ctx */
export async function openPrint(ctx) {
  const { app } = ctx;
  app.print = { ...app.print, open: true };
  ctx.render();
  if (ctx.demo) {
    const demo = await demoFiles();
    app.print = { ...app.print, files: demo.files };
    ctx.render();
    return;
  }
  showThumb(ctx, app.print.file);
  try {
    // A null conn throws here, and the catch shows it in a toast.
    // The list goes on app.print as it is when the list arrives, because a file chosen meanwhile replaces app.print.
    const files = await /** @type {import('./conn.js').Connection} */ (app.conn).files();
    app.print = { ...app.print, files };
  } catch (e) {
    ctx.toast(ctx.t('toast.print_files', { msg: /** @type {Error} */ (e).message }));
  }
  ctx.render();
}

// A click on the Print tab (from another tab) plans the chosen file again, keeping the user's picks, so a file
// uploaded again under the same name never keeps a stale plan. It goes through the reading as a card click does, so
// the view never falls back to the grid meanwhile. Nothing goes with no file chosen, a print on, offline, in the demo,
// or while another device's plan is live: the notice's "Plan … again" is the one way to take the plan back. Nor while
// this page still reads the file (the plan request in flight, or sent and its plan not come yet): leaving Print and
// coming back during a read sends no second request.
/** @param {Ctx} ctx */
export function replanChosen(ctx) {
  const { app } = ctx;
  if (!app.print.open || !app.print.file || ctx.demo || printing(app.status) || !ready(app)) return;
  if (otherDevice(app) || app.pending.has('print:file')) return;
  const reading = readingOf(app);
  if (reading && !reading.error) return;
  planAgain(ctx);
}

// The chosen file planned again, keeping the user's picks, merges and pins (the notice's "Plan … again" and the
// Print-tab click). The demo takes the plan from dev/demo-files.json.
/** @param {Ctx} ctx */
export function planAgain(ctx) {
  const file = ctx.app.print.file;
  if (!file) return;
  if (ctx.demo) {
    demoPlan(ctx, file);
    return;
  }
  const script = built(ctx, () => planScript(file));
  if (script !== null) sendPlan(ctx, file, script);
}

// Sends the plan of file and records its reading, which ends once the plan comes (state.js endReading).
// A refusal (another slicer, an unreadable file) ends the reading with the host's message, shown in place of
// the bar, not in a toast. A lost connection is not the host's refusal: it keeps its toast and also ends the reading
// with its message, so the view shows it with "← Files" rather than the other-device notice. A refusal whose reading
// is not on screen (the live plan already names the file, as in a re-plan of it, or another file was chosen since)
// keeps its toast too, or nothing would show it.
/** @param {Ctx} ctx @param {string} file @param {string} script */
function sendPlan(ctx, file, script) {
  const { app } = ctx;
  /** @param {string} msg */
  const quiet = (msg) => {
    const r = app.print.reading;
    const mine = r?.file === file;
    const shown = mine && readingOf(app) === r;
    if (mine) app.print = { ...app.print, reading: { ...r, error: msg } };
    if (msg === CONNECTION_CLOSED || !shown) return false;
    ctx.render();
    return true;
  };
  app.print = { ...app.print, reading: { file, at: Math.round(ctx.now() * 1000) } };
  ctx.sendScript(script, null, { key: 'print:file', quiet });
}

// A guide that starts over: the first step, no trail, no spool in hand, no eject run, the list of steps folded.
const FRESH_GUIDE = Object.freeze({ step: 0, trail: [], shown: null, held: [], eject: null, allSteps: false });

// At most this many cards load at once: each reads the file's metadata and two ranges of the file, and a grid
// scrolled fast asks for dozens. The others wait their turn, in the order asked.
export const CARD_SLOTS = 4;
let cardsBusy = 0;
/** @type {(() => void)[]} */
const cardQueue = [];

// Runs load once a slot is free, and frees it after. A slot freed while others wait goes straight to the first of
// them, so no new load can take it in between.
/** @template T @param {() => Promise<T>} load @returns {Promise<T>} */
async function inCardSlot(load) {
  if (cardsBusy < CARD_SLOTS) cardsBusy += 1;
  else await new Promise((/** @type {(v?: unknown) => void} */ go) => cardQueue.push(go));
  try {
    return await load();
  } finally {
    const next = cardQueue.shift();
    if (next) next();
    else cardsBusy -= 1;
  }
}

/** @returns {NonNullable<import('./types.js').CardEntry['card']>} */
const emptyCard = () => ({ colours: null, time: null, thumb: null, modified: null });

// The one write of a file's card. A card with the G-code bytes is never replaced by one without them (a read that
// failed, or a file list that came late), so the print's progress never loses its frame.
/** @param {Ctx} ctx @param {string} path @param {number} modified @param {import('./types.js').CardEntry['card']} card */
function putCard(ctx, path, modified, card) {
  const { app } = ctx;
  if (hasBytes(app.print.cards?.[path]?.card) && !hasBytes(card)) return false;
  app.print = { ...app.print, cards: { ...app.print.cards, [path]: { modified, card } } };
  return true;
}

// The one read of a file's card, null when it fails. queued: wait for a grid slot. bytesOnly: only the metadata's
// G-code bytes, added to the card held (a retry reads no range of the file).
/** @param {Ctx} ctx @param {string} path @param {{ queued?: boolean, bytesOnly?: boolean }} [how] */
async function readCard(ctx, path, { queued = true, bytesOnly = false } = {}) {
  const { app } = ctx;
  try {
    if (ctx.demo) return (await demoFiles()).cards[path] ?? null;
    const conn = /** @type {import('./conn.js').Connection} */ (app.conn);
    if (bytesOnly) {
      const bytes = await conn.fileBytes(path);
      const held = app.print.cards?.[path]?.card ?? emptyCard();
      return { ...held, ...bytes, modified: bytes.modified ?? held.modified };
    }
    return queued ? await inCardSlot(() => conn.fileCard(path)) : await conn.fileCard(path);
  } catch {
    return null;
  }
}

// Loads the card of a file once per time of change: its colours, time and thumbnail. The grid asks for
// a card when it scrolls into view. A failed read leaves the card empty and is not tried again until the file changes.
// A card with the G-code bytes is not read again for a list that does not know the file's time (0), and stays while
// it is read again.
/** @param {Ctx} ctx @param {string} path */
export async function loadCard(ctx, path) {
  const { app } = ctx;
  const modified = app.print.files.find((f) => f.path === path)?.modified ?? 0;
  const had = app.print.cards?.[path];
  if (had && (had.modified === modified || (hasBytes(had.card) && !modified))) return;
  const keep = hasBytes(had?.card);
  if (!keep) app.print = { ...app.print, cards: { ...app.print.cards, [path]: { modified, card: null } } };
  const card = (await readCard(ctx, path)) ?? emptyCard();
  if (!keep && app.print.cards?.[path]?.modified !== modified) return; // A newer request owns the entry.
  if (putCard(ctx, path, modified, card)) ctx.render();
}

// The running print's card: its G-code bytes are what the print's progress is counted over (clock.js). It is read
// outside the grid's queue (a grid scrolled fast must not delay it) with the file's time from the list when it is known,
// else from the metadata. Without the bytes it is read again, metadata only, after each of these waits; and again, a
// few times, if the card loses its bytes during the print. Every status looks at it, so it stays O(1).
export const RUNNING_RETRY_MS = [2000, 5000, 15000];
const RUNNING_LOSSES = 3;
/** @type {{ file: string, reads: number, busy: boolean, got: boolean, lost: number } | null} */
let runningJob = null;

/** @param {Ctx} ctx @param {NonNullable<typeof runningJob>} job */
async function readRunning(ctx, job) {
  const { app } = ctx;
  job.busy = true;
  const card = await readCard(ctx, job.file, { queued: false, bytesOnly: job.reads > 0 });
  job.reads += 1;
  job.busy = false;
  if (runningJob !== job) return; // The print ended, or another file runs.
  if (card) {
    const listed = app.print.files.find((f) => f.path === job.file)?.modified;
    if (putCard(ctx, job.file, listed ?? card.modified ?? 0, card)) ctx.render();
  }
  if (hasBytes(app.print.cards?.[job.file]?.card)) {
    job.got = true;
    return;
  }
  if (job.reads > RUNNING_RETRY_MS.length) return;
  job.busy = true; // The wait.
  setTimeout(
    () => {
      job.busy = false;
      if (runningJob === job && !hasBytes(app.print.cards?.[job.file]?.card)) readRunning(ctx, job);
    },
    RUNNING_RETRY_MS[job.reads - 1],
  );
}

// Called on every status, and by the demo: starts the running print's card for its file, and forgets the job once
// nothing prints. A card that has the bytes (the grid read it) is not read again.
/** @param {Ctx} ctx */
export function ensureRunningCard(ctx) {
  const { app } = ctx;
  const file = app.status.print_stats?.filename;
  const on = printing(app.status);
  if (!file || !on) {
    runningJob = null;
    return;
  }
  if (runningJob?.file !== file) runningJob = { file, reads: 0, busy: false, got: false, lost: 0 };
  const job = runningJob;
  if (hasBytes(app.print.cards?.[file]?.card)) {
    job.got = true;
    return;
  }
  if (job.busy) return;
  if (job.got) {
    // The bytes went: read them again, a few times.
    if (job.lost >= RUNNING_LOSSES) return;
    job.lost += 1;
    job.got = false;
    job.reads = 1;
  } else if (job.reads > 0) return; // Waiting for its own retry (or out of them).
  readRunning(ctx, job);
}

// A file was chosen. The adapter plans it: its tools, a suggested lane for each tool, and the swaps.
// Each file starts with no pick and no merge of the user's. The demo takes the plan from dev/demo-files.json.
/** @param {Ctx} ctx @param {string | null | undefined} file */
export function planFile(ctx, file) {
  if (!file) return;
  const { app } = ctx;
  // A file with no script to send (a name that cannot be planned) is not switched to: the grid stays.
  const script = ctx.demo ? null : built(ctx, () => planScript(file));
  if (!ctx.demo && script === null) return;
  app.print = {
    ...app.print,
    file,
    choice: {},
    choiceId: null,
    choiceTools: null,
    merges: {},
    pinned: {},
    auto: [],
    overrides: [],
    ...FRESH_GUIDE,
  };
  app.printOptions = { ...DEFAULT_OPTIONS }; // The screen starts each file from the default options.
  if (ctx.demo) {
    demoPlan(ctx, file);
    return;
  }
  sendPlan(ctx, file, /** @type {string} */ (script));
  showThumb(ctx, file);
}

// The file's thumbnail, the image that the slicer wrote in it. It is kept with its file's name,
// and the panel shows it only while that file is the one shown.
// A file with none, or a failed request, changes nothing, so the panel is as it was without it.
/** @param {Ctx} ctx @param {string | null} file */
async function showThumb(ctx, file) {
  const { app } = ctx;
  if (ctx.demo || !file) return;
  /** @type {string | null} */
  let url = null;
  try {
    url = await /** @type {import('./conn.js').Connection} */ (app.conn).thumbnail(file);
  } catch {
    return;
  }
  if (!url || app.print.file !== file) return;
  app.print = { ...app.print, thumb: { file, url } };
  ctx.render();
}

// Print sends the plan's choice, the U1's tool map and the start in one script.
// A refusal leaves the panel open. With steps pending in the bays, it asks first (ctx.confirm).
/** @param {ActionCtx} ctx */
export async function startPrint(ctx) {
  const { app } = ctx;
  const now = planNow(app);
  // The state is read at the click, as the lane actions do, never from the button's disabled state.
  if (!now || app.pending.has('print:go') || !ready(app) || printing(app.status)) return;
  const { plan } = now;
  // The print bar's gate: Print sends the bays the plan shows, and is disabled when the gate blocks.
  const { choice, block, asIs } = printGate(now, false);
  if (block) return;
  // Print sends the bays as they are (asIs): the guided steps are only a suggestion. It asks only about colours whose
  // bay holds a clearly other colour and steps still pending for a colour. Else it sends the steps' bays, and with
  // steps still pending (the operator may know a case the page does not) it asks and lists them.
  const g = guideOf(app, now);
  const checks = asIs ? asIsChecks(ctx.t, now, choice, g?.steps ?? []) : [];
  if (checks.length && !ctx.confirm(ctx.t('print.as_is_check', { list: checks.join('; ') }))) return;
  if (!asIs && g?.steps.length) {
    const list = g.steps.map((x) => stepText(ctx.t, x, g.now)).join('; ');
    if (!ctx.confirm(ctx.t('print.pending', { list }))) return;
  }
  const script = built(ctx, () => printScript(plan.file, choice, now.lanes, plan.order, app.printOptions));
  if (script === null) return;
  // When the file changed since its plan, it is planned again at once, and the toast says so.
  /** @type {Replan | null} */
  let replan = null;
  /** @param {string} msg */
  const quiet = (msg) => {
    replan = refusalAction(msg, plan.file);
    return replan !== null;
  };
  const ok = await ctx.sendScript(script, null, { key: 'print:go', quiet, once: true });
  if (replan) {
    ctx.toast(/** @type {Replan} */ (replan).toast); // The quiet callback set it.
    // The choice stays, because keepChoice carries it over to the new plan.
    ctx.sendScript(/** @type {Replan} */ (replan).script, null, { key: 'print:file' });
    return;
  }
  // The demo only shows the script in a toast. Nothing started, so the panel stays open.
  if (ok && !ctx.demo) {
    afterStart(app, plan.file);
    ctx.render();
  }
}

// The page after a print started: the panel closes and the choice goes. The file goes too, only if it is still the
// one printed (a file chosen while Print was in flight stays): the grid must not hide a chosen file, which a later
// Units -> Print would plan again.
/** @param {import('./types.js').AppState} app @param {string} file */
function afterStart(app, file) {
  if (app.print.file === file) toGrid(app);
  app.print = {
    ...app.print,
    open: false,
    choice: {},
    choiceId: null,
    choiceTools: null,
    merges: {},
    pinned: {},
    auto: [],
    overrides: [],
  };
}

// No pins: the value pinned stands for while the page has none, so that "nothing changed" compares as the same object.
/** @type {Record<number, import('./types.js').Pin>} */
const NO_PINS = Object.freeze({});

// Runs on each status. First the pins follow their spools in the live bays (rekeyPicks): a pick moves with its spool,
// waits through an empty bay or a status lag, and goes, with a toast, only when its bay holds another known spool. A
// bay the user set in the colour picker stays the colour's whatever it holds (userBays): when it holds another spool of
// another colour, a toast says so once.
// Then, when the plan shown was made again for the file of the choice, the choice, the merges and the pins move to
// the new plan. The lanes that the new plan no longer offers are dropped, and their tools go back to the suggestion.
// One toast lists them.
/** @param {Ctx} ctx */
export function keepChoice(ctx) {
  const { app } = ctx;
  const plan = shownPlan(app);
  // The bays the user set, worked out once for the pins and the re-plan.
  const mine = userBays(app.print.choice, app.print.overrides);
  if (!carriedChoice(app.print, plan, mine)) return;
  const p = /** @type {import('./types.js').Plan} */ (plan); // carriedChoice is null without a plan.
  const pins = app.print.pinned ?? NO_PINS;
  const lanes = liveLanes(app.status, p);
  const moved = rekeyPicks(app.print.choice, pins, lanes, mine);
  const carried = /** @type {NonNullable<ReturnType<typeof carriedChoice>>} */ (
    carriedChoice({ ...app.print, choice: moved.choice, pinned: moved.pinned }, p, mine)
  );
  // The same plan and no spool moved: nothing changes.
  if (app.print.choiceId !== planId(p) || carried.choice !== app.print.choice || carried.pinned !== pins)
    app.print = {
      ...app.print,
      choice: carried.choice,
      pinned: carried.pinned,
      merges: carried.merges,
      // The page's own settled picks go with the picks they settled.
      auto: (app.print.auto ?? []).filter((n) => carried.choice[n] != null),
      overrides: (app.print.overrides ?? []).filter((n) => carried.choice[n] != null),
      choiceId: planId(p),
      choiceTools: p.tools,
    };
  for (const n of moved.lost) ctx.toast(ctx.t('print.pin_lost', { n: n + 1 }));
  for (const n of moved.refilled) refillToast(ctx, p.tools[n], n, laneByKey(lanes, moved.choice[n]));
  if (carried.dropped.length)
    ctx.toast(
      ctx.t('print.choice_dropped', {
        choices: carried.dropped.map(({ n, key }) => `${ctx.t('print.colour_n', { n: n + 1 })}: ${key}`).join(', '),
      }),
    );
}

// The toast for a bay the user set whose spool is new (rekeyPicks's refilled: another spool, the first one in a bay
// picked empty, or one whose colour became known): when that spool's colour is clearly not the file colour's, it still
// prints from that bay, so the page says so once for that spool. A spool of no known material says nothing here: Print's
// "define first" covers it. Another material says nothing either: Print is blocked with its reason.
/**
 * @param {Ctx} ctx @param {import('./types.js').Tool} tool @param {number} n
 * @param {import('./types.js').PlanLane | undefined} lane
 */
function refillToast(ctx, tool, n, lane) {
  if (!lane?.material || materialMatch(tool.material, lane.material) === 'other') return;
  if (!colourDiffers(lane.colour, tool.colour)) return;
  ctx.toast(ctx.t('print.bay_now_holds', { key: lane.key, colour: laneColourName(lane), n: n + 1 }));
}

// How often eject all reads the live bays while an eject runs, and how long it waits for one eject at most: a
// loaded head is heated and unloaded on the U1 first, then the unit pulls the filament out of its bay.
const EJECT_POLL_MS = 500;
export const EJECT_WAIT_S = 600;
// A lane error that lasts this long fails the eject. The host clears the error of an unload that stopped short
// by itself (it unloads again, or the tail is past the unit's gear), so a shorter one is not a failure.
const EJECT_ERROR_S = 5;
// The number of the newest eject all run: an older run that still waits on its eject sends no more.
let ejectRun = 0;

// The run lives in the page: a reload ends it, while the unit still finishes the eject of the bay it was on. As the
// page goes away (pagehide) with a run on, it notes that bay in the tab's session storage, and the page loaded next
// in this tab says where the run stopped. The note is written only then: a tab duplicated during a run copies the
// session storage without it, and Stop the run, the end of a run or the demo leave none. A note older than
// RUN_NOTE_S is no reload's.
const RUN_KEY = 'ace2k.eject_run';
const RUN_NOTE_S = 30;

// pagehide: notes the bay of the run that is on (none in the demo).
/** @param {Ctx} ctx */
export function leaveEjectRun(ctx) {
  const run = ctx.app.print.eject;
  if (ctx.demo || run?.phase !== 'run') return;
  try {
    ctx.session?.setItem(RUN_KEY, JSON.stringify({ key: run.key, at: ctx.now() }));
  } catch {
    /* Private mode blocks storage. A reload then ends the run without a word. */
  }
}

// The page came back from the browser's cache with its run still on: the note is no reload's.
/** @param {Ctx} ctx */
export function dropEjectNote(ctx) {
  try {
    ctx.session?.removeItem(RUN_KEY);
  } catch {
    /* Nothing was kept. */
  }
}

// The note left by leaveEjectRun, taken (read and removed), or null.
/** @param {Ctx} ctx @returns {{ key: string, at: number } | null} */
function takeNote(ctx) {
  try {
    const raw = ctx.session?.getItem(RUN_KEY);
    ctx.session?.removeItem(RUN_KEY);
    const note = raw ? JSON.parse(raw) : null;
    return typeof note?.key === 'string' && typeof note.at === 'number' ? note : null;
  } catch {
    return null;
  }
}

// At the page's start: when this tab was reloaded during a run, a toast and the events say at which bay it
// stopped. Said once. The demo only drops the note.
/** @param {Ctx} ctx */
export function lostEjectRun(ctx) {
  const note = takeNote(ctx);
  const now = ctx.now();
  if (ctx.demo || !note || now - note.at > RUN_NOTE_S || now < note.at) return;
  const msg = ctx.t('eject_all.failed', { key: note.key, msg: ctx.t('eject_all.reloaded') });
  ctx.toast(msg);
  ctx.app.events = appendEvent(ctx.app.events, { t: now, k: 'e', lvl: 'warn', src: 'unit', msg });
}

// The host's lines about the eject of a bay, after its key: a failure ("eject failed: <why>", or "eject not
// started — <why>" when the unit's unload could not start), and the end of an eject whose tail is past the unit's
// gear, at its bay ("… at its bay: take it out by hand"), which the operator pulls out.
const FAIL_MARKS = [' eject failed: ', ' eject not started — '];
const TAIL_MARK = 'at its bay: take it out by hand';
/** @param {string} s */
const escapeRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

// The host's last word on the eject of the bay key since it was sent: { failed: <why> }, { tail: true }, or null.
// A line counts when the page had not got it at the send (before holds the lines it had) and it is not older than
// start. Its time is in whole seconds, so the time alone would take a line of the second before the send.
/**
 * @param {import('./types.js').AppState} app @param {string} key @param {EjectWatch} watch
 * @returns {{ failed: string } | { tail: true } | null}
 */
function hostLine(app, key, { start, before }) {
  const lane = new RegExp(`\\blane ${escapeRe(key)}(?=[ :])`);
  let found = /** @type {{ failed: string } | { tail: true } | null} */ (null);
  for (const e of app.events) {
    const msg = String(e.msg);
    const m = e.t >= Math.floor(start) && !before.has(e) ? lane.exec(msg) : null;
    if (!m) continue;
    const rest = msg.slice(m.index + m[0].length);
    const mark = FAIL_MARKS.find((k) => rest.startsWith(k));
    if (mark) return { failed: rest.slice(mark.length) };
    if (msg.includes(TAIL_MARK)) found = { tail: true };
  }
  return found;
}

/**
 * @typedef {{ start: number, before: Set<import('./types.js').Event>, errSince: number | null, tail: boolean }}
 *   EjectWatch
 */

// One look at the bay key while its eject runs: true once the bay reads empty and its lane idle. It throws, with the
// reason, when the host says the eject failed or did not start, when the lane stays in error EJECT_ERROR_S, when the
// bay is gone, or after EJECT_WAIT_S. Once the host says the tail waits at the bay for the operator, there is no time
// limit: the run waits for the bay to read empty. watch keeps when the eject was sent and the lines the page had then
// (the eject's own lines can come before the command returns), since when its lane reads in error, and whether its
// tail waits at the bay.
/** @param {ActionCtx} ctx @param {string} key @param {EjectWatch} watch */
function ejectEnded(ctx, key, watch) {
  const at = laneAt(ctx.app, key);
  if (!at) throw new Error(ctx.t('reason.unavailable'));
  const said = hostLine(ctx.app, key, watch);
  if (said && 'failed' in said) throw new Error(said.failed);
  watch.tail = Boolean(said);
  const lane = laneOf(at.slice, at.i);
  const now = ctx.now();
  watch.errSince = laneErrs(lane) ? (watch.errSince ?? now) : null;
  if (watch.errSince !== null && now - watch.errSince >= EJECT_ERROR_S) throw new Error(chipText(lane, 'error'));
  if (lane?.insert === false && (lane.mode ?? 'idle') === 'idle') return true;
  if (!watch.tail && now - watch.start >= EJECT_WAIT_S)
    throw new Error(ctx.t('eject_all.timeout', { time: fmtHM(EJECT_WAIT_S) }));
  return false;
}

// Waits on the live status until the eject of the bay key is over (ejectEnded). While its tail waits at the bay, the
// card says to take it out by hand. It also returns once the run is no longer the page's (alive false): the run then
// ends, stale.
/**
 * @param {ActionCtx} ctx @param {string} key @param {Pick<EjectWatch, 'start' | 'before'>} sent
 * @param {() => boolean} alive
 */
async function ejectOver(ctx, key, sent, alive) {
  /** @type {EjectWatch} */
  const watch = { ...sent, errSince: null, tail: false };
  while (alive() && !ejectEnded(ctx, key, watch)) {
    if (watch.tail && !(ctx.app.print.eject?.phase === 'run' && ctx.app.print.eject.tail)) {
      ctx.app.print = { ...ctx.app.print, eject: { phase: 'run', key, tail: true } };
      ctx.render();
    }
    await ctx.sleep(EJECT_POLL_MS);
  }
}

// Eject all out of place: every bay the steps take a known spool out of, one at a time. Each ACEK_EJECT waits for
// the host's decision (WAIT=1), then the run waits until that bay reads empty before the next: the bay being
// ejected shows for the whole eject. It stops at the first refusal or failure with the bay and the reason. A spool
// that goes to another bay is held once its eject is over, so its step becomes "put back". Go runs only the list
// the dialog showed (keys) and never during a print. A second run while one is on does nothing (runEjectAll).
// The run belongs to its file: once another file is chosen (or the guide starts over), it sends no more and writes
// nothing.
/** @param {ActionCtx} ctx @param {string | undefined} [shown] */
export async function ejectAll(ctx, shown) {
  const { app } = ctx;
  const g = guideOf(app);
  if (!g || app.print.eject?.phase !== 'ask') return;
  if (printing(app.status)) {
    app.print = { ...app.print, eject: null };
    ctx.toast(ctx.t('eject_all.printing'));
    ctx.render();
    return;
  }
  const keys = ejectList(g.steps, g.stays);
  // The bays changed while the dialog was open: nothing is sent, and the dialog shows the new list.
  if (shown !== keys.join(',')) {
    ctx.toast(ctx.t('eject_all.changed'));
    ctx.render();
    return;
  }
  const moved = new Map(g.steps.flatMap((s) => (s.kind === 'move' && s.spool ? [[s.from, s.spool]] : [])));
  const file = app.print.file;
  ejectRun += 1;
  const id = ejectRun;
  // The newest run only, on its file, while the page still shows a run.
  /** @returns {boolean} */
  const alive = () => id === ejectRun && app.print.file === file && app.print.eject?.phase === 'run';
  /** @param {string} key */
  const eject = async (key) => {
    const at = laneAt(app, key);
    /** @type {string | null} */
    let msg = null;
    /** @param {string} m */
    const quiet = (m) => {
      msg = m;
      return false; // The refusal still shows in a toast and in the events.
    };
    // The keys come from the live bays, so a bay is found; one that went meanwhile is refused as unavailable.
    const opts = { unit: at?.unit, key: `eject:${key}`, quiet };
    // The host can say the eject failed before WAIT=1 returns: its lines count from the send.
    const sent = { start: ctx.now(), before: new Set(app.events) };
    const ok = at !== null && (await ctx.send('eject', at.i + 1, { wait: true }, opts));
    if (!ok) throw new Error(msg ?? ctx.t('reason.unavailable'));
    if (!ctx.demo) await ejectOver(ctx, key, sent, alive); // The demo only shows the script.
  };
  // The run is the page's from here: alive reads this phase. on is the bay it is on.
  app.print = { ...app.print, eject: { phase: 'run', key: keys[0] ?? '' } };
  let on = keys[0] ?? '';
  const res = await runEjectAll(
    keys,
    eject,
    (key, state) => {
      if (state === 'running') {
        app.print = { ...app.print, eject: { phase: 'run', key } };
        on = key;
      } else if (!alive()) return;
      const spool = state === 'done' ? moved.get(key) : undefined;
      if (spool) app.print = { ...app.print, held: [...(app.print.held ?? []), { ...spool, from: key }] };
      ctx.render();
    },
    alive,
  );
  if (res === null) {
    // The run of the file chosen before still waits on its eject: nothing is sent; the dialog stays, and the toast says
    // to press Eject again.
    app.print = { ...app.print, eject: { phase: 'ask' } };
    ctx.toast(ctx.t('eject_all.busy'));
    ctx.render();
    return;
  }
  if (res.stale || !alive()) {
    // Stop the run says where it stopped on the card. Another file or a guide that started over ends the run too,
    // at the bay it was on, whose own eject the unit finishes.
    if (app.print.eject?.phase !== 'failed') ctx.toast(ctx.t('eject_all.left', { key: on }));
    return;
  }
  app.print = { ...app.print, eject: res.failed ? { phase: 'failed', ...res.failed } : null };
  ctx.render();
}
