import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { setCatalog } from '../js/i18n.js';
import { dryerHtml, faultText, FAN_SECONDS } from '../js/views/dryer.js';

setCatalog(JSON.parse(readFileSync(new URL('../i18n/en.json', import.meta.url), 'utf8')));

const view = (over = {}) => ({ state: 'idle', target: null, remaining: null, fault: null, fansOn: false,
  flaps: { bottom: 'closed', rear: 'closed' }, outletL: 30, outletR: 30, chamber: 27, humidity: 50, manual: true, ...over });
const opts = (over = {}) => ({ open: true, form: { preset: null, temp: 55, h: 4, m: 0 }, presets: [], progress: null, online: true, folds: {}, ...over });

test('an unknown fault name is escaped', () => {
  assert.ok(faultText('<x>').includes('<x>')); // raw text: every caller escapes
  assert.ok(dryerHtml(view({ state: 'fault', fault: '<x>', manual: false }), opts({ open: false })).includes('&lt;x&gt;'));
  assert.ok(!dryerHtml(view({ state: 'fault', fault: '<x>', manual: false }), opts()).includes('<x>'));
});

test('the Manual fold exists only when idle', () => {
  assert.ok(dryerHtml(view(), opts()).includes('id="manual"'));
  for (const state of ['heating', 'cooldown', 'fault']) {
    assert.ok(!dryerHtml(view({ state, manual: false, remaining: 10, fault: 'ntc' }), opts()).includes('id="manual"'), state);
  }
});

test('Start is offered only when idle', () => {
  assert.ok(dryerHtml(view(), opts()).includes('data-action="dry"'));
  for (const state of ['heating', 'cooldown', 'fault']) {
    assert.ok(!dryerHtml(view({ state, manual: false, remaining: 10, fault: 'ntc' }), opts()).includes('data-action="dry"'), state);
  }
});

test('a pending Start is disabled and says sending', () => {
  const html = dryerHtml(view(), opts({ pending: new Set(['dry:']) }));
  assert.match(html, /data-action="dry" disabled>sending…/);
});

test('the strip is a keyboard button; the fan label uses the shared duration', () => {
  const html = dryerHtml(view(), opts());
  assert.match(html, /role="button" tabindex="0" data-action="dryer_toggle"/);
  assert.ok(html.includes(`Fans on for ${FAN_SECONDS} s`));
});

test('the strip shows a visible Open / Close control and the numeric keypad inputs', () => {
  assert.ok(dryerHtml(view(), opts({ open: false })).includes('class="pill">Open ▾<'));
  assert.ok(dryerHtml(view(), opts()).includes('class="pill">Close ▴<'));
  const html = dryerHtml(view(), opts());
  for (const id of ['dry-temp', 'dry-h', 'dry-m']) assert.match(html, new RegExp(`id="${id}" type="number" inputmode="numeric" pattern="\\[0-9\\]\\*"`));
});
