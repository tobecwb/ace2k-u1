import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { setCatalog, t } from '../js/i18n.js';
import { laneHtml, ffKey } from '../js/views/lane.js';
import { topbarHtml } from '../js/views/topbar.js';

setCatalog(JSON.parse(readFileSync(new URL('../i18n/en.json', import.meta.url), 'utf8')));

const status = (ffOn) => ({ ace2k: { lanes: [{ mode: 'idle', insert: true, rest: true, ff_on: ffOn, tag: { state: 'no_tag' } }] } });
const ffButton = (html) => html.match(/<button[^>]*data-id="ff"[^>]*>[^<]*<\/button>/)[0];

test('the feed-forward button stays busy when the lane flips mid-request', () => {
  assert.equal(ffKey(1), 'ff:1');
  const pending = new Set([ffKey(1)]);
  for (const ffOn of [true, false]) {
    const b = ffButton(laneHtml(status(ffOn), 0, true, pending));
    assert.ok(b.includes(' disabled') && b.includes(t('action.sending')), String(ffOn));
  }
  assert.ok(!ffButton(laneHtml(status(true), 0, true, new Set())).includes(' disabled'));
});

test('the Edit spool form: open state, values, Save and Cancel', () => {
  const s = status(false);
  const edit = { lane: 1, type: 'TPU', vendor: 'Acme_1', color: '#12ab34' };
  const closed = laneHtml(s, 0, true, new Set(), {}, '10', null);
  assert.ok(closed.includes('data-action="edit_open"') && !closed.includes('id="edit-vendor"'));
  const open = laneHtml(s, 0, true, new Set(), {}, '10', edit);
  assert.ok(!open.includes('data-action="edit_open"'));
  assert.ok(open.includes('<option selected>TPU</option>') && open.includes('value="Acme_1"') && open.includes('type="color" id="edit-color" value="#12ab34"'));
  assert.ok(open.includes('data-action="edit_save"') && open.includes('data-action="edit_cancel"') && open.includes(t('lane.edit_note')));
  assert.ok(!laneHtml(s, 0, true, new Set(), {}, '10', { ...edit, lane: 2 }).includes('id="edit-vendor"'));
  assert.ok(laneHtml(s, 0, true, new Set(['edit_spool:1']), {}, '10', edit).match(/edit_save[^>]*disabled/));
});

test('the numeric inputs ask for the numeric keypad', () => {
  assert.match(laneHtml(status(false), 0, true, new Set()), /id="move-mm" inputmode="numeric" pattern="\[0-9\]\*"/);
});

test('Save of an open Edit spool form follows the lock', () => {
  const edit = { lane: 1, type: 'PLA', vendor: 'Generic', color: '#ffffff' };
  const printing = { ...status(false), print_stats: { state: 'printing' } };
  printing.ace2k.lanes[0].mode = 'following';
  const html = laneHtml(printing, 0, true, new Set(), {}, '10', edit);
  assert.match(html, /edit_save[^>]*disabled/);
  assert.ok(html.includes(t('reason.in_use')));
  assert.match(laneHtml(status(false), 0, false, new Set(), {}, '10', edit), /edit_save[^>]*disabled/);
  assert.ok(!/edit_save[^>]*disabled/.test(laneHtml(status(false), 0, true, new Set(), {}, '10', edit)));
});

const advOf = (html) => (html.match(/<details id="adv"[\s\S]*?<\/details>/) || [''])[0];

test('Feed-forward sits in Advanced; Follow off is gone; Stop stays on the card', () => {
  const following = { ace2k: { lanes: [{ mode: 'following', insert: true, rest: true, ff_on: true, tag: { state: 'no_tag' } }] } };
  const html = laneHtml(following, 0, true, new Set(), { adv: true });
  const adv = advOf(html);
  const main = html.replace(adv, '');
  assert.ok(adv.includes('data-id="ff"') && adv.includes(t('action.ff_off')));
  assert.ok(!main.includes('data-id="ff"'));
  assert.ok(main.includes('data-id="stop"'));
  assert.ok(!html.includes('follow_off'));
  // an empty bay: Advanced holds only the toggle, no length input
  const empty = { ace2k: { lanes: [{ mode: 'idle', insert: false, rest: true, ff_on: false, tag: { state: 'no_tag' } }] } };
  const eadv = advOf(laneHtml(empty, 0, true, new Set(), { adv: true }));
  assert.ok(eadv.includes('data-id="ff"') && !eadv.includes('id="move-mm"'));
});

test('the tag row says what each state means', () => {
  const row = (tag) => laneHtml({ ace2k: { lanes: [{ mode: 'idle', insert: true, rest: true, ff_on: true, tag }] } }, 0, true, new Set());
  const text = (tag) => row(tag).match(new RegExp(`<span>${t('lane.tag')}</span><b>([^<]*)</b>`))[1];
  assert.equal(text({ state: 'pending' }), 'pending — read when the spool turns');
  assert.equal(text({ state: 'searching' }), 'reading…');
  assert.equal(text({ state: 'reading' }), 'reading…');
  assert.equal(text({ state: 'read', record: { brand: 'Bambu Lab', material: 'PLA' } }), 'read — Bambu Lab PLA');
  assert.equal(text({ state: 'read', record: { material: 'PLA' } }), 'read — PLA');
  assert.equal(text({ state: 'read', record: {} }), 'read');
  assert.equal(text({ state: 'no_tag' }), 'no tag');
  assert.equal(text({ state: 'unknown' }), '—');
  assert.equal(text(undefined), '—');
  assert.ok(!row({ state: 'pending' }).includes('data-id="read_tag"'));
  assert.ok(row({ state: 'read', record: {} }).includes('data-id="read_tag"') && row({ state: 'no_tag' }).includes('data-id="read_tag"'));
});

test('a locked Stop says how to stop the lane; the generic note stays for the others', () => {
  const s = { ace2k: { lanes: [{ mode: 'following', insert: true, rest: true, ff_on: true, tag: { state: 'no_tag' } }] }, print_stats: { state: 'printing' } };
  const html = laneHtml(s, 0, true, new Set());
  const stop = html.match(/<button[^>]*data-id="stop"[^>]*>/)[0];
  assert.ok(stop.includes(' disabled') && stop.includes(t('reason.stop_in_use')));
  assert.ok(html.includes(`<div class="note">${t('reason.stop_in_use')}</div>`));
  assert.ok(html.includes(`<div class="note">${t('reason.in_use')}</div>`), 'eject is locked too');
  const idle = laneHtml({ ace2k: s.ace2k }, 0, true, new Set());
  assert.ok(!idle.includes(t('reason.stop_in_use')) && !idle.match(/<button[^>]*data-id="stop"[^>]*disabled/));
});

test('Stop all is disabled with the print note when every lane is in use', () => {
  const base = { version: null, link: true, health: true, failing: null, history: null, online: true };
  const off = topbarHtml({ ...base, stopLanes: [] });
  assert.match(off, /data-action="stop_all" disabled/);
  assert.ok(off.includes(t('top.stop_all_print')));
  for (const stopLanes of [null, [2, 3]]) {
    const on = topbarHtml({ ...base, stopLanes });
    assert.ok(!/data-action="stop_all" disabled/.test(on) && !on.includes(t('top.stop_all_print')));
  }
});
