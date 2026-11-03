// The plan logic of the Print panel. The module has no effects, so it runs under node.
// The adapter matches a G-code file's tools to spools (ACEK_PLAN), and the user can change that choice.
// This module works out each head's tools and lane swaps, and the start script.
// The start script holds ACEK_PLAN_SET, the U1's tool map and the file. js/ui/PlanView.js draws the plan.
import { t } from './i18n.js';
import {
  adapterLane,
  colorOf,
  headOf,
  LANES,
  laneOf,
  laneText,
  sliceFor,
  spoolOf,
  unitName,
  unitsOf,
} from './model.js';

/** @typedef {import('./types.js').Plan} Plan */
/** @typedef {import('./types.js').Status} Status */
/** @typedef {import('./types.js').PlanSpool} PlanSpool */
/** @typedef {import('./types.js').Tool} Tool */
/** @typedef {import('./types.js').PlanLane} PlanLane */
/** @typedef {import('./types.js').Pin} Pin */
/** @typedef {import('./types.js').PrintState} PrintState */
/** @typedef {import('./types.js').PrintOptions} PrintOptions */
/** @typedef {Record<number, string | null>} Choice  The lane key picked for each tool. */

export const HEADS = 4;

// A file name as a quoted G-code parameter.
// A quote, a comment mark, a checksum mark or a line break would cut the line where Klipper reads it.
// So such a name is refused.
/** @param {unknown} name */
function quoted(name) {
  const s = String(name ?? '');
  if (!s || /["#;*\r\n]/.test(s)) throw new Error(t('print.bad_name', { name: s }));
  return `"${s}"`;
}

/** @param {string} file */
export const planScript = (file) => `ACEK_PLAN FILE=${quoted(file)}`;

// When Print is refused because the file changed since its plan, the file is planned again.
// Then it returns the toast that says so, and the script. Any other refusal returns null.
// The adapter's refusal lines are in tests/fixtures/adapter-refusals.json.
// The adapter's tests check that file against its own text.
/**
 * @param {unknown} msg
 * @param {string} file
 * @returns {{ toast: string, script: string } | null}
 */
export function refusalAction(msg, file) {
  if (!/changed since its plan/.test(String(msg))) return null;
  return { toast: t('print.replanned', { file }), script: planScript(file) };
}

// A plan's identity is its file and the file's mtime. The page's choice is made for one plan.
/** @param {Plan | null | undefined} plan @returns {string | null} */
export const planId = (plan) => (plan ? `${plan.file}\n${plan.mtime}` : null);
// The file that a plan's identity names. A file name never holds a line break, because quoted refuses it.
/** @param {string | null} id */
const idFile = (id) => (id == null ? null : id.slice(0, id.lastIndexOf('\n')));

// The page's choice for a plan.
// It is null when the choice was made for another file, or when there is no plan.
// A choice made for an earlier plan of the same file is carried over. A tool keeps its lane while two things hold.
// The tool still exists with the colour and material it had, so the file was not sliced again with other colours.
// And the lane is still offered to the tool. The other tools lose their lane, and dropped lists them.
// A lane is offered while its bay holds a spool that is not of another known material.
// A tool whose material is not known keeps its lane, because its select lists the chosen lane
// and the adapter accepts it.
// A pick of NEW (a new spool) is kept while its tool is unchanged: it names no lane.
// The user's merges and the pinned spools go with it: a merge is kept while both its tools are unchanged and still
// of the same known material, and a pin while its tool's pick is kept.
/**
 * @param {PrintState} print
 * @param {Plan | null | undefined} plan
 * @returns {{ choice: Choice, dropped: { n: number, key: string }[], merges: Record<number, number>,
 *   pinned: Record<number, Pin> } | null}
 */
export function carriedChoice(print, plan) {
  if (!plan || idFile(print.choiceId) !== plan.file) return null;
  const merges = print.merges ?? {};
  const pinned = print.pinned ?? {};
  if (print.choiceId === planId(plan)) return { choice: print.choice, dropped: [], merges, pinned };
  /** @param {number} n */
  const unchanged = (n) => {
    const tool = plan.tools[n];
    const before = print.choiceTools?.[n];
    return Boolean(tool && before && sameTool(tool, before));
  };
  return { ...carriedPicks(print.choice, pinned, plan, unchanged), merges: carriedMerges(merges, plan, unchanged) };
}

// The picks (and their pins) that a re-plan keeps: the tool is unchanged, and its pick is NEW or a lane the new plan
// still offers it. The others are dropped.
/**
 * @param {Choice} picks @param {Record<number, Pin>} pinned @param {Plan} plan @param {(n: number) => boolean} unchanged
 * @returns {{ choice: Choice, dropped: { n: number, key: string }[], pinned: Record<number, Pin> }}
 */
function carriedPicks(picks, pinned, plan, unchanged) {
  /** @type {Choice} */
  const choice = {};
  /** @type {Record<number, Pin>} */
  const pins = {};
  const dropped = [];
  for (const [k, key] of Object.entries(picks)) {
    if (!key) continue;
    const n = Number(k);
    const lane = laneByKey(plan.lanes ?? [], key);
    // True when the new plan still offers the pick. An unchanged tool is in the plan, so its material can be read.
    /** @returns {boolean} */
    const offered = () =>
      key === NEW || Boolean(lane && !lane.empty && materialMatch(plan.tools[n].material, lane.material) !== 'other');
    if (!unchanged(n) || !offered()) {
      dropped.push({ n, key });
      continue;
    }
    choice[n] = key;
    if (pinned[n]) pins[n] = pinned[n];
  }
  return { choice, dropped, pinned: pins };
}

// The merges that a re-plan keeps: both tools unchanged and the join still allowed (canJoin).
/**
 * @param {Record<number, number>} merges @param {Plan} plan @param {(n: number) => boolean} unchanged
 * @returns {Record<number, number>}
 */
function carriedMerges(merges, plan, unchanged) {
  /** @type {Record<number, number>} */
  const kept = {};
  for (const [k, j] of Object.entries(merges)) {
    const n = Number(k);
    if (unchanged(n) && unchanged(j) && canJoin(plan, n, j)) kept[n] = j;
  }
  return kept;
}

// A spool's look, as a pin compares it: its material (as the adapter compares materials) and colour, in lower case.
// null unless both are known.
/** @param {string | null | undefined} mat @param {string | null | undefined} colour @returns {string | null} */
export const lookOf = (mat, colour) => (material(mat) && colour ? `${material(mat)}:${colour.toLowerCase()}` : null);

// The pin of the spool in a bay: its tag's uid and its look, as far as they are known, or null for an empty bay.
/** @param {PlanLane | undefined} lane @returns {Pin | null} */
export function pinOf(lane) {
  const spool = lane?.spool;
  return spool ? { uid: spool.uid ?? null, look: lookOf(spool.material, spool.colour) } : null;
}

// True when the spool in a bay is the pinned one: the same tag uid, or the same look.
/** @param {{ uid: string | null, look: string | null } | null | undefined} pin @param {PlanLane | undefined} lane */
function samePin(pin, lane) {
  const here = pinOf(lane);
  return Boolean(pin && here && ((pin.uid && here.uid === pin.uid) || (pin.look && here.look === pin.look)));
}

// What a pin learns when its spool is seen in its bay: a uid or a look it lacked; the spool it replaced (was) goes.
// The same pin back when there is nothing to learn.
/** @param {Pin} pin @param {PlanLane} lane @returns {Pin} */
function learnt(pin, lane) {
  const here = /** @type {Pin} */ (pinOf(lane));
  const next = { uid: pin.uid ?? here.uid, look: pin.look ?? here.look };
  return next.uid === pin.uid && next.look === pin.look && !pin.was ? pin : next;
}

// The picks after the bays changed, by the pins (tool -> Pin). A pin is dropped only when its spool is certainly
// gone, never on a gap:
// - its spool in its bay: kept (the bay it was in wins a tie);
// - its spool in another bay: the pick moves there;
// - its bay empty (the spool in hand during a move), gone from the status, holding a spool of no material, or still
//   showing the spool the pin replaced (was: the status lags a "Save and use"): kept as it is;
// - its bay holding another known spool, and its spool in no bay: dropped with its pick, and listed in lost.
// A pick with no pin (NEW, or the suggestion) stays. Returns the same objects when nothing changed.
/**
 * @param {Choice} choice @param {Record<number, Pin>} pinned @param {PlanLane[]} lanes
 * @returns {{ choice: Choice, pinned: Record<number, Pin>, lost: number[] }}
 */
export function rekeyPicks(choice, pinned, lanes) {
  const out = { ...choice };
  const pins = { ...pinned };
  /** @type {number[]} */
  const lost = [];
  let changed = false;
  for (const [k, pin] of Object.entries(pinned)) {
    const n = Number(k);
    const bay = laneByKey(lanes, choice[n]);
    const at = samePin(pin, bay) ? bay : lanes.find((l) => samePin(pin, l));
    if (at) {
      const next = learnt(pin, at);
      if (at === bay && next === pin) continue;
      out[n] = at.key;
      pins[n] = next;
    } else if (bay?.spool && bay.material && !samePin(pin.was, bay)) {
      delete out[n];
      delete pins[n];
      lost.push(n);
    } else continue;
    changed = true;
  }
  return changed ? { choice: out, pinned: pins, lost } : { choice, pinned, lost };
}

// True when a tool of a new plan matches the same tool of the plan that the choice was made for.
// Both need the same colour, in any case, and the same material, compared as the adapter compares materials.
/** @param {Tool} a @param {Tool} b */
export const sameTool = (a, b) =>
  String(a.colour ?? '').toLowerCase() === String(b.colour ?? '').toLowerCase() &&
  material(a.material) === material(b.material);

// The page's changes for the plan shown, or {} for none.
/** @param {PrintState} print @param {Plan | null | undefined} plan @returns {Choice} */
export const choiceFor = (print, plan) => carriedChoice(print, plan)?.choice ?? {};

// Counts the lane swaps of each head for a choice, as {head: swaps}.
// A swap is a T whose lane is not the last lane of its head.
// This is the rule of the adapter's plan module, line for line.
/**
 * @param {number[]} order
 * @param {Choice} choice
 * @param {(key: string) => number | null} headOf
 * @returns {Record<number, number>}
 */
export function swaps(order, choice, headOf) {
  /** @type {Record<number, string>} */
  const last = {};
  /** @type {Record<number, number>} */
  const count = {};
  for (const n of order) {
    const key = choice[n];
    const head = key != null ? headOf(key) : null;
    if (head == null) continue;
    count[head] ??= 0;
    if (head in last && last[head] !== key) count[head] += 1;
    last[head] = /** @type {string} */ (key); // A null key has no head, so it was skipped above.
  }
  return count;
}

/** @param {Choice} choice @returns {number[]} */
const tools = (choice) =>
  Object.keys(choice)
    .map(Number)
    .sort((a, b) => a - b);
// The lane of this key ("2" with one unit, "a:2" with several), or undefined when no lane has it.
/** @param {PlanLane[]} lanes @param {string | null | undefined} key @returns {PlanLane | undefined} */
export const laneByKey = (lanes, key) => lanes.find((l) => l.key === key);
/** @param {PlanLane[]} lanes @returns {(key: string | null | undefined) => number | null} */
export const headOfIn = (lanes) => (key) => laneByKey(lanes, key)?.head ?? null;

// Returns a test of whether the file prints a tool. The tools come from the file's T lines,
// in the plan's order. With no T line read, every tool of its config block counts.
// The adapter has the same rule.
/** @param {unknown} order @returns {(n: number) => boolean} */
export function usedTest(order) {
  const used = new Set(Array.isArray(order) ? order : []);
  return (n) => !used.size || used.has(n);
}

// The start options of the screen. Bed levelling is on, and the rest is off.
// flowHeads lists the heads that flow calibration runs on.
/** @type {PrintOptions} */
export const DEFAULT_OPTIONS = { bedLevel: true, flow: false, flowHeads: [], timeLapse: false, endUnload: false };

// The heads that a choice prints on, in ascending order. Only the tools that the file prints count.
/** @param {unknown} order @param {Choice} choice @param {PlanLane[]} lanes @returns {number[]} */
export function usedHeads(order, choice, lanes) {
  const headOf = headOfIn(lanes);
  const ns = tools(choice).filter(usedTest(order));
  return [...new Set(ns.map((n) => headOf(choice[n])))].filter((h) => h != null).sort((a, b) => a - b);
}

// The heads that flow calibration runs on. These are the used heads that are ticked.
// With flow on and no ticked head left, it is every used head, as when flow is first switched on.
// That happens when a spool change moved the tools to other heads.
/** @param {Partial<PrintOptions> | null | undefined} options @param {number[]} used @returns {number[]} */
export function flowHeadsOf(options, used) {
  const o = { ...DEFAULT_OPTIONS, ...options };
  if (!o.flow) return [];
  const ticked = used.filter((h) => o.flowHeads.includes(h));
  return ticked.length ? ticked : [...used];
}

// The options after one change from a checkbox. used lists the heads that the print uses.
// Unticking the last flow head turns flow calibration off.
/**
 * @param {Partial<PrintOptions> | null | undefined} options
 * @param {{ opt: string | undefined, on: boolean, head?: unknown }} change
 * @param {number[]} used
 * @returns {PrintOptions}
 */
export function nextOptions(options, change, used) {
  const o = { ...DEFAULT_OPTIONS, ...options };
  const { opt, on } = change;
  if (opt === 'flow') return { ...o, flow: on, flowHeads: on ? [...used] : [] };
  if (opt === 'flow_head') {
    const h = Number(change.head);
    const now = flowHeadsOf(o, used);
    const heads = on ? [...new Set([...now, h])] : now.filter((x) => x !== h);
    // Unticking the last head turns flow calibration off. It never ticks every head back.
    return heads.length ? { ...o, flowHeads: heads } : { ...o, flow: false, flowHeads: [] };
  }
  if (opt !== undefined && ['bedLevel', 'timeLapse', 'endUnload'].includes(opt)) return { ...o, [opt]: on };
  return o;
}

// The U1 keeps 32 logical tools (T0-T31). highTools returns the tools of the choice beyond that.
const LOGICAL_TOOLS = 32;
/** @param {unknown} order @param {Choice} choice @returns {number[]} */
export const highTools = (order, choice) =>
  tools(choice)
    .filter(usedTest(order))
    .filter((n) => n >= LOGICAL_TOOLS);

// The lines that Print sends, in one script. The first line sets the plan's choice.
// The second starts the file the way the U1's screen starts it, with SDCARD_PRINT_FILE_WITH_PARAMETERS.
// That line carries the tool map and the start options, in the U1's log format.
// The U1 derives the heads that a print uses from the file's consumption, never from the map,
// and the start resets them. So FILAMENT_USED_MM names each used tool with 1 mm, and the map gives the heads.
// This is the only line that leaves them right. The start resets SET_PRINT_USED_EXTRUDERS,
// and the U1 refuses that command once the print is on.
// Only the tools that the file prints count. order is the file's T order.
/**
 * @param {string} file
 * @param {Choice} choice
 * @param {PlanLane[]} lanes
 * @param {unknown} [order]
 * @param {Partial<PrintOptions>} [options]
 */
export function printScript(file, choice, lanes, order = null, options = DEFAULT_OPTIONS) {
  const name = quoted(file);
  const headOf = headOfIn(lanes);
  const ns = tools(choice).filter(usedTest(order));
  if (highTools(order, choice).length)
    throw new Error(
      t('print.tool_limit', {
        tools: highTools(order, choice)
          .map((n) => t('print.colour_n', { n: n + 1 }))
          .join(', '),
      }),
    );
  const o = { ...DEFAULT_OPTIONS, ...options };
  const used = usedHeads(order, choice, lanes);
  /** @param {unknown[]} xs */
  const list = (xs) => `"[${xs.join(', ')}]"`;
  const table = ns.map((n) => `[${n}, ${headOf(choice[n])}]`);
  const flowHeads = flowHeadsOf(o, used);
  const mm = Array.from({ length: Math.max(...ns, 0) + 1 }, (_, n) => (ns.includes(n) ? 1 : 0));
  const unload = Array.from({ length: HEADS }, (_, h) => (o.endUnload && used.includes(h) ? 1 : 0));
  return [
    `ACEK_PLAN_SET FILE=${name} ${ns.map((n) => `T${n}=${choice[n]}`).join(' ')}${o.endUnload ? ' END_UNLOAD=1' : ''}`,
    `SDCARD_PRINT_FILE_WITH_PARAMETERS FILENAME=${name} MAP_TABLE=${list(table)}` +
      ` BED_LEVEL=${o.bedLevel ? 1 : 0} FLOW_CALIBRATE=${o.flow ? 1 : 0} FLOW_CALIBRATE_EXTRUDERS=${list(flowHeads)}` +
      ` TIME_LAPSE_CAMERA=${o.timeLapse ? 1 : 0} END_UNLOAD_FILAMENT=${list(unload)} FILAMENT_USED_MM=${list(mm)}`,
  ].join('\n');
}

// The lane of each tool. It is the page's change, else the set plan's lane, else the suggestion.
// null means no lane yet.
/** @param {Plan | null | undefined} plan @param {Choice} choice @returns {Choice} */
export function chosen(plan, choice) {
  /** @type {Choice} */
  const out = {};
  (plan?.tools ?? []).forEach((tool, n) => {
    // forEach runs only with a plan, so plan is set here.
    const key = choice[n] ?? /** @type {Plan} */ (plan).choice?.[n] ?? tool.lane;
    out[n] = key || null;
  });
  return out;
}

// A copy of a tool table without tool n.
/** @template T @param {Record<number, T>} o @param {number} n @returns {Record<number, T>} */
export function without(o, n) {
  const out = { ...o };
  delete out[n];
  return out;
}

// A material as the adapter compares it, in lower case and trimmed. It is '' when unknown.
/** @param {unknown} m */
const material = (m) =>
  String(m ?? '')
    .trim()
    .toLowerCase();

// Compares a lane's material with a tool's material. 'same' means that they match.
// 'unknown' means that one of them is not known. The user may choose that lane, and the page warns.
// 'other' means that both are known and different. The page refuses that lane, as the adapter does.
/** @param {unknown} tool @param {unknown} lane @returns {'same' | 'unknown' | 'other'} */
export function materialMatch(tool, lane) {
  const a = material(tool),
    b = material(lane);
  if (!a || !b) return 'unknown';
  return a === b ? 'same' : 'other';
}

// The one rule for the bay that a tool prints from, read by every path of Print (blocked, printGate) and by the plan's
// Define lines:
// - 'missing': no bay, an empty bay, or a bay on no head;
// - 'wrong': a key that names no hooked bay, or a spool of another known material than the file;
// - 'unset': the spool's material is not known: the adapter says none is set (materialSet false: no tag read, no Edit
//   spool record), or the page knows none whatever the adapter says (materialSet null or undefined). A colour prints
//   only from a spool of a known material, so it blocks with the Define / Edit spool reason.
// Else null.
/** @param {Tool} tool @param {string | null | undefined} key @param {PlanLane[]} lanes @returns {'missing' | 'wrong' | 'unset' | null} */
export function bayProblem(tool, key, lanes) {
  if (!key) return 'missing';
  const lane = laneByKey(lanes, key);
  if (!lane) return 'wrong';
  if (lane.empty || lane.head == null) return 'missing';
  if (materialMatch(tool.material, lane.material) === 'other') return 'wrong';
  return lane.materialSet === false || !lane.material ? 'unset' : null;
}

// What blocks Print for a pick, by tool, by bayProblem's rule. Only the tools that the file prints count.
/**
 * @param {Plan | null | undefined} plan
 * @param {Choice} pick
 * @param {PlanLane[]} lanes
 * @returns {{ missing: number[], wrong: number[], unset: number[] }}
 */
export function blocked(plan, pick, lanes) {
  /** @type {Record<'missing' | 'wrong' | 'unset', number[]>} */
  const out = { missing: [], wrong: [], unset: [] };
  const used = usedTest(plan?.order);
  (plan?.tools ?? []).forEach((tool, n) => {
    const p = used(n) ? bayProblem(tool, pick[n], lanes) : null;
    if (p) out[p].push(n);
  });
  return out;
}

// The plan shown for file. It is the plan when the plan is for that file, or when no file is chosen. Else null.
/** @param {Plan | null | undefined} plan @param {string | null | undefined} file @returns {Plan | null} */
export const planFor = (plan, file) => (plan && (file == null || plan.file === file) ? plan : null);

// What keeps Print disabled, in words, or null when nothing does.
// It can be a print on, tools with no spool, tools of another material, or tools beyond the U1's logical tools.
// The tools are named as the file colours they print: "colour n", 1-based.
/**
 * @param {Plan} plan
 * @param {Choice} pick
 * @param {PlanLane[]} lanes
 * @param {boolean} [busy]
 * @returns {string | null}
 */
export function blockReason(plan, pick, lanes, busy = false) {
  const { missing, wrong, unset } = blocked(plan, pick, lanes);
  return reasonWords(busy, missing, wrong, unset, highTools(plan.order, pick));
}

// The reasons that keep Print disabled, in words, with the tools named as file colours. null when there is none.
/**
 * @param {boolean} busy @param {number[]} missing @param {number[]} wrong @param {number[]} unset @param {number[]} high
 * @param {Map<string, { empty: boolean, roots: Set<number> }>} [puts] each bay with a put still pending: empty or not,
 *   and the colours (merge roots, each once) it is for
 * @returns {string | null}
 */
function reasonWords(busy, missing, wrong, unset, high, puts = new Map()) {
  /** @param {number[]} ns */
  const names = (ns) => ns.map((n) => t('print.colour_n', { n: n + 1 })).join(', ');
  const lines = [
    busy ? t('print.busy') : null,
    missing.length ? t('print.choose_first', { tools: names(missing) }) : null,
    wrong.length ? t('print.material_first', { tools: names(wrong) }) : null,
    unset.length ? t('print.material_unset', { tools: names(unset) }) : null,
    ...[...puts].map(([key, at]) =>
      at.empty
        ? t('print.no_spool', { tools: names([...at.roots]), key })
        : t('print.put_pending', { tools: names([...at.roots]), key }),
    ),
    high.length ? t('print.tool_limit', { tools: names(high) }) : null,
  ];
  return lines.filter(Boolean).join(' ') || null;
}

// The most groups (colours) the planner's exact search plans. Its tables hold 2 ** count states per head, so a file
// with more colours (only possible with more than 16 bays) is not planned: the page says so instead of freezing.
export const MAX_GROUPS = 16;

// Why a placement does not fit, in words: more colours than the planner plans (tooMany), or more colours than bays:
// then what to do, joining colours when two of them may be joined (canJoin), else more bays.
/**
 * @param {import('./planner.js').Placement} place @param {Plan} [plan] @param {Record<number, number>} [merges]
 */
export function noFitWords(place, plan, merges = {}) {
  if (place.tooMany) return t('print.too_many', { n: place.colours, max: MAX_GROUPS });
  const words = { n: place.colours, m: place.lanes };
  return !plan || canJoinAny(plan, merges) ? t('print.no_fit_n', words) : t('print.no_fit_bays', words);
}

// True when some colour the file prints may join another (canJoin), both not merged into another yet.
/** @param {Plan} plan @param {Record<number, number>} merges */
function canJoinAny(plan, merges) {
  const used = usedTest(plan.order);
  const roots = plan.tools.map((_, n) => n).filter((n) => used(n) && mergeRoot(plan, merges, n) === n);
  return roots.some((n) => roots.some((j) => j !== n && canJoin(plan, n, j)));
}

// The bays as they are: the pick of each tool (the user's, else the set plan's, else the adapter's match), a merged
// tool printing from the bay of the tool it joins. This is how the print runs when nobody follows the guided steps.
/** @param {Plan} plan @param {Choice} pick @param {Record<number, number>} [merges] @returns {Choice} */
export function asIsChoice(plan, pick, merges = {}) {
  /** @type {Choice} */
  const out = {};
  plan.tools.forEach((_, n) => {
    out[n] = pick[mergeRoot(plan, merges, n)] ?? null;
  });
  return out;
}

// The first colour the file prints whose bay Print sends (choice, merges applied) is the user's own pick in the
// colour chips and has no material set (bayProblem's 'unset'), as { n, key }, or null. Such a pick blocks Print
// until the bay is defined. A pick of a tool merged into another is not sent, so it does not count.
/** @param {Plan} plan @param {Choice} user @param {Choice} choice @param {PlanLane[]} lanes @param {Record<number, number>} merges */
function undefinedPick(plan, user, choice, lanes, merges) {
  const used = usedTest(plan.order);
  for (const [n, tool] of plan.tools.entries()) {
    const key = choice[n];
    if (!used(n) || user[mergeRoot(plan, merges, n)] == null || bayProblem(tool, key, lanes) !== 'unset') continue;
    return { n: mergeRoot(plan, merges, n), key: /** @type {string} */ (key) };
  }
  return null;
}

/**
 * What Print sends and why it cannot, shared by the print bar and the start. It is worked out once per render
 * (PlanView) and read by the bar, its summary, the start options and the head-use bar, so they all show what Print
 * sends.
 * - The guided steps are a suggestion, never a lock. When every colour the file prints has a bay as it is (the pick,
 *   merges applied) that bayProblem passes (it holds, now, a spool of a known material the colour may print with),
 *   Print sends that,
 *   whatever the steps say (asIs is true). The start then asks about colours that differ and steps still pending.
 *   This holds even when the planner cannot plan the file (more colours than bays): the bar says so.
 * - A pick the user made on a bay whose spool has no material set blocks, naming the bay to define.
 * - Otherwise, with a placement that fits, Print sends the placement's bays (the ones the guided steps prepare,
 *   merges applied), only when every colour the file prints has there, right now, a spool of a known material it may
 *   print with. A put still pending for a colour (its bay empty, holding a spool of no material, or one the steps
 *   take out) blocks, naming the colour and the bay (print.no_spool). Other pending steps (a check of a spool of a
 *   known material) do not: Print asks about them at the start. It blocks too on what bayProblem says of the bays it
 *   sends, a tool beyond T31, or a print on. A placement that does not fit blocks.
 * - With no placement at all (a plan with no T order), the picks are sent, as before the guide.
 * @param {{ plan: Plan, lanes: PlanLane[], pick: Choice, user?: Choice, merges?: Record<number, number>,
 *   place: import('./planner.js').Placement | null }} now
 * @param {boolean} busy
 * @returns {{ choice: Choice, block: string | null, asIs: boolean }}
 */
export function printGate(now, busy) {
  const { plan, lanes, place } = now;
  if (!place) return { choice: now.pick, block: blockReason(plan, now.pick, lanes, busy), asIs: true };
  const asIs = asIsChoice(plan, now.pick, now.merges);
  const bad = undefinedPick(plan, now.user ?? {}, asIs, lanes, now.merges ?? {});
  if (bad) {
    const why = t('print.define_first', { key: bad.key, n: bad.n + 1 });
    return { choice: asIs, block: busy ? `${t('print.busy')} ${why}` : why, asIs: true };
  }
  const a = blocked(plan, asIs, lanes);
  if (![...a.missing, ...a.wrong, ...a.unset, ...highTools(plan.order, asIs)].length)
    return { choice: asIs, block: busy ? t('print.busy') : null, asIs: true };
  if (!place.fits) return { choice: {}, block: noFitWords(place, plan, now.merges), asIs: false };
  const choice = place.choice;
  const used = usedTest(plan.order);
  // The bays whose spool the steps take out or move to another bay: what they hold now is not what prints.
  const leaves = new Set([...place.takeOut, ...(place.lines ?? []).flatMap((l) => (l.act === 'move' ? [l.from] : []))]);
  /** @type {Record<'missing' | 'wrong' | 'unset', number[]>} */
  const out = { missing: [], wrong: [], unset: [] };
  /** @type {Map<string, { empty: boolean, roots: Set<number> }>} */
  const puts = new Map();
  plan.tools.forEach((tool, n) => {
    if (!used(n)) return;
    const lane = laneByKey(lanes, choice[n]);
    const p = lane ? bayProblem(tool, choice[n], lanes) : 'missing';
    // A colour prints only from a bay that holds, right now, a spool of a known material it may print with (bayProblem
    // passes). A put still pending into its bay blocks: the bay is empty ("no spool"), or the steps take out what it
    // holds, whatever it is ("put its spool there"). A spool the steps keep whose material is not known blocks with
    // the Define / Edit spool reason. Steps for bays no colour prints from, and checks of spools it may print with, do
    // not block: Print asks about them at the start.
    const put = lane && lane.head != null && (lane.empty || leaves.has(lane.key));
    if (put) {
      const at = puts.get(lane.key) ?? { empty: lane.empty, roots: new Set() };
      at.roots.add(mergeRoot(plan, now.merges ?? {}, n));
      puts.set(lane.key, at);
    } else if (p) out[p].push(n);
  });
  const { missing, wrong, unset } = out;
  const block = reasonWords(busy, missing, wrong, unset, highTools(plan.order, choice), puts);
  return { choice, block, asIs: false };
}

// The swaps of what Print sends, counted on the live lanes' heads, or null while a colour the file prints has no
// bay on a head.
/** @param {Plan} plan @param {Choice} choice @param {PlanLane[]} lanes @returns {number | null} */
export function sentSwaps(plan, choice, lanes) {
  const headOf = headOfIn(lanes);
  const used = usedTest(plan.order);
  if (!plan.tools.every((_, n) => !used(n) || headOf(choice[n]) != null)) return null;
  return Object.values(swaps(plan.order, choice, headOf)).reduce((x, y) => x + y, 0);
}

// A pick that asks for a new spool: the tool's group owns no spool in the units.
export const NEW = 'new';

// The largest distance (CIE76 ΔE) at which a spool matches a file's colour: the host's colour_max_distance.
export const CLOSE = 25;
// Below this distance (ΔE) two of the file's colours look the same, and the page offers to join them.
export const SIMILAR = 12;

// A #rrggbb colour in CIELAB (D65), as the host's plan module converts it.
/** @param {string} colour @returns {number[]} */
function lab(colour) {
  const lin = [1, 3, 5].map((i) => {
    const c = Number.parseInt(colour.slice(i, i + 2), 16) / 255;
    return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  const x = (0.4124 * lin[0] + 0.3576 * lin[1] + 0.1805 * lin[2]) / 0.95047;
  const y = 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2];
  const z = (0.0193 * lin[0] + 0.1192 * lin[1] + 0.9505 * lin[2]) / 1.08883;
  /** @param {number} v */
  const f = (v) => (v > 0.008856 ? Math.cbrt(v) : 7.787 * v + 16 / 116);
  return [116 * f(y) - 16, 500 * (f(x) - f(y)), 200 * (f(y) - f(z))];
}

// How far apart two #rrggbb colours look, as CIE76 ΔE: the host's distance(). About 2.3 can just be seen.
/** @param {string} a @param {string} b @returns {number} */
export function colourDistance(a, b) {
  const [p, q] = [lab(a), lab(b)];
  return Math.hypot(p[0] - q[0], p[1] - q[1], p[2] - q[2]);
}

// The names the host gives a spool's colour, each with a colour of its own (the host's COLOUR_NAMES, in the same
// order). A colour takes the name whose colour is closest to it, as the host's colour_name() does: the page names a
// colour the host has not named (a file's colour, a spool put in after the plan) the same way.
export const COLOUR_NAMES = [
  ['white', '#ffffff'],
  ['black', '#000000'],
  ['gray', '#808080'],
  ['silver', '#c0c0c0'],
  ['red', '#d32020'],
  ['dark red', '#8b0000'],
  ['orange', '#ff8c00'],
  ['yellow', '#ffe000'],
  ['gold', '#d4af37'],
  ['beige', '#e8d8b0'],
  ['brown', '#7a4a2a'],
  ['pink', '#ffb6c1'],
  ['magenta', '#ff00ff'],
  ['purple', '#800080'],
  ['violet', '#8a2be2'],
  ['blue', '#1e50e0'],
  ['navy', '#000080'],
  ['light blue', '#87ceeb'],
  ['cyan', '#00ffff'],
  ['teal', '#008080'],
  ['green', '#20a020'],
  ['dark green', '#006400'],
  ['lime', '#80ff00'],
  ['olive', '#808000'],
];

// A colour written #rgb, #rrggbb or #rrggbbaa (a tag's colour), in any case, as #rrggbb in lower case; null for
// anything else.
/** @param {string | null | undefined} colour @returns {string | null} */
export function normalColour(colour) {
  const m = /^#(?:([0-9a-f]{6})(?:[0-9a-f]{2})?|([0-9a-f]{3}))$/i.exec(colour ?? '');
  if (!m) return null;
  const hex = m[1] ?? [...m[2]].map((x) => x + x).join('');
  return `#${hex.toLowerCase()}`;
}

// COLOUR_NAMES with their colours in CIELAB, worked out once.
const NAMES_LAB = COLOUR_NAMES.map(([name, colour]) => /** @type {[string, number[]]} */ ([name, lab(colour)]));

// The name of a colour (normalColour: #rgb, #rrggbb or #rrggbbaa) from COLOUR_NAMES, or null when it is not a colour.
/** @param {string | null | undefined} colour @returns {string | null} */
export function colourName(colour) {
  const hex = normalColour(colour);
  if (!hex) return null;
  const p = lab(hex);
  let best = NAMES_LAB[0];
  let far = Infinity;
  for (const pair of NAMES_LAB) {
    const d = Math.hypot(p[0] - pair[1][0], p[1] - pair[1][1], p[2] - pair[1][2]);
    if (d < far) [best, far] = [pair, d];
  }
  return best[0];
}

// True when a distance is a match: below CLOSE. At CLOSE a spool no longer matches, as the colour rows name it (ColourRows).
// The file cards and the guided steps both test a spool with it.
/** @param {number} d */
export const withinClose = (d) => d < CLOSE;

// True when a spool fits a file colour: not of another known material, and of a colour within CLOSE (withinClose).
// The file cards and the guided steps both test a spool with it.
/** @param {{ colour: string | null, material: string | null }} want @param {{ colour: string | null, material: string | null }} lane */
export const fitsColour = (want, lane) =>
  materialMatch(want.material, lane.material) !== 'other' &&
  Boolean(want.colour && lane.colour && withinClose(colourDistance(want.colour, lane.colour)));

// True when tool n may join tool to: to is a tool the file prints, of the same known material as n, or n is a tool whose
// material the file does not give (it then prints from to's spool, and the start warns about it). A tool of a known
// material never joins one of no material, nor one of another material.
/** @param {Plan} plan @param {number} n @param {number | undefined} to */
const canJoin = (plan, n, to) => {
  if (to === undefined || plan.tools[to] === undefined || !usedTest(plan.order)(to)) return false;
  const m = materialMatch(plan.tools[n].material, plan.tools[to].material);
  return m === 'same' || (m === 'unknown' && !material(plan.tools[n].material));
};

// The tool that tool n joins by the user's merges, following them to the end. A merge counts while
// canJoin holds. A chain that runs into a cycle ends on the cycle's lowest tool, so every tool that
// leads there has one root.
/** @param {Plan} plan @param {Record<number, number>} merges @param {number} n @returns {number} */
export function mergeRoot(plan, merges, n) {
  const chain = [n];
  let at = n;
  // Each hop is held to n's material and to the material of the tool it leaves, so a chain never crosses materials.
  while (canJoin(plan, n, merges[at]) && canJoin(plan, at, merges[at])) {
    const to = merges[at];
    if (chain.includes(to)) return Math.min(...chain.slice(chain.indexOf(to)));
    chain.push(to);
    at = to;
  }
  return at;
}

// The colours that print from each bay as it is: bay key -> the roots of the merges whose pick it is, in tool order.
// Only the tools the file prints count. The picker works it out once per render, and a pick reads it the same way.
/** @param {Plan} plan @param {Choice} pick @param {Record<number, number>} merges @returns {Map<string, number[]>} */
export function bayRoots(plan, pick, merges) {
  const used = usedTest(plan.order);
  /** @type {Map<string, number[]>} */
  const out = new Map();
  for (const j of plan.tools.keys()) {
    const key = pick[j];
    if (!used(j) || !key || key === NEW || mergeRoot(plan, merges, j) !== j) continue;
    out.set(key, [...(out.get(key) ?? []), j]);
  }
  return out;
}

// The colour that tool n joins when it picks bay key, or null. A bay holds one spool, and the planner gives a bay to
// one colour only (the first that picks it), so a pick of a bay another colour prints from is a join: both colours
// then print from that spool, and neither loses its bay. The colour joined is the first root on that bay (bayRoots)
// that is not n's own (its root), not n's twin (sameTool: twins share a spool by themselves) and that n may join
// (canJoin: not of another known material).
/** @param {Plan} plan @param {Map<string, number[]>} roots @param {Record<number, number>} merges @param {number} n @param {string} key */
export function bayOwner(plan, roots, merges, n, key) {
  const mine = mergeRoot(plan, merges, n);
  const tool = plan.tools[n];
  // True when root r is n's twin: the same colour and material, which the planner already shares.
  /** @param {number} r */
  const twin = (r) => Boolean(tool.colour) && sameTool(tool, plan.tools[r]);
  return (roots.get(key) ?? []).find((r) => r !== mine && !twin(r) && canJoin(plan, n, r)) ?? null;
}

// True when two tools look the same but the planner does not already share them (sameTool): both
// have a colour, closer than SIMILAR, and the same known material (so that they can be joined).
/** @param {Tool} a @param {Tool} b */
const looksSame = (a, b) =>
  Boolean(a.colour && b.colour) &&
  !sameTool(a, b) &&
  materialMatch(a.material, b.material) === 'same' &&
  colourDistance(/** @type {string} */ (a.colour), /** @type {string} */ (b.colour)) < SIMILAR;

// The pairs of tools whose colours look the same: tools the file prints, not merged into another,
// with looksSame. In tool order, first pair first.
/** @param {Plan} plan @param {Record<number, number>} merges @returns {[number, number][]} */
export function similarPairs(plan, merges) {
  const used = usedTest(plan.order);
  const roots = plan.tools.map((_, n) => n).filter((n) => used(n) && mergeRoot(plan, merges, n) === n);
  /** @type {[number, number][]} */
  const pairs = [];
  roots.forEach((i, k) => {
    for (const j of roots.slice(k + 1)) if (looksSame(plan.tools[i], plan.tools[j])) pairs.push([i, j]);
  });
  return pairs;
}

// The spool in lane i of a unit's slice, or null for an empty bay. Its id names the spool: the tag's
// uid when the tag has one, else the material and colour. tag is true when the tag speaks for the lane.
/**
 * @param {Status} slice @param {number} i @param {string} key @param {boolean} empty
 * @returns {PlanSpool | null}
 */
function liveSpool(slice, i, key, empty) {
  if (empty) return null;
  const spool = spoolOf(slice, i);
  const material = spool?.type ?? null;
  const colour = colorOf(spool);
  const uid = laneOf(slice, i)?.tag?.uid;
  const id = uid ? `${key}:${uid}` : `${key}:${material ?? ''}:${colour ?? ''}`;
  return { id, colour, material, tag: spool?.fromTag === true, uid: uid ?? null };
}

// Lane i of a unit's slice as a plan lane, or null when it feeds no head and the plan does not list it.
// The colour's name is the plan's while the spool keeps the colour the plan read.
/** @param {Status} slice @param {number} i @param {Plan | null} plan @returns {PlanLane | null} */
function liveLane(slice, i, plan) {
  const key = laneText(slice, i);
  const head = headOf(slice, i);
  const before = laneByKey(plan?.lanes ?? [], key);
  if (head === null && !before) return null;
  // Empty is the Units tab's rule: no insert, whatever the lane is doing.
  const empty = !laneOf(slice, i)?.insert;
  const spool = liveSpool(slice, i, key, empty);
  const material = spool?.material ?? null;
  const colour = spool?.colour ?? null;
  const materialSet = adapterLane(slice, i)?.material_set;
  const name = before?.colour && before.colour === colour ? (before.colour_name ?? null) : null;
  return { key, colour, material, empty, head, materialSet, colour_name: name, spool, unknown: !empty && !material };
}

// The lanes of the plan, read from the live status instead of the plan's snapshot, so that a bay
// emptied or filled since ACEK_PLAN counts. Keys are the plan's keys ("2" with one unit, "a:2" with
// several). A spool with no material is unknown: the planner never uses it.
/** @param {Status} s @param {Plan | null} plan @returns {PlanLane[]} */
export function liveLanes(s, plan) {
  const names = unitsOf(s).length ? unitsOf(s).map(unitName) : [null];
  return names.flatMap((name) => {
    const slice = sliceFor(s, name);
    return Array.from({ length: LANES }, (_, i) => liveLane(slice, i, plan)).filter((l) => l !== null);
  });
}
