import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  AVG_MAX_S,
  SEEDS,
  STEP_SEEDS,
  bgStop,
  changesOf,
  printStopsOf,
  runStops,
  swapStops,
  timesOf,
} from '../js/swap-time.js';

const FIX = JSON.parse(readFileSync(new URL('./fixtures/swap-time-seeds.json', import.meta.url), 'utf8'));

test('the seeds are the fixture, which the adapter pins too', () => {
  assert.deepEqual({ ...SEEDS }, { inline: FIX.inline, ready: FIX.ready, dock: FIX.dock, resume: FIX.resume });
  assert.deepEqual({ ...STEP_SEEDS }, FIX.steps);
  assert.equal(AVG_MAX_S, FIX.max_s);
});

test('timesOf reads the status and keeps a seed for a missing or bad value', () => {
  assert.deepEqual(timesOf({}), { ...SEEDS });
  const st = {
    ace2k_u1: {
      swap_times: {
        inline: { s: 88.5, n: 3 },
        ready: { s: null, n: 0 },
        dock: { s: 601, n: 1 },
        resume: { s: -1, n: 1 },
      },
    },
  };
  assert.deepEqual(timesOf(st), { inline: 88.5, ready: 15, dock: 65, resume: 15 });
});

const plan = (order, at, size) => ({ order, order_at: at, size });

test('changesOf gives each change its share of the slicer time', () => {
  const c = changesOf(plan([0, 1, 0], [0, 250, 750], 1000), null, 1000);
  assert.deepEqual(
    c.map((x) => x.tool),
    [0, 1, 0],
  );
  assert.deepEqual(
    c.map((x) => Math.round(/** @type {number} */ (x.s))),
    [250, 500, 250],
  );
  assert.ok(changesOf(plan([0, 1], [0, 10], 100), null, null).every((x) => x.s === null));
  // A frame that cuts the first change: no negative time.
  assert.ok(changesOf(plan([0, 1, 0], [0, 50, 80], 100), { start: 60, end: 100 }, 400).every((x) => (x.s ?? 0) >= 0));
});

test('bgStop: the arrival, plus the dock work left and the resume cost', () => {
  assert.deepEqual(bgStop(SEEDS, null), { kind: 'ready', s: 15 });
  assert.deepEqual(bgStop(SEEDS, 70), { kind: 'ready', s: 15 });
  assert.deepEqual(bgStop(SEEDS, 50), { kind: 'late', s: 45 });
  assert.deepEqual(bgStop(SEEDS, 0), { kind: 'late', s: 95 });
});

// Lanes a:1, a:2 on head 0 and b:1 on head 1.
const headOf = (k) => ({ 'a:1': 0, 'a:2': 0, 'b:1': 1 })[k] ?? null;
const choice = { 0: 'a:1', 1: 'b:1', 2: 'a:2' };
const ch = (tools, secs) => tools.map((tool, i) => ({ tool, s: secs === null ? null : secs[i] }));

test('swapStops prices each swap in file order', () => {
  // 0 (a:1) 1 (b:1, 30 s) 2 (a:2): head 0 parked 30 s -> late, 15 + 35 + 15.
  const r = swapStops(ch([0, 1, 2], [100, 30, 100]), choice, headOf, [0], SEEDS);
  assert.deepEqual(r.stops, [null, null, { kind: 'late', s: 65 }]);
  assert.equal(r.total, 65);
  // With 80 s parked: ready.
  assert.deepEqual(swapStops(ch([0, 1, 2], [100, 80, 100]), choice, headOf, [0], SEEDS).stops[2], {
    kind: 'ready',
    s: 15,
  });
  // Not a background head: inline.
  assert.deepEqual(swapStops(ch([0, 1, 2], [100, 80, 100]), choice, headOf, [], SEEDS).stops[2], {
    kind: 'inline',
    s: 94,
  });
  // Two changes of head 0 in a row: inline.
  assert.deepEqual(swapStops(ch([0, 2], [100, 100]), choice, headOf, [0], SEEDS).stops[1], { kind: 'inline', s: 94 });
  // No time: ready.
  assert.deepEqual(swapStops(ch([0, 1, 2], null), choice, headOf, [0], SEEDS).stops[2], { kind: 'ready', s: 15 });
});

test('a window includes the stops of the swaps between', () => {
  // head 1 gets two lanes so it swaps inline between head 0's two uses.
  const h = (k) => ({ 'a:1': 0, 'a:2': 0, 'b:1': 1, 'b:2': 1 })[k] ?? null;
  const c = { 0: 'a:1', 1: 'b:1', 2: 'a:2', 3: 'b:2' };
  // 0 | 1 (10 s) | 3 (10 s, an inline swap of head 1: 94 s) | 2: head 0 parked 10 + 94 + 10 = 114 s -> ready.
  const r = swapStops(ch([0, 1, 3, 2], [100, 10, 10, 100]), c, h, [0], SEEDS);
  assert.deepEqual(r.stops[2], { kind: 'inline', s: 94 });
  assert.deepEqual(r.stops[3], { kind: 'ready', s: 15 });
});

test("printStopsOf: the adapter's measured stops of the running print, or null from an older adapter", () => {
  assert.equal(printStopsOf({}), null);
  assert.equal(printStopsOf({ ace2k_u1: {} }), null);
  const ok = { ace2k_u1: { print_stops: { s: 120.5, n: 3, since: null } } };
  assert.deepEqual(printStopsOf(ok), { s: 120.5, n: 3, since: null });
  const running = { ace2k_u1: { print_stops: { s: 0, n: 0, since: 812.4 } } };
  assert.deepEqual(printStopsOf(running), { s: 0, n: 0, since: 812.4 });
  for (const s of [null, -1, Number.NaN, '12']) {
    assert.equal(printStopsOf({ ace2k_u1: { print_stops: { s, n: 1, since: null } } }), null, String(s));
  }
  // A since that is not a time reads as no stop running; a bad count as 0.
  const odd = { ace2k_u1: { print_stops: { s: 10, n: 'x', since: Number.NaN } } };
  assert.deepEqual(printStopsOf(/** @type {any} */ (odd)), { s: 10, n: 0, since: null });
});

test('runStops: the stops passed, measured or estimated, and the stops ahead', () => {
  const xs = [0, 0.2, 0.5, 0.8];
  const stops = /** @type {const} */ ([
    null,
    { kind: 'inline', s: 94 },
    { kind: 'ready', s: 15 },
    { kind: 'inline', s: 94 },
  ]);
  // p 0.5: the file position at the T line of the change at 0.5 (near: a few bytes as a share).
  const at = { xs, stops: [...stops], p: 0.5, done: 1000, near: 0.001 };
  // An older adapter: the passed stops are their estimates.
  assert.deepEqual(runStops({ ...at, measured: null }), { passedS: 109, ahead: [{ x: 0.8, s: 94 }] });
  // Measured: the two passed stops took 130 s, whatever they were estimated at.
  assert.deepEqual(runStops({ ...at, measured: { s: 130, n: 2, since: null } }), {
    passedS: 130,
    ahead: [{ x: 0.8, s: 94 }],
  });
  // The stop at 0.5 runs since 990 s printed: its 10 s so far are passed, the 5 s left of its 15 s are ahead.
  assert.deepEqual(runStops({ ...at, measured: { s: 94, n: 1, since: 990 } }), {
    passedS: 104,
    ahead: [
      { x: 0.5, s: 5 },
      { x: 0.8, s: 94 },
    ],
  });
  // An earlier stop was not counted (a pause came into it): the running one is still matched by its position.
  assert.deepEqual(runStops({ ...at, measured: { s: 0, n: 0, since: 990 } }), {
    passedS: 10,
    ahead: [
      { x: 0.5, s: 5 },
      { x: 0.8, s: 94 },
    ],
  });
  // One that runs over its estimate: nothing of it left ahead.
  assert.deepEqual(runStops({ ...at, measured: { s: 94, n: 1, since: 900 } }), {
    passedS: 194,
    ahead: [{ x: 0.8, s: 94 }],
  });
  // A stop that runs with the file position past the last change's T line (a swap typed at the console mid-part):
  // only its elapsed part counts, none of that change's estimate.
  assert.deepEqual(runStops({ ...at, p: 0.6, measured: { s: 109, n: 2, since: 990 } }), {
    passedS: 119,
    ahead: [{ x: 0.8, s: 94 }],
  });
  // At a change with no swap of its own (estimate null): no leftover either.
  assert.deepEqual(runStops({ ...at, p: 0, measured: { s: 0, n: 0, since: 990 } }), {
    passedS: 10,
    ahead: [
      { x: 0.2, s: 94 },
      { x: 0.5, s: 15 },
      { x: 0.8, s: 94 },
    ],
  });
});
