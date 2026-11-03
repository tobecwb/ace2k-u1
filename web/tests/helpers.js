// Shared builders and the HTML reader of the page's tests.
import { readFileSync } from 'node:fs';
import { setCatalog } from '../js/i18n.js';

// The English catalogue, as the page loads it. Every view test starts with this.
export function loadEnglish() {
  setCatalog(JSON.parse(readFileSync(new URL('../i18n/en.json', import.meta.url), 'utf8')));
}

// A lane as the unit reports it, with every field the page reads.
export const lane = (over = {}) => ({
  mode: 'idle',
  insert: true,
  rest: true,
  pushed: false,
  error: null,
  encoder_mm: 0,
  ff_on: true,
  last_event: null,
  tag: { state: 'no_tag', record: null },
  ...over,
});

// Four lanes. `over` maps a lane index to its overrides.
export const bays = (over = {}) => [0, 1, 2, 3].map((i) => lane(over[i]));

// The adapter hooked, with no head map. Lane n of a single unit then feeds head n-1.
export function status({
  print = 'standby',
  lanes = bays(),
  heads = [false, false, false, false],
  ptc,
  hooked = true,
} = {}) {
  const s = {
    ace2k_u1: { hooked },
    ace2k: {
      version: '0.11.0',
      link_proven: true,
      health: { ok: true },
      lanes,
      chamber: 27,
      humidity: 52,
      ptc_left: 29.1,
      ptc_right: 28.7,
      dryer: {
        state: 'idle',
        target: null,
        remaining: null,
        fans: { left: false, right: false },
        flaps: { bottom: 'closed', rear: 'closed' },
        fault: null,
        notices: [],
      },
    },
    print_stats: { state: print },
    print_task_config: ptc || {
      filament_exist: [false, false, false, false],
      filament_vendor: ['NONE', 'NONE', 'NONE', 'NONE'],
      filament_type: ['NONE', 'NONE', 'NONE', 'NONE'],
      filament_sub_type: ['NONE', 'NONE', 'NONE', 'NONE'],
      filament_color_rgba: ['FFFFFFFF', 'FFFFFFFF', 'FFFFFFFF', 'FFFFFFFF'],
    },
  };
  heads.forEach((d, i) => {
    s[`filament_motion_sensor e${i}_filament`] = { filament_detected: d, enabled: true };
  });
  // The U1 screen's head load macro, with no batch open.
  s['gcode_macro AUTO_FEEDING_BATCH'] = { doing: false };
  return s;
}

// One unit of four lanes, with the adapter hooked. `over` maps a lane index to its overrides.
export const oneUnit = (over = {}) => ({ ace2k_u1: { hooked: true }, ace2k: { lanes: bays(over) } });

// Two units, "a" and "b", with no head map. `over` maps a lane index of b to its overrides.
export const twoUnits = (over = {}) => ({
  ace2k_u1: { hooked: true },
  ace2k_units: { units: ['ace2k a', 'ace2k b'] },
  'ace2k a': { lanes: bays() },
  'ace2k b': { lanes: bays(over) },
});

// Two units with a head map (a:1 and a:2 share head 0), links proven, b drying.
export const twoHeadedUnits = () => ({
  ace2k_units: { units: ['ace2k a', 'ace2k b'], active_lanes: {} },
  'ace2k a': { lanes: bays({ 0: { mode: 'following' } }), link_proven: true, health: { ok: true } },
  'ace2k b': { lanes: bays(), link_proven: true, health: { ok: true }, dryer: { state: 'heating' } },
  ace2k_u1: {
    hooked: true,
    heads: [
      [
        { unit: 'a', lane: 1 },
        { unit: 'a', lane: 2 },
      ],
      [{ unit: 'a', lane: 3 }],
      [{ unit: 'a', lane: 4 }],
      [{ unit: 'b', lane: 1 }],
    ],
    current: [
      { unit: 'a', lane: 1 },
      { unit: 'a', lane: 3 },
      { unit: 'a', lane: 4 },
      { unit: 'b', lane: 1 },
    ],
  },
});

const VOID_TAGS = new Set(['area', 'br', 'col', 'hr', 'img', 'input', 'link', 'meta', 'source', 'wbr']);
const ENTITIES = { '&amp;': '&', '&lt;': '<', '&gt;': '>', '&quot;': '"', '&#39;': "'" };
const unentity = (s) => s.replace(/&(?:amp|lt|gt|quot|#39);/g, (m) => ENTITIES[m]);

const readAttrs = (raw) =>
  Object.fromEntries(
    [...raw.matchAll(/([^\s=>/]+)(?:="([^"]*)")?/g)].map((a) => [a[1], a[2] === undefined ? '' : unentity(a[2])]),
  );

const closeTag = (open, name) => {
  const at = open.map((e) => e.tag).lastIndexOf(name.toLowerCase());
  if (at >= 0) open.length = at;
};

function openTag(found, open, [, , name, raw, selfClosing]) {
  const el = { tag: name.toLowerCase(), attrs: readAttrs(raw), text: '', ancestors: [...open] };
  found.push(el);
  if (!selfClosing && !VOID_TAGS.has(el.tag)) open.push(el);
}

// Reads the tags and the text of the markup that the views emit. It handles double-quoted or bare attributes,
// and void and self-closing tags, but no comments or CDATA. Returns every element, in document order.
function parse(html) {
  const found = [];
  const open = [];
  const token = /<(\/?)([a-zA-Z][\w-]*)((?:\s+[^\s=>/]+(?:="[^"]*")?)*)\s*(\/?)>|([^<]+)/g;
  let end = 0;
  for (let m = token.exec(html); m; m = token.exec(html)) {
    if (m.index !== end)
      throw new Error(`elements: cannot parse the markup at ${JSON.stringify(html.slice(end, end + 40))}`);
    end = token.lastIndex;
    if (m[5] !== undefined) for (const el of open) el.text += unentity(m[5]);
    else if (m[1]) closeTag(open, m[2]);
    else openTag(found, open, m);
  }
  if (end !== html.length)
    throw new Error(`elements: cannot parse the markup at ${JSON.stringify(html.slice(end, end + 40))}`);
  return found;
}

// One compound selector: tag, .class, #id, [attr], [attr=value], in any combination.
function matcher(compound) {
  const tag = /^[a-zA-Z][\w-]*/.exec(compound)?.[0].toLowerCase();
  const tests = [...compound.matchAll(/\.([\w-]+)|#([\w-]+)|\[([\w-]+)(?:=("?)([^\]"]*)\4)?\]/g)].map((m) => {
    if (m[1]) return (el) => (el.attrs.class || '').split(/\s+/).includes(m[1]);
    if (m[2]) return (el) => el.attrs.id === m[2];
    if (m[5] === undefined) return (el) => m[3] in el.attrs;
    return (el) => el.attrs[m[3]] === m[5];
  });
  return (el) => (!tag || el.tag === tag) && tests.every((f) => f(el));
}

// The elements of `html` that match `selector`. Compound selectors joined by spaces match descendants.
// Each element is {tag, attrs, text}. `text` is the visible text of the element and of everything inside it.
export function elements(html, selector) {
  const steps = selector.trim().split(/\s+/).map(matcher);
  const last = steps.pop();
  const within = (el) => {
    // The ancestors must hold the remaining steps, in order.
    let at = 0;
    for (const a of el.ancestors) if (at < steps.length && steps[at](a)) at += 1;
    return at === steps.length;
  };
  return parse(html)
    .filter((el) => last(el) && within(el))
    .map(({ tag, attrs, text }) => ({ tag, attrs, text }));
}

// The status of a case of fixtures/transitions.json.
// A `lanes` object keyed by lane index ("0" to "3") is a shorthand. It gives the four lanes of an idle, empty unit,
// with the fields of the listed lanes on top. The Python loader (tests/test_history.py) reads the same rule.
const IDLE_LANE = {
  insert: false,
  mode: 'idle',
  error: null,
  last_event: null,
  tag: { state: 'unknown', record: null },
};
export function expandStatus(st) {
  const over = st?.lanes;
  if (
    !over ||
    Array.isArray(over) ||
    typeof over !== 'object' ||
    !Object.keys(over).every((k) => ['0', '1', '2', '3'].includes(k))
  )
    return st;
  return { ...st, lanes: [0, 1, 2, 3].map((i) => structuredClone({ ...IDLE_LANE, ...over[i] })) };
}

// The Print tab's plan view on demo-unknown: a two-colour file (red, white PLA) planned, on a desktop or a phone's
// width (narrow), with the side panel named open.
export function appWithPlan({ narrow = false, panel = null } = {}) {
  const s = JSON.parse(readFileSync(new URL('../dev/demo-unknown.json', import.meta.url), 'utf8'));
  const plan = {
    file: 'p.gcode',
    mtime: 1,
    tools: [
      { material: 'PLA', colour: '#dd3333', lane: null, why: 'closest' },
      { material: 'PLA', colour: '#ffffff', lane: null, why: 'closest' },
    ],
    order: [0, 1, 0],
    raw: [],
    heads: {},
    set: false,
    choice: null,
    lanes: [],
  };
  return {
    status: { ...s, ace2k_u1: { ...s.ace2k_u1, plan } },
    online: true,
    klippy: 'ready',
    selected: null,
    pending: new Set(),
    folds: {},
    moveMm: '10',
    edit: null,
    form: { preset: 1, temp: 60, hm: '6:00' },
    forms: {},
    printOptions: {},
    print: { open: true, files: [], file: 'p.gcode', choice: {}, choiceId: null },
    ui: { tab: 'print', panel, sheet: false, narrow },
  };
}
