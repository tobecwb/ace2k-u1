import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { setCatalog } from '../js/i18n.js';
import { defaultWindow } from '../js/views/chart.js';
import { eventsHtml, anchoredTop } from '../js/views/events.js';

setCatalog(JSON.parse(readFileSync(new URL('../i18n/en.json', import.meta.url), 'utf8')));

test('defaultWindow', () => {
  const now = 100000;
  assert.equal(defaultWindow(null, now), 6);
  assert.equal(defaultWindow({ start: now - 1800 }, now), 1);
  assert.equal(defaultWindow({ start: now - 3600 }, now), 1);
  assert.equal(defaultWindow({ start: now - 4 * 3600 }, now), 6);
  assert.equal(defaultWindow({ start: now - 10 * 3600 }, now), 24);
  assert.equal(defaultWindow({ start: now - 30 * 3600 }, now), 24);
});

test('eventsHtml escapes the message and the source', () => {
  const html = eventsHtml([{ t: 1, lvl: 'err', src: '<b>', msg: '<img onerror=x>' }], { filter: 'all', limit: 200, more: false });
  assert.ok(!html.includes('<img'));
  assert.ok(html.includes('&lt;img onerror=x&gt;'));
  assert.ok(!html.includes('Show more'));
});

test('eventsHtml takes the already filtered list as is', () => {
  const ev = [{ t: 1, lvl: 'err', src: 'lane1', msg: 'a' }, { t: 2, lvl: 'info', src: 'unit', msg: 'b' }];
  const html = eventsHtml(ev, { filter: 'all', limit: 1, more: false, filtered: [ev[0], ev[1]] });
  assert.ok(html.includes('>a<') && !html.includes('>b<'));
});

test('anchoredTop: at the top the list stays at the top', () => {
  const rows = [{ k: 'a', offset: 0, height: 20 }, { k: 'b', offset: 20, height: 20 }];
  assert.equal(anchoredTop({ top: 0, rows }, { rows: [{ k: 'n', offset: 0, height: 20 }, { k: 'a', offset: 20, height: 20 }] }), 0);
});

test('anchoredTop: the first visible row keeps its place when rows arrive above', () => {
  const before = { top: 50, rows: [{ k: 'a', offset: 0, height: 20 }, { k: 'b', offset: 20, height: 20 }, { k: 'c', offset: 40, height: 20 }, { k: 'd', offset: 60, height: 20 }] };
  const after = { rows: [{ k: 'n', offset: 0, height: 40 }, ...before.rows.map((r) => ({ ...r, offset: r.offset + 40 }))] };
  assert.equal(anchoredTop(before, after), 90); // c was 10 px above the view's top, still is
});

test('anchoredTop: growth below the anchor changes nothing', () => {
  const before = { top: 50, rows: [{ k: 'a', offset: 0, height: 20 }, { k: 'c', offset: 40, height: 20 }, { k: 'd', offset: 60, height: 20 }] };
  const after = { rows: [...before.rows, { k: 'e', offset: 80, height: 20 }] };
  assert.equal(anchoredTop(before, after), 50);
});

test('anchoredTop: rows dropped above shift it back; an anchor that is gone keeps the position', () => {
  const before = { top: 50, rows: [{ k: 'a', offset: 0, height: 20 }, { k: 'c', offset: 40, height: 20 }] };
  assert.equal(anchoredTop(before, { rows: [{ k: 'c', offset: 0, height: 20 }] }), 10);
  assert.equal(anchoredTop(before, { rows: [{ k: 'z', offset: 0, height: 20 }] }), 50);
});

test('eventsHtml rows carry a key', () => {
  assert.match(eventsHtml([{ t: 5, lvl: 'info', src: 'unit', msg: 'x<y' }], { filter: 'all', limit: 10, more: false }), /data-k="5\|x&lt;y\|0"/);
  const two = eventsHtml([{ t: 5, lvl: 'info', src: 'unit', msg: 'a' }, { t: 5, lvl: 'info', src: 'unit', msg: 'a' }], { filter: 'all', limit: 10, more: false });
  assert.match(two, /data-k="5\|a\|0"[\s\S]*data-k="5\|a\|1"/);
});
