import {
  LANES,
  sliceFor,
  laneOf,
  headOf,
  spoolIn,
  colorOf,
  isCurrent,
  laneText,
  headSetting,
  sharedHeadsView,
  pathLoaded,
  spoolOf,
} from './model.js';
import { chipText, headAt, laneChip } from './lane-state.js';
import { LANE_ERRORS, laneErrs } from './model.js';
import { unitNames } from './state.js';
import { DASH, isNum, pct } from './format.js';
import { deep } from './diagram-layout.js';
import { HEADS } from './print-plan.js';
import { lanesLine, u1SpoolLine } from './words.js';

// What the Units diagram shows, worked out from the status: its lanes, heads and the words under them. It has no
// markup, so the diagram, the phone lists and the printing view all read it from here.

/** @typedef {import('./types.js').AppState} AppState */
/** @typedef {import('./types.js').UiCtx} UiCtx */
/** @typedef {import('./types.js').Status} Status */
/** @typedef {import('./diagram-layout.js').DModel} DModel */
/** @typedef {import('./diagram-layout.js').DLane} DLane */
/** @typedef {import('./diagram-layout.js').DHead} DHead */

// The one word a bay tile has room for (56 px at 11 px, so 8 characters): "Stalled" for "Motor stalled".
// An error the page does not know reads "Error".
/** @param {unknown} err */
const errorShortKey = (err) =>
  typeof err === 'string' && LANE_ERRORS.includes(err) ? `lane.error_short.${err}` : 'chip.error';

// One lane of a unit as the diagram draws it. Its spool is null while the bay is empty.
/** @param {Status} slice @param {string | null} name @param {number} i @returns {DLane} */
function laneModel(slice, name, i) {
  const head = headOf(slice, i);
  const lane = laneOf(slice, i);
  const sp = spoolIn(slice, i);
  const empty = !lane?.insert;
  const chip = laneChip(slice, i);
  const current = head !== null && isCurrent(slice, head, name, i);
  // The path from the bay to its head has filament in it (model.js pathLoaded): every "loaded" cue reads it.
  const filled = head !== null && pathLoaded(slice, head, current, !empty);
  const path = filled ? spoolOf(slice, i) : null;
  return {
    key: laneText(slice, i),
    n: i + 1,
    head,
    empty,
    spool: empty ? null : { colour: colorOf(sp), material: sp?.type ?? null, tag: sp?.fromTag === true },
    current,
    filled,
    // The filament in the path: the lane's spool (in the bay, else, for a run-out tail, its last known one: record,
    // tag or head setting).
    pathColour: colorOf(path),
    pathMaterial: path?.type ?? null,
    error: laneErrs(lane) ? chipText(lane, 'error') : null,
    errorShort: laneErrs(lane) ? errorShortKey(lane?.error) : null,
    // The bay card's state word. An empty bay already says "empty", so it gets none.
    chip,
    state: chip === 'empty' || chip === 'error' ? null : chipText(lane, chip),
  };
}

// The diagram's model from the status: every unit's lanes, and each head with the lane loaded in it.
// A head that no lane feeds runs on the U1's own spool, its setting read from print_task_config.
/** @param {AppState} app @returns {DModel} */
export function diagramModel(app) {
  const s = app.status;
  const units = unitNames(app).map((name) => {
    const slice = sliceFor(s, name);
    return { name, lanes: Array.from({ length: LANES }, (_, i) => laneModel(slice, name, i)) };
  });
  const lanes = units.flatMap((u) => u.lanes);
  const unknown = new Set(sharedHeadsView(s, true).flatMap((h) => (h.unknown ? [h.head] : [])));
  const heads = Array.from({ length: HEADS }, (_, head) => {
    const fed = lanes.some((l) => l.head === head);
    const setting = fed ? null : headSetting(s, head, false);
    return {
      head,
      u1Spool: fed ? null : { colour: colorOf(setting), material: setting?.type ?? null },
      loaded: lanes.find((l) => l.filled && l.head === head)?.key ?? null,
      unknown: unknown.has(head),
    };
  });
  return { units, heads };
}

// A head's lanes as short runs: "a:1–4", "a:1 · b:1", "1–2 · 4".
/** @param {{ unit: string | null, lane: DLane }[]} lanes @returns {string} */
export function headLanesText(lanes) {
  /** @type {{ unit: string | null, first: DLane, last: DLane }[]} */
  const runs = [];
  for (const { unit, lane } of lanes) {
    const run = runs.at(-1);
    if (run && run.unit === unit && run.last.n + 1 === lane.n) run.last = lane;
    else runs.push({ unit, first: lane, last: lane });
  }
  return runs.map((r) => (r.first === r.last ? r.first.key : `${r.first.key}–${r.last.n}`)).join(' · ');
}

// The mark for a colour that vanishes on a dark card (the style that paints it is words.js fil).
/** @param {string | null | undefined} c */
export const darkFil = (c) => (deep(c) ? 'true' : undefined);

// A head's first line: the lane in it and its material; "lane ?" while several lanes feed it and the adapter
// does not know which one is in; "—" when nothing is loaded.
/** @param {DLane | undefined} cur @param {boolean} unknown @param {UiCtx['t']} t */
function headLine(cur, unknown, t) {
  if (cur) return `${cur.key} · ${cur.pathMaterial ?? t('units.unknown_mark')}`;
  return unknown ? t('units.lane_unknown') : DASH;
}

// The lanes that feed a head, each with the name of its unit.
/** @param {DModel} model @param {number} head @returns {{ unit: string | null, lane: DLane }[]} */
export const headLanes = (model, head) =>
  model.units.flatMap((u) => u.lanes.filter((l) => l.head === head).map((lane) => ({ unit: u.name, lane })));

// A head card's two lines and its colour: the lane in it and what feeds it, or the U1's own spool.
/** @param {DModel} model @param {DHead} h @param {UiCtx['t']} t */
export function headView(model, h, t) {
  if (h.u1Spool) {
    return {
      colour: h.u1Spool.colour,
      line1: u1SpoolLine(t, h.u1Spool.material),
      line2: t('units.no_ace'),
      warn: false,
    };
  }
  const lanes = headLanes(model, h.head);
  const cur = lanes.find((x) => x.lane.key === h.loaded)?.lane;
  const list = headLanesText(lanes);
  return {
    colour: cur?.pathColour ?? null,
    line1: headLine(cur, h.unknown === true, t),
    warn: !cur && h.unknown === true,
    line2: lanesLine(t, lanes.length, list),
  };
}

/** @typedef {'printing' | 'in_head'} BayMark */
// The word on each mark.
export const MARK_WORDS = { printing: 'printing.mark_printing', in_head: 'printing.mark_in_head' };

// A unit's chamber reading for its footer: "27 °C · 41 %".
/** @param {Status} slice */
export function envText(slice) {
  const c = slice.ace2k?.chamber;
  return `${isNum(c) ? `${Math.round(c)} °C` : DASH} · ${pct(slice.ace2k?.humidity)}`;
}

// The bay whose side panel is open, or null.
/** @param {AppState} app */
export const openBay = (app) => (app.ui.panel?.kind === 'bay' ? (app.ui.panel.key ?? null) : null);

// The head (0-3) whose side panel is open, or null.
/** @param {AppState} app @returns {number | null} */
export const openHead = (app) => (app.ui.panel?.kind === 'head' ? headAt(app.ui.panel.id) : null);
