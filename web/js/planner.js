// The colour planner of the Print panel. The module has no effects, so it runs under node. Its state is
// a memo of the latest results (memo, below): the same inputs give the same object back. planStats, a count of the
// searches, is kept only for the tests.
// It works out where to put each spool, so that the file needs the fewest lane swaps.
// A swap happens at a T whose lane is not the last lane of its head, as in print-plan.js.
// Tools of the same colour and material share one spool, so the search places groups of tools.
// The search is exact. It walks the heads in order and keeps each result by (head, groups left).
// Among the placements with the fewest swaps, it keeps the one that moves the fewest spools.
import {
  colourDistance,
  withinClose,
  HEADS,
  headOfIn,
  laneByKey,
  materialMatch,
  MAX_GROUPS,
  mergeRoot,
  NEW,
  planId,
  sameTool,
  swaps,
  usedTest,
  without,
} from './print-plan.js';

/** @typedef {import('./types.js').Plan} Plan */
/** @typedef {import('./types.js').PlanLane} PlanLane */
/** @typedef {import('./print-plan.js').Choice} Choice */
/**
 * One line of the block, for a group of tools of the same colour and material. tools lists them.
 * act is 'there' (the spool is already there), 'move' (move it from the lane from), 'check' (the
 * lane holds a spool that no tool owns: if it is the group's colour, the user chooses it, and if
 * not, puts one) or 'put' (put a spool of the group's material and colour in key). Only a move line
 * has a lane from.
 * @typedef {{ act: 'move', from: string } | { act: 'there' | 'check' | 'put', from: null }} PlaceAct
 * @typedef {{ tools: number[], key: string } & PlaceAct} PlaceLine
 */
/**
 * What the planner found. fits is false when the file prints more colours than there are lanes.
 * Then lines and takeOut are empty, best is null and choice is {}.
 * colours counts the groups of tools: tools of the same colour and material count once.
 * lines has one line per group, in the order of the groups' first tools. choice is per tool.
 * takeOut lists the suggested lanes that hold a spool that no line keeps or checks.
 * now is the swaps of the pick, or null while a tool that the file prints has no lane on a head.
 * tooMany is true when the file has more colours than the search plans (MAX_GROUPS): it does not fit either.
 * @typedef {{ fits: boolean, colours: number, lanes: number, lines: PlaceLine[], takeOut: string[],
 *   now: number | null, best: number | null, choice: Choice, tooMany?: boolean }} Placement
 */
/** @typedef {{ swaps: number, moved: number, masks: number[] }} Best */

// One swap stops the print for about this long, in seconds. The bench measured 88 to 99 s.
export const SWAP_S = 90;

// The group of each used tool, by its index in used, and the number of groups.
// A tool that the user merged into another (merges, see mergeRoot) is in the group of the tool it joins.
// Tools of the same colour and material share one spool, as swaps() already counts them.
// A tool with no colour is a group of its own. The groups are in the order of their first tool.
// The colours the user set to a bay (mine: root -> bay) are grouped by that bay: two colours set to one bay print
// from its one spool, and two of the same colour set to two bays stay apart. A colour picked NEW never joins one the
// user set to a bay: the user asked for a new spool for it.
/**
 * @param {Plan} plan @param {number[]} used @param {Record<number, number>} [merges]
 * @param {Map<number, string>} [mine] @param {Choice} [pick] @returns {{ of: number[], count: number }}
 */
function groupsOf(plan, used, merges = {}, mine = new Map(), pick = {}) {
  const roots = used.map((n) => mergeRoot(plan, merges, n));
  /** @param {number} f @param {number} r */
  const twin = (f, r) => Boolean(plan.tools[r].colour) && sameTool(plan.tools[f], plan.tools[r]);
  // The group of each root, as a name, whatever the order of the tools: the bay the user set it to; else, unless it
  // asked for a new spool, the bay the user set one of its twins to; else its first twin not set to a bay (one that
  // takes no set twin's bay itself); else itself.
  /** @param {number} r @returns {string} */
  const setTwin = (r) => {
    const f = roots.find((x) => mine.has(x) && twin(x, r));
    return f === undefined ? '' : `bay:${mine.get(f)}`;
  };
  /** @param {number} r @returns {string} */
  const name = (r) => {
    const bay = mine.get(r);
    if (bay !== undefined) return `bay:${bay}`;
    const set = pick[r] === NEW ? '' : setTwin(r);
    if (set) return set;
    const f = roots.find((x) => !mine.has(x) && twin(x, r) && (pick[x] === NEW || !setTwin(x)));
    return `tool:${f ?? r}`;
  };
  /** @type {string[]} */
  const names = [];
  const of = roots.map((r) => {
    const k = name(r);
    if (!names.includes(k)) names.push(k);
    return names.indexOf(k);
  });
  return { of, count: names.length };
}

// The file's T order in groups. A repeat of a group on consecutive T lines is merged.
/** @param {number[]} order @param {number[]} used @param {number[]} of @returns {number[]} */
function groupOrder(order, used, of) {
  const group = new Map(used.map((n, i) => [n, of[i]]));
  return order
    .map((n) => group.get(n))
    .filter((g) => g !== undefined)
    .filter((g, i, a) => i === 0 || a[i - 1] !== g);
}

// The swaps of a set of groups on one head, by the rule of print-plan.js's swaps(): a T whose group
// is not the last group of the head. order is the T order of groups, and mask holds the bits of the
// set. Each group of the set is a lane of its own.
/** @param {number[]} order @param {number} mask @returns {number} */
function headSwaps(order, mask) {
  let last = -1;
  let count = 0;
  for (const g of order) {
    if (!((mask >> g) & 1)) continue;
    if (last !== -1 && last !== g) count += 1;
    last = g;
  }
  return count;
}

// The number of bits set in mask.
/** @param {number} mask @returns {number} */
function bitCount(mask) {
  let n = 0;
  for (let m = mask; m; m &= m - 1) n += 1;
  return n;
}

// The lane that each group owns today, or null. A tool owns the lane of its pick when the bay
// holds a spool that is not of another material. When two tools pick one lane, the first owns it.
// A group owns the lane of its first tool, in tool order, that owns one.
// A group with a tool picked NEW (a new spool) owns none, and a lane with an unknown spool is never owned.
// The tools the user chose a bay for (mine: tool -> bay) come first, and own their bay whatever it holds: the user's
// choice wins over every suggestion.
/**
 * @param {Plan} plan @param {number[]} used @param {{ of: number[], count: number }} groups
 * @param {Choice} pick @param {PlanLane[]} lanes @param {Set<number>} fresh @param {Map<number, string>} mine
 * @returns {(PlanLane | null)[]}
 */
function owned(plan, used, groups, pick, lanes, fresh, mine) {
  /** @type {(PlanLane | null)[]} */
  const own = Array.from({ length: groups.count }, () => null);
  const taken = new Set();
  /** @param {number} n @param {number} i */
  const take = (n, i) => {
    const g = groups.of[i];
    const lane = laneByKey(lanes, pick[n]);
    if (own[g] || fresh.has(g) || !lane || taken.has(lane.key)) return;
    const theirs = mine.has(n);
    if (!theirs && (lane.empty || lane.unknown)) return;
    if (!theirs && materialMatch(plan.tools[n]?.material, lane.material) === 'other') return;
    taken.add(lane.key);
    own[g] = lane;
  };
  for (const first of [true, false])
    used.forEach((n, i) => {
      if (mine.has(n) === first) take(n, i);
    });
  return own;
}

// The bays the user chose (tool -> bay): the tools of fixed that the file prints, that join no other colour and whose
// pick is a bay on a head. NEW is not a bay: the planner says where to put the new spool.
/**
 * @param {Plan} plan @param {number[]} used @param {Choice} pick @param {PlanLane[]} lanes
 * @param {Record<number, number>} merges @param {number[]} fixed @returns {Map<number, string>}
 */
function chosenBays(plan, used, pick, lanes, merges, fixed) {
  /** @type {Map<number, string>} */
  const out = new Map();
  for (const n of used) {
    const key = pick[n];
    if (!fixed.includes(n) || mergeRoot(plan, merges, n) !== n || !key || key === NEW) continue;
    if (laneByKey(lanes, key)?.head == null) continue;
    out.set(n, key);
  }
  return out;
}

// The rule that a bay the user set is the user's, for the pick as it is: a colour the user did not set (not in fixed)
// whose pick is a bay the user set for another colour has no bay as it is (null), so it never prints from that spool
// unasked; the suggestion places it. A colour of the same colour and material (a twin) still shares it. placement()
// applies it to the pick it is given, and the page shows and prints from the same (state.js planNow).
/** @param {Plan} plan @param {Choice} pick @param {number[]} fixed @returns {Choice} */
export function userPick(plan, pick, fixed) {
  /** @type {Map<string, number[]>} */
  const taken = new Map();
  for (const j of fixed)
    if (pick[j] && pick[j] !== NEW) taken.set(String(pick[j]), [...(taken.get(String(pick[j])) ?? []), j]);
  if (!taken.size) return pick;
  const out = { ...pick };
  plan.tools.forEach((tool, n) => {
    const set = taken.get(String(pick[n]));
    if (set && !fixed.includes(n) && !set.some((j) => sameTool(tool, plan.tools[j]))) out[n] = null;
  });
  return out;
}

// The groups that have a tool picked NEW: the user asked for a new spool for them.
/**
 * @param {number[]} used @param {{ of: number[] }} groups @param {Choice} pick @returns {Set<number>}
 */
function freshGroups(used, groups, pick) {
  return new Set(used.flatMap((n, i) => (pick[n] === NEW ? [groups.of[i]] : [])));
}

// The swaps of a state that has no placement. It is kept apart from every real count, which is
// far below it.
const NONE = 0x7fffffff;

// The exact search over count groups. caps holds the lanes of each head, and kept the head where
// each group keeps its spool (-1 for none). fixed holds, as a mask, the groups that stay on the head where they keep
// their spool: the user chose their spool. It returns the groups of each head as bit masks.
// The masks are 32-bit numbers, so count must stay below 31, and the tables hold 2 ** count states
// per head. It stays at 16 or less: work() searches only when every group has a hooked lane of its
// own, and there are at most four units of 4 lanes.
// When the heads cannot hold the groups, swaps and moved are Infinity and masks is empty.
/**
 * @param {number[]} order @param {number} count @param {number[]} caps @param {number[]} kept
 * @param {number} [fixed] @returns {Best}
 */
export function search(order, count, caps, kept, fixed = 0) {
  const heads = caps.length;
  const states = 2 ** count;
  // The swaps of each set of groups on one head, -1 until worked out.
  const cost = new Int32Array(states).fill(-1);
  /** @param {number} mask */
  const costOf = (mask) => {
    if (cost[mask] < 0) cost[mask] = headSwaps(order, mask);
    return cost[mask];
  };
  // The groups that keep their spool on each head, as a mask.
  const keptMask = caps.map((_, h) => kept.reduce((m, k, i) => (k === h ? m | (1 << i) : m), 0));
  // The lanes of the heads after head h. A head leaves the later heads no more groups than they hold.
  const room = caps.map((_, h) => caps.slice(h + 1).reduce((a, b) => a + b, 0));
  // The best result of each state (head h, the groups left in rest), at h * states + rest: its swaps
  // (-1 until worked out, NONE when the heads from h cannot hold rest), the spools it moves and the
  // groups that head h takes.
  const swapsAt = new Int32Array(heads * states).fill(-1);
  const movedAt = new Int32Array(heads * states);
  const maskAt = new Int32Array(heads * states);
  // Works out the state (h, rest) and returns its index, or -1 past the last head.
  // lo leaves the later heads no more groups than they hold, so the last head takes the rest.
  /** @param {number} h @param {number} rest @returns {number} */
  function solve(h, rest) {
    if (h === heads) return -1;
    const at = h * states + rest;
    if (swapsAt[at] >= 0) return at;
    // The fixed groups of this head go here, and the fixed groups of the other heads never do.
    const must = rest & fixed & keptMask[h];
    /** @type {number[]} */
    const bits = [];
    for (let i = 0; i < count; i += 1) if ((rest >> i) & 1 && !((fixed >> i) & 1)) bits.push(i);
    const left = bitCount(rest);
    const lo = Math.max(0, left - room[h]);
    const hi = Math.min(caps[h], left);
    let bestSwaps = Infinity;
    let bestMoved = Infinity;
    let bestMask = 0;
    /** @param {number} mask @param {number} size */
    const consider = (mask, size) => {
      const tail = solve(h + 1, rest & ~mask);
      if (tail >= 0 && swapsAt[tail] === NONE) return;
      const s = costOf(mask) + (tail < 0 ? 0 : swapsAt[tail]);
      const moved = size - bitCount(mask & keptMask[h]) + (tail < 0 ? 0 : movedAt[tail]);
      if (s < bestSwaps || (s === bestSwaps && moved < bestMoved)) {
        bestSwaps = s;
        bestMoved = moved;
        bestMask = mask;
      }
    };
    /** @param {number} from @param {number} mask @param {number} size */
    const walk = (from, mask, size) => {
      if (size >= lo) consider(mask, size);
      if (size === hi) return;
      for (let j = from; j < bits.length; j += 1) walk(j + 1, mask | (1 << bits[j]), size + 1);
    };
    if (bitCount(must) <= hi) walk(0, must, bitCount(must));
    swapsAt[at] = bestSwaps === Infinity ? NONE : bestSwaps;
    movedAt[at] = bestMoved;
    maskAt[at] = bestMask;
    return at;
  }
  const all = states - 1;
  const first = solve(0, all);
  if (swapsAt[first] === NONE) return { swaps: Infinity, moved: Infinity, masks: [] };
  // The masks of the best result, head by head from the first state.
  /** @type {number[]} */
  const masks = [];
  for (let h = 0, rest = all; h < heads; h += 1) {
    const mask = maskAt[h * states + rest];
    masks.push(mask);
    rest &= ~mask;
  }
  return { swaps: swapsAt[first], moved: movedAt[first], masks };
}

// The lane of each group on its head. A group that keeps its spool keeps its lane.
// The other groups take the free lanes of the head in this order: the empty bays, then the lanes
// whose spool goes to another head, then the other lanes. Inside each kind it is lane order.
// owners holds the lanes that a group owns.
/**
 * @param {number[]} masks @param {(PlanLane | null)[]} own @param {Set<string>} owners
 * @param {PlanLane[][]} byHead @returns {string[]}
 */
function assign(masks, own, owners, byHead) {
  /** @type {string[]} */
  const lane = [];
  masks.forEach((mask, h) => {
    const mine = own.map((_, g) => g).filter((g) => (mask >> g) & 1);
    const keeps = mine.filter((g) => own[g]?.head === h);
    const held = new Set(keeps.map((g) => own[g]?.key));
    // A lane owned by a group that is not kept here is left by its group.
    const open = byHead[h].filter((l) => !held.has(l.key));
    const left = open.filter((l) => !l.empty && owners.has(l.key));
    const other = open.filter((l) => !l.empty && !owners.has(l.key));
    const free = [...open.filter((l) => l.empty), ...left, ...other];
    for (const g of keeps) lane[g] = /** @type {PlanLane} */ (own[g]).key;
    mine
      .filter((g) => !keeps.includes(g))
      .forEach((g, k) => {
        lane[g] = free[k].key;
      });
  });
  return lane;
}

// How the spool's colour stands to every colour of a group: 'unsure' (a colour not known on one side), 'colour'
// (within CLOSE of none), 'some' (of some but not all), 'close' (of every one).
/**
 * @param {Pick<PlanLane, 'colour'>} lane @param {import('./types.js').Tool[]} tools
 * @returns {'unsure' | 'colour' | 'some' | 'close'}
 */
export function colourVerdict(lane, tools) {
  const colour = lane.colour;
  if (!colour || !tools.length || !tools.every((x) => x.colour)) return 'unsure';
  const close = tools.filter((x) => withinClose(colourDistance(/** @type {string} */ (x.colour), colour))).length;
  if (!close) return 'colour';
  return close < tools.length ? 'some' : 'close';
}

// The known materials of a group's tools, each once (compared as the adapter compares materials: trimmed, any case),
// in the spelling of their first tool.
/** @param {import('./types.js').Tool[]} tools @returns {string[]} */
export function knownMaterials(tools) {
  /** @type {string[]} */
  const out = [];
  for (const x of tools) {
    const m = x.material?.trim();
    if (m && !out.some((o) => materialMatch(o, m) === 'same')) out.push(m);
  }
  return out;
}

// The group's known materials that the spool's known material is not.
/** @param {Pick<PlanLane, 'material'>} lane @param {import('./types.js').Tool[]} tools @returns {string[]} */
export function otherMaterials(lane, tools) {
  return knownMaterials(tools).filter((m) => materialMatch(m, lane.material) === 'other');
}

// How the spool in a bay stands to a group (tools: every tool of the group, merges applied), the one test that the
// planner, the step's words and its Eject button read. The materials are told first, then the colours:
// - 'unknown': the spool has no material (the planner never uses it);
// - 'material': a known material other than every known material of the group;
// - 'mixed': a known material that is some of the group's known materials but not all (a group joined across
//   materials): one spool cannot print them all, so it is put over like another material;
// - 'colour': a known colour clearly none of the group's (each colour known, none within CLOSE);
// - 'some': a known colour within CLOSE of some of the group's colours but not all: the user checks it;
// - 'match': the group's spool: a material the same as every known material of the group (at least one), and a known
//   colour within CLOSE of every colour of the group;
// - 'unsure': the colour or the material cannot be compared (not known on one side): the user checks it.
/**
 * @param {Pick<PlanLane, 'unknown' | 'material' | 'colour'>} lane @param {import('./types.js').Tool[]} tools
 * @returns {'unknown' | 'material' | 'mixed' | 'colour' | 'some' | 'match' | 'unsure'}
 */
export function spoolVerdict(lane, tools) {
  if (lane.unknown === true) return 'unknown';
  const known = knownMaterials(tools);
  const other = known.filter((m) => materialMatch(m, lane.material) === 'other');
  if (known.length && other.length === known.length) return 'material';
  if (other.length) return 'mixed';
  const verdict = colourVerdict(lane, tools);
  if (verdict !== 'close') return verdict;
  return known.length && known.every((m) => materialMatch(m, lane.material) === 'same') ? 'match' : 'unsure';
}

// Why the spool in a bay is clearly not a group's (spoolVerdict), or null when it may be.
/**
 * @param {Pick<PlanLane, 'unknown' | 'material' | 'colour'>} lane @param {import('./types.js').Tool[]} tools
 * @returns {'unknown' | 'material' | 'mixed' | 'colour' | null}
 */
export function spoolMismatch(lane, tools) {
  const v = spoolVerdict(lane, tools);
  return v === 'unknown' || v === 'material' || v === 'mixed' || v === 'colour' ? v : null;
}

// The act of a group's line in lane. from is the lane of the group's own spool, or null. tools are the group's tools.
// When the group has no spool and the lane holds a spool that no group owns, that spool may be of the group's colour:
// the user checks it when its colour or material cannot be compared, or its colour is close to some of the group's
// colours only; when it matches (spoolVerdict 'match'), it is the group's spool and the line is "there". A spool of another known material, or
// of a known colour clearly not the group's, cannot be: the line puts one, and the second value says that the lane's
// spool comes out. So does a spool with no material (unknown), which the planner never uses, and any spool in the
// lane of a group that asked for a new spool (fresh). A move into a lane whose spool no group owns takes that spool
// out too.
/**
 * @param {PlanLane} lane @param {string | null} from @param {import('./types.js').Tool[]} tools
 * @param {Set<string>} owners @param {boolean} [fresh] @returns {[PlaceAct, boolean]}
 */
function actOf(lane, from, tools, owners, fresh = false) {
  if (from === lane.key) return [{ act: 'there', from: null }, false];
  // A move into a bay that holds a spool no group owns: that spool comes out first.
  if (from) return [{ act: 'move', from }, !lane.empty && !owners.has(lane.key)];
  if (lane.empty || owners.has(lane.key)) return [{ act: 'put', from: null }, false];
  const out = fresh || spoolMismatch(lane, tools) !== null;
  if (out) return [{ act: 'put', from: null }, true];
  // A spool of the group's material and colour is the group's: nothing to do there. Any other is checked by the user.
  return [{ act: spoolVerdict(lane, tools) === 'match' ? 'there' : 'check', from: null }, false];
}

// The act of the line of a group the user set to its bay: see linesOf.
/** @param {PlanLane} lane @param {import('./types.js').Tool[]} tools @returns {[PlaceAct, boolean]} */
function chosenAct(lane, tools) {
  if (lane.empty) return [{ act: 'put', from: null }, false];
  // Colours of two materials set to one bay ('mixed') have no spool that prints them all: no step can do it, and
  // Print refuses, naming the colour of the other material (bayProblem).
  return spoolVerdict(lane, tools) === 'material'
    ? [{ act: 'put', from: null }, true]
    : [{ act: 'there', from: null }, false];
}

// The lines of the block, one per group, the choice of each tool from the lane of its group,
// and the lanes to take out.
// A group the user set to its bay (pinned, a mask) reads "there", whatever its colour: unless the bay is empty now (put a
// spool there) or holds a spool of a material none of its colours is (take it out, put one of their material). A spool
// with no material keeps "there": the page asks to define it (Define), as for every such bay.
/**
 * @param {Plan} plan @param {{ used: number[], of: number[], fresh: Set<number>, pinned: number }} groups
 * @param {string[]} lane @param {(PlanLane | null)[]} own @param {Set<string>} owners @param {PlanLane[]} lanes
 * @returns {{ lines: PlaceLine[], choice: Choice, takeOut: string[] }}
 */
function linesOf(plan, { used, of, fresh, pinned }, lane, own, owners, lanes) {
  /** @type {Choice} */
  const choice = {};
  used.forEach((n, i) => {
    choice[n] = lane[of[i]];
  });
  /** @type {string[]} */
  const takeOut = [];
  const lines = lane.map((key, g) => {
    const tools = used.filter((_, i) => of[i] === g);
    const from = own[g]?.key ?? null;
    const at = /** @type {PlanLane} */ (laneByKey(lanes, key));
    const group = tools.map((n) => plan.tools[n]);
    const [act, out] = (pinned >> g) & 1 ? chosenAct(at, group) : actOf(at, from, group, owners, fresh.has(g));
    if (out) takeOut.push(key);
    return { tools, key, ...act };
  });
  return { lines, choice, takeOut };
}

// The placement. The bays the user chose (fixed) are kept as they are: those colours print from them, the guide has no
// step for them (but a put into an empty bay or over another material, see linesOf), and no other colour is put in
// them nor takes their spool (userPick). The other colours keep the suggestion they had (the search as if nothing
// were fixed); only when that suggestion uses a chosen bay are they placed again, around the chosen bays.
/**
 * @param {Plan} plan @param {number[]} used @param {Choice} given @param {PlanLane[]} lanes
 * @param {Record<number, number>} merges @param {number[]} fixed @returns {Placement}
 */
function work(plan, used, given, lanes, merges, fixed) {
  const pick = userPick(plan, given, fixed);
  const mine = chosenBays(plan, used, pick, lanes, merges, fixed);
  const byHead = Array.from({ length: HEADS }, (_, h) => lanes.filter((l) => l.head === h));
  const total = byHead.reduce((a, l) => a + l.length, 0);
  const headOf = headOfIn(lanes);
  // The pick as it is, with the user's joins: a merged tool prints from the bay of the tool it joins. A pinned
  // tool is on its pinned bay already (the pick holds it). So "now" and "best" differ only by spool moves.
  /** @type {Choice} */
  const asIs = {};
  for (const n of used) asIs[n] = pick[mergeRoot(plan, merges, n)];
  const counts = swaps(plan.order, asIs, headOf);
  // A pick that names no hooked lane on a head has no swaps to count.
  const now = used.every((n) => headOf(asIs[n]) != null) ? Object.values(counts).reduce((a, b) => a + b, 0) : null;
  const groups = groupsOf(plan, used, merges, mine, pick);
  const base = { colours: groups.count, lanes: total, now };
  const none = { fits: false, lines: [], takeOut: [], best: null, choice: {} };
  if (groups.count > total) return { ...base, ...none };
  if (groups.count > MAX_GROUPS) return { ...base, ...none, tooMany: true };
  planStats.searches += 1;
  const fresh = freshGroups(used, groups, pick);
  const own = owned(plan, used, groups, pick, lanes, fresh, mine);
  const owners = new Set(own.flatMap((l) => (l ? [l.key] : [])));
  const kept = own.map((l) => l?.head ?? -1);
  // The groups that keep the bay the user chose, as a mask.
  const pinned = used.reduce((m, n, i) => {
    const g = groups.of[i];
    return mine.has(n) && own[g]?.key === mine.get(n) ? m | (1 << g) : m;
  }, 0);
  const order = groupOrder(plan.order, used, groups.of);
  const caps = byHead.map((l) => l.length);
  const found = search(order, groups.count, caps, kept);
  const lane = assign(found.masks, own, owners, byHead);
  const of = { used, of: groups.of, fresh, pinned };
  if (pinned) {
    // The suggestion with the chosen bays put back: it stands while no other colour got one of them.
    const back = lane.map((key, g) => ((pinned >> g) & 1 ? /** @type {PlanLane} */ (own[g]).key : key));
    if (new Set(back).size === back.length) {
      const { lines, choice, takeOut } = linesOf(plan, of, back, own, owners, lanes);
      // Counted as the search counts: the groups of each head.
      const best = byHead.reduce((sum, hl) => {
        const keys = new Set(hl.map((l) => l.key));
        return (
          sum +
          headSwaps(
            order,
            back.reduce((m, key, g) => (keys.has(key) ? m | (1 << g) : m), 0),
          )
        );
      }, 0);
      return { ...base, fits: true, lines, takeOut, best, choice };
    }
    const around = search(order, groups.count, caps, kept, pinned);
    const again = assign(around.masks, own, owners, byHead);
    const { lines, choice, takeOut } = linesOf(plan, of, again, own, owners, lanes);
    return { ...base, fits: true, lines, takeOut, best: around.swaps, choice };
  }
  const { lines, choice, takeOut } = linesOf(plan, of, lane, own, owners, lanes);
  return { ...base, fits: true, lines, takeOut, best: found.swaps, choice };
}

// The latest results, by plan, the pick of the printed tools and what the planner reads of each lane, the
// most recent last. The panel draws often, and the search runs once per input. The colour picker asks for the
// pick as it is and for each option (pickCost): the pick as it is stays in the memo, so a picker of N options
// runs N + 1 searches, not 2N.
const MEMO_SIZE = 32;
/** @type {Map<string, Placement | null>} */
const memo = new Map();
// How many searches ran, for the tests.
export const planStats = { searches: 0 };

// Where to put each spool for plan, from the pick (chosen() in print-plan.js), the lanes (the plan's,
// or liveLanes) and the user's merges (tool -> the tool it joins). A pick may be NEW: a new spool.
// fixed lists the tools whose bay the user chose: they print from it, whatever the suggestion would say (see work).
// It is null when the plan has no T order or no tool that the file prints.
/**
 * @param {Plan | null | undefined} plan @param {Choice} pick @param {PlanLane[]} lanes
 * @param {Record<number, number>} [merges] @param {number[]} [fixed] @returns {Placement | null}
 */
export function placement(plan, pick, lanes, merges = {}, fixed = []) {
  if (!plan || !Array.isArray(plan.order) || !plan.order.length) return null;
  const used = plan.tools.map((_, n) => n).filter(usedTest(plan.order));
  const read = lanes.map(({ key, head, empty, material, unknown, colour }) => ({
    key,
    head,
    empty,
    material,
    unknown,
    colour,
  }));
  const roots = used.map((n) => mergeRoot(plan, merges, n));
  const key = JSON.stringify([planId(plan), used.map((n) => pick[n] ?? null), read, roots, fixed]);
  if (memo.has(key)) {
    const value = /** @type {Placement | null} */ (memo.get(key));
    memo.delete(key); // the most recent last
    memo.set(key, value);
    return value;
  }
  const value = used.length ? work(plan, used, pick, lanes, merges, fixed) : null;
  memo.set(key, value);
  if (memo.size > MEMO_SIZE) memo.delete(/** @type {string} */ (memo.keys().next().value));
  return value;
}

// The change in best swaps if tool n took value: a lane key, NEW, or { merge: j } to join tool j.
// A lane or NEW also takes tool n out of the merges, and a merge drops tool n's own pick.
// fixed lists the tools whose bay the user chose (see placement): a bay makes tool n one of them.
// It is null when that choice leaves more colours than lanes (the picker shows "does not fit"),
// and 0 when the plan has nothing to place.
/**
 * @param {Plan} plan @param {Choice} pick @param {PlanLane[]} lanes @param {Record<number, number>} merges
 * @param {number} n @param {string | { merge: number }} value @param {number[]} [fixed] @returns {number | null}
 */
export function pickCost(plan, pick, lanes, merges, n, value, fixed = []) {
  const base = placement(plan, pick, lanes, merges, fixed)?.best ?? 0;
  const lane = typeof value === 'string';
  const p2 = lane ? { ...pick, [n]: value } : without(pick, n);
  const m2 = lane ? without(merges, n) : { ...merges, [n]: value.merge };
  const others = fixed.filter((x) => x !== n);
  const f2 = lane && value !== NEW ? [...others, n].sort((a, b) => a - b) : others;
  const after = placement(plan, p2, lanes, m2, f2);
  if (!after) return 0;
  return after.best === null ? null : after.best - base;
}
