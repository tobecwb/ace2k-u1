import { test } from 'node:test';
import assert from 'node:assert/strict';
import { healthWords, HEALTH_NAMES } from '../js/health.js';
import { loadEnglish } from './helpers.js';

loadEnglish();

test('health: each failing check in words, joined with a comma and a space; an unknown one generically', () => {
  assert.equal(healthWords(['ntc_left', 'reader_b']), 'left heater sensor, RFID reader B');
  assert.equal(healthWords(['made_up', 'clock']), 'a check this page does not know, clock');
  assert.equal(healthWords(['encoder2', 'fg3']), 'lane 2 encoder, lane 3 motor speed signal');
  assert.equal(healthWords([]), '?');
  assert.equal(healthWords(null), '?');
  for (const name of HEALTH_NAMES) assert.doesNotMatch(healthWords([name]), /_|health\./, name);
});
