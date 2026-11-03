import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  parseJsonl,
  dryerLogLines,
  gcodeEvents,
  transitionEvents,
  snapshot,
  liveSample,
  appendSample,
  appendEvent,
  filterEvents,
  cycleProgress,
  listDays,
  loadDays,
} from '../js/history.js';
import { logLineText } from '../js/log-text.js';
import { lane, expandStatus, loadEnglish } from './helpers.js';

loadEnglish();

const fixture = JSON.parse(readFileSync(new URL('./fixtures/event-lines.json', import.meta.url)));

test('parseJsonl', () => {
  const text = '{"t":1,"k":"s","ch":20}\nnot json\n\n{"t":2,"k":"e","lvl":"info","src":"unit","msg":"x"}\n{"t":3}\n';
  const { samples, events } = parseJsonl(text);
  assert.deepEqual(
    samples.map((s) => s.t),
    [1],
  );
  assert.deepEqual(
    events.map((e) => e.t),
    [2],
  );
});

test('gcodeEvents matches the shared fixture', () => {
  for (const c of fixture) {
    assert.deepEqual(
      gcodeEvents(c.raw, 100, c.unit ?? null, c.units ?? []),
      c.events.map((e) => ({ t: 100, k: 'e', ...e })),
      c.raw,
    );
  }
});

const transitionCases = JSON.parse(readFileSync(new URL('./fixtures/transitions.json', import.meta.url)));

test('transitionEvents matches the shared fixture', () => {
  for (const c of transitionCases) {
    let prev = c.prev_status ? snapshot(expandStatus(c.prev_status)) : null;
    for (const b of c.between || []) prev = snapshot(expandStatus(b), prev);
    assert.deepEqual(
      transitionEvents(prev, expandStatus(c.status), 100),
      c.events.map((e) => ({ t: 100, k: 'e', ...e })),
      c.name,
    );
  }
});

test('a NaN filament_mm is missing', () => {
  const lane = { mode: 'idle', last_event: { kind: 'loaded', mode: 'loading', seq: 1, filament_mm: NaN } };
  assert.deepEqual(
    transitionEvents(snapshot(st()), { lanes: [lane] }, 2).map((e) => e.msg),
    ['lane 1 loaded (loading)'],
  );
});

test('an event of several moves shows the whole travel', () => {
  // A link loss in an assist ends a mode of two bursts, and the event carries both.
  const ev = { kind: 'stopped_link', mode: 'assisting', seq: 4, filament_mm: 33.323, motor_mm: 8.744 };
  const lane = { mode: 'idle', last_event: ev };
  assert.deepEqual(
    transitionEvents(snapshot(st()), { lanes: [lane] }, 2).map((e) => e.msg),
    ['lane 1 stopped_link (assisting, 33.3 mm)'],
  );
});

const st = (dryer = 'idle', fault = null, modes = ['idle', 'idle', 'idle', 'idle'], err = null) => ({
  dryer: { state: dryer, fault },
  lanes: modes.map((m) => lane({ mode: m, error: m === 'error' ? err : null })),
});

test('transitionEvents mirrors the recorder', () => {
  const a = snapshot(st());
  assert.deepEqual(transitionEvents(null, st(), 1), []);
  assert.deepEqual(
    transitionEvents(a, st('heating'), 2).map((e) => [e.lvl, e.src, e.msg]),
    [['info', 'dryer', 'dryer idle → heating']],
  );
  assert.deepEqual(
    transitionEvents(snapshot(st('heating')), st('fault', 'fans'), 3).map((e) => [e.lvl, e.msg]),
    [['err', 'dryer fault: fans']],
  );
  const lane4 = st('idle', null, ['idle', 'idle', 'idle', 'error'], 'stuck');
  assert.deepEqual(
    transitionEvents(a, lane4, 4).map((e) => [e.lvl, e.src, e.msg]),
    [['err', 'lane4', 'lane 4 error: stuck']],
  );
  assert.deepEqual(
    transitionEvents(snapshot(lane4), st(), 5).map((e) => e.msg),
    ['lane 4 error cleared'],
  );
});

test('liveSample and appendSample spacing', () => {
  const s = liveSample(
    {
      chamber: 27.123,
      humidity: 52,
      ptc_left: null,
      ptc_right: 28.7,
      dryer: { state: 'heating', target: 55, remaining: 30 },
    },
    100,
  );
  assert.deepEqual(s, { t: 100, k: 's', ch: 27.12, rh: 52, hl: null, hr: 28.7, dry: 'heating', tgt: 55, rem: 30 });
  // Outside starting and heating, a sample does not carry the last cycle's target.
  for (const state of ['idle', 'cooldown', 'fault'])
    assert.equal(liveSample({ dryer: { state, target: 55 } }, 1).tgt, null);
  let list = appendSample([], s);
  list = appendSample(list, { ...s, t: 120 });
  assert.equal(list.length, 1);
  list = appendSample(list, { ...s, t: 130 });
  assert.equal(list.length, 2);
});

test('appendEvent skips a duplicate within 2 s', () => {
  const e = { t: 10, k: 'e', lvl: 'info', src: 'unit', msg: 'x' };
  let list = appendEvent([], e);
  list = appendEvent(list, { ...e, t: 11 });
  assert.equal(list.length, 1);
  list = appendEvent(list, { ...e, t: 12 });
  assert.equal(list.length, 1); // exactly 2 s is a duplicate
  list = appendEvent(list, { ...e, t: 13 });
  assert.equal(list.length, 2);
});

test('filterEvents', () => {
  const ev = [
    { t: 1, lvl: 'info', src: 'lane1', msg: 'a' },
    { t: 2, lvl: 'err', src: 'lane4', msg: 'b' },
    { t: 3, lvl: 'warn', src: 'dryer', msg: 'c' },
    { t: 4, lvl: 'info', src: 'unit', msg: 'd' },
  ];
  assert.deepEqual(
    filterEvents(ev, 'all', 10).map((e) => e.msg),
    ['d', 'c', 'b', 'a'],
  );
  assert.deepEqual(
    filterEvents(ev, 'errors', 10).map((e) => e.msg),
    ['b'],
  );
  assert.deepEqual(
    filterEvents(ev, 'lane4', 10).map((e) => e.msg),
    ['b'],
  );
  assert.deepEqual(
    filterEvents(ev, 'dryer', 10).map((e) => e.msg),
    ['c'],
  );
  assert.deepEqual(
    filterEvents(ev, 'all', 2).map((e) => e.msg),
    ['d', 'c'],
  );
});

test('cycleProgress', () => {
  const samples = [
    { t: 0, dry: 'idle' },
    { t: 600, dry: 'starting' },
    { t: 630, dry: 'heating' },
    { t: 3600, dry: 'heating' },
  ];
  assert.deepEqual(cycleProgress(samples, 3600, 191), { start: 600, total: 241 });
  assert.equal(cycleProgress([{ t: 0, dry: 'idle' }], 10, null), null);
  assert.deepEqual(cycleProgress([], 3600, 100), { start: null, total: null });
});

test('cycleProgress takes the total from the largest rem of the running segment', () => {
  const samples = [
    { t: 0, dry: 'heating', rem: 99 }, // an earlier cycle, not part of this one
    { t: 500, dry: 'idle', rem: null },
    { t: 600, dry: 'starting', rem: 44 },
    { t: 630, dry: 'heating', rem: 44 },
    { t: 1500, dry: 'heating', rem: 29 },
  ];
  assert.deepEqual(cycleProgress(samples, 1530, 28), { start: 600, total: 45 });
  assert.deepEqual(cycleProgress(samples, 1530, 49), { start: 600, total: 50 });
  assert.equal(cycleProgress(samples, 1530, null), null);
  // older samples without rem fall back to elapsed + remaining
  assert.deepEqual(cycleProgress([{ t: 600, dry: 'heating' }], 1200, 35), { start: 600, total: 45 });
});

test('cycleProgress: a 10 min cycle is 10 min, though its first sample says 9', () => {
  // The unit cuts the remaining time down to whole minutes: 9.9 min left reads 9.
  const samples = [
    { t: 0, dry: 'idle', rem: null },
    { t: 20, dry: 'starting', rem: 9 },
    { t: 50, dry: 'heating', rem: 9 },
  ];
  assert.deepEqual(cycleProgress(samples, 60, 9), { start: 20, total: 10 });
});

test('cycleProgress: the empty sample of a Klipper restart does not end the cycle', () => {
  // At Klipper's start the recorder writes one sample before the unit reports: every field null.
  const samples = [
    { t: 0, dry: 'idle', rem: null },
    { t: 20, dry: 'starting', rem: 9 },
    { t: 50, dry: 'heating', rem: 9 },
    { t: 80, dry: 'heating', rem: 8 },
    { t: 125, ch: null, dry: null, tgt: null, rem: null },
    { t: 155, dry: 'heating', rem: 7 },
  ];
  assert.deepEqual(cycleProgress(samples, 160, 7), { start: 20, total: 10 });
});

test('cycleProgress: a cycle that began during the gap starts after it', () => {
  const samples = [
    { t: 0, dry: 'heating', rem: 3 }, // the earlier cycle, which ended while Klipper was away
    { t: 900, dry: null, rem: null },
    { t: 930, dry: 'heating', rem: 59 },
  ];
  assert.deepEqual(cycleProgress(samples, 940, 59), { start: 930, total: 60 });
  // with no remaining time to compare, the gap ends the search
  const old = [
    { t: 0, dry: 'heating' },
    { t: 900, dry: null },
    { t: 930, dry: 'heating' },
  ];
  assert.deepEqual(cycleProgress(old, 960, 30), { start: 930, total: 31 });
});

test('transitionEvents compares only lanes present in both snapshots', () => {
  const prev = snapshot({ dryer: { state: 'idle' }, lanes: [{}, {}] });
  assert.deepEqual(transitionEvents(prev, st('idle', null, ['idle', 'idle', 'error', 'idle'], 'x'), 1), []);
});

import { mergeDay } from '../js/history.js';

test('mergeDay: the same day twice adds nothing', () => {
  const day = {
    samples: [
      { t: 10, k: 's' },
      { t: 20, k: 's' },
    ],
    events: [{ t: 15, k: 'e', lvl: 'err', src: 'lane1', msg: 'stuck' }],
  };
  const once = mergeDay({ samples: [{ t: 30, k: 's' }], events: [] }, day);
  assert.deepEqual(
    once.samples.map((s) => s.t),
    [10, 20, 30],
  );
  const twice = mergeDay(once, day);
  assert.equal(twice.samples.length, 3);
  assert.equal(twice.events.length, 1);
});

test('transitionEvents: a state appearing from nothing is not a transition (mirrors the recorder)', () => {
  const blank = snapshot({ lanes: [] });
  assert.equal(blank.dryer, null);
  assert.deepEqual(transitionEvents(blank, st(), 1), []);
  assert.deepEqual(transitionEvents(snapshot(st()), { lanes: st().lanes }, 2), []);
  const lane2 = st('idle', null, ['idle', 'error', 'idle', 'idle'], 'stuck');
  assert.deepEqual(transitionEvents(blank, lane2, 3), []);
  assert.deepEqual(transitionEvents(snapshot(lane2), lane2, 4), []);
  const gone = { ...lane2, lanes: [lane2.lanes[0], null, lane2.lanes[2], lane2.lanes[3]] };
  assert.deepEqual(
    transitionEvents(snapshot(lane2), gone, 5).map((e) => e.msg),
    ['lane 2 error cleared'],
  );
});

test('mergeDay keeps the 2 s duplicate window and merges a large day fast', () => {
  const held = { samples: [], events: [{ t: 100, k: 'e', lvl: 'info', src: 'unit', msg: 'x' }] };
  const day = {
    samples: [],
    events: [
      { t: 98, k: 'e', lvl: 'info', src: 'unit', msg: 'x' },
      { t: 102, k: 'e', lvl: 'info', src: 'unit', msg: 'x' },
      { t: 103, k: 'e', lvl: 'info', src: 'unit', msg: 'x' },
      { t: 104, k: 'e', lvl: 'info', src: 'unit', msg: 'x' },
      { t: 97, k: 'e', lvl: 'info', src: 'unit', msg: 'y' },
    ],
  };
  const merged = mergeDay(held, day);
  assert.deepEqual(
    merged.events.map((e) => [e.t, e.msg]),
    [
      [97, 'y'],
      [100, 'x'],
      [103, 'x'],
    ],
  );
  const big = { samples: [], events: [] };
  for (let i = 0; i < 5000; i++) {
    big.samples.push({ t: i * 30, k: 's' });
    big.events.push({ t: i * 10, k: 'e', lvl: 'info', src: 'unit', msg: `m${i % 50}` });
  }
  const t0 = performance.now();
  const once = mergeDay({ samples: [], events: [] }, big);
  const twice = mergeDay(once, big);
  assert.ok(performance.now() - t0 < 200, 'linear merge');
  assert.equal(once.events.length, 5000);
  assert.equal(twice.events.length, 5000);
  assert.equal(twice.samples.length, 5000);
});

// Day loading. fetch is a table of answers by URL, and every call is recorded in order.
const DAY1 = '{"t":100,"k":"s","ch":20}\n{"t":101,"k":"e","lvl":"info","src":"unit","msg":"a"}\n';
const DAY2 = '{"t":50,"k":"s","ch":19}\n';
function loading(answers, keep = 7) {
  const calls = [];
  const state = { gen: 1, days: [], daysLoaded: 0, loaded: new Set(), samples: [], events: [] };
  const deps = {
    fetch: async (url) => {
      calls.push(url);
      const a = answers[url];
      if (a instanceof Error) throw a;
      return a ?? { ok: false, status: 404 };
    },
    render: () => calls.push('render'),
    keepDays: () => keep,
  };
  return { calls, state, deps };
}
const ok = (text) => ({ ok: true, status: 200, text: async () => text, json: async () => JSON.parse(text) });
const LIST = '/server/files/directory?path=logs/ace2k';
const dayUrl = (n) => `/server/files/logs/ace2k/${n}`;
const listing = (...names) => ok(JSON.stringify({ result: { files: names.map((n) => ({ filename: n })) } }));

test('listDays: lists the directory, then loads the two newest days in order', async () => {
  const { calls, state, deps } = loading({
    [LIST]: listing('2026-10-19.jsonl', '2026-10-20.jsonl', '2026-10-21.jsonl'),
    [dayUrl('2026-10-21.jsonl')]: ok(DAY1),
    [dayUrl('2026-10-20.jsonl')]: ok(DAY2),
  });
  await listDays(deps, state);
  assert.deepEqual(state.days, ['2026-10-21.jsonl', '2026-10-20.jsonl', '2026-10-19.jsonl']);
  assert.deepEqual(
    calls.filter((c) => c !== 'render'),
    [LIST, dayUrl('2026-10-21.jsonl'), dayUrl('2026-10-20.jsonl')],
  );
  assert.deepEqual(
    state.samples.map((x) => x.t),
    [50, 100],
  );
  assert.equal(state.daysLoaded, 2);
  assert.deepEqual([...state.loaded], ['2026-10-21.jsonl', '2026-10-20.jsonl']);
});

test('listDays: a 404 directory, a refused or failing listing and a stale reconnect are no history', async () => {
  const a = loading({});
  await listDays(a.deps, a.state);
  assert.deepEqual([a.state.days, a.calls], [[], [LIST, 'render']]);
  const b = loading({ [LIST]: new Error('down') });
  await listDays(b.deps, b.state);
  assert.deepEqual(b.state.days, []);
  const c = loading({ [LIST]: listing('2026-10-21.jsonl') });
  const racing = {
    ...c.deps,
    fetch: async (u) => {
      const r = await c.deps.fetch(u);
      c.state.gen += 1;
      return r;
    },
  };
  await listDays(racing, c.state);
  assert.deepEqual([c.state.days, c.calls], [[], [LIST]]); // The reconnect's listing runs later.
});

test('loadDays: a 404 day is loaded and empty, a failed one stays retryable', async () => {
  const { calls, state, deps } = loading({
    [dayUrl('b')]: new Error('net'),
    [dayUrl('c')]: { ok: false, status: 500 },
    [dayUrl('a')]: ok(DAY1),
  });
  state.days = ['a', 'b', 'c', 'd'];
  await loadDays(deps, state, 4);
  // d answered 404 and counts as loaded. b and c can be tried again.
  assert.deepEqual([...state.loaded].sort(), ['a', 'd']);
  assert.deepEqual(
    state.samples.map((x) => x.t),
    [100],
  );
  assert.equal(calls.at(-1), 'render');
  await loadDays(deps, state, 4); // only the failed ones again
  assert.deepEqual(calls.filter((c) => c.startsWith('/server/files/logs')).slice(4), [dayUrl('b'), dayUrl('c')]);
});

test('loadDays: keep_days cuts the count, and a loaded day is not fetched again', async () => {
  const { calls, state, deps } = loading({ [dayUrl('a')]: ok(DAY1), [dayUrl('b')]: ok(DAY2) }, 2);
  state.days = ['a', 'b', 'c'];
  await loadDays(deps, state, 3);
  assert.deepEqual(
    calls.filter((c) => c !== 'render'),
    [dayUrl('a'), dayUrl('b')],
  );
  assert.equal(state.daysLoaded, 3); // the count asked for, not the cut
  await loadDays(deps, state, 3);
  assert.equal(calls.filter((c) => c !== 'render').length, 2);
});

test('loadDays: a reconnect in flight drops the stale day, and a failure after it leaves the new loaded set alone', async () => {
  const { calls, state, deps } = loading({ [dayUrl('a')]: ok(DAY1), [dayUrl('b')]: ok(DAY2) });
  state.days = ['a', 'b'];
  const slow = {
    ...deps,
    fetch: async (u) => {
      const r = await deps.fetch(u);
      state.gen += 1;
      return r;
    },
  };
  await loadDays(slow, state, 2);
  assert.deepEqual([state.samples, calls.includes('render')], [[], false]);
  // A failure after the reconnect leaves the new generation's bookkeeping alone.
  state.loaded = new Set(['b']);
  const failing = {
    ...deps,
    fetch: async () => {
      state.gen += 1;
      throw new Error('x');
    },
  };
  state.days = ['b', 'a'];
  await loadDays(failing, state, 2);
  assert.deepEqual([...state.loaded], ['b', 'a']);
});

test("the events view says each of the recorder's texts in words; the data keeps the recorder's texts", () => {
  const words = [
    ['lane 1 spool inserted', 'lane 1: spool inserted'],
    ['lane b:2 spool removed', 'lane b:2: spool removed'],
    ['lane 3 assist_stall (rolling_back, 12.0 mm)', 'lane 3: the filament stopped moving (rolling back, 12.0 mm)'],
    ['lane 1 loaded (loading)', 'lane 1: loaded (loading)'],
    ['lane 4 brand_new_kind (odd_mode, 3.0 mm)', 'lane 4: unknown event (unknown mode, 3.0 mm)'],
    ['lane 1 ? (?)', 'lane 1: unknown event (unknown mode)'],
    ['lane 2 error: motor_stalled', 'lane 2 error: the motor stalled'],
    ['lane 2 error: made_up', 'lane 2 error: unknown event'],
    ['lane 2 error cleared', 'lane 2: error cleared'],
    ['lane 1 tag read: Bambu PLA', 'lane 1: tag read, Bambu PLA'],
    ['lane 1 tag read', 'lane 1: tag read'],
    ['lane 2 no tag', 'lane 2: no tag'],
    ['dryer heating → cooldown', 'dryer: Heating → Cooling down'],
    ['dryer idle → xyz', 'dryer: Idle → Unknown'],
    ['dryer fault: fans', 'The heater stopped: the fans did not start.'],
    ['dryer fault: ?', 'The heater stopped. This page does not know the reason (?).'],
    ['ace2k b: dryer heating → cooldown', 'Unit b: dryer: Heating → Cooling down'],
    ['ace2k b: dryer fault: cutout', 'Unit b: The thermal cutout tripped. Only a power cycle clears it.'],
    ['ace2k_u1: head 1 ready', 'head 1 ready'],
    ['lane 2 stuck', 'lane 2 stuck'],
  ];
  for (const [msg, text] of words) assert.equal(logLineText(msg), text, msg);
  assert.equal(logLineText(null), '');
});

test('parseJsonl leaves out an ACEK_STATUS header recorded before it stopped being an event', () => {
  const text = [
    '{"t": 1, "k": "e", "lvl": "info", "src": "unit", "msg": "ace2k a: 0.11.0-2034-g42a8951f, link proven: yes"}',
    '{"t": 1, "k": "e", "lvl": "info", "src": "unit", "msg": "ace2k A: 0.11.0-2034-g42a8951f, link proven: no"}',
    '{"t": 2, "k": "e", "lvl": "info", "src": "lane1", "msg": "ace2k a: lane 1 follow on"}',
  ].join('\n');
  assert.deepEqual(
    parseJsonl(text).events.map((e) => e.msg),
    ['ace2k a: lane 1 follow on'],
  );
});

test('a console line takes its level from its words; some event kinds are no words of a line', () => {
  const lvl = (msg) => gcodeEvents(`// ace2k: ${msg}`, 0)[0]?.lvl;
  for (const w of ['stuck', 'motor_stalled', 'assist_overrun', 'unload_incomplete', 'stopped_sensors', 'refused'])
    assert.equal(lvl(`lane 1 ${w}`), 'err', w);
  assert.equal(lvl('lane 1 sensors stale'), 'err');
  for (const w of ['runout', 'stopped_link', 'stopped_shutdown', 'lowered', 'notice'])
    assert.equal(lvl(`lane 1 ${w}`), 'warn', w);
  assert.equal(lvl('lane 1 no spool set'), 'warn');
  // Kinds of a lane's event that no console line is marked by.
  for (const w of ['assist_stall', 'behind', 'snag', 'blocked', 'tail']) assert.equal(lvl(`lane 1 ${w}`), 'info', w);
});

test("dryerLogLines: the host's ACEK_DRYER_LOG reply in the shared fixture, for the page's unit only", () => {
  const cases = fixture.filter((c) => 'dryer_log' in c);
  assert.ok(cases.length >= 8);
  for (const c of cases) assert.deepEqual(dryerLogLines(c.raw, c.unit ?? null, c.units ?? []), c.dryer_log, c.raw);
});
