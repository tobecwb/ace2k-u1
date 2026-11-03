import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  WHOLE,
  ZOOM_MIN,
  axisLabels,
  clampView,
  moveTo,
  pct,
  pickView,
  place,
  placeAt,
  resize,
  zoomIn,
  zoomOut,
} from '../js/zoom.js';
import { loadEnglish } from './helpers.js';
import { t } from '../js/i18n.js';

loadEnglish();

const ok = (v) => {
  assert.ok(v.a >= 0 && v.b <= 1 && v.b - v.a >= ZOOM_MIN - 1e-12, JSON.stringify(v));
};

test('clampView keeps the stretch inside and at least ZOOM_MIN wide', () => {
  assert.equal(ZOOM_MIN, 0.01);
  assert.deepEqual(clampView({ a: -0.2, b: 0.3 }), { a: 0, b: 0.5 });
  assert.deepEqual(clampView({ a: 0.8, b: 1.3 }), { a: 0.5, b: 1 });
  const tiny = clampView({ a: 0.5, b: 0.5 });
  ok(tiny);
  assert.ok(Math.abs(tiny.a - 0.495) < 1e-12);
  ok(clampView({ a: Number.NaN, b: 2 }));
  assert.deepEqual(clampView({ a: 0, b: 5 }), WHOLE);
  assert.deepEqual(clampView({ a: 0.6, b: 0.2 }), { a: 0.2, b: 0.6 }, 'reversed edges are swapped');
  assert.deepEqual(clampView({ a: 0.2, b: Number.NaN }), { a: 0.2, b: 1 }, 'a missing end is the file end');
});

test('zoomIn halves around the centre, zoomOut doubles, both kept inside', () => {
  assert.deepEqual(zoomIn({ a: 0.2, b: 0.6 }), { a: 0.3, b: 0.5 });
  assert.deepEqual(zoomOut({ a: 0.3, b: 0.5 }), { a: 0.2, b: 0.6 });
  assert.deepEqual(zoomOut(WHOLE), WHOLE);
  assert.deepEqual(zoomOut({ a: 0, b: 0.2 }), { a: 0, b: 0.4 });
  ok(zoomIn({ a: 0.5, b: 0.505 }));
});

test('moveTo keeps the width and stays inside', () => {
  assert.deepEqual(moveTo({ a: 0.2, b: 0.4 }, 0.5), { a: 0.5, b: 0.7 });
  assert.deepEqual(moveTo({ a: 0.2, b: 0.4 }, 0.95), { a: 0.8, b: 1 });
  assert.deepEqual(moveTo({ a: 0.2, b: 0.4 }, -1), { a: 0, b: 0.2 });
});

test('resize moves one edge, never past the other minus ZOOM_MIN', () => {
  assert.deepEqual(resize({ a: 0.2, b: 0.4 }, 'a', 0.1), { a: 0.1, b: 0.4 });
  assert.deepEqual(resize({ a: 0.2, b: 0.4 }, 'b', 0.9), { a: 0.2, b: 0.9 });
  const pinched = resize({ a: 0.2, b: 0.4 }, 'a', 0.5);
  ok(pinched);
  assert.ok(Math.abs(pinched.b - 0.4) < 1e-12);
});

test('pickView takes the stretch between two points, in either order', () => {
  assert.deepEqual(pickView(0.7, 0.3), { a: 0.3, b: 0.7 });
  ok(pickView(0.5, 0.5));
});

test('place draws a part in the stretch, clipped, or not at all', () => {
  const v = { a: 0.2, b: 0.4 };
  assert.deepEqual(place(0.25, 0.3, v), { left: 25, width: 25 });
  assert.deepEqual(place(0.1, 0.3, v), { left: 0, width: 50 });
  assert.deepEqual(place(0.35, 0.9, v), { left: 75, width: 25 });
  assert.equal(place(0.5, 0.6, v), null);
  assert.equal(place(0, 0.2, v), null);
  assert.deepEqual(place(0, 1, WHOLE), { left: 0, width: 100 });
});

test('pct rounds a fraction to three decimals of a percent; placeAt puts a point on the stretch, or nowhere', () => {
  assert.equal(pct(1 - 0.8), 20);
  assert.equal(pct(1 / 3), 33.333);
  const v = { a: 0.2, b: 0.4 };
  assert.equal(placeAt(0.2, v), 0);
  assert.equal(placeAt(0.3, v), 50);
  assert.equal(placeAt(0.4, v), 100);
  assert.equal(placeAt(0.1, v), null);
  assert.equal(placeAt(0.41, v), null);
  assert.equal(placeAt(0.5, WHOLE), 50);
});

test('axisLabels: ≈ h:mm from the slicer time, else percent of the file', () => {
  assert.deepEqual(axisLabels(t, { a: 0, b: 0.5 }, 7200), ['≈ 0:00', '≈ 0:30', '≈ 1:00']);
  assert.deepEqual(axisLabels(t, { a: 0.25, b: 0.75 }, null), ['25 %', '50 %', '75 %']);
});
