// A lane's state in words, and the lanes and units that the side panel names by key.
// The functions take the status or the page state and return values. None changes app, so they run under node.
import { t } from './i18n.js';
import { mm } from './format.js';
import { LANE_ERRORS, LANES, chipFor, laneActions, laneErrs, laneOf, laneText, sliceFor } from './model.js';
import { HEADS } from './print-plan.js';
import { ready, unitNames } from './state.js';

/** @typedef {import('./types.js').AppState} AppState */
/** @typedef {import('./types.js').Status} Status */
/** @typedef {import('./types.js').Lane} Lane */
/** @typedef {import('./types.js').LaneEvent} LaneEvent */

// An error chip names the error itself, such as "Stuck" or "Tangled".
// An error that the page does not know reads "Error".
/** @param {Lane | null | undefined} lane @param {string} chip */
export function chipText(lane, chip) {
  const err = lane?.error;
  return chip === 'error' && typeof err === 'string' && LANE_ERRORS.includes(err)
    ? t(`lane.error.${err}`)
    : t(`chip.${chip}`);
}

// The chip of lane i: its state as the bay card and the diagram's bay both name it.
// A lane in error by the page's one rule (laneErrs) shows its error.
/** @param {Status | null | undefined} slice @param {number} i @returns {string} */
export const laneChip = (slice, i) => (laneErrs(laneOf(slice, i)) ? 'error' : chipFor(slice, i));

// The modes and the event kinds that the unit reports. Each has its own words in the catalogue.
export const LANE_MODES = [
  'idle',
  'feeding',
  'rolling_back',
  'unloading',
  'loading',
  'assisting',
  'assisting_back',
  'error',
  'following',
];
export const EVENT_KINDS = [
  'done',
  'stopped',
  'stopped_link',
  'stopped_shutdown',
  'stopped_sensors',
  'runout',
  'loaded',
  'unloaded',
  'stuck',
  'tangled',
  'motor_stalled',
  'timeout',
  'assist_stall',
  'assist_overrun',
  'unload_incomplete',
  'behind',
  'snag',
  'tail',
  'tail_out',
  'blocked',
  'held',
  'at_rest',
];

/** @param {string | null | undefined} kind */
export const kindWord = (kind) => t(`lane.kind.${kind && EVENT_KINDS.includes(kind) ? kind : 'unknown'}`);
/** @param {string | null | undefined} mode */
export const modeWord = (mode) => t(`lane.mode.${mode && LANE_MODES.includes(mode) ? mode : 'unknown'}`);

// The last move in words: what ended it, in which mode, and how far the filament went. None says so.
/** @param {LaneEvent | null | undefined} ev */
export function eventText(ev) {
  if (!ev) return t('lane.none');
  return t('lane.event_text', { kind: kindWord(ev.kind), mode: modeWord(ev.mode), mm: mm(ev.filament_mm) });
}

// The lane's tag state, in words. The unit reads a pending tag by itself whenever the spool turns.
/** @param {Lane | null | undefined} lane @param {boolean} forgotten */
export function tagText(lane, forgotten) {
  const tag = lane?.tag || {};
  if (forgotten && lane?.insert) return t('tag.forgotten');
  switch (tag.state) {
    case 'pending':
      return t('tag.pending');
    case 'searching':
    case 'reading':
      return t('tag.reading');
    case 'read': {
      const who = [tag.record?.brand, tag.record?.material].filter(Boolean).join(' ');
      return t(who ? 'tag.read_who' : 'tag.read', { who });
    }
    case 'no_tag':
      return t('tag.no_tag');
    default:
      return t('lane.no_spool');
  }
}

// The feed-forward toggle's request is pending under one key, whichever way it switches.
// So a status flip while the request is in flight keeps the button busy.
/** @param {number} n */
export const ffKey = (n) => `ff:${n}`;

/**
 * A lane that the side panel names: its unit (null for the unnamed [ace2k]), its index and the unit's status.
 * @typedef {{ unit: string | null, i: number, slice: Status, key: string }} LaneAt
 */

// The lane with this key ("2" with one unit, "a:2" with several), or null when no unit has it.
// The key is the one the diagram writes on the bay (laneText), so one unit and several resolve alike.
/** @param {AppState} app @param {string | null | undefined} key @returns {LaneAt | null} */
export function laneAt(app, key) {
  for (const unit of unitNames(app)) {
    const slice = sliceFor(app.status, unit);
    for (let i = 0; i < LANES; i++) if (laneText(slice, i) === key) return { unit, i, slice, key };
  }
  return null;
}

// The unit that a panel id names: its name, or '' for the unnamed [ace2k]. Undefined when it is gone.
/** @param {AppState} app @param {string | null | undefined} id @returns {string | null | undefined} */
export const unitAt = (app, id) => unitNames(app).find((u) => (u ?? '') === id);

// The head (0-3) that a panel id names, or null when the id names none.
/** @param {string | null | undefined} id @returns {number | null} */
export function headAt(id) {
  const head = id === null || id === undefined || id === '' ? Number.NaN : Number(id);
  return Number.isInteger(head) && head >= 0 && head < HEADS ? head : null;
}

// The unit as the actions' data carries it: its name, or '' for the unnamed [ace2k].
/** @param {string | null} unit */
export const unitId = (unit) => unit ?? '';

// The keys of every lane in error, unit by unit.
/** @param {AppState} app @param {Status} status @returns {string[]} */
function errorKeys(app, status) {
  return unitNames({ ...app, status }).flatMap((unit) => {
    const slice = sliceFor(status, unit);
    return Array.from({ length: LANES }, (_, i) => i).flatMap((i) =>
      laneErrs(laneOf(slice, i)) ? [laneText(slice, i)] : [],
    );
  });
}

// The panel that a status update opens by itself, or null to leave the page as it is.
// When the page loads, or a lane enters an error, on the Units tab with no panel open, the first lane newly in
// error opens on its bay card. A lane already in error before the update does not open again, so a closed card
// stays closed. An open panel is the operator's choice and is never taken over, and another tab is left alone.
// The panel is marked auto: it does not take the focus, so typing elsewhere goes on.
/**
 * @param {AppState} app @param {Status | null | undefined} prev
 * @returns {{ kind: 'bay', key: string, auto: '1' } | null}
 */
export function autoPanel(app, prev) {
  if (app.ui.panel || app.ui.tab !== 'units') return null;
  const before = new Set(prev ? errorKeys(app, prev) : []);
  const key = errorKeys(app, app.status).find((k) => !before.has(k));
  return key ? { kind: 'bay', key, auto: '1' } : null;
}

// The open spool editor while its lane still offers Edit spool, else null: a tag that now speaks for the lane,
// an emptied bay or a unit gone closes it, as the old lane card did.
/** @param {AppState} app */
export function keptEdit(app) {
  const e = app.edit;
  if (!e) return null;
  const unit = e.unit ?? null;
  if (!unitNames(app).includes(unit)) return null;
  return laneActions(sliceFor(app.status, unit), e.lane - 1, ready(app)).some((a) => a.id === 'edit_spool') ? e : null;
}

// Escape closes the side panel only while nothing else is open over the page: another overlay (app.ui.sheet) or
// the dryer's warning has its own way out. The panel's own phone form (app.ui.narrow) never blocks it.
/** @param {AppState} app */
export const escapeCloses = (app) => !app.ui.sheet && !app.modeWarn;

// How long the encoder may stay still before the speed reads 0: the unit reports it about once a second.
const STILL_MS = 2500;

/** @typedef {{ mm: number, at: number, speed: number | null }} SpeedMemo  The last encoder change and the speed. */
// The filament speed from the encoder, kept across renders. memo is the last change of the encoder (null at first);
// the result is the memo to keep, with speed in mm/s, or null while it cannot be told yet.
// An idle lane is still. An encoder that has not moved for STILL_MS reads 0.
/**
 * @param {SpeedMemo | null} memo
 * @param {number | null | undefined} now  The encoder now, in mm.
 * @param {number} at  The time now, in ms.
 * @param {boolean} idle
 * @returns {SpeedMemo | null}
 */
export function encoderSpeed(memo, now, at, idle) {
  if (typeof now !== 'number') return null;
  if (idle) return { mm: now, at, speed: 0 };
  if (!memo) return { mm: now, at, speed: null };
  if (now !== memo.mm) {
    const speed = at > memo.at ? Math.abs(now - memo.mm) / ((at - memo.at) / 1000) : memo.speed;
    return { mm: now, at, speed };
  }
  return at - memo.at > STILL_MS ? { ...memo, speed: 0 } : memo;
}
