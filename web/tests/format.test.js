import { test } from 'node:test';
import assert from 'node:assert/strict';
import { duration, temp, pct, clock, historyDays } from '../js/format.js';

test('duration', () => {
  assert.equal(duration(151), '2 h 31 min');
  assert.equal(duration(240), '4 h');
  assert.equal(duration(45), '45 min');
  assert.equal(duration(0), '0 min');
  assert.equal(duration(null), '—');
});

test('temp and pct', () => {
  assert.equal(temp(27.12), '27.1 °C');
  assert.equal(temp(null), '—');
  assert.equal(temp(NaN), '—');
  assert.equal(pct(51.9), '52 %');
  assert.equal(pct(undefined), '—');
});

test('clock is local HH:MM', () => {
  const t = new Date(2026, 9, 8, 14, 2, 30).getTime() / 1000;
  assert.equal(clock(t), '14:02');
});

test('historyDays: the recorder\'s day files, newest first, at most keep_days', () => {
  const files = [
    { filename: '2026-09-30.jsonl' }, { filename: 'notes.txt' }, { filename: '2026-10-01.jsonl' },
    { filename: '2026-10-01.jsonl.tmp' }, { filename: 'x.jsonl' }, { filename: '2026-09-28.jsonl' },
  ];
  assert.deepEqual(historyDays(files, 7), ['2026-10-01.jsonl', '2026-09-30.jsonl', '2026-09-28.jsonl']);
  assert.deepEqual(historyDays(files, 2), ['2026-10-01.jsonl', '2026-09-30.jsonl']);
  assert.deepEqual(historyDays(undefined, 7), []);
  assert.deepEqual(historyDays([null, { filename: 5 }], 7), []);
});
