import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { MOTION_MS, motionMs, presence, travel } from '../js/motion.js';

test('MOTION_MS matches --dur in tokens.css, and reduce motion zeroes it', () => {
  const css = readFileSync(new URL('../css/tokens.css', import.meta.url), 'utf8');
  assert.equal(MOTION_MS, 150);
  assert.match(css, /--dur:\s*150ms/);
  assert.match(css, /@media \(prefers-reduced-motion: reduce\)\s*\{[^}]*--dur:\s*0ms/s);
  assert.equal(
    motionMs(() => ({ matches: true })),
    0,
  );
  assert.equal(
    motionMs(() => ({ matches: false })),
    150,
  );
  assert.equal(motionMs(undefined), 150);
});

test('presence keeps a closed panel for its exit, and a new one replaces it at once', () => {
  const a = { kind: 'bay', key: '1' };
  const b = { kind: 'unit', id: 'a' };
  assert.deepEqual(presence({ shown: null, leaving: false }, a), { shown: a, leaving: false });
  const closing = presence({ shown: a, leaving: false }, null);
  assert.deepEqual(closing, { shown: a, leaving: true });
  assert.deepEqual(presence(closing, b), { shown: b, leaving: false });
  assert.deepEqual(presence(closing, null), closing);
  assert.deepEqual(presence({ shown: null, leaving: false }, null), { shown: null, leaving: false });
});

test('travel: the side the new tab comes from', () => {
  assert.equal(travel('units', 'print'), 'right');
  assert.equal(travel('history', 'units'), 'left');
  assert.equal(travel('print', 'print'), null);
  assert.equal(travel(null, 'print'), null);
});
