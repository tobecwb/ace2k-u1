import { test } from 'node:test';
import assert from 'node:assert/strict';
import { temp, tempWithTarget, readingWithTarget, pct, clock, historyDays, isNum, fmtHM, fmtMS } from '../js/format.js';

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

test("historyDays: the recorder's day files, newest first, at most keep_days", () => {
  const files = [
    { filename: '2026-09-30.jsonl' },
    { filename: 'notes.txt' },
    { filename: '2026-10-01.jsonl' },
    { filename: '2026-10-01.jsonl.tmp' },
    { filename: 'x.jsonl' },
    { filename: '2026-09-28.jsonl' },
  ];
  assert.deepEqual(historyDays(files, 7), ['2026-10-01.jsonl', '2026-09-30.jsonl', '2026-09-28.jsonl']);
  assert.deepEqual(historyDays(files, 2), ['2026-10-01.jsonl', '2026-09-30.jsonl']);
  assert.deepEqual(historyDays(undefined, 7), []);
  assert.deepEqual(historyDays([null, { filename: 5 }], 7), []);
});

test('isNum: a finite number only', () => {
  assert.deepEqual([0, -1.5, 42].map(isNum), [true, true, true]);
  assert.deepEqual([NaN, Infinity, '3', null, undefined].map(isNum), [false, false, false, false, false]);
});

test('tempWithTarget shows the target beside the reading, and nothing without one', () => {
  assert.equal(tempWithTarget(45.2, 50), '45.2 / 50.0 °C');
  assert.equal(tempWithTarget(45.2, 62.04), '45.2 / 62.0 °C');
  for (const off of [0, null, undefined, NaN, '55'])
    assert.equal(tempWithTarget(45.2, off), '45.2 °C', `target ${String(off)}`);
  assert.equal(tempWithTarget(null, 50), '—');
});

test('readingWithTarget gives the numbers only', () => {
  assert.equal(readingWithTarget(45.2, 50), '45.2 / 50.0');
  assert.equal(readingWithTarget(45.2, null), '45.2');
  assert.equal(readingWithTarget(null, 50), '—');
});

test('fmtHM', () => {
  assert.equal(fmtHM(5760), '1:36');
  assert.equal(fmtHM(59), '0:01');
  assert.equal(fmtHM(0), '0:00');
  assert.equal(fmtHM(86400), '24:00');
});

test('fmtMS: minutes and seconds, for the reading bar', () => {
  assert.equal(fmtMS(7), '0:07');
  assert.equal(fmtMS(75), '1:15');
  assert.equal(fmtMS(7.9), '0:07');
  for (const bad of [0, -3, Number.NaN]) assert.equal(fmtMS(bad), '0:00');
});
