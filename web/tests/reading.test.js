import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  READ_BASE_MS,
  READ_MIN_SHOWN_MS,
  READ_CAP,
  READ_MS_PER_MB,
  READ_SHOW_MS,
  estimateMs,
  readView,
} from '../js/reading.js';

test('the constants are the measured ones', () => {
  assert.equal(READ_MS_PER_MB, 48);
  assert.equal(READ_BASE_MS, 150);
  assert.equal(READ_SHOW_MS, 600);
  assert.equal(READ_CAP, 0.95);
});

test('estimateMs: 150 ms plus size in bytes × 48 ms per MB, null without a size', () => {
  assert.equal(estimateMs(254594261), 12371);
  assert.equal(estimateMs(6117340), 444);
  for (const bad of [null, undefined, 0, -5, Number.NaN, '12'])
    assert.equal(estimateMs(/** @type {any} */ (bad)), null);
});

test('readView: hidden before READ_SHOW_MS', () => {
  assert.deepEqual(readView(254594261, 0), { show: false, share: 0, leftS: 13, elapsedS: 0, still: false });
  assert.equal(readView(254594261, 599).show, false);
  assert.equal(readView(254594261, 600).show, true);
});

test('readView: share and seconds left, capped, then still reading', () => {
  const mid = readView(254594261, 5000);
  assert.equal(mid.leftS, 8);
  assert.ok(Math.abs(/** @type {number} */ (mid.share) - 5000 / 12371) < 1e-9);
  assert.equal(mid.still, false);
  const late = readView(254594261, 12371);
  assert.deepEqual(late, { show: true, share: READ_CAP, leftS: 0, elapsedS: 12, still: true });
  assert.equal(readView(254594261, 60000).share, READ_CAP);
});

test('readView: no size gives elapsed time only', () => {
  assert.deepEqual(readView(null, 4200), { show: true, share: null, leftS: null, elapsedS: 4, still: false });
});

test('readView: the bar never goes away once shown, a short read shows it only well past its estimate', () => {
  assert.equal(READ_MIN_SHOWN_MS, 500);
  for (const size of [254594261, 20e6, 12e6, null]) {
    let shown = false;
    for (let ms = 0; ms <= 30000; ms += 50) {
      const now = readView(size, ms).show;
      assert.ok(!(shown && !now), `${size} bar gone at ${ms} ms`);
      shown = now;
    }
  }
  // 12 MB: estimate 726 ms.
  assert.equal(readView(12e6, 726).show, false);
  assert.equal(readView(12e6, 760).show, false);
  assert.equal(readView(12e6, 726 + 500).show, true);
  assert.equal(readView(12e6, 726 + 500).still, true);
  assert.equal(readView(254594261, 12000).show, true);
  assert.equal(readView(20e6, 600).show, true);
});
