// Turns the merged Moonraker status into what the page shows and what it may do.
// The module has no effects, so it runs under node.
/** @typedef {import('./types.js').Status} Status */
/** @typedef {import('./types.js').Lane} Lane */
/** @typedef {import('./types.js').LaneRef} LaneRef */
/** @typedef {import('./types.js').Spool} Spool */
/** @typedef {import('./types.js').LaneAction} LaneAction */
/** @typedef {import('./types.js').EditForm} EditForm */
export const LANES = 4;

// The errors that the unit reports for a lane. Each has its own words in the catalogue.
export const LANE_ERRORS = [
  'stuck',
  'tangled',
  'motor_stalled',
  'timeout',
  'assist_stall',
  'assist_overrun',
  'unload_incomplete',
];

// True when the lane is in error: the unit stopped it with a known error or set its mode to error. It is the
// page's one rule (the bay's tile and card, the lane shown first).
/** @param {Lane | null | undefined} lane */
export const laneErrs = (lane) => lane?.mode === 'error' || LANE_ERRORS.includes(String(lane?.error));

export const CHIP_KINDS = ['following', 'loaded', 'in_bay', 'parked', 'moving', 'empty', 'error', 'blocked', 'unknown'];
export const BUFFER_KINDS = ['at_rest', 'pushed', 'taut'];
// There is no Follow off button. With the follow off on a loaded head, the lane stays idle while the extruder pulls.
// Then the extruder grinds the filament. The console still has ACEK_ASSIST LANE=n OFF=1.
export const ACTION_IDS = [
  'load',
  'eject',
  'stop',
  'clear',
  'read_tag',
  'forget_tag',
  'ff',
  'feed',
  'rollback',
  'edit_spool',
  'probe_head',
  'load_head',
];
// The modes of a lane in a move. Following is not one of them: a loaded lane follows at rest, and the head probe
// stops the follow itself, as a print start does.
/** @type {unknown[]} */
const MOVING = ['feeding', 'rolling_back', 'unloading', 'loading', 'assisting', 'assisting_back'];
/** @type {unknown[]} */
const PRINTING = ['printing', 'paused'];
// Stop is locked during a print too. ACEK_STOP on a following lane stops the follow and leaves it off.
// Then the extruder pulls against a stopped lane and grinds the filament.
const LOCKED = ['load', 'eject', 'stop', 'ff', 'feed', 'rollback', 'edit_spool'];
// The types of the U1 filament table. Edit spool may set only these.
/** @type {readonly unknown[]} */
export const FILAMENT_TYPES = [
  'ABS',
  'ASA',
  'PA',
  'PA-CF',
  'PA-GF',
  'PA6-CF',
  'PA6-GF',
  'PC',
  'PC-ABS',
  'PEBA',
  'PETG',
  'PETG-CF',
  'PLA',
  'PLA-CF',
  'PVA',
  'TPU',
];
// The colour swatches of the spool editor: common filament colours, as the spools come. Any other colour is
// typed in the free colour field.
export const SWATCHES = [
  '#ffffff',
  '#000000',
  '#8a8d91',
  '#d32c34',
  '#f28c28',
  '#ffcc33',
  '#3aa655',
  '#1f6feb',
  '#7b3fb8',
  '#e86fa8',
  '#8b5a2b',
  '#c0c4c8',
];
const VENDOR_RE = /^[A-Za-z0-9_+-]{1,24}$/;
export const CONFIRM = ['eject', 'probe_head']; // The page asks t(`action.confirm_${id}`) before these.
// Why an action is disabled. Each reason has its text at t(`reason.${r}`).
// The head_* reasons name the head: their texts take {head}, the head's name T0-T3. head_hot also takes {limit},
// the probe's cold limit (words.js headVars).
export const REASONS = [
  'offline',
  'in_use',
  'no_head',
  'not_hooked',
  'print_on',
  'batch_open',
  'head_loaded',
  'head_unknown',
  'head_moving',
  'head_hot',
  'no_material',
  'print_running',
];
/**
 * An action's values. The G-code builder that uses a value checks it.
 * @typedef {{ lanes?: unknown, head?: number | null, forgotten?: boolean, length?: number, type?: unknown,
 *   vendor?: unknown, rgb?: unknown, temp?: number, minutes?: number, seconds?: number, which?: unknown,
 *   open?: boolean, mode?: unknown, wait?: boolean }} GcodeArgs
 */
const MOVE_SPEED = 20; // mm/s for the Advanced feed and rollback

/** @param {Status | null | undefined} s @returns {Lane[]} */
const lanesOf = (s) => s?.ace2k?.lanes || [];
/** @param {Status | null | undefined} s @param {number} i @returns {Lane | undefined} */
export const laneOf = (s, i) => lanesOf(s)[i];

// The units that ace2k_units lists, in printer.cfg order, by object name ('ace2k a').
/** @param {Status | null | undefined} s @returns {string[]} */
export const unitsOf = (s) => (Array.isArray(s?.ace2k_units?.units) ? s.ace2k_units.units : []);
/** @param {Status | null | undefined} s */
export const several = (s) => unitsOf(s).length > 1;

// A unit's object name. 'a' gives 'ace2k a', and null gives 'ace2k' for the unnamed unit.
/** @param {string | null} name */
export const unitObj = (name) => (name ? `ace2k ${name}` : 'ace2k');

// What the views read for one unit. It is the status with that unit's object as ace2k,
// and the unit's name as page_unit, null for the unnamed unit. Without a name it is the first unit.
/**
 * @param {Status | null | undefined} s
 * @param {string | null} [name]
 * @returns {Status}
 */
export function sliceFor(s, name) {
  const obj = name ? unitObj(name) : (unitsOf(s)[0] ?? 'ace2k');
  // The status holds each unit under its object name ('ace2k a').
  const byName = /** @type {Record<string, import('./types.js').Unit | undefined>} */ (/** @type {unknown} */ (s));
  return { ...s, ace2k: byName?.[obj] ?? s?.ace2k, page_unit: unitName(obj) };
}

// The unit that a status shows. A slice names its own unit. Else it is the first unit, and null for [ace2k].
/** @param {Status | null | undefined} s @returns {string | null | undefined} */
const pageUnit = (s) => (s && Object.hasOwn(s, 'page_unit') ? s.page_unit : unitName(unitsOf(s)[0] ?? 'ace2k'));

/** @param {{ unit?: string | null }} x @param {string | null | undefined} unit */
const sameUnit = (x, unit) => x.unit == null || String(x.unit).toLowerCase() === String(unit ?? '').toLowerCase();
// A head's lanes in the adapter's status, as a list of {unit, lane}.
// Null means that no lane feeds the head. A single {unit, lane} is read as a list of one.
/** @param {LaneRef[] | LaneRef | null | undefined} entry @returns {LaneRef[]} */
const headLanes = (entry) => (Array.isArray(entry) ? entry.filter(Boolean) : entry ? [entry] : []);

// Returns the head (0-3) that lane i (0-based) of the unit feeds, or null.
// unit is the unit's name. By default it is the status's unit (pageUnit).
// ace2k_u1.heads lists each head's lanes, with unit null when there is one unit.
// It lists the hooked heads, or the map that lanes: configures while the adapter is not hooked.
// So the in-use locks hold either way.
// Without that map, lane n of a single unit feeds head n-1. With several units, no lane is known to feed a head.
/**
 * @param {Status | null | undefined} s
 * @param {number} i
 * @param {string | null | undefined} [unit]
 * @returns {number | null}
 */
export function headOf(s, i, unit = pageUnit(s)) {
  const heads = s?.ace2k_u1?.heads;
  if (!Array.isArray(heads)) return several(s) ? null : i;
  const h = heads.findIndex((x) => headLanes(x).some((y) => y?.lane === i + 1 && sameUnit(y, unit)));
  return h < 0 ? null : h;
}

// True when lane i of the unit is head h's current lane (ace2k_u1.current[h]).
/**
 * @param {Status | null | undefined} s
 * @param {number | null} h
 * @param {string | null | undefined} unit
 * @param {number} i
 */
export function isCurrent(s, h, unit, i) {
  const cur = s?.ace2k_u1?.current?.[/** @type {number} */ (h)]; // With no head (null), nothing is found.
  return Boolean(cur) && /** @type {LaneRef} */ (cur).lane === i + 1 && sameUnit(/** @type {LaneRef} */ (cur), unit);
}

// True when the rail from a lane's bay to its head h has filament in it, as the diagram draws it: the lane is its head's
// current lane (current), and its bay holds a spool (inBay) or the head's sensor still sees filament (a run-out tail
// still in the tube or the head). A current lane whose bay is empty and whose head is unloaded (after an eject) has an
// empty rail. The lane stays its head's current lane all the same (isCurrent).
/** @param {Status | null | undefined} s @param {number} h @param {boolean} current @param {boolean} inBay */
export const pathLoaded = (s, h, current, inBay) => current && (inBay || headSensor(s, h));

// True for a lane that waits in its bay. Several lanes feed its head, and the hooked adapter names another lane
// as the current one. When no current lane is known, the head sensor tells the state instead.
/** @param {Status | null | undefined} s @param {number} i */
export function parkedOf(s, i) {
  const h = headOf(s, i);
  if (h === null || !adapterHooked(s) || !s.ace2k_u1.current?.[h]) return false;
  return headLanes(s.ace2k_u1.heads?.[h]).length > 1 && !isCurrent(s, h, pageUnit(s), i);
}

// The lane's ACEK_SPOOL record, as a spool. ace2k_u1.spools keys it as "a:2", or "-:2" for the unnamed unit.
/** @param {Status | null | undefined} s @param {number} i @returns {Spool | null} */
export function recordSpool(s, i) {
  const rec = s?.ace2k_u1?.spools?.[`${pageUnit(s) ?? '-'}:${i + 1}`];
  if (!rec || typeof rec.type !== 'string') return null;
  const hex = /^#[0-9A-Fa-f]{6}$/.test(rec.color) ? `${rec.color.slice(1)}ff` : null;
  return { vendor: rec.vendor || null, type: rec.type || null, subtype: null, rgba: hex, fromTag: false };
}

// A lane as the console names it: "a:2" with several units, "2" with one.
/** @param {Status | null | undefined} s @param {string | null | undefined} unit @param {number} n */
const laneName = (s, unit, n) => (several(s) ? `${unit}:${n}` : String(n));
/** @param {Status | null | undefined} s @param {number} i */
export const laneText = (s, i) => laneName(s, pageUnit(s), i + 1);

// The other lanes of the head that the lane feeds, as text. A head of its own gives [].
/** @param {Status | null | undefined} s @param {number} i @returns {string[]} */
export function sharedWith(s, i) {
  const h = headOf(s, i);
  if (h === null) return [];
  const unit = pageUnit(s);
  return headLanes(s?.ace2k_u1?.heads?.[h])
    .filter((y) => !(y.lane === i + 1 && sameUnit(y, unit)))
    .map((y) => laneName(s, y.unit, y.lane));
}

// True when head h's filament sensor sees filament.
/** @param {Status | null | undefined} s @param {number} h */
function headSensor(s, h) {
  const sensors = /** @type {Record<string, { filament_detected?: boolean } | undefined> | null | undefined} */ (
    /** @type {unknown} */ (s)
  );
  return sensors?.[`filament_motion_sensor e${h}_filament`]?.filament_detected === true;
}

/** @param {Status | null | undefined} s @param {number} h */
const sensorRead = (s, h) =>
  Boolean(
    /** @type {Record<string, unknown> | null | undefined} */ (/** @type {unknown} */ (s))?.[
      `filament_motion_sensor e${h}_filament`
    ],
  );

/** @param {Status | null | undefined} s @param {number} i */
export function headHasFilament(s, i) {
  const h = headOf(s, i);
  return h !== null && headSensor(s, h);
}

// True when the adapter does not know which lane is in head h. The adapter is hooked and several lanes feed the head.
// current[h] is null, as after a Klipper restart, and the head sensor sees filament.
/** @param {Status | null | undefined} s @param {number} h */
export function laneUnknownIn(s, h) {
  return (
    adapterHooked(s) &&
    headLanes(s.ace2k_u1.heads?.[h]).length > 1 &&
    s.ace2k_u1.current?.[h] == null &&
    headSensor(s, h)
  );
}

// True when one of a head's lanes (of any unit) moves: a feed, rollback, eject, load or assist runs on it.
// The probe moves every lane of the head, so it waits for that move to end.
/** @param {Status} s @param {LaneRef[]} lanes */
const headMoving = (s, lanes) =>
  lanes.some((y) => MOVING.includes(String(laneOf(sliceFor(s, y.unit ?? null), y.lane - 1)?.mode)));

// One line for each head that several lanes feed, while the adapter is hooked.
// head is 0-3, and lanes holds the lanes as text. current is the lane that the adapter names in the head, or null.
// empty is true when a lane is named and the head sensor reads no filament. The adapter keeps the lane after
// the screen unloads the head, so the line still names it, as empty.
// unknown is true when that lane is unknown (laneUnknownIn).
// probe is the state of Find the lane ({enabled, reason}) while the lane is unknown, else null.
// unit is the unit of the head's first lane.
/**
 * @param {Status | null | undefined} s
 * @param {boolean} online
 */
export function sharedHeadsView(s, online) {
  if (!adapterHooked(s)) return [];
  const heads = s.ace2k_u1.heads ?? [];
  const printOn = printing(s);
  return heads.flatMap((entry, head) => {
    const lanes = headLanes(entry);
    if (lanes.length < 2) return [];
    const cur = s.ace2k_u1.current?.[head];
    const unknown = laneUnknownIn(s, head);
    // With no lane named and no reading from the head sensor, there is nothing to say.
    // "empty" needs a reading.
    if (!cur && !unknown && !sensorRead(s, head)) return [];
    const reason = actionReason('probe_head', {
      online,
      hooked: true,
      noHead: false,
      inUse: false,
      printing: printOn,
      headMoving: headMoving(s, lanes),
      headHot: s.ace2k_u1.probe_cold?.[head] === false,
    });
    return [
      {
        head,
        lanes: lanes.map((y) => laneName(s, y.unit, y.lane)),
        current: cur ? laneName(s, cur.unit, cur.lane) : null,
        empty: Boolean(cur) && sensorRead(s, head) && !headSensor(s, head),
        unknown,
        probe: unknown ? { enabled: reason === null, reason } : null,
        unit: lanes[0].unit ?? null,
      },
    ];
  });
}
// True when the adapter is hooked (ace2k_u1.hooked). Only then does Edit spool write a head's setting.
/** @param {Status | null | undefined} s @returns {s is Status & { ace2k_u1: import('./types.js').U1Adapter }} */
const adapterHooked = (s) => s?.ace2k_u1?.hooked === true;
// The Print tab plans and starts a file while the adapter is hooked and a head has several lanes: a file whose
// tools share a head is started from the page. Else it only says so, and sends nothing.
/** @param {Status | null | undefined} s @returns {boolean} */
export const printOffered = (s) => adapterHooked(s) && (s.ace2k_u1.heads ?? []).some((x) => headLanes(x).length > 1);
/** @param {Status | null | undefined} s */
export const printing = (s) => PRINTING.includes(s?.print_stats?.state);
// True while the print is printing, not paused.
/** @param {Status | null | undefined} s */
export const printingNow = (s) => s?.print_stats?.state === 'printing';
// True while the print is paused.
/** @param {Status | null | undefined} s */
export const paused = (s) => s?.print_stats?.state === 'paused';
// True while a RESUME runs a pending swap (ace2k_u1.resuming). The RESUME may come from this page,
// another tab or the screen. The Resume button waits for it, so no RESUME is sent twice.
/** @param {Status | null | undefined} s */
export const resuming = (s) => s?.ace2k_u1?.resuming === true;
// The script of the Resume button. It is RESUME while the print is paused, else null.
/** @param {Status | null | undefined} s @returns {string | null} */
export const resumeScript = (s) => (paused(s) ? 'RESUME' : null);
// The script of the Pause button. It is PAUSE while the print is printing, else null.
/** @param {Status | null | undefined} s @returns {string | null} */
export const pauseScript = (s) => (printingNow(s) ? 'PAUSE' : null);
// The script of the Printing button. It is CANCEL_PRINT while a print is on, else null.
/** @param {Status | null | undefined} s @returns {string | null} */
export const cancelScript = (s) => (printing(s) ? 'CANCEL_PRINT' : null);

/** @param {Status | null | undefined} s @param {number} i */
export function laneInUse(s, i) {
  const lane = laneOf(s, i);
  return printing(s) && ((lane?.mode && lane.mode !== 'idle') || headHasFilament(s, i));
}

// The chip of a lane. parked means that the lane waits outside a head it shares (parkedOf).
// An idle lane with filament in its bay then shows Parked, whatever the head sensor reads,
// because the head holds another lane.
// unknown means that the adapter does not know which lane is in the head where the sensor sees filament.
// The chip then shows In head?, not Loaded.
/**
 * @param {Lane | null | undefined} lane
 * @param {boolean} headDetected
 * @param {{ parked?: boolean, unknown?: boolean }} [opts]
 * @returns {string}
 */
export function chipOf(lane, headDetected, { parked = false, unknown = false } = {}) {
  if (!lane) return 'empty';
  if (lane.mode === 'error') return 'error';
  if (lane.mode === 'following') return 'following';
  if (MOVING.includes(lane.mode)) return 'moving';
  if (!lane.insert) return 'empty';
  if (parked) return 'parked';
  if (!headDetected) return 'in_bay';
  return unknown ? 'unknown' : 'loaded';
}

// The lane's entry in ace2k_u1.lanes, keyed "a:2" or "-:2" as the spools are.
/** @param {Status | null | undefined} s @param {number} i */
export const adapterLane = (s, i) => s?.ace2k_u1?.lanes?.[`${pageUnit(s) ?? '-'}:${i + 1}`];
// True when the lane's last push ended blocked (push_end).
/** @param {Status | null | undefined} s @param {number} i */
const pushBlocked = (s, i) => adapterLane(s, i)?.push_end === 'blocked';
// True when the tag was forgotten by hand (ACEK_TAG_FORGET).
// Then the lane's spool is shown instead of the tag, until its bay empties.
/** @param {Status | null | undefined} s @param {number} i */
export const tagForgotten = (s, i) => adapterLane(s, i)?.tag_forgotten === true;

// The chip of lane i in the status.
// A lane whose last push ended blocked shows Blocked until its next move. Only an in-bay lane does.
// A loaded, moving, error or parked lane keeps its own chip.
// Only the page shows Blocked. The unit's LED does not change.
/** @param {Status | null | undefined} s @param {number} i @returns {string} */
export function chipFor(s, i) {
  const h = headOf(s, i);
  const chip = chipOf(laneOf(s, i), headHasFilament(s, i), {
    parked: parkedOf(s, i),
    unknown: h !== null && laneUnknownIn(s, h),
  });
  return chip === 'in_bay' && pushBlocked(s, i) ? 'blocked' : chip;
}

/** @param {string | null | undefined} v */
const real = (v) => (v && v !== 'NONE' ? v : null);

// True when the lane's tag counts: it carries a record with a material, and was not forgotten by hand.
// After a restart a tag waits to be read again (pending) with its last record kept; the adapter goes by that
// record, so the page does too.
/** @param {Status | null | undefined} s @param {number} i */
const tagSpeaks = (s, i) => Boolean(laneOf(s, i)?.tag?.record?.material) && !tagForgotten(s, i);

// True when the operator may edit the lane's spool: its tag does not speak for it. A tag read with
// no usable material and colour counts as no tag, because tagSpeaks needs the record's material.
/** @param {Status | null | undefined} s @param {number} i */
export const editableSpool = (s, i) => !tagSpeaks(s, i);

// The lane's tag record as a spool, while the tag counts. Else null.
/** @param {Status | null | undefined} s @param {number} i @returns {Spool | null} */
export function tagSpool(s, i) {
  const rec = tagSpeaks(s, i) ? laneOf(s, i)?.tag?.record : null;
  return rec
    ? {
        vendor: rec.brand || null,
        type: rec.material || null,
        subtype: null,
        rgba: rec.color_rgba || null,
        fromTag: true,
      }
    : null;
}

// The filament setting of head h on the U1 (print_task_config), as a spool.
// It is null when the head has none, or when h is null. fromTag says whether the lane's tag counts.
/** @param {Status | null | undefined} s @param {number | null} h @param {boolean} fromTag @returns {Spool | null} */
export function headSetting(s, h, fromTag) {
  const ptc = s?.print_task_config;
  // A head with no material set (NONE, as the U1 leaves the heads a print does not use) has no setting.
  if (h === null || !ptc?.filament_exist?.[h] || !real(ptc.filament_type?.[h])) return null;
  return {
    vendor: real(ptc.filament_vendor?.[h]),
    type: real(ptc.filament_type?.[h]),
    subtype: real(ptc.filament_sub_type?.[h]),
    rgba: real(ptc.filament_color_rgba?.[h]),
    fromTag,
  };
}

// True for a shared head while the adapter is hooked.
// Then the head's setting belongs to a lane only when it was written for that lane (setting_for).
/** @param {Status | null | undefined} s @param {number | null} h @returns {h is number} */
function sharedHooked(s, h) {
  return h !== null && adapterHooked(s) && headLanes(s.ace2k_u1?.heads?.[h]).length > 1;
}
/** @param {Status} s @param {number} h @param {number} i */
function settingIsLane(s, h, i) {
  const mine = s.ace2k_u1?.setting_for?.[h];
  return (
    Boolean(mine) &&
    /** @type {LaneRef} */ (mine).lane === i + 1 &&
    sameUnit(/** @type {LaneRef} */ (mine), pageUnit(s))
  );
}

// The lane's spool, by precedence. The U1 keeps its settings per head.
// On a shared head with the adapter hooked, the order is the one of lane_spool in the adapter.
// It is the lane's tag, else its record, else the head's setting when it was written for this lane.
// Else the spool is not known.
// On a head of its own, the head's setting comes first, because it holds the U1's names.
// That setting was written from the tag. Then come the record and the tag.
/** @param {Status | null | undefined} s @param {number} i @returns {Spool | null} */
export function spoolOf(s, i) {
  const fromTag = tagSpeaks(s, i);
  const h = headOf(s, i);
  const tag = tagSpool(s, i);
  const record = fromTag ? null : recordSpool(s, i);
  const setting = headSetting(s, h, fromTag);
  // sharedHooked means that the adapter is hooked, so s is set.
  if (sharedHooked(s, h)) return tag || record || (settingIsLane(/** @type {Status} */ (s), h, i) ? setting : null);
  return setting || record || tag;
}

// The spool in bay i, or null while the bay is empty (its insert sensor reads nothing).
/** @param {Status | null | undefined} s @param {number} i @returns {Spool | null} */
export const spoolIn = (s, i) => (laneOf(s, i)?.insert ? spoolOf(s, i) : null);

/** @param {Spool | null | undefined} spool @returns {string | null} */
export function colorOf(spool) {
  const rgba = spool?.rgba;
  return rgba && /^[0-9A-Fa-f]{8}$/.test(rgba) ? `#${rgba.slice(0, 6).toLowerCase()}` : null;
}

// The Edit spool action as laneActions gives it, or null when it is not offered.
// The form and its Save guard use it.
/** @param {Status | null | undefined} s @param {number} i @param {boolean} online @returns {LaneAction | null} */
export const editAction = (s, i, online) => laneActions(s, i, online).find((a) => a.id === 'edit_spool') || null;

// What the Edit spool form starts with. It takes the current setting of the head when there is one.
// head is the head that the form writes, or null when the lane feeds no head.
/** @param {Status | null | undefined} s @param {number} i @returns {EditForm} */
export function editPrefill(s, i) {
  const sp = spoolOf(s, i);
  return {
    lane: i + 1,
    head: headOf(s, i),
    type: FILAMENT_TYPES.includes(sp?.type)
      ? /** @type {string} */ (sp?.type)
      : 'PLA' /* includes() found it, so it is a string */,
    vendor: sp?.vendor && VENDOR_RE.test(sp.vendor) ? sp.vendor : 'Generic',
    color: colorOf(sp) || '#ffffff',
  };
}

// Checks a Save of the Edit spool form e against the current status. e comes from editPrefill, as edited.
// The action's lock comes first, with its reason. Then comes the head.
// The form writes only to the head that it was opened for.
// When the lane no longer feeds that head, Save is refused ('head_changed'). So a map that moved never writes
// another head.
/**
 * @param {Status | null | undefined} s
 * @param {EditForm} e
 * @param {boolean} online
 * @returns {{ ok: false, reason: string } |
 *   { ok: true, writeHead: boolean, args: { type: string, vendor: string, rgb: string, head: number | null } }}
 */
export function editSave(s, e, online) {
  const ea = editAction(s, e.lane - 1, online);
  if (!ea?.enabled) return { ok: false, reason: ea?.reason || 'offline' };
  if (headOf(s, e.lane - 1) !== e.head) return { ok: false, reason: 'head_changed' };
  // The head's setting belongs to the lane only when the head has no other lane, or the lane is in it now.
  // e.head is null only when headOf found no head either, as checked above. The lookup then finds nothing.
  const writeHead =
    headLanes(s?.ace2k_u1?.heads?.[/** @type {number} */ (e.head)]).length <= 1 ||
    isCurrent(s, e.head, pageUnit(s), e.lane - 1);
  return {
    ok: true,
    writeHead,
    // The vendor is optional: an empty one is saved as Generic, as the U1 names a spool of no known maker.
    args: { type: e.type, vendor: e.vendor.trim() || 'Generic', rgb: String(e.color).replace('#', ''), head: e.head },
  };
}

// What Save sends once editSave is ok. It sends the lane's spool record (ACEK_SPOOL),
// then the head's setting when writeHead is true.
// unit is the name of the lane's unit, and null for [ace2k].
/**
 * @param {{ writeHead: boolean, args: GcodeArgs }} save
 * @param {number} n
 * @param {string | null | undefined} [unit]
 */
export function editScript(save, n, unit = null) {
  const lines = [gcodeFor('spool', n, save.args, unit)];
  if (save.writeHead) lines.push(gcodeFor('edit_spool', n, save.args, unit));
  return lines.join('\n');
}

// The lanes that Stop all may stop. During a print these are the lanes not in use, 1-based and in order.
// Outside a print it returns null, because Stop all is then the plain ACEK_STOP.
/** @param {Status | null | undefined} s @returns {number[] | null} */
export function stopAllLanes(s) {
  if (!printing(s)) return null;
  if (!lanesOf(s).length) return []; // Without lane telemetry, no lane is known to be idle.
  const out = [];
  for (let i = 0; i < LANES; i++) if (!laneInUse(s, i)) out.push(i + 1);
  return out;
}

// What Stop all says that it stops, as t() keys and their vars.
// During a print it stops only the idle lanes of this page's unit.
// The page shows that unit alone, so it cannot tell which of the other units' lanes feed the print.
// The unit is named when it has a name. With several units, the words say that the others do not stop.
// Outside a print it is the plain ACEK_STOP, which stops every lane of every unit.
// STOP_ALL_KEYS also holds the toasts of stopAllPlan and stopAllConfirmed.
export const STOP_ALL_KEYS = [
  'top.stop_all',
  'top.confirm_stop_all',
  'top.stop_all_units',
  'top.confirm_stop_all_units',
  'top.stop_idle',
  'top.confirm_stop_all_print',
  'top.stop_idle_unit',
  'top.confirm_stop_idle_unit',
  'top.confirm_stop_idle_units',
  'top.stop_all_print',
  'top.stop_all_changed',
];

/** @param {Status | null | undefined} s @returns {import('./types.js').StopWords} */
export function stopAllWords(s) {
  const several = unitsOf(s).length > 1;
  const unit = unitName(unitsOf(s)[0] ?? 'ace2k');
  const vars = { unit: unit ?? '' };
  if (printing(s)) {
    if (!unit) return { label: 'top.stop_idle', confirm: 'top.confirm_stop_all_print', vars };
    return {
      label: 'top.stop_idle_unit',
      confirm: several ? 'top.confirm_stop_idle_units' : 'top.confirm_stop_idle_unit',
      vars,
    };
  }
  if (several) return { label: 'top.stop_all_units', confirm: 'top.confirm_stop_all_units', vars };
  return { label: 'top.stop_all', confirm: 'top.confirm_stop_all', vars };
}

// Stop all, decided once before its confirmation.
// The plan holds the exact G-code lines, the lanes they stop during a print (null outside one),
// the words of the dialog, and whether a print was running.
// unit is the name of the page's unit, and null for [ace2k].
// During a print with every lane in use there is nothing to stop. Then it returns { ok: false, toast },
// and no dialog shows.
/**
 * @param {Status | null | undefined} s
 * @param {string | null | undefined} [unit]
 * @returns {{ ok: false, toast: string } |
 *   { ok: true, printing: boolean, lanes: number[] | null, script: string, words: import('./types.js').StopWords }}
 */
export function stopAllPlan(s, unit = null) {
  const lanes = stopAllLanes(s);
  if (lanes && !lanes.length) return { ok: false, toast: 'top.stop_all_print' };
  return {
    ok: true,
    printing: printing(s),
    lanes,
    script: gcodeFor('stop_all', null, lanes ? { lanes } : {}, unit),
    words: stopAllWords(s),
  };
}

// After the confirmation, returns the plan's lines exactly as the dialog promised them.
// It returns nothing when the print started or ended while the dialog was open.
// It also returns nothing when a lane of the plan went into use meanwhile, as in a tool change.
// The idle lanes are worked out again on the current status, so a lane that the print now uses is never stopped.
// The user can press Stop all again.
/**
 * @param {{ printing: boolean, lanes: number[] | null, script: string }} plan
 * @param {Status | null | undefined} s
 * @returns {{ ok: false, toast: string } | { ok: true, script: string }}
 */
export function stopAllConfirmed(plan, s) {
  if (printing(s) !== plan.printing) return { ok: false, toast: 'top.stop_all_changed' };
  if (plan.lanes) {
    const idle = stopAllLanes(s) || [];
    if (!plan.lanes.every((n) => idle.includes(n))) return { ok: false, toast: 'top.stop_all_changed' };
  }
  return { ok: true, script: plan.script };
}

/** @param {Lane | null | undefined} lane @returns {string} */
export function bufferOf(lane) {
  if (lane?.rest) return 'at_rest';
  if (lane?.pushed) return 'pushed';
  return 'taut';
}

// The actions that a lane shows, in the order of the card.
/** @param {Status | null | undefined} s @param {number} i @param {Lane} lane @returns {string[]} */
function visibleActions(s, i, lane) {
  const h = headOf(s, i);
  const idle = !lane.mode || lane.mode === 'idle';
  const forgotten = tagForgotten(s, i);
  const read = lane.tag?.state === 'read';
  /** @param {unknown} on @param {...string} ids */
  const has = (on, ...ids) => (on ? ids : []);
  return [
    ...has(lane.insert && idle, 'load'),
    ...has(lane.insert, 'eject'),
    ...has(!idle && lane.mode !== 'error', 'stop'),
    ...has(lane.mode === 'error', 'clear'),
    // Read tag forgets the tag. The lane goes back to pending, and the unit reads the tag on its next move.
    // A forgotten lane keeps the button. There it undoes the forget, and the tag is read again.
    ...has(
      lane.insert && (forgotten || ['read', 'no_tag'].includes(/** @type {string} */ (lane.tag?.state))),
      'read_tag',
    ),
    // Load into head N needs the head's sensor reading and the macro's batch state.
    // It is offered only when the page can tell the head is empty and no head load runs.
    ...has(lane.insert && idle && h !== null && sensorRead(s, h) && batchOf(s) !== undefined, 'load_head'),
    'ff',
    ...has(lane.insert && idle, 'feed', 'rollback'),
    // Forget tag forgets without reading the tag again. So the spool can be edited,
    // and the tag does not put its colour back.
    ...has(lane.insert && !forgotten && read, 'forget_tag'),
    ...has(lane.insert && editableSpool(s, i), 'edit_spool'),
  ];
}

// Whether the U1's head load macro (AUTO_FEEDING_BATCH) has a batch open: true while the screen or a page
// loads a head. undefined when the macro is not in the status.
/** @param {Status | null | undefined} s @returns {boolean | undefined} */
function batchOf(s) {
  const macro = /** @type {Record<string, { doing?: unknown } | undefined> | null | undefined} */ (
    /** @type {unknown} */ (s)
  )?.['gcode_macro AUTO_FEEDING_BATCH'];
  return macro ? macro.doing === true : undefined;
}

// The actions that need the adapter hooked. Edit spool sets the head that the adapter feeds from the lane.
// When the adapter is not hooked, that map is not in effect. Load into head N chooses the head's lane with it.
const HOOKED_ONLY = ['edit_spool', 'forget_tag', 'load_head'];
// The actions refused while a print is printing or paused, on every lane. They move or heat a whole head.
const NOT_IN_PRINT = ['probe_head', 'load_head'];
// The actions that change a bay's spool (Edit spool and its Save, Read tag again, Forget tag), refused on every lane while the
// print is printing; while it is paused they are offered as before.
const NOT_WHILE_PRINTING = ['edit_spool', 'read_tag', 'forget_tag'];

// The facts that the reasons of a disabled action depend on.
// headLoaded is true when the head's sensor sees filament, and headUnknown when the adapter also knows no lane in it.
// headMoving is true when a lane of the head moves, and headHot when the adapter finds the head not cold (probe_head).
// running is true while the print is printing (not paused). batchOpen is true while a head load runs.
/**
 * @typedef {{ online: boolean, hooked: boolean, noHead: boolean, inUse: boolean, printing: boolean,
 *   running?: boolean, headLoaded?: boolean, headUnknown?: boolean, headMoving?: boolean, headHot?: boolean,
 *   batchOpen?: boolean,
 *   ejectNoMaterial?: boolean }} ReasonFacts
 */
// The reasons, in order, each with its test. The first reason whose test holds is the one given.
/** @type {[string, (id: string, c: ReasonFacts) => boolean | undefined][]} */
const REASON_RULES = [
  ['offline', (_id, c) => !c.online],
  ['not_hooked', (id, c) => !c.hooked && HOOKED_ONLY.includes(id)],
  // Edit spool sets a head. A lane that feeds no head has nothing to set.
  ['no_head', (id, c) => c.noHead && id === 'edit_spool'],
  // A bay's spool is not changed while the print runs: pause it first.
  ['print_running', (id, c) => Boolean(c.running) && NOT_WHILE_PRINTING.includes(id)],
  // Eject works whatever the head holds, because ACEK_EJECT first unloads a loaded head on the U1.
  ['in_use', (id, c) => c.inUse && LOCKED.includes(id)],
  // The probe moves every lane of the head. So the adapter refuses it while a print is printing or paused.
  // Load into head N heats the head, and the macro's end turns every head's heater off outside a print.
  ['print_on', (id, c) => NOT_IN_PRINT.includes(id) && c.printing],
  // One head load at a time on the printer. A second START would replace the first one's saved temperatures.
  ['batch_open', (id, c) => id === 'load_head' && c.batchOpen],
  // The probe moves every lane of the head: not while one of them already moves (the adapter refuses it too).
  ['head_moving', (id, c) => id === 'probe_head' && Boolean(c.headMoving)],
  // The probe runs on a cold head only: heater off and below the adapter's limit (the adapter refuses it too).
  ['head_hot', (id, c) => id === 'probe_head' && Boolean(c.headHot)],
  // A head with filament that the adapter cannot name (after a Klipper restart): Find the lane in T<n> names it.
  ['head_unknown', (id, c) => id === 'load_head' && Boolean(c.headUnknown)],
  // The U1 loads only an empty head. The filament in it is unloaded first.
  ['head_loaded', (id, c) => id === 'load_head' && c.headLoaded],
  // An eject of a lane in its head may heat the head for its material, so the adapter refuses one with none set
  // (ACEK_EJECT). A lane out of its head is rolled back cold, and its eject goes on.
  ['no_material', (id, c) => id === 'eject' && c.ejectNoMaterial],
];

// Why the action id is disabled, or null when it is enabled. c holds the facts that the reasons depend on.
/** @param {string} id @param {ReasonFacts} c @returns {string | null} */
function actionReason(id, c) {
  return REASON_RULES.find(([, holds]) => holds(id, c))?.[0] ?? null;
}

// True when the adapter refuses the lane's eject for its material: it says so in eject_no_material. An older
// adapter gives only material_set, and refuses every lane with none set.
/** @param {{ material_set?: boolean | null, eject_no_material?: boolean } | undefined} l @returns {boolean} */
function ejectNoMaterial(l) {
  return l?.eject_no_material ?? l?.material_set === false;
}

/** @param {Status | null | undefined} s @param {number} i @param {boolean} online @returns {LaneAction[]} */
export function laneActions(s, i, online) {
  const lane = laneOf(s, i) || {};
  const h = headOf(s, i);
  const c = {
    online,
    hooked: adapterHooked(s),
    noHead: h === null,
    inUse: laneInUse(s, i),
    printing: printing(s),
    running: printingNow(s),
    headLoaded: headHasFilament(s, i),
    headUnknown: h !== null && laneUnknownIn(s, h),
    batchOpen: batchOf(s) === true,
    ejectNoMaterial: ejectNoMaterial(adapterLane(s, i)),
  };
  return visibleActions(s, i, lane).map((id) => {
    const reason = actionReason(id, c);
    return { id, enabled: reason === null, reason, confirm: CONFIRM.includes(id) };
  });
}

// Why an action is disabled, as the suffix of the catalogue key t(`reason.${…}`).
// Stop locked by the print has its own words, because pausing the print is what lets the lane stop.
/** @param {string} id @param {string | null} reason */
export const reasonKey = (id, reason) => (id === 'stop' && reason === 'in_use' ? 'stop_in_use' : reason);

// The check at the click. It asks the current status whether the action is offered,
// never the DOM's disabled state.
/**
 * @param {Status | null | undefined} s
 * @param {number} i
 * @param {string} id
 * @param {boolean} online
 * @returns {{ ok: boolean, reason: string | null }}
 */
export function allowedNow(s, i, id, online) {
  const a = laneActions(s, i, online).find((x) => x.id === id);
  if (a?.enabled) return { ok: true, reason: null };
  const reason = !a ? (online ? 'unavailable' : 'offline') : a.reason;
  return { ok: false, reason: reasonKey(id, reason) };
}

const LANE_ACTIONS = [
  'lane_active',
  'head_spool',
  'head_prepare',
  'load',
  'eject',
  'stop',
  'clear',
  'read_tag',
  'forget_tag',
  'ff_on',
  'ff_off',
  'feed',
  'rollback',
  'edit_spool',
  'spool',
];
export const FLAP_WHICH = ['bottom', 'rear'];
// The dryer's modes, in the order of their buttons (ACEK_DRY_MODE MODE=…).
export const DRYER_MODES = ['manual', 'humidity', 'print'];
const MOVE_MAX_MM = 2000; // the length limit of the Advanced feed and rollback

// The refusals that the page makes itself. Each has its sentence in the catalogue as `refuse.<name>`.
export const REFUSALS = [
  'temp',
  'minutes',
  'seconds',
  'type',
  'vendor',
  'colour',
  'head',
  'length_min',
  'length_max',
  'stop_all',
  'which',
  'mode',
  'lane',
  'no_action',
];

// A refusal that carries the catalogue name and the values of its sentence. The toast says it in words.
export class RefusedError extends Error {
  /** @param {string} name  One of REFUSALS. @param {Record<string, unknown>} [vars] */
  constructor(name, vars = {}) {
    super(`refuse.${name}`);
    this.key = `refuse.${name}`;
    this.vars = vars;
  }
}

// Refuses anything that is not a plain positive number, because the G-code is built from these values.
/** @param {number} v @param {'temp' | 'minutes' | 'seconds'} name @returns {number} */
function posNum(v, name) {
  if (!Number.isFinite(v) || v <= 0) throw new RefusedError(name);
  return v;
}

// Checks Edit spool's values. Each must be one that the G-code can carry as it is.
/** @param {GcodeArgs} args */
function spoolArgs(args) {
  if (!FILAMENT_TYPES.includes(args.type)) throw new RefusedError('type');
  if (typeof args.vendor !== 'string' || !VENDOR_RE.test(args.vendor)) throw new RefusedError('vendor');
  if (typeof args.rgb !== 'string' || !/^[0-9A-Fa-f]{6}$/.test(args.rgb)) throw new RefusedError('colour');
}

// The head that a line names. It is the head that the lane feeds (headOf), never one assumed from the lane number.
/** @param {number} h @returns {number} */
export function headArg(h) {
  if (!(Number.isInteger(h) && h >= 0 && h < LANES)) throw new RefusedError('head');
  return h;
}

/** @param {number} v @returns {number} */
function moveLength(v) {
  if (!Number.isFinite(v) || v < 1) throw new RefusedError('length_min');
  if (v > MOVE_MAX_MM) throw new RefusedError('length_max', { max: MOVE_MAX_MM });
  return v;
}

// A unit's name from its object. 'ace2k a' gives 'a', and 'ace2k' gives null for the unnamed unit.
/** @param {string | null | undefined} object @returns {string | null} */
export const unitName = (object) => (object?.startsWith('ace2k ') ? object.slice(6) : null);

// With a named unit, every ACEK_ line carries UNIT=<name>. With [ace2k] alone nothing changes.
// Stop all outside a print is the exception. It stays a plain ACEK_STOP, which ace2k runs on every lane
// of every unit, as the button promises. With UNIT= it would stop only that unit.
/**
 * @param {string} id
 * @param {number | null} n
 * @param {GcodeArgs} [args]
 * @param {string | null | undefined} [unit]
 */
export function gcodeFor(id, n, args = {}, unit = null) {
  const script = gcodeScript(id, n, args);
  if (script === 'ACEK_STOP') return script;
  return unit
    ? script
        .split('\n')
        .map((l) => (l.startsWith('ACEK_') ? `${l} UNIT=${unit}` : l))
        .join('\n')
    : script;
}

// During a print, Stop all stops the listed lanes one by one. Else it is the plain ACEK_STOP.
/** @param {unknown} lanes */
function stopAllScript(lanes) {
  if (lanes === undefined || lanes === null) return 'ACEK_STOP';
  if (!Array.isArray(lanes) || !lanes.length || !lanes.every((l) => Number.isInteger(l) && l >= 1 && l <= LANES)) {
    throw new RefusedError('stop_all', { max: LANES });
  }
  return lanes.map((l) => `ACEK_STOP LANE=${l}`).join('\n');
}

// Each action's G-code. n is the lane, and args holds its values.
// A value that the G-code cannot carry is refused.
/** @type {Record<string, (n: number | null, args: GcodeArgs) => string>} */
const GCODE = {
  load: (n) => `ACEK_LOAD LANE=${n} WAIT=0`,
  // The bay card's Eject returns at once (WAIT=0). Eject all waits for each eject's decision (WAIT=1), and a
  // refusal is then the command's error.
  eject: (n, args) => `ACEK_EJECT LANE=${n} WAIT=${args.wait ? 1 : 0}`,
  stop: (n) => `ACEK_STOP LANE=${n}`,
  stop_all: (_n, args) => stopAllScript(args.lanes),
  clear: (n) => `ACEK_CLEAR LANE=${n}`,
  probe_head: (_n, args) => `ACEK_HEAD_PROBE HEAD=${headArg(/** @type {number} */ (args.head)) + 1}`,
  // On a forgotten lane, Read tag first clears the adapter's mark (READ=1). Then the core forgets the tag.
  read_tag: (n, args) => (args.forgotten ? `ACEK_TAG_FORGET LANE=${n} READ=1` : `ACEK_RFID_FORGET LANE=${n}`),
  forget_tag: (n) => `ACEK_TAG_FORGET LANE=${n}`,
  ff_on: (n) => `ACEK_FEED_FORWARD LANE=${n} ON=1`,
  ff_off: (n) => `ACEK_FEED_FORWARD LANE=${n} OFF=1`,
  feed: (n, args) =>
    `ACEK_FEED LANE=${n} LENGTH=${moveLength(/** @type {number} */ (args.length))} SPEED=${MOVE_SPEED} WAIT=0`,
  rollback: (n, args) =>
    `ACEK_ROLLBACK LANE=${n} LENGTH=${moveLength(/** @type {number} */ (args.length))} SPEED=${MOVE_SPEED} WAIT=0`,
  spool: (n, args) => {
    spoolArgs(args);
    return `ACEK_SPOOL LANE=${n} TYPE=${args.type} COLOR=${args.rgb} VENDOR=${args.vendor}`;
  },
  edit_spool: (_n, args) => {
    spoolArgs(args);
    return `SET_PRINT_FILAMENT_CONFIG CONFIG_EXTRUDER=${headArg(/** @type {number} */ (args.head))} VENDOR=${args.vendor} FILAMENT_TYPE=${args.type} FILAMENT_SUBTYPE='' FILAMENT_COLOR_RGBA=${/** @type {string} */ (args.rgb).toUpperCase()}FF FORCE=1`;
  },
  dry: (_n, args) =>
    `ACEK_DRY TEMP=${posNum(/** @type {number} */ (args.temp), 'temp')} DURATION=${posNum(/** @type {number} */ (args.minutes), 'minutes')}`,
  dry_mode: (_n, args) => {
    if (!DRYER_MODES.includes(/** @type {string} */ (args.mode))) throw new RefusedError('mode');
    return `ACEK_DRY_MODE MODE=${args.mode}`;
  },
  dry_stop: () => 'ACEK_DRY_STOP',
  dry_clear: () => 'ACEK_DRY_CLEAR',
  dry_log: () => 'ACEK_DRYER_LOG',
  fans_on: (_n, args) => `ACEK_FAN ON=1 SECONDS=${posNum(/** @type {number} */ (args.seconds), 'seconds')}`,
  fans_off: () => 'ACEK_FAN ON=0',
  // The U1 screen's head load (the AUTO_FEEDING_BATCH macro). EXTRUDER is the head, 0-based.
  lane_active: (n) => `ACEK_LANE_ACTIVE LANE=${n}`,
  // The head takes the lane's spool, as a swap writes it: its tag, else its ACEK_SPOOL record.
  head_spool: (n) => `ACEK_HEAD_SPOOL LANE=${n}`,
  // A lane with no material set is pushed to its head's sensor, its tag read on the way, before the load.
  head_prepare: (n) => `ACEK_HEAD_PREPARE LANE=${n}`,
  feed_start: () => 'AUTO_FEEDING_BATCH ACTION=START',
  feed_doing: (_n, args) =>
    `AUTO_FEEDING_BATCH ACTION=DOING EXTRUDER=${headArg(/** @type {number} */ (args.head))} LOAD=1`,
  feed_stop: () => 'AUTO_FEEDING_BATCH ACTION=STOP',
  flap: (_n, args) => {
    if (!FLAP_WHICH.includes(/** @type {string} */ (args.which))) throw new RefusedError('which');
    return `ACEK_FLAP WHICH=${args.which} OPEN=${args.open ? 1 : 0}`;
  },
};

/** @param {string} id @param {number | null} n @param {GcodeArgs} args */
function gcodeScript(id, n, args) {
  if (
    LANE_ACTIONS.includes(id) &&
    !(Number.isInteger(n) && /** @type {number} */ (n) >= 1 && /** @type {number} */ (n) <= LANES)
  ) {
    throw new RefusedError('lane', { max: LANES });
  }
  if (!Object.hasOwn(GCODE, id)) throw new RefusedError('no_action');
  return GCODE[id](n, args);
}

// Load into head N, as the lines that it sends one by one. It is the U1 screen's own head load.
// START saves the heads' targets, DOING heats the head and loads it, and returns when the load ends.
// STOP turns the heads' heaters off outside a print, and lets the next load start.
// On a shared head, select names the lane as the head's lane first (ACEK_LANE_ACTIVE). Else select is null.
// Before START, prepare reads the tag of a lane whose material is not set yet (ACEK_HEAD_PREPARE), and it is
// refused when the material stays unset. Then spool gives the head the lane's spool (ACEK_HEAD_SPOOL): the U1
// reads the load temperature from it.
// head is the head, 0-3. unit is the name of the lane's unit, and null for [ace2k].
/**
 * @param {Status | null | undefined} s
 * @param {number} i
 * @param {string | null | undefined} unit
 * @returns {{ head: number, select: string | null, prepare: string, start: string, doing: string, stop: string,
 *   spool: string }}
 */
export function loadHeadPlan(s, i, unit) {
  const head = /** @type {number} */ (headOf(s, i)); // The action is offered only for a lane with a head.
  return {
    head,
    // The action is offered only while the head's sensor reads it empty. So this line only names the lane
    // of an empty head. The spool line after it does last: the head keeps the setting even when the load
    // after it fails or never starts (the operator's choice).
    select: sharedWith(s, i).length ? gcodeFor('lane_active', i + 1, {}, unit) : null,
    prepare: gcodeFor('head_prepare', i + 1, {}, unit),
    start: gcodeFor('feed_start', null),
    doing: gcodeFor('feed_doing', null, { head }),
    stop: gcodeFor('feed_stop', null),
    spool: gcodeFor('head_spool', i + 1, {}, unit),
  };
}

/** @param {Status | null | undefined} s @param {boolean} online @returns {import('./types.js').TopbarView} */
export function topbarView(s, online) {
  const a = s?.ace2k || {};
  const failing = a.health?.failing;
  return {
    version: a.version || null,
    link: a.link_proven === true,
    health: a.health?.ok !== false,
    failing: failing?.length ? failing : null,
    history: s?.ace2k_u1_history?.error || null,
    // Why the adapter paused the print, or keeps it paused (ace2k_u1.pause_reason).
    // It shows only while the print is paused, so a reason with no pause after it never shows.
    paused: (paused(s) && s?.ace2k_u1?.pause_reason) || null,
    online,
    stopLanes: stopAllLanes(s),
    stopWords: stopAllWords(s),
  };
}
