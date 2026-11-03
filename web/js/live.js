// The live history. A status frame, a reconnect and the demo files each change the samples, events and snapshots.
// These functions change app, the page state. They take the clock as a parameter, so they run under node.
import {
  appendEvent,
  appendSample,
  dryerLogLines,
  gcodeEvents,
  liveSample,
  parseJsonl,
  snapshot,
  transitionEvents,
} from './history.js';
import { unitName } from './model.js';
import { unitNames, unitObjects, forgetEndedPrint, endReading } from './state.js';

/** @typedef {import('./types.js').AppState} AppState */
/** @typedef {import('./types.js').Status} Status */

// Every unit is sampled and compared on its own.
// With several units, each sample and event names its unit, as the recorder does.
// The first unit's lanes and dryer are the ones that the page files apart.
/** @param {AppState} app @param {Status} status @param {number} nowS */
export function ingestStatus(app, status, nowS) {
  app.status = status; // The views read one unit at a time through sliceFor.
  forgetEndedPrint(status); // A print that ended forgets the share its clock showed.
  endReading(app); // A reading whose plan came is over.
  const objs = unitObjects(app);
  objs.forEach((obj, k) => {
    // Each unit is a key of its own in the status.
    const unit = /** @type {import('./types.js').UnitMap} */ (/** @type {unknown} */ (status))[obj];
    if (!unit) return;
    const name = objs.length > 1 ? unitName(obj) : null;
    app.samples = appendSample(app.samples, liveSample(unit, nowS, name));
    for (const ev of transitionEvents(app.snaps[obj] ?? null, unit, nowS, name, k === 0))
      app.events = appendEvent(app.events, ev);
    app.snaps[obj] = snapshot(unit, app.snaps[obj] ?? null);
  });
  // A dryer log of a unit that left the list goes with it.
  if (app.dryerLog && !unitNames(app).includes(app.dryerLog.unit)) app.dryerLog = null;
}

// After a connect or a reconnect, the day files are the history. So the history held is dropped.
// gen moves on, so a day load still in flight drops its result. Live points start again from here.
/** @param {AppState} app @param {number} nowS */
export function resetHistory(app, nowS) {
  app.gen += 1;
  app.days = [];
  app.daysLoaded = 0;
  app.loaded = new Set();
  const objs = unitObjects(app);
  // Each unit is a key of its own in the status.
  const byObj = /** @type {import('./types.js').UnitMap} */ (/** @type {unknown} */ (app.status));
  app.samples = objs
    .filter((o) => byObj[o])
    .map((o) => liveSample(byObj[o], nowS, objs.length > 1 ? unitName(o) : null));
  app.events = [];
  app.snaps = {};
}

// The committed history of the demo would age. So every record is shifted, and the newest one is "now".
/** @param {import('./types.js').Day} day @param {number} nowS @returns {import('./types.js').Day} */
export function shiftDay(day, nowS) {
  const newest = Math.max(...day.samples.map((s) => s.t), ...day.events.map((e) => e.t));
  const shift = Math.floor(nowS) - newest;
  return {
    samples: day.samples.map((s) => ({ ...s, t: s.t + shift })),
    events: day.events.map((e) => ({ ...e, t: e.t + shift })),
  };
}

// With ?demo, the page reads its status and history from the demo files in dev/.
// A release has no dev/ folder. So when a file is missing, the page stays offline,
// and its banner says that the demo data is not installed (klippy 'demo_missing').
// Returns whether the demo loaded.
// name picks the scenario: dev/demo-<name>.json. Without a name, or with one that is not [a-z-]+, it is dev/demo-status.json.
/** @param {typeof fetch} fetchFn @param {AppState} app @param {number} nowS @param {string | null} [name] */
export async function loadDemo(fetchFn, app, nowS, name = null) {
  const file = name && /^[a-z-]+$/.test(name) ? `dev/demo-${name}.json` : 'dev/demo-status.json';
  try {
    /** @param {string} path */
    const get = async (path) => {
      const res = await fetchFn(path);
      if (!res.ok) throw new Error(`${path}: ${res.status}`);
      return res;
    };
    const [statusRes, historyRes] = await Promise.all([get(file), get('dev/demo-history.jsonl')]);
    const [status, history] = await Promise.all([statusRes.json(), historyRes.text()]);
    ({ samples: app.samples, events: app.events } = shiftDay(parseJsonl(history), nowS));
    app.online = true;
    app.klippy = 'ready';
    app.status = status;
    return true;
  } catch {
    app.online = false;
    app.klippy = 'demo_missing';
    return false;
  }
}

// A console line from the printer. Its events are named for the connection's unit.
// While the page waits for a dryer log, the reply of its unit fills it.
/** @param {AppState} app @param {string} line @param {number} nowS */
export function ingestGcode(app, line, nowS) {
  for (const ev of gcodeEvents(line, nowS, unitName(app.conn?.unit), unitNames(app)))
    app.events = appendEvent(app.events, ev);
  const log = app.dryerLog;
  const lines = log && log.lines === null ? dryerLogLines(line, log.unit, unitNames(app)) : null;
  if (log && lines) app.dryerLog = { ...log, lines };
}

// The state of the link changed, and the page keeps the new state.
// When Klipper is ready, the history starts over from the files.
// Then it returns true, so the caller lists the days.
/** @param {AppState} app @param {import('./conn.js').LinkState} st @param {number} nowS */
export function connectionChange(app, st, nowS) {
  app.dryerLog = null; // A read in flight, or a log shown, is dropped: the reply would not come, or is old.
  app.online = st.online;
  app.klippy = st.online ? st.klippy : null;
  app.linkMessage = st.online ? st.message : undefined;
  if (!(st.online && st.klippy === 'ready')) return false;
  resetHistory(app, nowS);
  return true;
}
