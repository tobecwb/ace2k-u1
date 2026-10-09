import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { CHIP_KINDS, ACTION_IDS, BUFFER_KINDS, REASONS, CONFIRM, FLAP_WHICH } from '../js/model.js';
import { FILTERS, SOURCES } from '../js/views/events.js';
import { DRYER_STATES, FAULTS } from '../js/views/dryer.js';
import { WINDOWS } from '../js/views/chart.js';

const root = fileURLToPath(new URL('..', import.meta.url));
const en = JSON.parse(readFileSync(join(root, 'i18n/en.json'), 'utf8'));

function sources(dir) {
  return readdirSync(dir).flatMap((name) => {
    const p = join(dir, name);
    return statSync(p).isDirectory() ? sources(p) : p.endsWith('.js') ? [p] : [];
  });
}
const code = [join(root, 'app.js'), ...sources(join(root, 'js'))].map((p) => readFileSync(p, 'utf8')).join('\n');
const literal = new Set([...code.matchAll(/\bt\(\s*'([a-z0-9_.]+)'/g)].map((m) => m[1]));

// Keys the literal scan cannot see: built from a variable, or chosen by a ternary / a map.
const dynamic = [
  ...CHIP_KINDS.map((k) => `chip.${k}`), // unit.js chipText
  ...ACTION_IDS.filter((a) => a !== 'ff').map((a) => `action.${a}`), 'action.ff_on', 'action.ff_off', // lane.js label
  ...BUFFER_KINDS.map((k) => `buffer.${k}`), // lane.js bufferOf
  ...DRYER_STATES.map((s) => `dryer.state.${s}`), // dryer.js strip chip
  ...FAULTS.map((f) => `dryer.fault.${f}`), // dryer.js faultText
  ...WINDOWS.map((w) => `chart.window_${w}h`), // chart.js window buttons
  ...FILTERS.filter((f) => !f.startsWith('lane')).map((f) => `events.filter.${f}`), // events.js filterLabel
  ...REASONS.map((r) => `reason.${r}`), // lane.js button title (laneActions reasons)
  ...FLAP_WHICH.map((w) => `dryer.flap.${w}`), // dryer.js flap names
  ...CONFIRM.map((a) => `action.confirm_${a}`), // app.js confirm before a lane action
  ...SOURCES.map((s) => `events.src.${s}`), // events.js sourceLabel
  'tag.pending', 'tag.reading', 'tag.read', 'tag.read_who', 'tag.no_tag', // lane.js tagText
  'reason.stop_in_use', 'reason.unavailable', 'top.stop_all_print', 'top.confirm_stop_all_print', 'top.confirm_stop_all', // lane.js / topbar.js / app.js (ternary keys)
  'toast.tag_rearmed', // app.js readTag
  'on', 'off', 'dryer.open', 'dryer.closed', 'dryer.open_panel', 'dryer.close_panel', // dryer.js / lane.js ternaries (no list in code)
];

test('the scan finds the literal keys', () => {
  assert.ok(literal.has('top.stop_all') && literal.has('dryer.fault.unknown'));
});

test('every used key exists', () => {
  for (const key of [...literal, ...dynamic]) assert.ok(Object.hasOwn(en, key), `missing ${key}`);
});

test('no unused key', () => {
  const used = new Set([...literal, ...dynamic]);
  const unused = Object.keys(en).filter((k) => !used.has(k));
  assert.deepEqual(unused, []);
});
