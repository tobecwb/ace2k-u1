import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import {
  unitNames,
  known,
  unitObjects,
  pkey,
  scoped,
  PAGE_WIDE_KEYS,
  LOAD_HEAD_KEY,
  selection,
  samplesOf,
  keepDays,
  olderDays,
  ready,
  livePlan,
  shownPlan,
  readingOf,
  otherDevice,
  readHidden,
  readTheme,
  nextTheme,
  loadTheme,
  storeTheme,
  TABS,
  tabOf,
  loadTab,
  storeTab,
  defaultSelection,
  dryerOf,
  titleView,
} from '../js/state.js';
import { sliceFor } from '../js/model.js';
import { dryerView } from '../js/dryer-form.js';
import { bays, lane, twoHeadedUnits } from './helpers.js';

const one = (extra = {}) => ({
  status: { ace2k: { lanes: bays() } },
  pending: new Set(),
  samples: [],
  days: [],
  loaded: new Set(),
  print: { file: null },
  conn: null,
  selected: null,
  ...extra,
});
const two = (extra = {}) =>
  one({
    status: {
      ace2k_units: { units: ['ace2k a', 'ace2k b'] },
      'ace2k a': { lanes: bays() },
      'ace2k b': { lanes: bays() },
    },
    ...extra,
  });

test('unitNames: one unit without a list is the connection own unit, else the unnamed one', () => {
  assert.deepEqual(unitNames(one()), [null]);
  assert.deepEqual(unitNames(one({ conn: { unit: 'ace2k a' } })), ['a']);
  assert.deepEqual(unitNames(two()), ['a', 'b']);
});

test('known: a unit while it exists, else the first', () => {
  assert.equal(known(two(), 'b'), 'b');
  assert.equal(known(two(), 'gone'), 'a');
  assert.equal(known(two(), null), 'a');
  assert.equal(known(one(), 'x'), null);
});

test('unitObjects: the listed objects, else the connection units, else ace2k', () => {
  assert.deepEqual(unitObjects(two()), ['ace2k a', 'ace2k b']);
  assert.deepEqual(unitObjects(one()), ['ace2k']);
  assert.deepEqual(unitObjects(one({ conn: { units: ['ace2k z'] } })), ['ace2k z']);
});

test('pkey and scoped: plain keys with one unit, the unit in front with several', () => {
  const o = one({ pending: new Set(['feed:1', 'x']) });
  assert.equal(pkey(o, 'a', 'feed:1'), 'feed:1');
  assert.equal(scoped(o, 'a'), o.pending);
  const m = two({ pending: new Set(['a|feed:1', 'b|feed:2', 'a|dry:']) });
  assert.equal(pkey(m, 'b', 'feed:2'), 'b|feed:2');
  assert.deepEqual([...scoped(m, 'a')].sort(), ['dry:', 'feed:1']);
  assert.deepEqual([...scoped(m, 'b')], ['feed:2']);
  // Load into head N's key belongs to the whole page (PAGE_WIDE_KEYS): every unit sees it. Other plain keys stay out.
  assert.ok(PAGE_WIDE_KEYS.has(LOAD_HEAD_KEY) && LOAD_HEAD_KEY === 'load_head');
  const l = two({ pending: new Set(['load_head', 'stop_all:', 'b|feed:2']) });
  assert.deepEqual([...scoped(l, 'a')], ['load_head']);
  assert.deepEqual([...scoped(l, 'b')], ['load_head', 'feed:2']);
});

test('selection: the chosen lane while its unit exists, else the default', () => {
  assert.deepEqual(selection(two({ selected: { unit: 'b', lane: 2 } })), { unit: 'b', lane: 2 });
  assert.deepEqual(selection(two({ selected: { unit: 'gone', lane: 2 } })), { unit: 'a', lane: 0 });
  assert.deepEqual(selection(two()), { unit: 'a', lane: 0 });
  const err = two();
  err.status['ace2k b'].lanes[3] = lane({ mode: 'error' });
  assert.deepEqual(selection(err), { unit: 'b', lane: 3 });
});

test('samplesOf: all samples with one unit; per unit, cached until app.samples changes', () => {
  const o = one({ samples: [{ t: 1 }] });
  assert.equal(samplesOf(o, null, { src: null, by: new Map() }), o.samples);
  const m = two({ samples: [{ t: 1 }, { t: 2, u: 'a' }, { t: 3, u: 'b' }] });
  const cache = { src: null, by: new Map() };
  const a = samplesOf(m, 'a', cache);
  assert.deepEqual(
    a.map((x) => x.t),
    [1, 2],
  );
  assert.equal(samplesOf(m, 'a', cache), a);
  assert.deepEqual(
    samplesOf(m, 'b', cache).map((x) => x.t),
    [3],
  );
  m.samples = [...m.samples, { t: 4, u: 'a' }];
  const a2 = samplesOf(m, 'a', cache);
  assert.notEqual(a2, a);
  assert.equal(a2.length, 3);
});

test('keepDays: the host value when a positive integer, else 7', () => {
  const k = (v) => keepDays(one({ status: { ace2k_u1_history: { keep_days: v } } }));
  assert.equal(keepDays(one()), 7);
  assert.equal(k(3), 3);
  for (const bad of [0, -2, 1.5, '4', null]) assert.equal(k(bad), 7);
});

test('olderDays: an unloaded day within keep_days; never in the demo', () => {
  const days = ['d1', 'd2', 'd3'];
  assert.equal(olderDays(one({ days, loaded: new Set(['d1']) }), false), true);
  assert.equal(olderDays(one({ days, loaded: new Set(days) }), false), false);
  assert.equal(olderDays(one({ days, loaded: new Set() }), true), false);
  const nine = Array.from({ length: 9 }, (_, i) => `d${i}`);
  assert.equal(olderDays(one({ days: nine, loaded: new Set(nine.slice(0, 7)) }), false), false);
});

test('ready: online and Klippy ready', () => {
  assert.equal(ready({ online: true, klippy: 'ready' }), true);
  assert.equal(ready({ online: true, klippy: 'startup' }), false);
  assert.equal(ready({ online: false, klippy: 'ready' }), false);
});

test('livePlan and shownPlan: the reported plan, for the chosen file', () => {
  assert.equal(livePlan(one()), null);
  assert.equal(shownPlan(one()), null);
  const plan = { file: 'a.gcode', lanes: [] };
  const o = one({ status: { ace2k_u1: { plan } }, print: { file: 'a.gcode' } });
  assert.equal(livePlan(o), plan);
  assert.equal(shownPlan(o), plan);
  assert.equal(shownPlan({ ...o, print: { file: 'other.gcode' } }), null);
});

test('readHidden: the stored list, or none for nothing, bad JSON or a non-list', () => {
  assert.deepEqual(readHidden('["rh","hl"]'), ['rh', 'hl']);
  assert.deepEqual(readHidden(null), []);
  assert.deepEqual(readHidden('{'), []);
  assert.deepEqual(readHidden('{"a":1}'), []);
});

test('readTheme: light and dark as stored, system for nothing or anything else', () => {
  assert.equal(readTheme('light'), 'light');
  assert.equal(readTheme('dark'), 'dark');
  for (const v of [null, undefined, '', 'system', 'blue']) assert.equal(readTheme(v), 'system');
});

test('nextTheme: system, light, dark, and round again; an unknown mode starts over', () => {
  assert.deepEqual(['system', 'light', 'dark'].map(nextTheme), ['light', 'dark', 'system']);
  assert.equal(nextTheme('unknown'), 'system');
});

test('loadTheme and storeTheme: the choice in storage; system removes it; no storage, or one that throws, follows the system', () => {
  const data = new Map();
  const storage = {
    getItem: (k) => data.get(k) ?? null,
    setItem: (k, v) => data.set(k, v),
    removeItem: (k) => data.delete(k),
  };
  assert.equal(loadTheme(storage), 'system');
  storeTheme(storage, 'dark');
  assert.equal(data.get('ace2k.theme'), 'dark');
  assert.equal(loadTheme(storage), 'dark');
  storeTheme(storage, 'system');
  assert.equal(data.has('ace2k.theme'), false);
  const broken = {
    getItem: () => {
      throw new Error('blocked');
    },
    setItem: () => {
      throw new Error('blocked');
    },
    removeItem: () => {
      throw new Error('blocked');
    },
  };
  assert.equal(loadTheme(broken), 'system');
  assert.doesNotThrow(() => storeTheme(broken, 'light'));
  assert.doesNotThrow(() => storeTheme(broken, 'system'));
  assert.equal(loadTheme(null), 'system');
  assert.doesNotThrow(() => storeTheme(null, 'light'));
});

// The live plan is read in one place, livePlan. A second copy of the read could drift from it.
test('livePlan: the only code that reads ace2k_u1.plan from the status', () => {
  const root = new URL('..', import.meta.url).pathname;
  const files = (dir) =>
    readdirSync(join(root, dir), { withFileTypes: true }).flatMap((d) =>
      d.isDirectory() ? files(join(dir, d.name)) : d.name.endsWith('.js') ? [join(dir, d.name)] : [],
    );
  const reads = [...files('js'), 'main.js'].filter((f) =>
    /ace2k_u1\??\.plan\b/.test(readFileSync(join(root, f), 'utf8').replace(/^\s*(\/\/|\*).*$/gm, '')),
  );
  assert.deepEqual(reads, [join('js', 'state.js')]);
});

test('tabOf: one of the four tabs, else units', () => {
  assert.deepEqual(TABS, ['units', 'print', 'dryer', 'history']);
  for (const tab of TABS) assert.equal(tabOf(tab), tab);
  for (const v of [null, undefined, '', 'toString', 'other']) assert.equal(tabOf(v), 'units');
});

test('loadTab and storeTab: the tab survives a reload; no storage, or one that throws, starts on units', () => {
  const data = new Map();
  const storage = { getItem: (k) => data.get(k) ?? null, setItem: (k, v) => data.set(k, v) };
  assert.equal(loadTab(storage), 'units');
  storeTab(storage, 'dryer');
  assert.equal(data.get('ace2k.tab'), 'dryer');
  assert.equal(loadTab(storage), 'dryer');
  data.set('ace2k.tab', 'junk');
  assert.equal(loadTab(storage), 'units');
  const broken = {
    getItem: () => {
      throw new Error('blocked');
    },
    setItem: () => {
      throw new Error('blocked');
    },
  };
  assert.equal(loadTab(broken), 'units');
  assert.doesNotThrow(() => storeTab(broken, 'print'));
  assert.equal(loadTab(null), 'units');
  assert.doesNotThrow(() => storeTab(null, 'print'));
});

test("defaultSelection: the first lane in error of any unit, else the first unit's lane 1", () => {
  const s = twoHeadedUnits();
  assert.deepEqual(defaultSelection(s), { unit: 'a', lane: 0 });
  s['ace2k b'].lanes[2] = lane({ mode: 'error', error: 'stuck' });
  assert.deepEqual(defaultSelection(s), { unit: 'b', lane: 2 });
  const one = { ace2k: { lanes: [lane(), lane({ mode: 'error' }), lane(), lane()] } };
  assert.deepEqual(defaultSelection(one), { unit: null, lane: 1 });
  assert.deepEqual(defaultSelection({}), { unit: null, lane: 0 });
  // A known error with a mode not yet "error" is a lane in error by the page's one rule (laneErrs), as on its tile.
  const tangled = { ace2k: { lanes: [lane(), lane(), lane({ mode: 'idle', error: 'tangled' }), lane()] } };
  assert.deepEqual(defaultSelection(tangled), { unit: null, lane: 2 });
});

test('dryerOf: the unit dryer and its cycle progress', () => {
  const a = {
    status: { ace2k: { lanes: bays() } },
    samples: [
      { t: 100, dry: 'heating', rem: 60 },
      { t: 130, dry: 'heating', rem: 59 },
    ],
  };
  a.status.ace2k.dryer = { state: 'heating', target: 55, remaining: 59 };
  const dry = dryerOf(a, null, 160, { src: null, by: new Map() });
  assert.deepEqual(dry.d, dryerView(sliceFor(a.status, null)));
  assert.equal(dry.progress.start, 100);
});

test('titleView', () => {
  // One unit: its name, or none for the unnamed [ace2k]. Several units: no name.
  assert.deepEqual(titleView(['b']), { key: 'app.title_unit', vars: { name: 'b' } });
  assert.deepEqual(titleView([null]), { key: 'app.title', vars: {} });
  assert.deepEqual(titleView(['a', 'b']), { key: 'app.title', vars: {} });
  assert.deepEqual(titleView([]), { key: 'app.title', vars: {} });
});

const readApp = (print, plan, pending = []) => ({
  status: { ace2k_u1: { plan } },
  print: { open: true, files: [], choice: {}, choiceId: null, ...print },
  pending: new Set(pending),
});

test('readingOf: the reading entry while the live plan is not yet the chosen file', () => {
  const r = { file: 'big.gcode', at: 1000 };
  assert.deepEqual(readingOf(readApp({ file: 'big.gcode', reading: r }, { file: 'old.gcode' })), r);
  assert.equal(readingOf(readApp({ file: 'big.gcode', reading: r }, { file: 'big.gcode' })), null);
  assert.equal(readingOf(readApp({ file: 'other.gcode', reading: r }, null)), null);
  assert.equal(readingOf(readApp({ file: 'big.gcode' }, null)), null);
});

test('otherDevice: another file in the live plan, not while this page reads', () => {
  assert.equal(otherDevice(readApp({ file: 'mine.gcode' }, { file: 'theirs.gcode' })), 'theirs.gcode');
  assert.equal(otherDevice(readApp({ file: 'mine.gcode' }, { file: 'mine.gcode' })), null);
  assert.equal(otherDevice(readApp({ file: 'mine.gcode' }, null)), null);
  assert.equal(otherDevice(readApp({ file: null }, { file: 'theirs.gcode' })), null);
  const reading = { file: 'mine.gcode', at: 1 };
  assert.equal(otherDevice(readApp({ file: 'mine.gcode', reading }, { file: 'first.gcode' })), null);
  assert.equal(otherDevice(readApp({ file: 'mine.gcode' }, { file: 'theirs.gcode' }, ['print:file'])), null);
});
