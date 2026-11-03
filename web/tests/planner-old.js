// The colour planner as it was before its search was made faster, kept only as a reference for
// tests/planner-same.test.js. The page never loads it. Do not change it.
// With tests/planner-same.test.js, it proves that the rewrite of the search kept the result and the
// order among equal results of the planner as of 2026-10-27. A change to the planner's rule on
// purpose replaces this pair by tests with fixed expected results, or deletes it.
// It works out where to put each spool, so that the file needs the fewest lane swaps.
// A swap happens at a T whose lane is not the last lane of its head, as in print-plan.js.
// Tools of the same colour and material share one spool, so the search places groups of tools.
// The search is exact. It walks the heads in order and keeps each result by (head, groups left).
// Among the placements with the fewest swaps, it keeps the one that moves the fewest spools.
import { HEADS, headOfIn, materialMatch, planId, sameTool, swaps, usedTest } from '../js/print-plan.js';

/** @typedef {import('../js/types.js').Plan} Plan */
/** @typedef {import('../js/types.js').PlanLane} PlanLane */
/** @typedef {import('../js/print-plan.js').Choice} Choice */
/**
 * One line of the block, for a group of tools of the same colour and material. tools lists them.
 * act is 'there' (the spool is already there), 'move' (move it from the lane from), 'check' (the
 * lane holds a spool that no tool owns: if it is the group's colour, the user chooses it, and if
 * not, puts one) or 'put' (put a spool of the group's material and colour in key).
 * @typedef {{ tools: number[], key: string, act: 'there' | 'move' | 'check' | 'put', from: string | null }} PlaceLine
 */
/**
 * What the planner found. fits is false when the file prints more colours than there are lanes.
 * Then lines and takeOut are empty, best is null and choice is {}.
 * colours counts the groups of tools: tools of the same colour and material count once.
 * lines has one line per group, in the order of the groups' first tools. choice is per tool.
 * takeOut lists the suggested lanes that hold a spool that no line keeps or checks.
 * now is the swaps of the pick, or null while a tool that the file prints has no lane on a head.
 * @typedef {{ fits: boolean, colours: number, lanes: number, lines: PlaceLine[], takeOut: string[],
 *   now: number | null, best: number | null, choice: Choice }} Placement
 */
/** @typedef {{ swaps: number, moved: number, masks: number[] }} Best */

// One swap stops the print for about this long, in seconds. The bench measured 88 to 99 s.
export const SWAP_S = 90;

// The group of each used tool, by its index in used, and the number of groups.
// Tools of the same colour and material share one spool, as swaps() already counts them.
// A tool with no colour is a group of its own. The groups are in the order of their first tool.
/** @param {Plan} plan @param {number[]} used @returns {{ of: number[], count: number }} */
function groupsOf(plan, used) {
  /** @type {number[]} */
  const firsts = [];
  const of = used.map((n) => {
    const tool = plan.tools[n];
    const g = tool.colour ? firsts.findIndex((f) => sameTool(plan.tools[f], tool)) : -1;
    if (g >= 0) return g;
    firsts.push(n);
    return firsts.length - 1;
  });
  return { of, count: firsts.length };
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

// The swaps of a set of groups on one head, with print-plan.js's swaps(). order is the T order
// of groups, and mask holds the bits of the set. Each group of the set is a lane of its own.
/** @param {number[]} order @param {number} mask */
function headSwaps(order, mask) {
  /** @type {Choice} */
  const choice = {};
  for (const g of order) if ((mask >> g) & 1) choice[g] = String(g);
  return swaps(order, choice, () => 0)[0] ?? 0;
}

// The lane that each group owns today, or null. A tool owns the lane of its pick when the bay
// holds a spool that is not of another material. When two tools pick one lane, the first owns it.
// A group owns the lane of its first tool, in tool order, that owns one.
/**
 * @param {Plan} plan @param {number[]} used @param {{ of: number[], count: number }} groups
 * @param {Choice} pick @param {PlanLane[]} lanes @returns {(PlanLane | null)[]}
 */
function owned(plan, used, groups, pick, lanes) {
  /** @type {(PlanLane | null)[]} */
  const own = Array.from({ length: groups.count }, () => null);
  const taken = new Set();
  used.forEach((n, i) => {
    const g = groups.of[i];
    const lane = lanes.find((l) => l.key === pick[n]);
    if (own[g] || !lane || lane.empty || taken.has(lane.key)) return;
    if (materialMatch(plan.tools[n]?.material, lane.material) === 'other') return;
    taken.add(lane.key);
    own[g] = lane;
  });
  return own;
}

// The exact search over count groups. caps holds the lanes of each head, and kept the head where
// each group keeps its spool (-1 for none). It returns the groups of each head as bit masks.
/**
 * @param {number[]} order @param {number} count @param {number[]} caps @param {number[]} kept
 * @returns {Best}
 */
function search(order, count, caps, kept) {
  /** @type {Map<number, number>} */
  const cost = new Map();
  /** @param {number} mask */
  const costOf = (mask) => {
    let c = cost.get(mask);
    if (c === undefined) {
      c = headSwaps(order, mask);
      cost.set(mask, c);
    }
    return c;
  };
  /** @param {number} mask @param {number} h */
  const keptIn = (mask, h) => kept.filter((k, i) => k === h && (mask >> i) & 1).length;
  // The lanes of the heads after head h. A head leaves the later heads no more groups than they hold.
  const room = caps.map((_, h) => caps.slice(h + 1).reduce((a, b) => a + b, 0));
  /** @type {Map<string, Best>} */
  const memo = new Map();
  // lo leaves the later heads no more groups than they hold, so the last head takes the rest.
  /** @param {number} h @param {number} rest @returns {Best} */
  function best(h, rest) {
    if (h === caps.length) return { swaps: 0, moved: 0, masks: [] };
    const key = `${h}:${rest}`;
    const seen = memo.get(key);
    if (seen !== undefined) return seen;
    const bits = Array.from({ length: count }, (_, i) => i).filter((i) => (rest >> i) & 1);
    const lo = Math.max(0, bits.length - room[h]);
    const hi = Math.min(caps[h], bits.length);
    let out = /** @type {Best | null} */ (null);
    /** @param {number} mask @param {number} size */
    const consider = (mask, size) => {
      const tail = best(h + 1, rest & ~mask);
      const s = costOf(mask) + tail.swaps;
      const moved = size - keptIn(mask, h) + tail.moved;
      if (!out || s < out.swaps || (s === out.swaps && moved < out.moved))
        out = { swaps: s, moved, masks: [mask, ...tail.masks] };
    };
    /** @param {number} from @param {number} mask @param {number} size */
    const walk = (from, mask, size) => {
      if (size >= lo) consider(mask, size);
      if (size === hi) return;
      for (let j = from; j < bits.length; j += 1) walk(j + 1, mask | (1 << bits[j]), size + 1);
    };
    walk(0, 0, 0);
    const found = /** @type {Best} */ (out);
    memo.set(key, found);
    return found;
  }
  return best(0, (1 << count) - 1);
}

// The lane of each group on its head. A group that keeps its spool keeps its lane.
// The other groups take the free lanes of the head in this order: the empty bays, then the lanes
// whose spool goes to another head, then the other lanes. Inside each kind it is lane order.
/**
 * @param {number[]} masks @param {(PlanLane | null)[]} own @param {PlanLane[][]} byHead
 * @returns {string[]}
 */
function assign(masks, own, byHead) {
  /** @type {string[]} */
  const lane = [];
  const owners = new Set(own.map((l) => l?.key));
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

// The act of a group's line in lane. from is the lane of the group's own spool, or null.
// The planner does not read colours. So when the group has no spool and the lane holds a spool
// that no group owns, that spool may be of the group's colour: the user checks it.
// A spool of another known material cannot be.
/**
 * @param {PlanLane} lane @param {string | null} from @param {string | null | undefined} material
 * @param {Set<string | undefined>} owners @returns {PlaceLine['act']}
 */
function actOf(lane, from, material, owners) {
  if (from) return from === lane.key ? 'there' : 'move';
  if (lane.empty || owners.has(lane.key)) return 'put';
  return materialMatch(material, lane.material) === 'other' ? 'put' : 'check';
}

// The lines of the block, one per group, the choice of each tool from the lane of its group,
// and the lanes to take out. A put or a move into a lane whose spool no group owns takes that spool out
// (a put: it is of another material; a move: the moved spool needs the bay).
/**
 * @param {Plan} plan @param {{ used: number[], of: number[] }} groups @param {string[]} lane
 * @param {(PlanLane | null)[]} own @param {PlanLane[]} lanes
 * @returns {{ lines: PlaceLine[], choice: Choice, takeOut: string[] }}
 */
function linesOf(plan, { used, of }, lane, own, lanes) {
  /** @type {Choice} */
  const choice = {};
  used.forEach((n, i) => {
    choice[n] = lane[of[i]];
  });
  const owners = new Set(own.map((l) => l?.key));
  const lines = lane.map((key, g) => {
    const tools = used.filter((_, i) => of[i] === g);
    const from = own[g]?.key ?? null;
    const act = actOf(
      /** @type {PlanLane} */ (lanes.find((l) => l.key === key)),
      from,
      plan.tools[tools[0]].material,
      owners,
    );
    return { tools, key, act, from: act === 'move' ? from : null };
  });
  const takeOut = lines
    .filter(
      (l) => l.act !== 'there' && l.act !== 'check' && !owners.has(l.key) && !lanes.find((x) => x.key === l.key)?.empty,
    )
    .map((l) => l.key);
  return { lines, choice, takeOut };
}

/** @param {Plan} plan @param {number[]} used @param {Choice} pick @param {PlanLane[]} lanes @returns {Placement} */
function work(plan, used, pick, lanes) {
  const byHead = Array.from({ length: HEADS }, (_, h) => lanes.filter((l) => l.head === h));
  const total = byHead.reduce((a, l) => a + l.length, 0);
  const headOf = headOfIn(lanes);
  const counts = swaps(plan.order, pick, headOf);
  // A pick that names no hooked lane on a head has no swaps to count.
  const now = used.every((n) => headOf(pick[n]) != null) ? Object.values(counts).reduce((a, b) => a + b, 0) : null;
  const groups = groupsOf(plan, used);
  const base = { colours: groups.count, lanes: total, now };
  if (groups.count > total) return { ...base, fits: false, lines: [], takeOut: [], best: null, choice: {} };
  const own = owned(plan, used, groups, pick, lanes);
  const kept = own.map((l) => l?.head ?? -1);
  const found = search(
    groupOrder(plan.order, used, groups.of),
    groups.count,
    byHead.map((l) => l.length),
    kept,
  );
  const lane = assign(found.masks, own, byHead);
  const { lines, choice, takeOut } = linesOf(plan, { used, of: groups.of }, lane, own, lanes);
  return { ...base, fits: true, lines, takeOut, best: found.swaps, choice };
}

// The last result, by plan, the pick of the printed tools and what the planner reads of each lane.
// The panel draws often, and the search runs once.
/** @type {{ key: string, value: Placement | null } | null} */
let last = null;

// Where to put each spool for plan, from the pick (chosen() in print-plan.js) and the plan's lanes.
// It is null when the plan has no T order or no tool that the file prints.
/**
 * @param {Plan | null | undefined} plan @param {Choice} pick @param {PlanLane[]} lanes
 * @returns {Placement | null}
 */
export function placement(plan, pick, lanes) {
  if (!plan || !Array.isArray(plan.order) || !plan.order.length) return null;
  const used = plan.tools.map((_, n) => n).filter(usedTest(plan.order));
  const read = lanes.map(({ key, head, empty, material }) => ({ key, head, empty, material }));
  const key = JSON.stringify([planId(plan), used.map((n) => pick[n] ?? null), read]);
  if (last?.key !== key) {
    last = { key, value: used.length ? work(plan, used, pick, lanes) : null };
  }
  return last.value;
}
