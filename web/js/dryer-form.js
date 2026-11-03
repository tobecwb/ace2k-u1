// The dryer as the page reads it: its states and faults in words, the form's values, and the unit's dryer view.
import { t } from './i18n.js';

/** @typedef {import('./types.js').Preset} Preset */
/** @typedef {import('./types.js').Status} Status */

export const FAN_SECONDS = 60;
// The dryer states that the strip names, as t(`dryer.state.${state}`),
// and the faults that faultText knows.
export const DRYER_STATES = ['idle', 'starting', 'heating', 'cooldown', 'fault'];
export const FAULTS = [
  'not_heating',
  'response',
  'sides',
  'chamber_over',
  'chamber_stale',
  'ntc',
  'mains',
  'cutout',
  'heat',
  'fans',
  'flaps',
  'lid',
];

/** @param {string | null} name */
export function faultText(name) {
  return typeof name === 'string' && FAULTS.includes(name)
    ? t(`dryer.fault.${name}`)
    : t('dryer.fault.unknown', { name: name || '?' });
}

// The word for a state. A state that the page does not know reads "Unknown", not the catalogue key.
/** @param {string | null | undefined} state */
export function stateText(state) {
  return t(`dryer.state.${state && DRYER_STATES.includes(state) ? state : 'unknown'}`);
}

// Each unit's card keeps its own form in app.forms, keyed by the unit's name ('' for the unnamed unit).
// A unit not yet edited shows app.form, the page's starting values.
/** @param {{ forms: Record<string, import('./types.js').DryForm>, form: import('./types.js').DryForm }} app @param {string | null} unit */
export const formOf = (app, unit) => app.forms[unit ?? ''] ?? app.form;

// The minutes of a time typed as h:mm (0:01 to 24:00), or null for anything else.
/** @param {unknown} text @returns {number | null} */
export function parseHM(text) {
  const m = /^(\d{1,2}):([0-5]\d)$/.exec(String(text ?? '').trim());
  if (!m) return null;
  const minutes = Number(m[1]) * 60 + Number(m[2]);
  return minutes >= 1 && minutes <= 1440 ? minutes : null;
}

// The preset that suits the spools: the one of the material that dries coolest. A spool type matches a
// preset by its name ("PLA+" counts as PLA). Null when no spool has a material that a preset names.
/** @param {Preset[]} presets @param {(string | null | undefined)[]} materials @returns {Preset | null} */
export function suggestPreset(presets, materials) {
  /** @type {Preset | null} */
  let best = null;
  for (const p of presets) {
    const name = p.material.toUpperCase();
    const wanted = materials.some((m) => {
      const type = typeof m === 'string' ? m.toUpperCase() : '';
      return type.startsWith(name) && !/[A-Z]/.test(type.charAt(name.length));
    });
    if (wanted && (best === null || p.temp < best.temp)) best = p;
  }
  return best;
}

// True while the dryer runs, that is while it starts or heats.
/** @param {string | null | undefined} state */
export const isRunning = (state) => state === 'starting' || state === 'heating';

// True when the unit's cycle is an automatic one: own is the humidity mode's cycle, and bound is the
// cycle that the print mode runs during a print.
/** @param {{ own: boolean, bound: boolean }} d */
export const autoCycle = (d) => d.own || d.bound;

// A history sample with no dryer state: the one the recorder writes at Klipper's start, before
// the unit reports.
/** @param {{ dry?: string | null }} s */
export const noDryerState = (s) => s.dry === null || s.dry === undefined;

// The mode fields of the dryer view: the mode, its limits and its flags.
/** @param {import('./types.js').Dryer} d */
function dryerModeFields(d) {
  return {
    mode: d.mode ?? null,
    humidityAllowed: d.humidity_allowed === true,
    limit: d.limit ?? null,
    failures: d.failures ?? 0,
    maxFailures: d.max_failures ?? null,
    stopped: d.stopped === true,
    cold: d.cold === true,
    own: d.own === true,
    bound: d.bound === true,
  };
}

// The unit's readings that the dryer view shows: the two outlets, the chamber and its humidity.
/** @param {import('./types.js').Unit} a */
const dryerReadings = (a) => ({
  outletL: a.ptc_left ?? null,
  outletR: a.ptc_right ?? null,
  chamber: a.chamber ?? null,
  humidity: a.humidity ?? null,
});

/** @param {Status | null | undefined} s @returns {import('./types.js').DryerView} */
export function dryerView(s) {
  const a = s?.ace2k || {};
  const d = /** @type {import('./types.js').Dryer} */ (a.dryer || {});
  const state = d.state || 'idle';
  return {
    state,
    target: d.target ?? null,
    drive: d.drive ?? null,
    remaining: d.remaining ?? null,
    fault: d.fault ?? null,
    fansOn: Boolean(d.fans?.left || d.fans?.right),
    flaps: d.flaps || {},
    ...dryerReadings(a),
    manual: state === 'idle',
    ...dryerModeFields(d),
    // The waiting line shows what the unit judges: its idle cold reading, not the live one.
    rh: d.rh ?? a.humidity ?? null,
  };
}
