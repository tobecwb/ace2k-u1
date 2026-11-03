// The geometry of the Units diagram: where the head cards, the units, their bays and the rails go.
// It is pure: it takes the diagram's model and returns rectangles and SVG path data, so it runs under node.
// The numbers are the approved mockup's (a view 1200 wide): the heads on top, the rails, the units below.
import { normalColour } from './print-plan.js';

/**
 * A lane as the diagram draws it. key is the console's name ("a:2", or "2" with one unit), head is 0-3 or null.
 * error is the words of the lane's error ("Motor stalled"), absent or null while it is not in error; errorShort
 * is the catalogue key of the one word of at most 8 characters that its bay tile has room for ("Stalled"). filled
 * says the path from the bay to its head has filament in it (model.js pathLoaded), and pathColour is that filament's
 * colour (the spool in the bay, or a run-out tail's last known spool).
 * @typedef {{ key: string, n: number, head: number | null, empty: boolean,
 *   spool: { colour: string | null, material: string | null, tag: boolean } | null, current: boolean,
 *   filled: boolean, pathColour?: string | null, pathMaterial?: string | null, error?: string | null, errorShort?: string | null, chip?: string, state?: string | null }} DLane
 * @typedef {{ name: string | null, lanes: DLane[] }} DUnit
 * A head. unknown is true while several lanes feed it and the adapter does not know which one is in it.
 * @typedef {{ head: number, u1Spool: { colour: string | null, material: string | null } | null,
 *   loaded: string | null, unknown?: boolean }} DHead
 * @typedef {{ units: DUnit[], heads: DHead[] }} DModel
 * @typedef {{ x: number, y: number, w: number, h: number }} Rect
 * @typedef {Rect & { key: string, cx: number }} BayBox
 * @typedef {Rect & { name: string | null, bays: BayBox[] }} UnitBox
 * @typedef {Rect & { head: number, cx: number }} HeadBox
 * @typedef {{ key: string, head: number, x: number, y: number, d: string, dashed: boolean, live: boolean,
 *   colour: string | null }} Rail
 * @typedef {{ head: number, d: string, dashed: boolean }} Segment
 * @typedef {{ width: number, height: number, heads: HeadBox[], units: UnitBox[], rails: Rail[],
 *   segments: Segment[] }} Layout
 */

const VIEW_W = 1200;
const VIEW_H = 520;
// The head cards: four in a row, 300 apart.
const HEAD_X0 = 18;
const HEAD_STEP = 300;
const HEAD_Y = 26;
const HEAD_W = 264;
const HEAD_H = 96;
// The riser of a head's rails ends under its nozzle; the live path stops just below the tip.
const RISER_TOP = 140;
const LIVE_TOP = 142;
// The units: amber cards side by side, the block kept off the view's edges.
const UNIT_Y = 330;
const UNIT_W = 272;
const UNIT_H = 182;
const UNIT_GAP = 18;
const SIDE = 29;
// The bays inside a unit.
const BAY_DX = 15;
const BAY_Y = 342;
const BAY_W = 56;
const BAY_H = 128;
const BAY_STEP = 62;
// Each fed head has its own bus, the first at 296 and each next 22 higher; the live path turns with radius 14.
const BUS_Y = 296;
const BUS_STEP = 22;
const TURN = 14;

// The one head each unit feeds, or null when it feeds none or several.
/** @param {DUnit} u @returns {number | null} */
function onlyHead(u) {
  const fed = new Set(u.lanes.map((l) => l.head).filter((h) => h !== null));
  return fed.size === 1 ? /** @type {number} */ ([...fed][0]) : null;
}

// The heads the units sit under: with several units, where each feeds one head of its own. Else null.
/** @param {DUnit[]} units @returns {number[] | null} */
function headsUnder(units) {
  if (units.length < 2) return null;
  const heads = units.map(onlyHead);
  if (heads.some((h) => h === null) || new Set(heads).size !== heads.length) return null;
  return /** @type {number[]} */ (heads);
}

// The path of the lane in a head: up from its bay to the bus, along it to the riser, and up to the nozzle,
// turning round both corners. A lane right under the head goes straight up.
/** @param {number} x @param {number} y @param {number} cx @returns {string} */
export function livePath(x, y, cx) {
  if (x === cx) return `M${x} ${BAY_Y} L${x} ${LIVE_TOP}`;
  const dir = Math.sign(cx - x);
  const r = Math.min(TURN, Math.abs(cx - x) / 2);
  return `M${x} ${BAY_Y} L${x} ${y + r} Q${x} ${y} ${x + dir * r} ${y} L${cx - dir * r} ${y} Q${cx} ${y} ${cx} ${y - r} L${cx} ${LIVE_TOP}`;
}

// Where everything goes. The view is 1200 wide, or wider when the units need more; the heads stay centred.
/** @param {DModel} model @returns {Layout} */
export function layout(model) {
  const n = model.units.length;
  const blockW = n * UNIT_W + Math.max(0, n - 1) * UNIT_GAP;
  const width = Math.max(VIEW_W, blockW + 2 * SIDE);
  const ox = (width - VIEW_W) / 2;
  const heads = model.heads.map((h) => {
    const x = ox + HEAD_X0 + HEAD_STEP * h.head;
    return { head: h.head, x, y: HEAD_Y, w: HEAD_W, h: HEAD_H, cx: x + HEAD_W / 2 };
  });
  /** @param {number} h */
  const headCx = (h) => ox + HEAD_X0 + HEAD_STEP * h + HEAD_W / 2;
  const under = headsUnder(model.units);
  const start = (width - blockW) / 2;
  const units = model.units.map((u, i) => {
    const x = under ? headCx(under[i]) - UNIT_W / 2 : start + i * (UNIT_W + UNIT_GAP);
    const bays = u.lanes.map((l, k) => {
      const bx = x + BAY_DX + k * BAY_STEP;
      return { key: l.key, x: bx, y: BAY_Y, w: BAY_W, h: BAY_H, cx: bx + BAY_W / 2 };
    });
    return { name: u.name, x, y: UNIT_Y, w: UNIT_W, h: UNIT_H, bays };
  });
  const { rails, segments } = railsOf(model, units, headCx);
  return { width, height: VIEW_H, heads, units, rails, segments };
}

// The rails: one per hooked lane (its stub up to the bus, or the whole path for the lane in the head),
// and each head's bus segments and riser, dashed where only empty lanes pass.
/** @param {DModel} model @param {UnitBox[]} units @param {(h: number) => number} headCx */
function railsOf(model, units, headCx) {
  const hooked = model.units.flatMap((u, i) =>
    u.lanes.flatMap((l, k) => (l.head === null ? [] : [{ lane: l, head: l.head, x: units[i].bays[k].cx }])),
  );
  const fed = [...new Set(hooked.map((r) => r.head))].sort((a, b) => a - b);
  /** @param {number} h */
  const busY = (h) => BUS_Y - BUS_STEP * fed.indexOf(h);
  const rails = hooked.map(({ lane, head, x }) => {
    const y = busY(head);
    // The lane in its head is drawn live only while its rail has filament in it; else it is an empty stub.
    const live = lane.filled;
    return {
      key: lane.key,
      head,
      x,
      y,
      d: live ? livePath(x, y, headCx(head)) : `M${x} ${BAY_Y} L${x} ${y}`,
      dashed: !live && lane.empty,
      live,
      colour: (live ? lane.pathColour : null) ?? lane.spool?.colour ?? null,
    };
  });
  const segments = fed.flatMap((head) =>
    busOf(
      head,
      busY(head),
      headCx(head),
      hooked.filter((r) => r.head === head),
    ),
  );
  return { rails, segments };
}

// One head's bus, cut at every lane and at the riser, then the riser itself.
/**
 * @param {number} head @param {number} y @param {number} cx
 * @param {{ lane: DLane, x: number }[]} lanes
 * @returns {Segment[]}
 */
function busOf(head, y, cx, lanes) {
  const pts = [...new Set([...lanes.map((l) => l.x), cx])].sort((a, b) => a - b);
  const bus = pts.slice(1).map((b, i) => {
    const a = pts[i];
    const on = lanes.filter((l) => (l.x <= a && cx >= b) || (l.x >= b && cx <= a));
    return { head, d: `M${a} ${y} L${b} ${y}`, dashed: on.every((l) => l.lane.empty) };
  });
  return [...bus, { head, d: `M${cx} ${y} L${cx} ${RISER_TOP}`, dashed: lanes.every((l) => l.lane.empty) }];
}

// A colour as three channels 0-1, or null when it is not a colour normalColour knows.
/** @param {string | null | undefined} c @returns {number[] | null} */
function channels(c) {
  const hex = normalColour(c);
  if (!hex) return null;
  return [0, 2, 4].map((i) => Number.parseInt(hex.slice(i + 1, i + 3), 16) / 255);
}

// The relative luminance of three channels (WCAG).
/** @param {number[]} rgb */
const luminance = (rgb) => {
  const [r, g, b] = rgb.map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
};

// True for a filament colour that vanishes on a light card: white, light grey and yellow.
// The light theme gives those a thin grey outline.
/** @param {string | null | undefined} c */
export function pale(c) {
  const v = channels(c);
  if (!v) return false;
  return luminance(v) > 0.7 || (v[0] >= 0.8 && v[1] >= 0.6 && v[2] <= 0.5);
}

// True for a filament colour that vanishes on a dark card: black and near black.
// The dark theme gives those a light casing.
/** @param {string | null | undefined} c */
export function deep(c) {
  const v = channels(c);
  return Boolean(v) && luminance(/** @type {number[]} */ (v)) < 0.04;
}
