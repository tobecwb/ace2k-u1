// Helpers that read the page state. Each takes app, or the parts it reads, and returns a value.
// endReading (a status step) is the one that writes app; printClock keeps the shown share in module state.
// They have no effects, so they run under node.
import { LANES, unitName, unitsOf, several, sliceFor, laneOf, laneErrs } from './model.js';
import { planFor, liveLanes, chosen, choiceFor, headOfIn, userBays } from './print-plan.js';
import { placement, userPick } from './planner.js';
import { changesOf, printStopsOf, runStops, swapStops, timeKnown, timesOf } from './swap-time.js';
import { segmentsOf } from './timeline.js';
import { dryerView } from './dryer-form.js';
import { cycleProgress } from './history.js';
import { clockOf, frameOf, hasBytes, shareOf } from './clock.js';
import { isNum } from './format.js';

/** @typedef {import('./types.js').AppState} AppState */
/** @typedef {import('./types.js').Selection} Selection */
/** @typedef {import('./types.js').Progress} Progress */
/** @typedef {import('./types.js').DryerView} DryerView */
/** @typedef {import('./types.js').SampleCache} SampleCache */
/** @typedef {import('./types.js').TitleView} TitleView */
/** @typedef {{ d: DryerView, progress: Progress }} DryerOf  A unit's dryer and its running cycle. */

const DEFAULT_KEEP_DAYS = 7;

// The units by name, in printer.cfg order, with null for the unnamed [ace2k].
// The first is the page's main unit.
/** @param {AppState} app @returns {(string | null)[]} */
export function unitNames(app) {
  const listed = unitsOf(app.status).map(unitName);
  return listed.length ? listed : [unitName(app.conn?.unit ?? 'ace2k')];
}

// The unit chosen on the page while it still exists, else the first unit.
/** @param {AppState} app @param {string | null} u @returns {string | null} */
export const known = (app, u) => (unitNames(app).includes(u) ? u : unitNames(app)[0]);

// The unit objects that the status carries. Each is sampled and compared on its own.
/** @param {AppState} app @returns {string[]} */
export const unitObjects = (app) => (unitsOf(app.status).length ? unitsOf(app.status) : (app.conn?.units ?? ['ace2k']));

// Load into head N has one pending key for the whole page, because the U1 runs one head load at a time.
// While it is held, every lane's Load into head N is busy, on every unit.
export const LOAD_HEAD_KEY = 'load_head';
// The pending keys that belong to the whole page, not to one unit or lane. Each is also its action's id.
export const PAGE_WIDE_KEYS = new Set([LOAD_HEAD_KEY]);

// A pending request's key. With several units, the unit's name goes in front.
// So lane 2 of one unit and lane 2 of another are never busy together.
// scoped(app, u) returns the keys of one unit, as the views name them.
/** @param {AppState} app @param {string | null | undefined} unit @param {string} key */
export const pkey = (app, unit, key) => (several(app.status) ? `${unit}|${key}` : key);

/** @param {AppState} app @param {string | null} unit @returns {Set<string>} */
export function scoped(app, unit) {
  if (!several(app.status)) return app.pending;
  const pfx = `${unit}|`;
  // A page-wide key belongs to every unit, so every unit's view sees it.
  return new Set(
    [...app.pending].flatMap((k) => (k.startsWith(pfx) ? [k.slice(pfx.length)] : PAGE_WIDE_KEYS.has(k) ? [k] : [])),
  );
}

// The units' names, or [null] for a status that lists none (the unnamed [ace2k]).
/** @param {import('./types.js').Status} s @returns {(string | null)[]} */
const names = (s) => {
  const units = unitsOf(s).map(unitName);
  return units.length ? units : [null];
};

// The lane shown first. It is the first lane in error of any unit (laneErrs, the page's one rule), else the first
// unit's lane 1.
/** @param {import('./types.js').Status} s @returns {Selection} */
export function defaultSelection(s) {
  for (const unit of names(s)) {
    const slice = sliceFor(s, unit);
    for (let i = 0; i < LANES; i++) if (laneErrs(laneOf(slice, i))) return { unit, lane: i };
  }
  return { unit: names(s)[0], lane: 0 };
}

// The lane shown. It is the chosen lane while its unit still exists, else the default.
/** @param {AppState} app @returns {Selection} */
export function selection(app) {
  const sel = app.selected;
  return sel && unitNames(app).includes(sel.unit) ? sel : defaultSelection(app.status);
}

// A unit's samples. A sample without u belongs to the first unit.
// The caller owns cache ({ src, by }), and it is kept until app.samples is replaced.
/** @param {AppState} app @param {string | null} unit @param {import('./types.js').SampleCache} cache */
export function samplesOf(app, unit, cache) {
  if (!several(app.status)) return app.samples;
  if (cache.src !== app.samples) {
    cache.src = app.samples;
    cache.by = new Map();
  }
  if (!cache.by.has(unit)) {
    const first = unitNames(app)[0];
    cache.by.set(
      unit,
      app.samples.filter((x) => (x.u ?? first) === unit),
    );
  }
  return /** @type {import('./types.js').Sample[]} */ (cache.by.get(unit)); // It was set just above.
}

// A unit's dryer and its running cycle. cache is samplesOf's cache.
/**
 * @param {AppState} app
 * @param {string | null} unit
 * @param {number} nowS
 * @param {SampleCache} cache
 * @returns {DryerOf}
 */
export function dryerOf(app, unit, nowS, cache) {
  const d = dryerView(sliceFor(app.status, unit));
  return { d, progress: cycleProgress(samplesOf(app, unit, cache), nowS, d.remaining) };
}

// The tab's title, as a catalogue key and its values. names lists the units (state.js unitNames). With one
// named unit the title carries its name. With the unnamed [ace2k], or with several units, it carries none.
/** @param {(string | null)[]} names @returns {TitleView} */
export const titleView = (names) => {
  const name = names.length === 1 ? names[0] : null;
  return name ? { key: 'app.title_unit', vars: { name } } : { key: 'app.title', vars: {} };
};

/** @param {AppState} app @returns {number} */
export function keepDays(app) {
  const k = app.status.ace2k_u1_history?.keep_days;
  return Number.isInteger(k) && /** @type {number} */ (k) > 0 ? /** @type {number} */ (k) : DEFAULT_KEEP_DAYS;
}

// True when "Show more" can fetch an older listed day, one that is not loaded yet or whose load failed.
/** @param {AppState} app @param {boolean} demo */
export const olderDays = (app, demo) => !demo && app.days.slice(0, keepDays(app)).some((n) => !app.loaded.has(n));

// True when the adapter is reachable and Klipper is ready.
/** @param {AppState} app */
export const ready = (app) => app.online && app.klippy === 'ready';

// The plan that the adapter reports. shownPlan narrows it to the file chosen in the Print panel.
/** @param {AppState} app @returns {import('./types.js').Plan | null} */
export const livePlan = (app) => app.status.ace2k_u1?.plan ?? null;
/** @param {AppState} app */
export const shownPlan = (app) => planFor(livePlan(app), app.print.file);

// The heads (0-based) that swap in the background: the heads the adapter's background status lists (a head not in
// background_heads is null there). None while the adapter publishes no background status.
/** @param {import('./types.js').Status} status @returns {number[]} */
export function bgHeads(status) {
  const list = status.ace2k_u1?.background;
  return Array.isArray(list) ? list.flatMap((entry, h) => (entry ? [h] : [])) : [];
}

// What the running print's Now and Next cards read of the swaps. free: the heads of bgHeads whose swap did not stop
// (a stopped one goes on at its T, and that stops the print). waiting: the heads whose background swap still runs (a T
// of that head waits for it). swapping: the heads whose lane changes now (the adapter's swapping: a swap at a T, its
// end after a background swap, or a background swap that runs or waits for its T).
/** @param {import('./types.js').Status} status @returns {{ free: number[], waiting: number[], swapping: number[] }} */
export function liveBg(status) {
  const list = status.ace2k_u1?.background;
  const entries = Array.isArray(list) ? list : [];
  /** @param {(state: string) => boolean} test */
  const where = (test) => entries.flatMap((entry, h) => (entry && test(entry.state) ? [h] : []));
  return {
    free: where((state) => state !== 'stopped'),
    waiting: where((state) => state === 'running'),
    swapping: Array.isArray(status.ace2k_u1?.swapping) ? status.ace2k_u1.swapping : [],
  };
}

// The reading of the chosen file: set by planFile, and over once the live plan names that file. Null otherwise.
/** @param {AppState} app @returns {import('./types.js').Reading | null} */
export function readingOf(app) {
  const r = app.print.reading;
  if (!r || r.file !== app.print.file) return null;
  return livePlan(app)?.file === r.file ? null : r;
}

// Ends the reading once the live plan names its file: called on each status (live.js ingestStatus) and when the demo
// sets its plan. A reading left after its plan came would show again ("Still reading…") as soon as the live plan
// names another file, in place of the other-device notice.
/** @param {AppState} app */
export function endReading(app) {
  const r = app.print.reading;
  if (r && livePlan(app)?.file === r.file) app.print = { ...app.print, reading: null };
}

// The file another device planned: the live plan names another file than the one this page shows, while this page
// is not reading its own (its own click in flight, or read and refused). Null otherwise.
/** @param {AppState} app @returns {string | null} */
export function otherDevice(app) {
  const live = livePlan(app)?.file;
  const mine = app.print.file;
  if (!mine || !live || live === mine) return null;
  if (readingOf(app) || app.pending.has('print:file')) return null;
  return live;
}

// The frame of a file, one helper for the print view and the plan view: its card's G-code bytes, else the running
// file's size (virtual_sdcard.file_size), else its plan's size.
/** @param {AppState} app @param {string} file @returns {import('./timeline.js').Frame} */
export function fileFrame(app, file) {
  const plan = livePlan(app);
  const running = app.status.print_stats?.filename === file;
  return frameOf(
    app.print.cards?.[file]?.card,
    running ? app.status.virtual_sdcard?.file_size : null,
    plan?.file === file ? plan.size : null,
  );
}

// The print states that end a print: its shares are forgotten (forgetEndedPrint).
const ENDED = ['complete', 'cancelled', 'error', 'standby'];

// The largest share shown of the running print over its G-code bytes, kept by printClock so the page never jumps: a
// status without the file position keeps it, and a share is never shown lower than one already kept. Only a share
// counted over the card's G-code bytes is kept: the one counted before them (Klipper's whole-file progress, header
// bytes included) is shown but dropped, so the frame replaces it, and while the card is read again the kept share
// holds. It belongs to one print: its file and its time printed, which never goes back (a new print of the same
// file starts from 0). The status ingest forgets it once the print ends.
/** @type {{ file: string, done: number, p: number } | null} */
let shown = null;

// Called on each status (live.js ingestStatus): the shown share goes once the print has ended.
/** @param {import('./types.js').Status} status */
export function forgetEndedPrint(status) {
  if (ENDED.includes(status?.print_stats?.state ?? '')) shown = null;
}

// The share shown: the fresh one, never lower than the kept one. A card without the G-code bytes (read again, or
// metadata that lacks them) gives the whole-file share: while the print runs the kept share holds over it. A print
// that completed shows 1.
/** @param {number | null} fresh @param {number | null} kept @param {boolean} framed @param {string} state */
function shownShare(fresh, kept, framed, state) {
  if (fresh === null) return null;
  if (state === 'complete') return 1;
  if (kept !== null && !framed && !ENDED.includes(state)) return kept;
  return Math.max(fresh, kept ?? 0);
}

// How far, in bytes, the file position may be past a change's T line for a stop that runs now to be that change's:
// the U1 moves the position past a line only once the line has run, so during the T it sits at the T line itself.
const NEAR_BYTES = 256;

// The last printStops worked out, with what it was worked out from: one status, one share and one time printed give
// one set of stops, however many readers ask (the top bar, the Print tab).
/** @type {{ from: unknown[], stops: import('./clock.js').Stops | undefined } | null} */
let lastStops = null;

// The swap stops of the running print for its clock, or undefined (no stops) without its plan with a choice and
// changes, without a slicer time, or with no share known: the estimate of each change's stop on the plan's choice
// (swapStops), with the passed ones measured by the adapter when it publishes them (runStops).
/**
 * @param {AppState} app @param {string} file @param {import('./timeline.js').Frame} frame
 * @param {number | null | undefined} timeS @param {number | null} p @param {number} done
 * @returns {import('./clock.js').Stops | undefined}
 */
function printStops(app, file, frame, timeS, p, done) {
  const from = [app.status, app.print.cards, file, frame?.start, frame?.end, timeS, p, done];
  if (lastStops?.from.every((v, i) => v === from[i])) return lastStops.stops;
  const stops = workStops(app, file, frame, timeS, p, done);
  lastStops = { from, stops };
  return stops;
}

/**
 * @param {AppState} app @param {string} file @param {import('./timeline.js').Frame} frame
 * @param {number | null | undefined} timeS @param {number | null} p @param {number} done
 * @returns {import('./clock.js').Stops | undefined}
 */
function workStops(app, file, frame, timeS, p, done) {
  const plan = livePlan(app);
  if (!plan || plan.file !== file || !plan.choice || !plan.order?.length || !timeKnown(timeS) || p === null) return;
  const headOf = headOfIn(liveLanes(app.status, plan));
  const est = swapStops(changesOf(plan, frame, timeS), plan.choice, headOf, bgHeads(app.status), timesOf(app.status));
  const xs = segmentsOf(plan, frame).map((g) => g.x);
  const span = frame ? frame.end - frame.start : (plan.size ?? 0);
  const near = span > 0 ? NEAR_BYTES / span : 0;
  return runStops({ xs, stops: est.stops, p, done, near, measured: printStopsOf(app.status) });
}

// The running print's clock (clock.js), one for the whole page (the Print tab, the top bar's %), and its frame. It
// counts the swap stops (printStops). It does not change the page state.
/** @param {AppState} app */
export function printClock(app) {
  const s = app.status;
  const file = s.print_stats?.filename ?? '';
  const state = s.print_stats?.state;
  const done = isNum(s.print_stats?.print_duration) ? /** @type {number} */ (s.print_stats.print_duration) : 0;
  const card = app.print.cards?.[file]?.card ?? null;
  const frame = fileFrame(app, file);
  const mine = shown && shown.file === file && done >= shown.done ? shown : null;
  const vs = s.virtual_sdcard;
  const fresh = shareOf({
    position: vs?.file_position,
    progress: vs?.progress,
    frame,
    cardLoaded: Boolean(card),
    last: mine?.p ?? null,
  });
  const framed = hasBytes(card);
  const p = shownShare(fresh, mine?.p ?? null, framed, state ?? '');
  // A share counted without the bytes (Klipper's whole-file progress, header included: ~2 % at the start, where
  // Fluidd shows 0) is shown but not kept, so the frame's share replaces it, even a lower one.
  shown = p === null || !framed || ENDED.includes(state ?? '') ? mine : { file, done, p };
  const clock = clockOf(state, done, p, card?.time);
  // The stops change only a running clock: a complete, stopped or finishing print skips them.
  if (clock.phase !== 'running') return { file, card, frame, clock };
  const stops = printStops(app, file, frame, card?.time, p, done);
  return { file, card, frame, clock: stops ? clockOf(state, done, p, card?.time, stops) : clock };
}

// The user's merges in the Print panel: a tool to the tool it joins. planFile starts each file with none.
/** @param {import('./types.js').PrintState} print @returns {Record<number, number>} */
export const mergesOf = (print) => print.merges ?? {};

// True when the user kept the file's similar colours apart ("Keep apart"): the hint stays hidden for that file.
/** @param {import('./types.js').PrintState} print @param {string} file */
export const hintKept = (print, file) => (print.dismissed ?? []).includes(file);

/**
 * What the plan view reads. plan is the plan of the chosen file. lanes are its lanes read from the live status.
 * user holds the user's own picks, pick the lane of each tool (the user's, else the set plan's, else the
 * suggestion), merges the user's merges, and place the placement of them all. planLanes are the lanes as the planner
 * read them (a spool held in hand by eject all counts in its bay). fixed lists the colours whose bay the user set in
 * the colour picker (print.overrides): they print from it, and the suggestion works around them. raw is the pick before
 * userPick gives those bays to them (what placement and pickCost are given); pick is after it. bg lists the heads that
 * swap in the background (bgHeads). changes are the file's changes with their print time, times the four swap
 * averages, timeS the slicer's time and frame the file's frame (swap-time.js): what the placement was scored with.
 * @typedef {{ plan: import('./types.js').Plan, lanes: import('./types.js').PlanLane[],
 *   user: import('./print-plan.js').Choice, pick: import('./print-plan.js').Choice, merges: Record<number, number>,
 *   place: import('./planner.js').Placement | null, planLanes: import('./types.js').PlanLane[],
 *   fixed: number[], raw: import('./print-plan.js').Choice, bg: number[],
 *   changes: import('./swap-time.js').Change[], times: import('./swap-time.js').SwapTimes, timeS: number | null,
 *   frame: import('./timeline.js').Frame }} PlanNow
 */

// The last planNow of each status, with the parts of the Print state it was worked out from.
/** @type {WeakMap<object, { from: unknown[], now: PlanNow | null }>} */
const nowOf = new WeakMap();

// The plan view's inputs, or null while no file is chosen or its plan has not come. A pick or a merge shows at
// once: the placement is worked out again whenever the status or a part of the Print state it reads changed. The
// status and those parts are replaced, never changed in place, so one render (and one status) works it out once
// for all its readers.
/** @param {AppState} app @returns {PlanNow | null} */
export function planNow(app) {
  const p = app.print;
  const from = [p.file, p.choice, p.choiceId, p.choiceTools, p.merges, p.pinned, p.held, p.overrides, p.cards];
  const hit = nowOf.get(app.status);
  if (hit?.from.every((v, i) => v === from[i])) return hit.now;
  const now = workPlanNow(app);
  nowOf.set(app.status, { from, now });
  return now;
}

/** @param {AppState} app @returns {PlanNow | null} */
function workPlanNow(app) {
  const plan = app.print.file ? shownPlan(app) : null;
  if (!plan) return null;
  const lanes = liveLanes(app.status, plan);
  const user = choiceFor(app.print, plan);
  const fixed = userBays(user, app.print.overrides);
  const raw = chosen(plan, user);
  const pick = userPick(plan, raw, fixed);
  const merges = mergesOf(app.print);
  // planLanes: the lanes as the planner read them (spools held in hand by eject all count in their bays).
  const planLanes = withHeld(lanes, app.print.held);
  const bg = bgHeads(app.status);
  const card = app.print.cards?.[plan.file]?.card ?? null;
  const frame = fileFrame(app, plan.file);
  const timeS = typeof card?.time === 'number' ? card.time : null;
  const times = timesOf(app.status);
  return {
    plan,
    lanes,
    user,
    raw,
    pick,
    merges,
    planLanes,
    fixed,
    bg,
    changes: changesOf(plan, frame, timeS),
    times,
    timeS,
    frame,
    place: placement(plan, raw, planLanes, merges, fixed, bg, { frame, timeS, times }),
  };
}

// The lanes as the planner reads them: an empty bay whose spool "eject all" took out (it is held, in hand) still
// counts as holding that spool, so the planner keeps that spool's move and the guide asks to put it back. The guide
// itself (stepsOf, stepDone) reads the live lanes.
/** @param {import('./types.js').PlanLane[]} lanes @param {import('./types.js').PlanSpool[] | undefined} held */
function withHeld(lanes, held) {
  if (!held?.length) return lanes;
  return lanes.map((l) => {
    const h = l.empty ? held.find((x) => x.from === l.key) : undefined;
    return h ? { ...l, empty: false, unknown: false, material: h.material, colour: h.colour, spool: h } : l;
  });
}

// The series that the user hid, read from the stored JSON text. null means that nothing is stored.
// Anything unreadable counts as none.
/** @param {string | null | undefined} stored @returns {string[]} */
export function readHidden(stored) {
  try {
    const v = JSON.parse(stored || '[]');
    return Array.isArray(v) ? v : [];
  } catch {
    return [];
  }
}

// The theme that the page shows. It is the system theme, or one the user chose with the button in the top bar.
/** @typedef {'system' | 'light' | 'dark'} Theme */
const THEMES = /** @type {Theme[]} */ (['system', 'light', 'dark']);
export const THEME_KEY = 'ace2k.theme';

// The stored choice, 'light' or 'dark'. Anything else, or nothing, means the system theme.
/** @param {string | null | undefined} stored @returns {Theme} */
export const readTheme = (stored) => (stored === 'light' || stored === 'dark' ? stored : 'system');

// The next mode of the button: system, light, dark, and round again.
/** @param {string} theme @returns {Theme} */
export const nextTheme = (theme) => THEMES[(THEMES.indexOf(/** @type {Theme} */ (theme)) + 1) % THEMES.length];

// The choice kept in storage. With no storage, or a browser that blocks it, the page follows the system.
/** @param {Pick<Storage, 'getItem'> | null} storage @returns {Theme} */
export function loadTheme(storage) {
  try {
    return readTheme(storage?.getItem(THEME_KEY));
  } catch {
    return 'system';
  }
}

// Keeps the choice. The system theme is stored as no choice at all. A blocked storage keeps nothing.
/** @param {Pick<Storage, 'setItem' | 'removeItem'> | null} storage @param {Theme} theme */
export function storeTheme(storage, theme) {
  try {
    if (theme === 'system') storage?.removeItem(THEME_KEY);
    else storage?.setItem(THEME_KEY, theme);
  } catch {
    /* Private mode blocks storage. The choice lasts until the page is reloaded. */
  }
}

// The browser storage that read() returns, or null when the browser blocks it: even reading
// localStorage or sessionStorage can throw.
/** @param {() => Storage} read @returns {Storage | null} */
export function storageOr(read) {
  try {
    return read();
  } catch {
    return null;
  }
}

// The Print panel when the page starts. files lists the G-code files, newest first. choice holds the lanes that
// the page changed, as {tool: lane key}. choiceId names the plan that choice was made for.
/** @returns {import('./types.js').PrintState} */
export const initialPrint = () => ({
  open: false,
  files: [],
  file: null,
  choice: {},
  choiceId: null,
  choiceTools: null,
});

// The page's tabs, in the order the top bar shows them. The first is the default.
/** @typedef {'units' | 'print' | 'dryer' | 'history'} Tab */
export const TABS = /** @type {Tab[]} */ (['units', 'print', 'dryer', 'history']);
const TAB_KEY = 'ace2k.tab';

// The tab named, or the first tab for anything that is not one.
/** @param {string | null | undefined} name @returns {Tab} */
export const tabOf = (name) => TABS.find((tab) => tab === name) ?? TABS[0];

// The tab stored at the last visit. With no storage, or a browser that blocks it, the first tab.
/** @param {Pick<Storage, 'getItem'> | null} storage @returns {Tab} */
export function loadTab(storage) {
  try {
    return tabOf(storage?.getItem(TAB_KEY));
  } catch {
    return TABS[0];
  }
}

// Keeps the tab for the next visit. A blocked storage keeps nothing.
/** @param {Pick<Storage, 'setItem'> | null} storage @param {Tab} tab */
export function storeTab(storage, tab) {
  try {
    storage?.setItem(TAB_KEY, tab);
  } catch {
    /* Private mode blocks storage. The tab lasts until the page is reloaded. */
  }
}
