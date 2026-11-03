import { test } from 'node:test';
import assert from 'node:assert/strict';
import { connectionChange, ingestStatus, resetHistory, shiftDay, ingestGcode, loadDemo } from '../js/live.js';

const unit = (dryer, lane = 'idle') => ({
  chamber: 25,
  humidity: 40,
  ptc_left: 26,
  ptc_right: 27,
  dryer: { state: dryer },
  lanes: [{ mode: lane }],
});
const page = (o = {}) => ({
  status: {},
  samples: [],
  events: [],
  snaps: {},
  gen: 0,
  days: ['a'],
  daysLoaded: 2,
  loaded: new Set(['a']),
  conn: null,
  print: { file: null, reading: null },
  ...o,
});

test('ingestStatus: one unit is sampled unnamed and its transitions become events', () => {
  const app = page();
  ingestStatus(app, { ace2k: unit('stopped') }, 1000);
  assert.deepEqual(
    app.samples.map((s) => [s.t, s.u]),
    [[1000, undefined]],
  );
  assert.deepEqual(app.events, []); // the first frame has nothing to compare with
  ingestStatus(app, { ace2k: unit('heating') }, 1100);
  assert.equal(app.status.ace2k.dryer.state, 'heating');
  assert.equal(app.samples.length, 2);
  assert.equal(app.events.length, 1);
  assert.equal(app.events[0].src, 'dryer');
});

test("ingestStatus: several units are sampled by name, only the first one's events are the page's own", () => {
  const names = { ace2k_units: { units: ['ace2k a', 'ace2k b'] } };
  const app = page();
  ingestStatus(app, { ...names, 'ace2k a': unit('stopped'), 'ace2k b': unit('stopped') }, 1000);
  assert.deepEqual(
    app.samples.map((s) => s.u),
    ['a', 'b'],
  );
  ingestStatus(app, { ...names, 'ace2k a': unit('heating'), 'ace2k b': unit('heating') }, 1100);
  assert.deepEqual(
    app.events.map((e) => e.src),
    ['dryer', 'unit'],
  );
  // a unit missing from a frame is skipped
  ingestStatus(app, { ...names, 'ace2k a': unit('heating') }, 1200);
  assert.deepEqual(
    app.samples.map((s) => s.u),
    ['a', 'b', 'a', 'b', 'a'],
  );
});

test('resetHistory: the held history is dropped, the generation moves on, live points resume', () => {
  const app = page({ status: { ace2k: unit('heating') }, events: [{ t: 1 }], snaps: { ace2k: {} }, gen: 4 });
  resetHistory(app, 2000);
  assert.deepEqual([app.gen, app.days, app.daysLoaded, [...app.loaded], app.events, app.snaps], [5, [], 0, [], [], {}]);
  assert.deepEqual(
    app.samples.map((s) => s.t),
    [2000],
  );
  const two = page({ status: { ace2k_units: { units: ['ace2k a', 'ace2k b'] }, 'ace2k a': unit('stopped') } });
  resetHistory(two, 2000);
  assert.deepEqual(
    two.samples.map((s) => s.u),
    ['a'],
  ); // b has no status yet
});

test('shiftDay: the newest record becomes now', () => {
  const day = {
    samples: [
      { t: 100, ch: 1 },
      { t: 160, ch: 2 },
    ],
    events: [{ t: 170, msg: 'x' }],
  };
  const out = shiftDay(day, 1000.7);
  assert.deepEqual(
    out.samples.map((s) => s.t),
    [930, 990],
  );
  assert.deepEqual(
    out.events.map((e) => e.t),
    [1000],
  );
  assert.equal(day.samples[0].t, 100); // the input is untouched
});

test("ingestGcode: a console line becomes events, named for the connection's unit", () => {
  const app = page({ status: { ace2k_units: { units: ['ace2k a', 'ace2k b'] } }, conn: { unit: 'ace2k b' } });
  ingestGcode(app, '!! ace2k b: lane 2 stuck', 500);
  assert.equal(app.events.length, 1);
  assert.equal(app.events[0].lvl, 'err');
  ingestGcode(app, 'not an ace2k line', 501);
  assert.equal(app.events.length, 1);
});

test('connectionChange: the link state is kept; the history starts over only when Klipper is ready', () => {
  const app = page({ gen: 2, events: [{ t: 1 }] });
  assert.equal(connectionChange(app, { online: true, klippy: 'startup' }, 10), false);
  assert.deepEqual([app.online, app.klippy, app.gen, app.events.length], [true, 'startup', 2, 1]);
  assert.equal(connectionChange(app, { online: false, klippy: 'ready' }, 10), false);
  assert.deepEqual([app.online, app.klippy, app.gen], [false, null, 2]);
  assert.equal(connectionChange(app, { online: true, klippy: 'ready' }, 10), true);
  assert.deepEqual([app.online, app.klippy, app.gen, app.events], [true, 'ready', 3, []]);
  connectionChange(app, { online: true, klippy: 'page_error', message: 'boom' }, 10);
  assert.deepEqual([app.klippy, app.linkMessage], ['page_error', 'boom']);
  connectionChange(app, { online: true, klippy: 'ready' }, 10);
  assert.equal(app.linkMessage, undefined);
});

// A fake fetch. Each path answers with its body, or with a 404 when it has none.
const fakeFetch = (files) => async (path) =>
  path in files
    ? { ok: true, json: async () => JSON.parse(files[path]), text: async () => files[path] }
    : { ok: false, status: 404, json: async () => JSON.parse('<html>'), text: async () => '<html>' };

test('loadDemo: the demo files become the page, its history shifted to now', async () => {
  const app = page({ online: false, klippy: null });
  const ok = await loadDemo(
    fakeFetch({
      'dev/demo-status.json': '{"ace2k":{}}',
      'dev/demo-history.jsonl': '{"t":100,"k":"s","ch":25}\n',
    }),
    app,
    1000,
  );
  assert.equal(ok, true);
  assert.deepEqual([app.online, app.klippy, app.status], [true, 'ready', { ace2k: {} }]);
  assert.deepEqual(
    app.samples.map((s) => s.t),
    [1000],
  );
});

test('loadDemo: without the demo files (a release has no dev/) the page says so', async () => {
  const app = page({ online: false, klippy: null });
  assert.equal(await loadDemo(fakeFetch({ 'dev/demo-history.jsonl': '' }), app, 1000), false);
  assert.deepEqual([app.online, app.klippy], [false, 'demo_missing']);
  const failed = page({ online: false, klippy: null });
  const throwing = async () => {
    throw new Error('network');
  };
  assert.equal(await loadDemo(throwing, failed, 1000), false);
  assert.equal(failed.klippy, 'demo_missing');
});

test('a named demo loads its own status file', async () => {
  /** @type {string[]} */
  const asked = [];
  /** @param {string} p */
  const fetchFn = async (p) => {
    asked.push(p);
    return p.endsWith('.jsonl') ? new Response('') : Response.json({});
  };
  for (const [name, file] of [
    ['four', 'dev/demo-four.json'],
    [null, 'dev/demo-status.json'],
    ['', 'dev/demo-status.json'],
    ['../x', 'dev/demo-status.json'],
  ]) {
    asked.length = 0;
    await loadDemo(/** @type {any} */ (fetchFn), /** @type {any} */ ({}), 0, name);
    assert.ok(asked.includes(file), `${name} -> ${file}`);
  }
});

test('loadDemo: missing demo data leaves the samples, events and status as they were', async () => {
  const before = { samples: [{ t: 1, k: 's', ch: 20 }], events: [{ t: 1, k: 'e' }], status: { ace2k: { lanes: [] } } };
  for (const files of [{}, { 'dev/demo-status.json': '{"ace2k":{}}' }, { 'dev/demo-history.jsonl': '' }]) {
    const app = page({ online: false, klippy: null, ...structuredClone(before) });
    await loadDemo(fakeFetch(files), app, 1000);
    assert.deepEqual([app.samples, app.events, app.status], [before.samples, before.events, before.status]);
  }
  const badJson = page({ online: false, klippy: null, ...structuredClone(before) });
  await loadDemo(fakeFetch({ 'dev/demo-status.json': '{', 'dev/demo-history.jsonl': '' }), badJson, 1000);
  assert.deepEqual([badJson.samples, badJson.status, badJson.klippy], [before.samples, before.status, 'demo_missing']);
});

const LOG_REPLY = [
  '// ace2k b: dryer: 3 cycles started, 2 completed, 0 faults; 0.4 h of heating, 36 Wh at 360 W',
  '// #1 cycle 3 at 0.4 h: interrupted — left 35.0 °C, right 35.5 °C, chamber 28.0 °C',
].join('\n');

test('ingestGcode: the reply of ACEK_DRYER_LOG fills the dryer log the page waits for, for its unit only', () => {
  const app = page({
    status: { ace2k_units: { units: ['ace2k a', 'ace2k b'] } },
    dryerLog: { unit: 'a', lines: null, token: 1 },
  });
  ingestGcode(app, LOG_REPLY, 500);
  assert.deepEqual(app.dryerLog, { unit: 'a', lines: null, token: 1 });
  app.dryerLog = { unit: 'b', lines: null, token: 2 };
  ingestGcode(app, '// something else', 500);
  assert.deepEqual(app.dryerLog, { unit: 'b', lines: null, token: 2 });
  ingestGcode(app, LOG_REPLY, 500);
  assert.deepEqual(app.dryerLog, {
    unit: 'b',
    token: 2,
    lines: [
      '3 cycles started, 2 completed, 0 faults; 0.4 h of heating, 36 Wh at 360 W',
      '#1 cycle 3 at 0.4 h: interrupted — left 35.0 °C, right 35.5 °C, chamber 28.0 °C',
    ],
  });
  // A reply already shown is not replaced by a later one, and with no read waiting a reply is dropped.
  ingestGcode(app, '// ace2k b: dryer: 9 cycles started', 501);
  assert.ok(app.dryerLog.lines[0].startsWith('3 cycles'));
  app.dryerLog = null;
  ingestGcode(app, LOG_REPLY, 502);
  assert.equal(app.dryerLog, null);
});

test('ingestGcode: with one unit, named or not, its "ace2k dryer:" reply is the log', () => {
  const app = page({ status: { ace2k_units: { units: ['ace2k a'] } }, dryerLog: { unit: 'a', lines: null, token: 3 } });
  ingestGcode(app, '// ace2k dryer: 0 cycles started, 0 completed, 0 faults; 0.0 h of heating, 0 Wh at 360 W', 500);
  assert.deepEqual(app.dryerLog.lines, ['0 cycles started, 0 completed, 0 faults; 0.0 h of heating, 0 Wh at 360 W']);
});

test('the dryer log goes on a change of the link, and with its unit when the unit leaves the list', () => {
  const app = page({ dryerLog: { unit: null, lines: null, token: 1 } });
  connectionChange(app, { online: true, klippy: 'startup' }, 10);
  assert.equal(app.dryerLog, null);
  const two = page({ dryerLog: { unit: 'b', lines: ['x'], token: 1 } });
  ingestStatus(
    two,
    { ace2k_units: { units: ['ace2k a', 'ace2k b'] }, 'ace2k a': unit('idle'), 'ace2k b': unit('idle') },
    10,
  );
  assert.deepEqual(two.dryerLog, { unit: 'b', lines: ['x'], token: 1 });
  ingestStatus(two, { ace2k_units: { units: ['ace2k a'] }, 'ace2k a': unit('idle') }, 11);
  assert.equal(two.dryerLog, null);
});
