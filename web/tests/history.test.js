import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  parseJsonl, gcodeEvents, transitionEvents, snapshot, liveSample, appendSample, appendEvent,
  filterEvents, cycleProgress,
} from '../js/history.js';

const fixture = JSON.parse(readFileSync(new URL('./fixtures/event-lines.json', import.meta.url)));

test('parseJsonl', () => {
  const text = '{"t":1,"k":"s","ch":20}\nnot json\n\n{"t":2,"k":"e","lvl":"info","src":"unit","msg":"x"}\n{"t":3}\n';
  const { samples, events } = parseJsonl(text);
  assert.deepEqual(samples.map((s) => s.t), [1]);
  assert.deepEqual(events.map((e) => e.t), [2]);
});

test('gcodeEvents matches the shared fixture', () => {
  for (const c of fixture) {
    assert.deepEqual(gcodeEvents(c.raw, 100), c.events.map((e) => ({ t: 100, k: 'e', ...e })), c.raw);
  }
});

const transitionCases = JSON.parse(readFileSync(new URL('./fixtures/transitions.json', import.meta.url)));

test('transitionEvents matches the shared fixture', () => {
  for (const c of transitionCases) {
    let prev = c.prev_status ? snapshot(c.prev_status) : null;
    for (const b of c.between || []) prev = snapshot(b, prev);
    assert.deepEqual(transitionEvents(prev, c.status, 100), c.events.map((e) => ({ t: 100, k: 'e', ...e })), c.name);
  }
});

test('a NaN filament_mm is missing', () => {
  const lane = { mode: 'idle', last_event: { kind: 'loaded', mode: 'loading', seq: 1, filament_mm: NaN } };
  assert.deepEqual(transitionEvents(snapshot(st()), { lanes: [lane] }, 2).map((e) => e.msg), ['lane 1 loaded (loading)']);
});

const st = (dryer = 'idle', fault = null, modes = ['idle', 'idle', 'idle', 'idle'], err = null) => ({
  dryer: { state: dryer, fault }, lanes: modes.map((m) => ({ mode: m, error: m === 'error' ? err : null })),
});

test('transitionEvents mirrors the recorder', () => {
  const a = snapshot(st());
  assert.deepEqual(transitionEvents(null, st(), 1), []);
  assert.deepEqual(transitionEvents(a, st('heating'), 2).map((e) => [e.lvl, e.src, e.msg]),
    [['info', 'dryer', 'dryer idle → heating']]);
  assert.deepEqual(transitionEvents(snapshot(st('heating')), st('fault', 'fans'), 3).map((e) => [e.lvl, e.msg]),
    [['err', 'dryer fault: fans']]);
  const lane4 = st('idle', null, ['idle', 'idle', 'idle', 'error'], 'stuck');
  assert.deepEqual(transitionEvents(a, lane4, 4).map((e) => [e.lvl, e.src, e.msg]), [['err', 'lane4', 'lane 4 error: stuck']]);
  assert.deepEqual(transitionEvents(snapshot(lane4), st(), 5).map((e) => e.msg), ['lane 4 error cleared']);
});

test('liveSample and appendSample spacing', () => {
  const s = liveSample({ chamber: 27.123, humidity: 52, ptc_left: null, ptc_right: 28.7, dryer: { state: 'heating', target: 55, remaining: 30 } }, 100);
  assert.deepEqual(s, { t: 100, k: 's', ch: 27.12, rh: 52, hl: null, hr: 28.7, dry: 'heating', tgt: 55, rem: 30 });
  // the last cycle's target is not a sample's outside starting/heating
  for (const state of ['idle', 'cooldown', 'fault']) assert.equal(liveSample({ dryer: { state, target: 55 } }, 1).tgt, null);
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
    { t: 1, lvl: 'info', src: 'lane1', msg: 'a' }, { t: 2, lvl: 'err', src: 'lane4', msg: 'b' },
    { t: 3, lvl: 'warn', src: 'dryer', msg: 'c' }, { t: 4, lvl: 'info', src: 'unit', msg: 'd' },
  ];
  assert.deepEqual(filterEvents(ev, 'all', 10).map((e) => e.msg), ['d', 'c', 'b', 'a']);
  assert.deepEqual(filterEvents(ev, 'errors', 10).map((e) => e.msg), ['b']);
  assert.deepEqual(filterEvents(ev, 'lane4', 10).map((e) => e.msg), ['b']);
  assert.deepEqual(filterEvents(ev, 'dryer', 10).map((e) => e.msg), ['c']);
  assert.deepEqual(filterEvents(ev, 'all', 2).map((e) => e.msg), ['d', 'c']);
});

test('cycleProgress', () => {
  const samples = [
    { t: 0, dry: 'idle' }, { t: 600, dry: 'starting' }, { t: 630, dry: 'heating' }, { t: 3600, dry: 'heating' },
  ];
  assert.deepEqual(cycleProgress(samples, 3600, 191), { start: 600, total: 241 });
  assert.equal(cycleProgress([{ t: 0, dry: 'idle' }], 10, null), null);
  assert.deepEqual(cycleProgress([], 3600, 100), { start: null, total: null });
});

test('cycleProgress takes the total from the largest rem of the running segment', () => {
  const samples = [
    { t: 0, dry: 'heating', rem: 99 }, // an earlier cycle: not this segment
    { t: 500, dry: 'idle', rem: null },
    { t: 600, dry: 'starting', rem: 45 }, { t: 630, dry: 'heating', rem: 45 }, { t: 1500, dry: 'heating', rem: 30 },
  ];
  assert.deepEqual(cycleProgress(samples, 1530, 29), { start: 600, total: 45 });
  assert.deepEqual(cycleProgress(samples, 1530, 50), { start: 600, total: 50 });
  assert.equal(cycleProgress(samples, 1530, null), null);
  // older samples without rem fall back to elapsed + remaining
  assert.deepEqual(cycleProgress([{ t: 600, dry: 'heating' }], 1200, 35), { start: 600, total: 45 });
});

test('transitionEvents compares only lanes present in both snapshots', () => {
  const prev = snapshot({ dryer: { state: 'idle' }, lanes: [{}, {}] });
  assert.deepEqual(transitionEvents(prev, st('idle', null, ['idle', 'idle', 'error', 'idle'], 'x'), 1), []);
});

import { mergeDay } from '../js/history.js';

test('mergeDay: the same day twice adds nothing', () => {
  const day = { samples: [{ t: 10, k: 's' }, { t: 20, k: 's' }], events: [{ t: 15, k: 'e', lvl: 'err', src: 'lane1', msg: 'stuck' }] };
  const once = mergeDay({ samples: [{ t: 30, k: 's' }], events: [] }, day);
  assert.deepEqual(once.samples.map((s) => s.t), [10, 20, 30]);
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
  assert.deepEqual(transitionEvents(snapshot(lane2), gone, 5).map((e) => e.msg), ['lane 2 error cleared']);
});

test('mergeDay keeps the 2 s duplicate window and merges a large day fast', () => {
  const held = { samples: [], events: [{ t: 100, k: 'e', lvl: 'info', src: 'unit', msg: 'x' }] };
  const day = { samples: [], events: [
    { t: 98, k: 'e', lvl: 'info', src: 'unit', msg: 'x' }, { t: 102, k: 'e', lvl: 'info', src: 'unit', msg: 'x' },
    { t: 103, k: 'e', lvl: 'info', src: 'unit', msg: 'x' }, { t: 104, k: 'e', lvl: 'info', src: 'unit', msg: 'x' },
    { t: 97, k: 'e', lvl: 'info', src: 'unit', msg: 'y' },
  ] };
  const merged = mergeDay(held, day);
  assert.deepEqual(merged.events.map((e) => [e.t, e.msg]), [[97, 'y'], [100, 'x'], [103, 'x']]);
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
