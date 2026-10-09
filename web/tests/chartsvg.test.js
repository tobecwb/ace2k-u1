import { test } from 'node:test';
import assert from 'node:assert/strict';
import { chartSvg, chartBox, nearest, SERIES } from '../js/chartsvg.js';

const s = (t, ch, extra = {}) => ({ t, k: 's', ch, rh: 50, hl: ch + 2, hr: ch + 3, dry: 'idle', tgt: null, ...extra });

test('chartSvg draws each series and breaks on gaps', () => {
  const samples = [s(0, 20), s(30, 21), s(60, 22), s(300, 25), s(330, 26)];
  const svg = chartSvg(samples, { t0: 0, t1: 330, hidden: [], labels: { ago: '-1h', now: 'now' }, width: 400 });
  const runs = (key) => (svg.match(new RegExp(`data-series="${key}"`, 'g')) || []).length;
  assert.equal(runs('ch'), 2);
  assert.equal(runs('rh'), 2);
  assert.ok(svg.startsWith('<svg'));
  assert.deepEqual(SERIES.map((x) => x.key), ['hl', 'hr', 'ch', 'rh']);
});

test('chartSvg hides series and draws the target dashed', () => {
  const samples = [s(0, 20, { tgt: 55 }), s(30, 21, { tgt: 55 })];
  const svg = chartSvg(samples, { t0: 0, t1: 30, hidden: ['rh'], labels: { ago: '-1h', now: 'now' }, width: 400 });
  assert.ok(!svg.includes('data-series="rh"'));
  assert.ok(svg.includes('data-series="tgt"'));
  assert.ok(svg.includes('stroke-dasharray'));
});

test('chartSvg skips nulls', () => {
  const svg = chartSvg([s(0, 20), { ...s(30, 21), ch: null }, s(60, 22)], { t0: 0, t1: 60, hidden: [], labels: { ago: '', now: '' } });
  assert.equal((svg.match(/data-series="ch"/g) || []).length, 0); // single points draw no line
});

test('nearest', () => {
  const samples = [s(0, 20), s(30, 21), s(60, 22)];
  assert.equal(nearest(samples, 40).t, 30);
  assert.equal(nearest([], 40), null);
});

test('chartSvg escapes the labels', () => {
  const svg = chartSvg([s(0, 20), s(30, 21)], { t0: 0, t1: 30, hidden: [], labels: { ago: '<b>&"', now: 'x' } });
  assert.ok(svg.includes('&lt;b&gt;&amp;&quot;'));
  assert.ok(!svg.includes('<b>'));
});

test('chartBox: height by width, margins inside the width', () => {
  assert.deepEqual([chartBox(390).W, chartBox(390).H], [390, 180]);
  assert.deepEqual([chartBox(1200).W, chartBox(1200).H], [1200, 220]);
  const b = chartBox(390);
  assert.ok(b.L > 0 && b.R < b.W && b.BOT < b.H);
});

test('chartSvg is drawn in CSS pixels: viewBox = size, strokes 1.5 / 2 / 1', () => {
  const samples = [s(0, 20, { tgt: 55 }), s(30, 21, { tgt: 55 }), s(60, 22, { tgt: 55 })];
  const svg = chartSvg(samples, { t0: 0, t1: 60, hidden: [], labels: { ago: 'a', now: 'n' }, width: 390 });
  assert.ok(svg.includes('viewBox="0 0 390 180" width="390" height="180"'));
  const w = (key) => new RegExp(`data-series="${key}"[^>]*stroke-width="([\\d.]+)"`).exec(svg)[1];
  assert.deepEqual(['hl', 'hr', 'ch', 'rh', 'tgt'].map(w), ['1.5', '1.5', '2', '2', '1']);
  assert.ok(svg.includes('stroke-dasharray="4 3"'));
  // the first point sits at the left margin, the last at the right edge of the plot
  const b = chartBox(390);
  const pts = /data-series="ch"[^>]*points="([^"]+)"/.exec(svg)[1].split(' ');
  assert.equal(Number(pts[0].split(',')[0]), b.L);
  assert.equal(Number(pts[2].split(',')[0]), b.R);
});

test('chartSvg draws the live target from the cycle start to now, once', () => {
  const samples = [s(0, 20, { tgt: 45 }), s(100, 21, { tgt: 45 }), s(130, 22, { tgt: 45 })];
  const svg = chartSvg(samples, { t0: 0, t1: 200, hidden: [], labels: { ago: 'a', now: 'n' }, width: 400, live: { from: 100, value: 45 } });
  const b = chartBox(400);
  const live = /data-live="1"[^>]*points="([^"]+)"/.exec(svg)[1].split(' ');
  assert.equal(live[0].split(',')[0], (b.L + (100 / 200) * (b.R - b.L)).toFixed(1));
  assert.equal(Number(live[1].split(',')[0]), b.R);
  assert.equal(live[0].split(',')[1], live[1].split(',')[1]);
  // samples of the running cycle are not drawn a second time under it; an earlier one is a single point here
  assert.equal((svg.match(/data-series="tgt"/g) || []).length, 1);
  // a start before the window clamps to the left edge; hidden tgt draws nothing
  const edge = chartSvg(samples, { t0: 50, t1: 200, hidden: [], labels: { ago: 'a', now: 'n' }, width: 400, live: { from: null, value: 45 } });
  assert.equal(/data-live="1"[^>]*points="([^"]+)"/.exec(edge)[1].split(' ')[0].split(',')[0], String(b.L.toFixed(1)));
  assert.ok(!chartSvg(samples, { t0: 0, t1: 200, hidden: ['tgt'], labels: { ago: 'a', now: 'n' }, width: 400, live: { from: 100, value: 45 } }).includes('data-series="tgt"'));
});
