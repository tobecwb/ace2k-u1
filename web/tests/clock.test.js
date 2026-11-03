import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { clockOf, END_SHARE, etaTo, FILE_FLOOR, frameOf, leftOf, shareOf, swapShareOf, totalOf } from '../js/clock.js';
import { fmtHM } from '../js/format.js';

test("the frame: the card's G-code bytes, else the file's size, else the plan's size, else none", () => {
  assert.deepEqual(frameOf({ gcodeStart: 300, gcodeEnd: 9000 }, 10000, 8000), { start: 300, end: 9000 });
  assert.deepEqual(frameOf({ gcodeStart: null, gcodeEnd: null }, 10000, 8000), { start: 0, end: 10000 });
  assert.deepEqual(frameOf({ gcodeStart: 50, gcodeEnd: 40 }, 0, 8000), { start: 0, end: 8000 });
  assert.deepEqual(frameOf(null, undefined, 8000), { start: 0, end: 8000 });
  assert.equal(frameOf({}, undefined, undefined), null);
});

test("the share: over the frame once the card came; Klipper's progress before; the last one without either", () => {
  const frame = { start: 300, end: 10300 };
  const at = { position: 5300, progress: 0.515, frame, cardLoaded: true, last: null };
  // A 3 % header: half the G-code printed is 0.5, not the file's 0.515.
  assert.equal(shareOf(at), 0.5);
  assert.equal(shareOf({ ...at, position: 99999 }), 1);
  // The header bytes (before the G-code starts) count 0, as in Fluidd, not Klipper's ~2 %.
  assert.equal(shareOf({ ...at, position: 120, progress: 0.02 }), 0);
  assert.equal(shareOf({ ...at, position: 300 }), 0);
  // Metadata without the G-code bytes: the whole file, which is Klipper's progress.
  const whole = frameOf({ gcodeStart: null, gcodeEnd: null }, 8000, undefined);
  assert.equal(shareOf({ ...at, frame: whole, position: 4000 }), 0.5);
  // Before the card comes: Klipper's whole-file progress, so a share shows at once.
  assert.equal(shareOf({ ...at, cardLoaded: false }), 0.515);
  // A card but no frame, or no position: Klipper's progress, not a frozen last share.
  assert.equal(shareOf({ ...at, frame: null, last: 0.4 }), 0.515);
  assert.equal(shareOf({ ...at, position: undefined, last: 0.4 }), 0.515);
  // Nothing reported: the last share, else unknown.
  assert.equal(shareOf({ ...at, position: undefined, progress: undefined, last: 0.4 }), 0.4);
  assert.equal(shareOf({ position: undefined, progress: undefined, frame: null, cardLoaded: false, last: null }), null);
});

test('the time left: the file rate takes over from the slicer as the print advances', () => {
  // Half printed in 50 min, the slicer said 96 min: 50 min by the file, 48 by the slicer, half each.
  assert.equal(leftOf(50 * 60, 0.5, 96 * 60), 0.5 * 50 * 60 + 0.5 * 48 * 60);
  // A 6 min heat-up at 1 %: below FILE_FLOOR only the slicer counts, so no jump to hours.
  assert.equal(leftOf(6 * 60, 0.01, 96 * 60), 96 * 60 * 0.99);
  // At 3 %, the file's 3 h 14 min weighs 3 %: about the slicer's time still (within a few minutes).
  assert.ok(Math.abs(/** @type {number} */ (leftOf(6 * 60, 0.03, 96 * 60)) - 96 * 60) < 5 * 60);
  // 1:37 printed at 91 % of a 1:36 file: never 0:00 left (≈ 9.5 min, shown 0:10).
  assert.equal(fmtHM(Math.round(/** @type {number} */ (leftOf(97 * 60, 0.91, 96 * 60)))), '0:10');
  // Either alone; neither.
  assert.equal(leftOf(600, 0.25, null), 1800);
  assert.equal(leftOf(30, FILE_FLOOR / 2, null), null);
  assert.equal(leftOf(0, 0.5, undefined), null);
});

test('the clock: running, finishing, complete, stopped, unknown', () => {
  const run = clockOf('printing', 2304, 0.4, 5760);
  assert.deepEqual([run.phase, run.left, totalOf(run)], ['running', 3456, 5760]);
  assert.equal(clockOf('paused', 2304, 0.4, 5760).phase, 'running');
  assert.deepEqual(clockOf('printing', 5000, END_SHARE, 5760), {
    p: END_SHARE,
    done: 5000,
    left: null,
    phase: 'finishing',
  });
  assert.deepEqual(clockOf('complete', 5000, 0.98, 5760), { p: 0.98, done: 5000, left: 0, phase: 'complete' });
  assert.equal(clockOf('cancelled', 5000, 0.5, 5760).left, null);
  assert.equal(clockOf('error', 5000, 0.5, 5760).phase, 'stopped');
  assert.equal(clockOf('printing', 30, null, 5760).phase, 'unknown');
  assert.equal(totalOf(clockOf('printing', 30, null, 5760)), null);
});

test('every estimate on the same clock: the time to a share, and the share a swap covers', () => {
  const c = clockOf('printing', 2304, 0.4, 5760); // 3456 s left for 60 %
  assert.equal(etaTo(c, 0.7), Math.round((0.3 / 0.6) * 3456));
  assert.equal(etaTo(c, 0.40001), 60);
  assert.equal(etaTo(clockOf('printing', 30, null, 5760), 0.5), null);
  assert.equal(swapShareOf(c, 90), (90 * 0.6) / 3456);
  assert.equal(swapShareOf(clockOf('cancelled', 1, 0.5, 5760), 90), null);
  // Complete or stopped: no time to anything.
  assert.equal(etaTo(clockOf('complete', 5000, 0.5, 5760), 0.7), null);
  assert.equal(etaTo(clockOf('cancelled', 5000, 0.5, 5760), 0.7), null);
});

test('the demo cards carry a G-code byte range, so the demo counts over a frame', () => {
  const demo = JSON.parse(readFileSync(new URL('../dev/demo-files.json', import.meta.url), 'utf8'));
  for (const [file, card] of Object.entries(demo.cards)) {
    const f = frameOf(/** @type {any} */ (card), null, null);
    assert.ok(f && f.end > f.start, file);
  }
  assert.ok(demo.cards['objeto_5_cores_PLA_1h36m.gcode']);
});
