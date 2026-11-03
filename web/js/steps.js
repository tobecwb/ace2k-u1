// The guided steps of the Print tab: what to change in the bays, one step at a time, and when a
// step is certainly done. Also the "eject all out of place" run. No effects except runEjectAll's
// calls to the eject it is given, and autoAdvance and the step moves, which change app.print only.
import {
  choiceFor,
  colourDistance,
  colourName,
  laneByKey,
  lookOf,
  mergeRoot,
  usedTest,
  pinOf,
  planId,
  fitsColour,
  withinClose,
} from './print-plan.js';
import { planNow } from './state.js';
import { colourVerdict, knownMaterials, otherMaterials, spoolMismatch, spoolVerdict } from './planner.js';
import { headOf, isCurrent } from './model.js';
import { laneAt } from './lane-state.js';
import { headName } from './words.js';

/** @typedef {import('./types.js').PlanLane} LiveLane */
/** @typedef {import('./types.js').AppState} AppState */
/** @typedef {import('./state.js').PlanNow} PlanNow */
/** @typedef {(key: string, vars?: Record<string, unknown>) => string} Words */
/** @typedef {import('./types.js').PlanSpool} Spoolish */
/**
 * @typedef {{ kind: 'out', key: string, spool: Spoolish | null, unknown?: boolean }
 *   | { kind: 'move', from: string, key: string, spool: Spoolish | null, tools: number[] }
 *   | { kind: 'putBack', key: string, spool: Spoolish, tools: number[] }
 *   | { kind: 'put' | 'check', key: string, tools: number[] }} Step
 */

// The step for a line that moves a spool. When eject all already emptied the source, the spool is in
// hand, held by its source key, and the step is to put it back.
/** @param {{ tools: number[], key: string, from: string }} line @param {LiveLane[]} lanes @param {Spoolish[]} held @returns {Step} */
function moveStep(line, lanes, held) {
  const src = laneByKey(lanes, line.from);
  const back = src?.empty ? held.find((h) => h.from === line.from) : undefined;
  if (back) return { kind: 'putBack', key: line.key, spool: back, tools: line.tools };
  return { kind: 'move', from: line.from, key: line.key, spool: src?.spool ?? null, tools: line.tools };
}

// The steps for a placement over the live lanes. held lists the spools that eject all took out and
// that go to another bay.
/** @param {import('./planner.js').Placement} p @param {LiveLane[]} lanes @param {Spoolish[]} held @returns {Step[]} */
export function stepsOf(p, lanes, held) {
  /** @type {Step[]} */
  const out = p.takeOut.map((key) => {
    const l = laneByKey(lanes, key);
    return l?.unknown
      ? { kind: 'out', key, spool: null, unknown: true }
      : { kind: 'out', key, spool: l?.spool ?? null };
  });
  /** @type {Step[]} */
  const moves = [];
  /** @type {Step[]} */
  const puts = [];
  /** @type {Step[]} */
  const checks = [];
  for (const line of p.lines) {
    if (line.act === 'move') moves.push(moveStep(line, lanes, held));
    else if (line.act === 'put') puts.push({ kind: 'put', key: line.key, tools: line.tools });
    else if (line.act === 'check') checks.push({ kind: 'check', key: line.key, tools: line.tools });
  }
  return [...out, ...moves, ...puts, ...checks];
}

// True when the step is certainly done: the bay emptied (out), or a tagged spool of the step's
// material and colour sits in the bay (move, put back, put). A check is never done by itself.
/**
 * @param {Step} s @param {LiveLane[]} lanes
 * @param {(tools: number[]) => { colour: string | null, material: string | null }} colourOf
 */
export function stepDone(s, lanes, colourOf) {
  const l = laneByKey(lanes, s.key);
  if (!l) return false;
  if (s.kind === 'out') return l.empty;
  if (s.kind === 'check' || l.empty || !l.spool?.tag) return false;
  return fitsColour(colourOf(s.tools), l);
}

// The bays the steps empty: every take-out and every move's source that holds a spool.
/** @param {Step[]} steps @returns {string[]} */
const emptied = (steps) =>
  steps.flatMap((s) => (s.kind === 'out' ? [s.key] : s.kind === 'move' && s.spool ? [s.from] : []));

// The bays eject all empties: those the steps empty, but the ones it must leave (stays: see stays).
/** @param {Step[]} steps @param {Set<string>} [stays] @returns {string[]} */
export const ejectList = (steps, stays = new Set()) => emptied(steps).filter((k) => !stays.has(k));

// The bays eject all leaves alone, of those the steps empty (stays: see stays).
/** @param {Step[]} steps @param {Set<string>} [stays] @returns {string[]} */
export const ejectSkipped = (steps, stays = new Set()) => emptied(steps).filter((k) => stays.has(k));

// The head a bay's spool is loaded in, or null while it waits in its bay. The U1 heats that head to unload it.
/** @param {AppState} app @param {string} key @returns {number | null} */
export function loadedHead(app, key) {
  const at = laneAt(app, key);
  if (!at) return null;
  const head = headOf(at.slice, at.i);
  return head !== null && isCurrent(at.slice, head, at.unit, at.i) ? head : null;
}

// The bays eject all must leave: the spool has no material set for the adapter (bayProblem's 'unset':
// materialSet false) and is loaded in its head. The host refuses that eject, which needs the U1's unload and so heat
// for a material it does not know. A spool with no material that waits in its bay is rolled back cold: it stays in
// the run.
/** @param {AppState} app @param {LiveLane[]} lanes @returns {Set<string>} */
function staysOf(app, lanes) {
  return new Set(lanes.flatMap((l) => (l.materialSet === false && loadedHead(app, l.key) !== null ? [l.key] : [])));
}

let ejectBusy = false;

// Ejects the bays one at a time, in order, and stops at the first failure. Null when a run is on. alive is asked
// before each bay: once it is false (the run's file or run went from the page), the run ends there, stale.
/**
 * @param {string[]} keys @param {(key: string) => Promise<unknown>} eject
 * @param {(key: string, state: 'running' | 'done' | 'failed', msg?: string) => void} onEach
 * @param {() => boolean} [alive]
 * @returns {Promise<{ done: string[], failed: { key: string, msg: string } | null, stale?: boolean } | null>}
 */
export async function runEjectAll(keys, eject, onEach, alive = () => true) {
  if (ejectBusy) return null;
  ejectBusy = true;
  /** @type {string[]} */
  const done = [];
  try {
    for (const key of keys) {
      if (!alive()) return { done, failed: null, stale: true };
      onEach(key, 'running');
      try {
        await eject(key);
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        onEach(key, 'failed', msg);
        return { done, failed: { key, msg } };
      }
      done.push(key);
      onEach(key, 'done');
    }
    return { done, failed: null };
  } finally {
    ejectBusy = false;
  }
}

// A step's name in the guide: "o:<bay>" for a take-out, "i:<bay>" for what goes into a bay. A move that eject all
// turned into a put back, or a put that became a check once a spool went in (its material or colour cannot be
// compared: see asShown), keeps its name.
/** @param {Step} s */
export const stepId = (s) => (s.kind === 'out' ? `o:${s.key}` : `i:${s.key}`);

// The file colour that a step's tools print: the colour and material of the tool they join, and its number.
/** @param {PlanNow} now @param {number[]} tools */
export function toolLook(now, tools) {
  const n = mergeRoot(now.plan, now.merges, tools[0]);
  const tool = now.plan.tools[n];
  return { n, colour: tool?.colour ?? null, material: tool?.material ?? null };
}

/**
 * The guide over the live bays. trail lists the steps in the order they were first shown, so a step that was done
 * (and left the live list) keeps its place in "step k of N". at is the trail index of the current step: the first
 * live step from app.print.step on; trail.length once every step was passed. cur is that step, else null.
 * stays lists the bays eject all must leave (staysOf).
 * @typedef {{ now: PlanNow, steps: Step[], trail: string[], at: number, cur: Step | null, live: Set<string>,
 *   stays: Set<string> }} Guide
 */

// The guide of the plan shown, or null while there is none (no file, no plan, nothing to place, or more colours
// than bays: then there is no placement to prepare, and the header says it does not fit).
// now is the plan view's inputs; the start passes the ones it sends, so the guide and the script read one snapshot.
/** @param {AppState} app @param {PlanNow | null} [now] @returns {Guide | null} */
export function guideOf(app, now = planNow(app)) {
  if (!now?.place?.fits) return null;
  const steps = stepsOf(now.place, now.lanes, app.print.held ?? []);
  const ids = steps.map(stepId);
  const before = app.print.trail ?? [];
  const trail = [...before, ...ids.filter((id) => !before.includes(id))];
  const live = new Set(ids);
  let at = app.print.step ?? 0;
  while (at < trail.length && !live.has(trail[at])) at += 1;
  const cur = at < trail.length ? steps[ids.indexOf(trail[at])] : null;
  return { now, steps, trail, at, cur, live, stays: staysOf(app, now.lanes) };
}

// Keeps the guide's trail, its current step and the kind that step was first shown as on app.print, when they changed.
/** @param {AppState} app @param {Guide} g @param {number} at */
function keep(app, g, at) {
  const id = g.trail[at];
  // The kind the step was first shown as stays while it is the current step.
  const was = app.print.shown;
  // Past the last step there is no id, and shown is null: nothing was shown then.
  const kind = was && was.id === id ? was.kind : g.steps.find((s) => stepId(s) === id)?.kind;
  const shown = id && kind ? { id, kind } : null;
  const same =
    app.print.step === at &&
    (app.print.trail ?? []).length === g.trail.length &&
    app.print.shown?.id === shown?.id &&
    app.print.shown?.kind === shown?.kind;
  if (!same) app.print = { ...app.print, trail: g.trail, step: at, shown };
}

// The step as the done test reads it. A spool that went into the bay of a put can turn that bay's line into a check
// when the planner cannot compare its material (a colour of no material in the file, say) though its colour is close
// to every colour of the group: it is then tested as the put it was shown as, so a tagged spool of that colour passes
// it. A check for any other reason (a colour close to some only, a material conflict, a colour not known) stays one.
/** @param {AppState} app @param {PlanNow} now @param {Step} s @returns {Step} */
export function asShown(app, now, s) {
  const was = app.print.shown;
  if (s.kind !== 'check' || was?.id !== stepId(s) || was.kind !== 'put') return s;
  const lane = laneByKey(now.lanes, s.key);
  const tools = groupTools(now, s.tools);
  if (!lane || spoolVerdict(lane, tools) !== 'unsure' || colourVerdict(lane, tools) !== 'close') return s;
  return { kind: 'put', key: s.key, tools: s.tools };
}

// The plan tools of a step's group.
/** @param {PlanNow} now @param {number[]} tools */
const groupTools = (now, tools) => tools.map((x) => now.plan.tools[x]).filter(Boolean);

// The pin a done step leaves on its colour: the spool in the bay when it is the step's (its material and a close
// colour), else the spool the step moves there, remembering the one the bay still shows (was), so the pick holds
// while the status catches up. A new spool the page has not seen yet pins nothing.
/** @param {Step} s @param {LiveLane | undefined} lane @param {{ colour: string | null, material: string | null }} want */
function pinFor(s, lane, want) {
  if (lane?.spool && fitsColour(want, lane)) return pinOf(lane);
  const spool = s.kind === 'move' || s.kind === 'putBack' ? s.spool : null;
  const pin = spool ? { uid: spool.uid ?? null, look: lookOf(spool.material, spool.colour), was: pinOf(lane) } : null;
  return pin && (pin.uid || pin.look) ? pin : null;
}

// A step that put a spool in a bay is done: the colour of its tools now prints from that bay. The pick of the
// colour (its merge root, so the merges stay) becomes the bay, pinned on its spool as a pick in the colour picker
// is, and a held spool that came from the step's move leaves the hand. The planner then owns that bay, and the
// step's line reads "there". A take-out changes no pick.
// auto: settled by the page itself (settleMatches), not by the user's Done or a step seen done.
/** @param {AppState} app @param {Guide} g @param {Step} s @param {boolean} [auto] */
function settle(app, g, s, auto = false) {
  if (s.kind === 'out') return;
  const { plan, merges, lanes } = g.now;
  const n = mergeRoot(plan, merges, s.tools[0]);
  const pin = pinFor(s, laneByKey(lanes, s.key), toolLook(g.now, s.tools));
  const pinned = { ...(app.print.pinned ?? {}) };
  if (pin) pinned[n] = pin;
  else delete pinned[n];
  const from = s.kind === 'move' ? s.from : s.kind === 'putBack' ? s.spool.from : null;
  app.print = {
    ...app.print,
    choice: { ...choiceFor(app.print, plan), [n]: s.key },
    pinned,
    choiceId: planId(plan),
    choiceTools: plan.tools,
    held: (app.print.held ?? []).filter((h) => h.from !== from),
    // A pick the page settled itself is kept apart from the user's own (settleMatches may settle it again).
    auto: [...(app.print.auto ?? []).filter((x) => x !== n), ...(auto ? [n] : [])],
  };
}

// A spool the planner found to be a group's own in a bay (a "there" line whose bay is not yet the colour's pick) is
// settled as a done step is: the colour's pick becomes that bay, pinned on its spool, so the bay stays the colour's
// on later statuses (another bay emptied on the same head does not move it). The colour is then listed as settled by
// the page (app.print.auto). Only a colour with no pick or pin of the user's is filled, or one this function settled
// before. Only a spool really in its bay counts (the live lanes, not one held in hand by eject all), and only one that
// matches the group (spoolVerdict 'match'). Returns true when it changed a pick.
/** @param {AppState} app @param {Guide} g @returns {boolean} */
function settleMatches(app, g) {
  const { plan, merges, pick, place, user, lanes } = g.now;
  const pinned = app.print.pinned ?? {};
  const auto = app.print.auto ?? [];
  let changed = false;
  for (const line of place?.lines ?? []) {
    const n = mergeRoot(plan, merges, line.tools[0]);
    // The user's own pick or pin is never touched; one this function settled before may be settled again.
    const mine = (user[n] != null || pinned[n] != null) && !auto.includes(n);
    if (line.act !== 'there' || pick[n] === line.key || mine) continue;
    const lane = laneByKey(lanes, line.key);
    if (!lane || lane.empty || spoolVerdict(lane, groupTools(g.now, line.tools)) !== 'match') continue;
    settle(app, g, { kind: 'put', key: line.key, tools: line.tools }, true);
    changed = true;
  }
  return changed;
}

// Runs on each status: the current step advances by itself when its change is certainly done (stepDone), and a step
// that left the live list counts as done.
/** @param {AppState} app */
export function autoAdvance(app) {
  const first = guideOf(app);
  if (!first) return;
  // Settling changes only the picks, so the guide is worked out again only then.
  const g = settleMatches(app, first) ? guideOf(app) : first;
  if (!g) return;
  /** @param {number[]} tools */
  const look = (tools) => toolLook(g.now, tools);
  const done = g.cur ? doneStep(app, g, g.cur, look) : null;
  if (!done) {
    keep(app, g, g.at);
    return;
  }
  keep(app, g, g.at + 1);
  settle(app, g, done);
}

// The step that is certainly done now, or null: the current one, or, while the current one takes a spool out of a
// bay, the step that puts the right spool in that same bay (the spool was swapped in one go). Settling that one makes
// the bay's line "there", and the take-out goes with it.
/**
 * @param {AppState} app @param {Guide} g @param {Step} cur
 * @param {(tools: number[]) => { colour: string | null, material: string | null }} look
 * @returns {Step | null}
 */
function doneStep(app, g, cur, look) {
  const shown = asShown(app, g.now, cur);
  if (stepDone(shown, g.now.lanes, look)) return shown;
  if (cur.kind !== 'out') return null;
  const into = g.steps.find((s) => s.kind !== 'out' && s.key === cur.key);
  return into && stepDone(into, g.now.lanes, look) ? into : null;
}

// "Done, next →": the operator says the current step is done. Its colour then prints from its bay (settle).
/** @param {AppState} app */
export function stepNext(app) {
  const g = guideOf(app);
  if (!g?.cur) return;
  keep(app, g, g.at + 1);
  settle(app, g, g.cur);
}

// The trail index of the live step before the current one, or null when there is none.
/** @param {Guide} g @returns {number | null} */
export function prevAt(g) {
  for (let i = Math.min(g.at, g.trail.length) - 1; i >= 0; i -= 1) if (g.live.has(g.trail[i])) return i;
  return null;
}

// "←": back to the live step before.
/** @param {AppState} app */
export function stepPrev(app) {
  const g = guideOf(app);
  const at = g ? prevAt(g) : null;
  if (g && at !== null) keep(app, g, at);
}

// A spool in words: "white PETG" when its colour has a name, else its material, else "spool".
/** @param {Words} t @param {{ colour: string | null, material: string | null } | null} spool @param {string | null} [name] */
function spoolWords(t, spool, name = null) {
  const material = spool?.material ?? t('step.spool');
  return name ? t('step.what', { colour: name, material }) : material;
}

// The name the host gave a bay's colour, while it still holds that colour.
/** @param {PlanNow} now @param {string | undefined} key @param {string | null | undefined} colour */
function nameAt(now, key, colour) {
  const lane = laneByKey(now.lanes, key);
  return lane && lane.colour === colour ? (lane.colour_name ?? null) : null;
}

// The step as one sentence: "Take the white PETG out of bay a:2".
/** @param {Words} t @param {Step} s @param {PlanNow} now @returns {string} */
export function stepText(t, s, now) {
  const { key } = s;
  if (s.kind === 'out') {
    if (s.unknown) return t('step.out_unknown', { key });
    return t('step.out', { key, what: spoolWords(t, s.spool, nameAt(now, key, s.spool?.colour)) });
  }
  if (s.kind === 'move')
    return t('step.move', { key, from: s.from, what: spoolWords(t, s.spool, nameAt(now, s.from, s.spool?.colour)) });
  if (s.kind === 'putBack') return t('step.put_back', { key, what: spoolWords(t, s.spool) });
  const look = toolLook(now, s.tools);
  if (s.kind === 'put') return t('step.put', { key, n: look.n + 1, what: look.material ?? t('step.spool') });
  return t('step.check', { key, n: look.n + 1 });
}

// Why a put or a check step's bay holds a spool that is not the colour's, by the planner's own test (spoolMismatch,
// over every tool of the step's group), or null: an empty bay, a spool that may be the colour's, or a spool put over
// only because the colour asked for a new one. The step's words and its Eject button read it, so they agree.
/** @param {PlanNow} now @param {Step} s @returns {'unknown' | 'material' | 'mixed' | 'colour' | null} */
export function wrongSpoolIn(now, s) {
  if (s.kind !== 'put' && s.kind !== 'check') return null;
  const { lane } = stepBay(now, s.key);
  if (!lane || lane.empty) return null;
  return spoolMismatch(lane, groupTools(now, s.tools));
}

// The bay of a put or a check, as the step's words, its verdict and its Eject read it: one source, the live bay. held
// is true when the bay is empty because eject all took its spool out and it is in the user's hand (the planner still
// counts it there).
/** @param {PlanNow} now @param {string} key @returns {{ lane: LiveLane | undefined, held: boolean }} */
function stepBay(now, key) {
  const lane = laneByKey(now.lanes, key);
  const planned = laneByKey(now.planLanes ?? now.lanes, key);
  return { lane, held: Boolean(lane?.empty && planned && !planned.empty) };
}

// Why a put goes over the spool in a bay, in words, with what the user may do; the material first. No material: eject
// it, or define its material and press Done. Another material: Print cannot use it, so only the eject. Some of the
// materials of colours joined on the bay, not all: one spool cannot print them all, so only the eject. Another colour:
// eject it and put the right one, or press Done to print the colour with that spool anyway (Done picks the bay).
/**
 * @param {Words} t @param {'unknown' | 'material' | 'mixed' | 'colour'} why @param {LiveLane} lane @param {string} key
 * @param {number} n @param {{ colour: string | null, tools: import('./types.js').Tool[] }} want the colour's look and
 *   every tool of its group
 */
function putOverWords(t, why, lane, key, n, want) {
  if (why === 'unknown') return t('step.sub.put_over_unknown', { key, n });
  if (why === 'material')
    return t('step.sub.put_over_material', { key, what: lane.material, want: knownMaterials(want.tools).join('/'), n });
  if (why === 'mixed')
    return t('step.sub.put_over_mixed', {
      key,
      what: lane.material,
      other: otherMaterials(lane, want.tools).join('/'),
    });
  const what = spoolLook(t, lane);
  const name = colourName(want.colour);
  // A colour the page cannot name is never written raw: the words say "that colour" instead.
  return name ? t('step.sub.put_over', { key, what, n, want: name }) : t('step.sub.put_over_any', { key, what, n });
}

// A bay's spool in words, its colour named: "red PLA", by the host's colour name, else the page's nearest name.
/** @param {Words} t @param {LiveLane} lane */
function spoolLook(t, lane) {
  return spoolWords(t, lane, lane.colour_name ?? colourName(lane.colour));
}

// The line under the step: why, and whether the page moves on by itself. A move into a bay still full has none.
/** @param {Words} t @param {Step} s @param {PlanNow} now @returns {string} */
export function stepSub(t, s, now) {
  if (s.kind === 'out') return s.unknown ? t('step.sub.out_unknown') : t('step.sub.out');
  if (s.kind === 'put') return putSub(t, s, now);
  if (s.kind === 'check') return checkSub(t, s, now);
  if (s.kind === 'putBack') return t('step.sub.put_back');
  return laneByKey(now.lanes, s.key)?.empty ? t('step.sub.move_empty') : '';
}

// The line under a put. Over a spool that is not the colour's it says why, as the planner decided it (wrongSpoolIn);
// over one that may be the colour's, that the colour asked for a new spool.
/** @param {Words} t @param {Step & { kind: "put" | "check", tools: number[] }} s @param {PlanNow} now @returns {string} */
function putSub(t, s, now) {
  const { lane, held } = stepBay(now, s.key);
  const look = toolLook(now, s.tools);
  const n = look.n + 1;
  if (held) return t('step.sub.in_hand', { key: s.key, n });
  const why = wrongSpoolIn(now, s);
  if (lane && why)
    return putOverWords(t, why, lane, s.key, n, { colour: look.colour, tools: groupTools(now, s.tools) });
  if (lane && !lane.empty) return t('step.sub.put_fresh', { key: s.key, n });
  return t('step.sub.put', { n });
}

// The line under a check, by the verdict of its live bay (stepBay; a spool in the user's hand said so): close to some
// of the colours (one it is close to, named); not comparable (the file's colour, the spool's colour, or a material
// not known); a match needs no words; any mismatch takes the put's words.
/** @param {Words} t @param {{ key: string, tools: number[] }} s @param {PlanNow} now @returns {string} */
function checkSub(t, s, now) {
  const look = toolLook(now, s.tools);
  const n = look.n + 1;
  const tools = groupTools(now, s.tools);
  const { lane, held } = stepBay(now, s.key);
  if (held) return t('step.sub.in_hand', { key: s.key, n });
  if (!lane || lane.empty) return t('step.sub.check');
  const v = spoolVerdict(lane, tools);
  if (v === 'match') return '';
  if (v === 'some') {
    const near = s.tools.find((x) => colourVerdict(lane, [now.plan.tools[x]]) === 'close') ?? s.tools[0];
    return t('step.sub.check_some', { n: near + 1 });
  }
  if (v !== 'unsure') return putOverWords(t, v, lane, s.key, n, { colour: look.colour, tools });
  const bare = s.tools.find((x) => !now.plan.tools[x]?.colour);
  if (bare !== undefined) return t('step.sub.check_no_file_colour', { n: bare + 1 });
  return lane.colour ? t('step.sub.check_unsure', { n }) : t('step.sub.check');
}

// What the start asks about when Print sends the bays as they are (choice): each colour the file prints whose bay
// holds a spool of a clearly other colour, or whose colour the file gives no material ("T0 prints colour 2 with the
// blue PLA spool in a:1"; the host's colour name, else the colour itself: "the PLA spool in a:1 (#1f6feb)"), and each
// guided step still pending for a colour. One line per colour (a step shared by several, once); a confirm, never a block, so the
// operator chooses freely.
/** @param {Words} t @param {PlanNow} now @param {import('./print-plan.js').Choice} choice @param {Step[]} steps @returns {string[]} */
export function asIsChecks(t, now, choice, steps) {
  const used = usedTest(now.plan.order);
  /** @type {string[]} */
  const out = [];
  now.plan.tools.forEach((tool, n) => {
    if (!used(n)) return;
    // A joined colour counts too: it prints from the bay of the colour it joins, whatever its own colour.
    const line = bayCheck(t, tool, n, laneByKey(now.lanes, choice[n]));
    const step = line ? null : steps.find((s) => s.kind !== 'out' && s.tools.includes(n));
    const text = line ?? (step ? stepText(t, step, now) : null);
    if (text && !out.includes(text)) out.push(text);
  });
  return out;
}

// The line asIsChecks says of colour n's bay, or null: the file gives the colour no material, or the spool is of a
// clearly other colour. (A spool of no material never prints: Print is blocked for it, bayProblem.)
/**
 * @param {Words} t @param {{ colour?: string | null, material?: string | null }} tool @param {number} n
 * @param {LiveLane | undefined} lane
 * @returns {string | null}
 */
function bayCheck(t, tool, n, lane) {
  if (!lane) return null;
  const at = { head: headName(t, lane.head), n: n + 1, key: lane.key };
  // A colour whose material the file does not give prints with whatever spool the bay holds.
  if (!tool.material && lane.material) return t('print.as_is_tool_unknown', { ...at, material: lane.material });
  if (!tool.colour || !lane.colour || withinClose(colourDistance(tool.colour, lane.colour))) return null;
  const name = lane.colour_name ?? null;
  const what = spoolWords(t, lane, name);
  return name
    ? t('print.as_is_colour', { ...at, what })
    : t('print.as_is_colour_hex', { ...at, what, colour: lane.colour });
}
