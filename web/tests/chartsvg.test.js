import { test } from 'node:test';
import assert from 'node:assert/strict';
import { elements } from './helpers.js';
import {
  chartSvg,
  chartBox,
  nearest,
  SERIES,
  tooltipAt,
  tooltipText,
  tooltipSample,
  tooltipPlace,
} from '../js/chartsvg.js';

const s = (t, ch, extra = {}) => ({ t, k: 's', ch, rh: 50, hl: ch + 2, hr: ch + 3, dry: 'idle', tgt: null, ...extra });

test('chartSvg draws each series and breaks on gaps', () => {
  const samples = [s(0, 20), s(30, 21), s(60, 22), s(300, 25), s(330, 26)];
  const svg = chartSvg(samples, { t0: 0, t1: 330, hidden: [], labels: { ago: '-1h', now: 'now' }, width: 400 });
  const runs = (key) => elements(svg, `[data-series=${key}]`).length;
  assert.equal(runs('ch'), 2);
  assert.equal(runs('rh'), 2);
  assert.equal(elements(svg, 'svg').length, 1);
  assert.deepEqual(
    SERIES.map((x) => x.key),
    ['hl', 'hr', 'ch', 'rh'],
  );
});

test('chartSvg hides series and draws the target dashed', () => {
  const samples = [s(0, 20, { tgt: 55 }), s(30, 21, { tgt: 55 })];
  const svg = chartSvg(samples, { t0: 0, t1: 30, hidden: ['rh'], labels: { ago: '-1h', now: 'now' }, width: 400 });
  assert.equal(elements(svg, '[data-series=rh]').length, 0);
  const [target] = elements(svg, '[data-series=tgt]');
  assert.ok(target.attrs['stroke-dasharray']);
});

test('chartSvg skips nulls', () => {
  const svg = chartSvg([s(0, 20), { ...s(30, 21), ch: null }, s(60, 22)], {
    t0: 0,
    t1: 60,
    hidden: [],
    labels: { ago: '', now: '' },
  });
  assert.equal(elements(svg, '[data-series=ch]').length, 0); // single points draw no line
});

test('nearest', () => {
  const samples = [s(0, 20), s(30, 21), s(60, 22)];
  assert.equal(nearest(samples, 40).t, 30);
  assert.equal(nearest([], 40), null);
});

test('chartSvg escapes the labels', () => {
  const svg = chartSvg([s(0, 20), s(30, 21)], { t0: 0, t1: 30, hidden: [], labels: { ago: '<b>&"', now: 'x' } });
  assert.equal(elements(svg, 'b').length, 0);
  assert.ok(elements(svg, 'text').some((x) => x.text === '<b>&"'));
});

test("chartSvg: no <title>, which a browser would show as a second tooltip over the chart's own", () => {
  const svg = chartSvg([s(0, 20), s(30, 21)], { t0: 0, t1: 30, labels: { ago: 'a', now: 'Now' }, width: 400 });
  assert.equal(elements(svg, 'title').length, 0);
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
  const [box] = elements(svg, 'svg');
  assert.deepEqual([box.attrs.viewBox, box.attrs.width, box.attrs.height], ['0 0 390 180', '390', '180']);
  const w = (key) => elements(svg, `[data-series=${key}]`)[0].attrs['stroke-width'];
  assert.deepEqual(['hl', 'hr', 'ch', 'rh', 'tgt'].map(w), ['1.5', '1.5', '2', '2', '1']);
  assert.equal(elements(svg, '[data-series=tgt]')[0].attrs['stroke-dasharray'], '4 3');
  // the first point sits at the left margin, the last at the right edge of the plot
  const b = chartBox(390);
  const pts = elements(svg, '[data-series=ch]')[0].attrs.points.split(' ');
  assert.equal(Number(pts[0].split(',')[0]), b.L);
  assert.equal(Number(pts[2].split(',')[0]), b.R);
});

// The target's points, as [x, y] strings, for the tests of the target below.
const targetPoints = (svg) =>
  elements(svg, '[data-series=tgt]').map((el) => el.attrs.points.split(' ').map((p) => p.split(',')));
const xAt = (b, t, t0, t1) => (b.L + ((t - t0) / (t1 - t0)) * (b.R - b.L)).toFixed(1);

test("the chamber's target is a dashed step in the chamber's colour: 0, the target while heating, 0", () => {
  const samples = [s(0, 20), s(30, 21, { tgt: 45 }), s(60, 22, { tgt: 45 }), s(90, 23)];
  const svg = chartSvg(samples, { t0: 0, t1: 90, hidden: [], labels: { ago: 'a', now: 'n' }, width: 400 });
  const [line] = elements(svg, '[data-series=tgt]');
  assert.equal(line.attrs.style, 'stroke:var(--line-ch)');
  assert.equal(line.attrs['stroke-dasharray'], '4 3');
  const b = chartBox(400);
  const [pts] = targetPoints(svg);
  const off = b.BOT.toFixed(1); // 0 °C is below the axis here: the off level sits on the plot's bottom edge
  const [x30, x90] = [xAt(b, 30, 0, 90), xAt(b, 90, 0, 90)];
  const y45 = pts[2][1];
  assert.ok(Number(y45) < b.BOT);
  assert.deepEqual(pts, [
    [b.L.toFixed(1), off],
    [x30, off],
    [x30, y45],
    [x90, y45],
    [x90, off],
  ]);
});

test('the off level is 0 °C when the axis reaches 0', () => {
  const samples = [s(0, 1), s(30, 1, { tgt: 45 }), s(60, 1)];
  const svg = chartSvg(samples, { t0: 0, t1: 60, hidden: [], labels: { ago: 'a', now: 'n' }, width: 400 });
  const [pts] = targetPoints(svg);
  const b = chartBox(400);
  assert.ok(Number(pts[0][1]) < b.BOT); // the axis starts below 0 (-5°), so 0 °C sits above its bottom edge
  assert.equal(pts[0][1], pts.at(-1)[1]);
});

test('a gap in the samples breaks the target, as it breaks the readings', () => {
  const samples = [s(0, 20), s(30, 21, { tgt: 45 }), s(300, 22), s(330, 23)];
  const svg = chartSvg(samples, { t0: 0, t1: 330, hidden: [], labels: { ago: 'a', now: 'n' }, width: 400 });
  assert.equal(targetPoints(svg).length, 2);
});

test('the empty sample of a Klipper restart breaks the target, it does not drop it to 0', () => {
  const gap = { t: 60, k: 's', ch: null, rh: null, hl: null, hr: null, dry: null, tgt: null, rem: null };
  const samples = [s(0, 20, { tgt: 45 }), s(30, 21, { tgt: 45 }), gap, s(90, 22, { tgt: 45 }), s(120, 23, { tgt: 45 })];
  const svg = chartSvg(samples, { t0: 0, t1: 120, hidden: [], labels: { ago: 'a', now: 'n' }, width: 400 });
  const runs = targetPoints(svg);
  assert.equal(runs.length, 2);
  const y45 = runs[0][0][1];
  for (const run of runs) for (const [, y] of run) assert.equal(y, y45);
});

test('while the dryer heats, the target rises at the cycle start and runs to now, once', () => {
  const samples = [s(0, 20), s(100, 21, { tgt: 45 }), s(130, 22, { tgt: 45 })];
  const opts = { t0: 0, t1: 200, hidden: [], labels: { ago: 'a', now: 'n' }, width: 400 };
  const b = chartBox(400);
  const svg = chartSvg(samples, { ...opts, live: { from: 90, value: 45 } });
  const all = targetPoints(svg);
  assert.equal(all.length, 1); // the samples of the running cycle are not drawn a second time
  const [pts] = all;
  const x90 = xAt(b, 90, 0, 200);
  const off = b.BOT.toFixed(1);
  const y = pts.at(-1)[1];
  assert.deepEqual(pts, [
    [b.L.toFixed(1), off],
    [x90, off],
    [x90, y],
    [b.R.toFixed(1), y],
  ]);
  // A start before the window starts the target at the left edge, at the target.
  const [edge] = targetPoints(chartSvg(samples, { ...opts, t0: 50, live: { from: null, value: 45 } }));
  assert.deepEqual(edge, [
    [b.L.toFixed(1), y],
    [b.R.toFixed(1), y],
  ]);
  // A hidden target draws nothing.
  const hidden = chartSvg(samples, { ...opts, hidden: ['tgt'], live: { from: 90, value: 45 } });
  assert.equal(elements(hidden, '[data-series=tgt]').length, 0);
});

test('a live target that starts long after the last sample is a run of its own', () => {
  const samples = [s(0, 20, { tgt: 45 }), s(30, 21, { tgt: 45 })];
  const svg = chartSvg(samples, {
    t0: 0,
    t1: 400,
    hidden: [],
    labels: { ago: 'a', now: 'n' },
    width: 400,
    live: { from: 300, value: 45 },
  });
  assert.equal(targetPoints(svg).length, 2);
});

test('with no target in the window, the target stays at 0', () => {
  const samples = [s(0, 20), s(30, 21), s(60, 22)];
  const svg = chartSvg(samples, { t0: 0, t1: 60, hidden: [], labels: { ago: 'a', now: 'n' }, width: 400 });
  const b = chartBox(400);
  assert.deepEqual(targetPoints(svg), [
    [
      [b.L.toFixed(1), b.BOT.toFixed(1)],
      [b.R.toFixed(1), b.BOT.toFixed(1)],
    ],
  ]);
});

test('tooltipAt maps the pointer to a time through the plot edges', () => {
  const win = { w: 400, l: 32, r: 362, t0: 1000, t1: 2000 };
  assert.equal(tooltipAt(32, 400, win), 1000); // the left edge of the plot
  assert.equal(tooltipAt(362, 400, win), 2000);
  assert.equal(tooltipAt(197, 400, win), 1500);
  assert.equal(tooltipAt(100, 200, win), tooltipAt(200, 400, win)); // a scaled box
});

test('tooltipText names the readings, and the target only when there is one', () => {
  const fmt = { clock: (x) => `T${x}`, temp: (x) => `${x}C`, pct: (x) => `${x}%`, t: (k) => k.slice(6) };
  const base = { t: 5, ch: 30, hl: 40, hr: 41, rh: 20 };
  assert.equal(tooltipText(base, fmt), 'T5 · chamber 30C · heater_l 40C · heater_r 41C · humidity 20%');
  assert.equal(
    tooltipText({ ...base, tgt: 55 }, fmt),
    'T5 · chamber 30C · heater_l 40C · heater_r 41C · humidity 20% · target 55C',
  );
  assert.ok(!tooltipText({ ...base, tgt: null }, fmt).includes('target'));
});

test('tooltipSample: the nearest sample inside the window, none when the window holds none', () => {
  const samples = [s(10, 20), s(100, 21), s(200, 22)];
  assert.equal(tooltipSample(samples, 50, 12).t, 100); // the one at 10 is before the window
  assert.equal(tooltipSample(samples, 50, 190).t, 200);
  assert.equal(tooltipSample(samples, 300, 190), null);
});

test('tooltipPlace: 8 px from the pointer, kept inside the box', () => {
  assert.deepEqual(tooltipPlace({ x: 20, y: 30 }, 400, 100), { left: 28, top: 38 });
  assert.deepEqual(tooltipPlace({ x: 390, y: 5 }, 400, 100), { left: 300, top: 13 });
  assert.deepEqual(tooltipPlace({ x: 10, y: 5 }, 50, 100), { left: 0, top: 13 }); // a tip wider than the box
});

test('events: one dot on the time axis per event inside the window, at its time, with its tone', () => {
  const samples = [s(0, 20), s(30, 21), s(60, 22)];
  const b = chartBox(400);
  const draw = (events) =>
    chartSvg(samples, { t0: 0, t1: 60, hidden: [], labels: { ago: 'a', now: 'n' }, width: 400, events });
  const svg = draw([
    { t: 0, tone: 'err' },
    { t: 30, tone: 'dryer' },
    { t: 45, tone: 'info' },
    { t: 99, tone: 'err' },
    { t: -5, tone: 'err' },
  ]);
  const dots = elements(svg, 'circle.ev');
  assert.deepEqual(
    dots.map((d) => [d.attrs.class, d.attrs.cx]),
    [
      ['ev ev-err', (b.L + 4).toFixed(1)],
      ['ev ev-dryer', ((b.L + b.R) / 2).toFixed(1)],
      ['ev ev-info', (b.L + 0.75 * (b.R - b.L)).toFixed(1)],
    ],
  );
  assert.equal(elements(svg, 'line.evline').length, 3);
  // An event at the right edge stays inside the plot, clear of the "100%" label, and below the top edge.
  const edge = elements(draw([{ t: 60, tone: 'info' }]), 'circle.ev')[0].attrs;
  assert.equal(Number(edge.cx) + 4, b.R);
  assert.ok(Number(edge.cy) - 4 > 8 - 1, 'the dot is under the plot top');
  assert.equal(elements(draw([{ t: 60, tone: 'info' }]), 'line.evline')[0].attrs.x1, b.R.toFixed(1));
  assert.equal(elements(draw([]), 'circle.ev').length, 0);
  assert.equal(
    elements(chartSvg(samples, { t0: 0, t1: 60, labels: { ago: 'a', now: 'n' }, width: 400 }), 'circle').length,
    0,
  );
  assert.equal(elements(draw([{ t: 30, tone: '"><x' }]), 'circle.ev').length, 1, 'the tone is escaped');
});
