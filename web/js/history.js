// The page's history. It reads the recorder's day files, and builds the same samples and events live.
import { historyDays, isNum } from './format.js';
import { isRunning, noDryerState } from './dryer-form.js';

/** @typedef {import('./types.js').Sample} Sample */
/** @typedef {import('./types.js').Event} Event */
/** @typedef {import('./types.js').Day} Day */
/** @typedef {import('./types.js').Snapshot} Snapshot */
/** @typedef {import('./types.js').LaneSnapshot} LaneSnapshot */
/** @typedef {import('./types.js').Unit} Unit */
/** @typedef {import('./types.js').Lane} Lane */
// The classification words are the same as in the recorder, klippy/extras/ace2k_u1_history.py.
// The shared fixture web/tests/fixtures/event-lines.json keeps both in step.
// PREFIX_RE matches the lines of ace2k ("ace2k:") and of the adapter ("ace2k_u1:").
// With several units, a line can also start with "ace2k <name>:".
// It belongs to a unit only when the name is declared. So "ace2k dryer:" is not the line of a unit.
const PREFIX_RE = /^(?:ace2k|ace2k_u1):/;
// The first line of an ACEK_STATUS answer with several units ("ace2k a: <version>, link proven: yes"):
// a reply, not an event. It is tested on the line in lower case, as unitOf reads the unit. With one unit
// the header is "ace2k <version>, link proven: …", which PREFIX_RE already leaves out.
const STATUS_HEAD_RE = /^ace2k [a-z0-9_-]+: \S+, link proven: (?:yes|no)$/;
/** @param {string} text */
const statusHead = (text) => STATUS_HEAD_RE.test(text.toLowerCase());
const UNIT_RE = /^ace2k ([a-z0-9_-]+):/; // "ace2k <word>:", a unit only when the word is declared
const LANE_RE = /\blane (?:([a-z0-9_-]+):)?(\d)\b/; // "lane 3", or "lane b:3"
const DRYER_RE = /\b(dryer|drying|heater|fans?|flaps?)\b/;
// The kinds of a lane's event that also mark a console line, as words of it. The event kinds that
// ERR_KINDS and WARN_KINDS add below (assist_stall, behind, snag, blocked, tail) are left out on
// purpose: they are common words of plain lines, as in "lane 2 ejected (300 mm back): its tail is
// …" or "lane 3 no longer behind e1's piece", which would read as warnings.
const ERR_LINE_KINDS = [
  'stuck',
  'tangled',
  'motor_stalled',
  'timeout',
  'assist_overrun',
  'unload_incomplete',
  'stopped_sensors',
];
const WARN_LINE_KINDS = ['stopped_link', 'stopped_shutdown', 'runout'];
// The words of a console line that give its level: those kinds, then other words and phrases.
const ERR_RE = new RegExp(
  `\\b(${[...ERR_LINE_KINDS, 'refused', 'fault', 'failed', 'error'].join('|')})\\b|sensors stale`,
);
const WARN_RE = new RegExp(
  `\\b(${[...WARN_LINE_KINDS, 'lowered', 'interrupted', 'notices?'].join('|')})\\b|not written|no answer|no spool set`,
);
const SAMPLE_GAP_S = 30;
const DUP_S = 2;

/** @param {unknown} v @returns {number | null} */
const num = (v) => (isNum(v) ? Math.round(v * 100) / 100 : null);

/** @param {string} text @returns {Day} */
export function parseJsonl(text) {
  /** @type {Sample[]} */
  const samples = [];
  /** @type {Event[]} */
  const events = [];
  for (const line of text.split('\n')) {
    if (!line.trim()) continue;
    let rec;
    try {
      rec = JSON.parse(line);
    } catch {
      continue;
    }
    if (rec?.k === 's') samples.push(rec);
    // A header that an older version recorded as an event is left out too.
    else if (rec?.k === 'e' && !statusHead(String(rec.msg ?? ''))) events.push(rec);
  }
  return { samples, events };
}

// Returns the declared unit that owns the line ("ace2k b: …", in any case), in lower case.
// It returns null when the line names no declared unit.
// With a single unit it always returns null, because then a line belongs to the unit only as "ace2k:".
/** @param {string} text @param {(string | null)[]} [units] @returns {string | null} */
function unitOf(text, units = []) {
  if (units.length < 2) return null;
  const named = UNIT_RE.exec(text.toLowerCase());
  const declared = new Set(units.filter(Boolean).map((u) => String(u).toLowerCase()));
  return named && declared.has(named[1]) ? named[1] : null;
}

// True for a line of ace2k or of the adapter.
// A line of ace2k starts with "ace2k:", or with "ace2k <name>:" for a declared unit.
// The first line of an ACEK_STATUS answer (STATUS_HEAD_RE) is not one.
/** @param {string} text @param {(string | null)[]} [units] */
const isEvent = (text, units = []) => !statusHead(text) && (PREFIX_RE.test(text) || unitOf(text, units) !== null);

// Returns the level and the source of a line.
// unit is the unit's name that the page shows, and null for [ace2k].
// units lists the names that the printer declares, with null for [ace2k].
// A line is filed under a lane or the dryer only when it belongs to the page's unit.
// A unit line belongs by its declared name ("ace2k b:"), and a lane by its unit ("lane b:3").
// A bare lane in "ace2k:" or "ace2k_u1:" belongs to the only unit.
// The line of another unit gets the source 'unit'.
/** @param {string} text @param {boolean} [error] @param {string | null} [unit] @param {(string | null)[]} [units] */
function classify(text, error = false, unit = null, units = []) {
  const low = text.toLowerCase();
  return { lvl: levelOf(low, error), src: sourceOf(text, low, unit, units) };
}

// The level of a line. An error line ("!!") or an error word gives err.
// Else a warning word gives warn, and anything else gives info.
/** @param {string} low @param {boolean} error */
function levelOf(low, error) {
  if (error || ERR_RE.test(low)) return 'err';
  return WARN_RE.test(low) ? 'warn' : 'info';
}

// The source of a line. low is the text in lower case.
// The line of another unit gets the source 'unit'. A lane of the page's unit gets that lane.
// Else a dryer word files the line under the dryer, and anything else goes under 'unit'.
/** @param {string} text @param {string} low @param {string | null} unit @param {(string | null)[]} units */
function sourceOf(text, low, unit, units) {
  const page = unit ? unit.toLowerCase() : null;
  const owned = unitOf(text, units);
  const named = owned !== null;
  // The owner of the line. It is a unit name, or null for the only unit.
  // It is '' when there are several units and the line names none of them.
  const owner = named ? owned : units.length > 1 ? '' : page;
  const lane = LANE_RE.exec(low);
  const laneOwner = lane === null ? null : lane[1] || owner;
  if ((named && owner !== page) || (lane?.[1] && laneOwner !== page)) return 'unit'; // the line of another unit
  if (lane && laneOwner === page) return `lane${lane[2]}`;
  return DRYER_RE.test(low) ? 'dryer' : 'unit';
}

// The lines of a console message, each without its "//" or "!!" mark. error is true for a "!!" line.
/** @param {unknown} raw @returns {{ error: boolean, body: string }[]} */
function consoleLines(raw) {
  return String(raw)
    .split('\n')
    .map((l) => l.trim())
    .map((line) => {
      const error = line.startsWith('!!');
      return { error, body: error || line.startsWith('//') ? line.slice(2).trim() : line };
    });
}

// The counters line of ACEK_DRYER_LOG, as the host starts it (ace2k_units.py head(unit, "dryer")).
// With one unit, named or not, it is "ace2k dryer: …". With several, "ace2k <name>: dryer: …".
const LOG_ONE_RE = /^ace2k dryer: (\d+ cycles? started\b.*)$/;
const LOG_UNIT_RE = /^ace2k ([a-z0-9_-]+): dryer: (\d+ cycles? started\b.*)$/i;

// The reply of ACEK_DRYER_LOG for unit, out of one console message, or null when the message holds none.
// units are the names the printer declares, with null for [ace2k]. With one unit, every reply is its.
// The counters line loses its start, as the panel's title names the unit. The lines after it are the reply's
// notes and its faults or interrupted cycles, up to the next line of ace2k or of the adapter.
/** @param {unknown} raw @param {string | null} unit @param {(string | null)[]} units @returns {string[] | null} */
export function dryerLogLines(raw, unit, units) {
  const bodies = consoleLines(raw)
    .filter((l) => !l.error)
    .map((l) => l.body)
    .filter(Boolean);
  const several = units.length > 1;
  const page = unit?.toLowerCase();
  const re = several ? LOG_UNIT_RE : LOG_ONE_RE;
  let at = -1;
  /** @type {RegExpExecArray | null} */
  let head = null;
  for (const [i, b] of bodies.entries()) {
    const m = re.exec(b);
    if (m !== null && (!several || m[1].toLowerCase() === page)) {
      at = i;
      head = m;
      break;
    }
  }
  if (head === null) return null;
  const rest = bodies.slice(at + 1);
  const end = rest.findIndex((b) => /^ace2k[ _:]/.test(b));
  return [head[several ? 2 : 1], ...(end < 0 ? rest : rest.slice(0, end))];
}

// unit and units are as in classify.
/**
 * @param {unknown} raw
 * @param {number} t
 * @param {string | null | undefined} [unit]
 * @param {(string | null)[]} [units]
 * @returns {Event[]}
 */
export function gcodeEvents(raw, t, unit = null, units = []) {
  /** @type {Event[]} */
  const out = [];
  for (const { error, body } of consoleLines(raw)) {
    if (isEvent(body, units)) {
      const { lvl, src } = classify(body, error, unit ?? null, units);
      out.push({ t: Math.floor(t), k: 'e', lvl, src, msg: body });
    }
  }
  return out;
}

// The levels of a lane's event kinds. The kinds that also mark a console line are listed above.
const ERR_KINDS = [...ERR_LINE_KINDS, 'assist_stall'];
const WARN_KINDS = [...WARN_LINE_KINDS, 'behind', 'snag', 'blocked', 'tail'];
/** @param {unknown} v @returns {v is Record<string, unknown>} */
const isObj = (v) => v !== null && typeof v === 'object' && !Array.isArray(v);

// filament_mm to one decimal with halves rounded up, or null. The recorder does the same arithmetic.
/** @param {unknown} v @returns {string | null} */
const mm = (v) => (isNum(v) ? (Math.floor(v * 10 + 0.5) / 10).toFixed(1) : null);

/** @param {unknown} v @returns {string | null} */
const text = (v) => (typeof v === 'string' && v ? v : null);

/** @param {unknown} l @returns {LaneSnapshot} */
function laneSnapshot(l) {
  // The status is not trusted, so each field below is checked before it is used.
  const lane = /** @type {import('./types.js').Lane} */ (isObj(l) ? l : {});
  const ev = isObj(lane.last_event) ? lane.last_event : null;
  const tag = isObj(lane.tag) ? lane.tag : {};
  const rec = isObj(tag.record) ? tag.record : {};
  return {
    err: lane.mode === 'error' ? lane.error || 'error' : null,
    insert: typeof lane.insert === 'boolean' ? lane.insert : null,
    event: ev && { seq: ev.seq ?? null, kind: ev.kind ?? null, mode: ev.mode ?? null, mm: mm(ev.filament_mm) },
    tag: tag.state ?? null,
    brand: text(rec.brand),
    material: text(rec.material),
    seen: ev && { seq: ev.seq ?? null },
  };
}

// prev is the previous snapshot. When the last_event of a lane is missing this time,
// the lane keeps the last event seen. So a dropout does not replay the event.
/** @param {Unit | null | undefined} status @param {Snapshot | null} [prev] @returns {Snapshot} */
export function snapshot(status, prev = null) {
  const d = status?.dryer || {};
  const lanes = Array.isArray(status?.lanes) ? status.lanes : [];
  const snaps = lanes.map(laneSnapshot);
  if (prev)
    snaps.forEach((s, i) => {
      if (!s.seen && prev.lanes[i]) s.seen = prev.lanes[i].seen;
    });
  return { dryer: d.state ?? null, fault: d.fault ?? null, lanes: snaps };
}

// The changes of one lane between two snapshots, was and now, as [level, message] pairs.
// The words are those of the recorder, and n names the lane as the recorder does.
// The changes cover the spool, a new event, the error and the tag.
/** @param {LaneSnapshot} was @param {LaneSnapshot} now @param {string | number} n @returns {[string, string][]} */
function laneChanges(was, now, n) {
  /** @type {[string, string][]} */
  const out = [];
  if (was.insert !== null && now.insert !== null && was.insert !== now.insert) {
    out.push(['info', `lane ${n} spool ${now.insert ? 'inserted' : 'removed'}`]);
  }
  const e = now.event;
  if (e && (!was.seen || was.seen.seq !== e.seq)) out.push(eventChange(e, n));
  if (was.err !== now.err) {
    out.push(now.err !== null ? ['err', `lane ${n} error: ${now.err}`] : ['info', `lane ${n} error cleared`]);
  }
  if (was.tag !== null && now.tag !== was.tag) out.push(...tagChange(now, n));
  return out;
}

// A new event of a lane. Its kind gives the level. The message holds the mode and the filament it moved.
/** @param {NonNullable<LaneSnapshot['event']>} e @param {string | number} n @returns {[string, string]} */
function eventChange(e, n) {
  const kind = e.kind ?? '?';
  const mode = e.mode ?? '?';
  const lvl = ERR_KINDS.includes(kind) ? 'err' : WARN_KINDS.includes(kind) ? 'warn' : 'info';
  return [lvl, `lane ${n} ${kind} (${e.mm === null ? mode : `${mode}, ${e.mm} mm`})`];
}

// A tag that is now read, with its maker and material, or that is found absent.
// Other changes of the tag add no event.
/** @param {LaneSnapshot} now @param {string | number} n @returns {[string, string][]} */
function tagChange(now, n) {
  if (now.tag === 'read') {
    const who = [now.brand, now.material].filter(Boolean).join(' ');
    return [['info', who ? `lane ${n} tag read: ${who}` : `lane ${n} tag read`]];
  }
  return now.tag === 'no_tag' ? [['info', `lane ${n} no tag`]] : [];
}

// The events between the previous snapshot and the new status.
// With several units, unit names the unit of this status.
// The texts name it as the recorder does ("lane b:3", "ace2k b: dryer …").
// own is true for the first unit, whose lanes and dryer the page files apart.
// The events of the other units get the source 'unit'. With one unit, unit is null and own is true.
/**
 * @param {Snapshot | null} prev
 * @param {Unit} status
 * @param {number} t
 * @param {string | null} [unit]
 * @param {boolean} [own]
 * @returns {Event[]}
 */
export function transitionEvents(prev, status, t, unit = null, own = true) {
  if (!prev) return [];
  const snap = snapshot(status, prev);
  /** @param {string} lvl @param {string} src @param {string} msg */
  const ev = (lvl, src, msg) => ({
    t: Math.floor(t),
    k: 'e',
    lvl,
    src,
    msg,
  });
  /** @type {Event[]} */
  const out = [];
  const label = unit === null ? '' : `ace2k ${unit}: `;
  const dsrc = own ? 'dryer' : 'unit';
  // A state that appears from nothing or vanishes is not a transition. The status may still be filling in.
  if (snap.dryer !== prev.dryer && snap.dryer !== null && prev.dryer !== null) {
    out.push(
      snap.dryer === 'fault'
        ? ev('err', dsrc, `${label}dryer fault: ${snap.fault || '?'}`)
        : ev('info', dsrc, `${label}dryer ${prev.dryer} → ${snap.dryer}`),
    );
  }
  snap.lanes.slice(0, prev.lanes.length).forEach((now, i) => {
    const n = unit === null ? i + 1 : `${unit}:${i + 1}`;
    const src = own ? `lane${i + 1}` : 'unit';
    for (const [lvl, msg] of laneChanges(prev.lanes[i], now, n)) out.push(ev(lvl, src, msg));
  });
  return out;
}

// With several units, unit names the sampled unit. The sample carries it as u, as the recorder's samples do.
/** @param {Unit | null | undefined} status @param {number} t @param {string | null} [unit] @returns {Sample} */
export function liveSample(status, t, unit = null) {
  const d = status?.dryer || {};
  return {
    t: Math.floor(t),
    k: 's',
    ...(unit === null ? {} : { u: unit }),
    ch: num(status?.chamber),
    rh: num(status?.humidity),
    hl: num(status?.ptc_left),
    hr: num(status?.ptc_right),
    dry: d.state ?? null,
    tgt: isRunning(d.state) ? num(d.target) : null,
    rem: num(d.remaining),
  };
}

// Each unit's samples keep their own pace.
// So a new sample is compared with the same unit's last sample (u).
/** @param {Sample[]} samples @param {Sample} sample */
export function appendSample(samples, sample) {
  const u = sample.u ?? null;
  const last = samples.findLast((x) => (x.u ?? null) === u);
  if (last && sample.t - last.t < SAMPLE_GAP_S) return samples;
  return [...samples, sample];
}

/** @param {Event[]} events @param {Event} event */
export function appendEvent(events, event) {
  const dup = events.some((e) => e.msg === event.msg && Math.abs(e.t - event.t) <= DUP_S);
  return dup ? events : [...events, event];
}

// Merges one day file into the history held. Merging the same day twice adds nothing.
// An event is a duplicate under the rule of appendEvent: the same message within DUP_S seconds.
// A set of (message, whole second) keys checks this, so a large day merges in linear time.
/** @param {string} msg @param {number} t */
const eventKey = (msg, t) => `${t}\u0000${msg}`;
/** @param {Day} state @param {Day} day @returns {Day} */
export function mergeDay(state, day) {
  // A sample is keyed by its unit (u) and its second, because two units can sample the same second.
  /** @param {Sample} x */
  const sampleKey = (x) => `${x.u ?? ''}\u0000${x.t}`;
  const seen = new Set(state.samples.map(sampleKey));
  const fresh = day.samples.filter((x) => !seen.has(sampleKey(x)) && seen.add(sampleKey(x)));
  const samples = fresh.length ? [...state.samples, ...fresh].sort((a, b) => a.t - b.t) : state.samples;
  const keys = new Set(state.events.map((e) => eventKey(e.msg, Math.round(e.t))));
  /** @type {Event[]} */
  const added = [];
  for (const e of day.events) {
    const t = Math.round(e.t);
    let dup = false;
    for (let d = -DUP_S; d <= DUP_S && !dup; d++) dup = keys.has(eventKey(e.msg, t + d));
    if (dup) continue;
    keys.add(eventKey(e.msg, t));
    added.push(e);
  }
  const events = added.length ? [...state.events, ...added].sort((a, b) => a.t - b.t) : state.events;
  return { samples, events };
}

// The filter 'unit:<name>' keeps the events whose text names that unit ("lane b:3", "ace2k b:").
/** @param {string} name @returns {(e: Event) => boolean} */
const unitFilter = (name) => {
  const n = name.toLowerCase();
  const lane = new RegExp(`\\blane ${n.replace(/[^a-z0-9_]/g, '\\$&')}:\\d`);
  return (e) => {
    const msg = String(e.msg ?? '').toLowerCase();
    return msg.startsWith(`ace2k ${n}:`) || lane.test(msg);
  };
};

/** @param {Event[]} events @param {string} filter @param {number} [limit] */
export function filterEvents(events, filter, limit = Infinity) {
  const keep = filter.startsWith('unit:')
    ? unitFilter(filter.slice(5))
    : (/** @type {Event} */ e) => (filter === 'all' ? true : filter === 'errors' ? e.lvl === 'err' : e.src === filter);
  return events
    .filter(keep)
    .sort((a, b) => b.t - a.t)
    .slice(0, limit);
}

// How many events the list holds under the filter the page has now. "Show more" compares it with the limit.
/** @param {{ events: Event[], filter: string }} app */
export const eventCount = (app) => filterEvents(app.events, app.filter).length;

// Whether an older sample, rem minutes left, is of the same cycle as its newer neighbour, newer
// minutes left: the remaining time only falls within a cycle. Unknown either way, it is not.
/** @param {number | null | undefined} rem @param {number | null} newer */
const sameCycle = (rem, newer) => typeof rem === 'number' && newer !== null && rem >= newer;

// The running cycle. start is its start in unix seconds, taken from the samples.
// total is its length in minutes. It is the largest remaining time that a sample of the cycle recorded,
// plus one, because the first sample of the cycle comes within a minute of its start.
// The unit cuts the remaining time down to whole minutes: a 10 min cycle reads 9 in its first minute.
// Files without remaining times give the elapsed time plus the remaining time.
// A sample with no dryer state, as the one the recorder writes at Klipper's start before the unit
// reports, does not end the cycle: the search goes on past it when the sample before it is running
// with as much time left or more, so the same cycle. A cycle that began while Klipper was away
// starts after the gap.
// Returns null when the dryer is not running, that is when remainingMin is null.
/**
 * @param {Sample[]} samples
 * @param {number} nowS
 * @param {number | null | undefined} remainingMin
 * @returns {{ start: number | null, total: number | null } | null}
 */
export function cycleProgress(samples, nowS, remainingMin) {
  if (remainingMin === null || remainingMin === undefined) return null;
  let start = null;
  let maxRem = null;
  let lastRem = null; // the remaining time of the newer neighbour, as the search goes back
  let gap = false;
  for (let i = samples.length - 1; i >= 0; i--) {
    const s = samples[i];
    if (noDryerState(s)) {
      gap = true;
      continue;
    }
    if (!isRunning(s.dry) || (gap && !sameCycle(s.rem, lastRem))) break;
    gap = false;
    start = s.t;
    if (typeof s.rem === 'number') {
      maxRem = Math.max(maxRem ?? s.rem, s.rem);
      lastRem = s.rem;
    }
  }
  if (start === null) return { start: null, total: null };
  if (maxRem !== null) return { start, total: Math.floor(Math.max(maxRem, remainingMin)) + 1 };
  return { start, total: Math.round((nowS - start) / 60) + remainingMin };
}

// Lists the recorder's day files on every connect and reconnect.
// The files carry the printer's dates, not the browser's. A missing directory means no history yet.
// keepDays() in deps returns the number of days that the status keeps.
/** @param {import('./types.js').DayDeps} deps @param {import('./types.js').DayState} state */
export async function listDays(deps, state) {
  const gen = state.gen;
  /** @type {unknown} */
  let files = [];
  try {
    const res = await deps.fetch('/server/files/directory?path=logs/ace2k');
    if (res.ok) files = (await res.json())?.result?.files;
  } catch {
    /* Without a listing there is no history. */
  }
  if (gen !== state.gen) return;
  state.days = historyDays(files, Infinity); // Cut to keep_days where used, because the status may come later.
  await loadDays(deps, state, 2);
}

// Fetches and parses one day file. A 404 returns null, because the file was pruned meanwhile.
// The day then counts as loaded and empty. Any other failure throws.
/** @param {import('./types.js').DayDeps} deps @param {string} name @returns {Promise<Day | null>} */
async function fetchDay(deps, name) {
  const res = await deps.fetch(`/server/files/logs/ace2k/${name}`);
  if (res.ok) return parseJsonl(await res.text());
  if (res.status !== 404) throw new Error(`HTTP ${res.status}`);
  return null;
}

// Loads the count newest listed days, one at a time. A day already loaded or in flight is never fetched again.
// A failed day can be tried again. A reconnect changes gen and drops what was in flight.
/** @param {import('./types.js').DayDeps} deps @param {import('./types.js').DayState} state @param {number} count */
export async function loadDays(deps, state, count) {
  const gen = state.gen;
  const names = state.days.slice(0, Math.min(count, deps.keepDays())).filter((n) => !state.loaded.has(n));
  for (const n of names) state.loaded.add(n);
  state.daysLoaded = Math.max(state.daysLoaded, count);
  for (const name of names) {
    /** @type {Day | null} */
    let day = null;
    try {
      day = await fetchDay(deps, name);
    } catch {
      // A later call tries a failed fetch again, such as "Show more" or the next reconnect.
      if (gen === state.gen) state.loaded.delete(name);
      continue;
    }
    if (gen !== state.gen) return; // A reconnect reset the history meanwhile.
    if (day) {
      ({ samples: state.samples, events: state.events } = mergeDay(state, day));
      deps.render();
    }
  }
  if (gen === state.gen) deps.render();
}
