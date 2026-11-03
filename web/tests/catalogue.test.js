import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  CHIP_KINDS,
  ACTION_IDS,
  BUFFER_KINDS,
  REASONS,
  CONFIRM,
  FLAP_WHICH,
  DRYER_MODES,
  STOP_ALL_KEYS,
  REFUSALS,
} from '../js/model.js';
import { DRYER_STATES, FAULTS } from '../js/dryer-form.js';
import { WINDOWS, FILTERS } from '../js/ui/HistoryView.js';
import { KLIPPY_STATES } from '../js/conn.js';
import { MARK_WORDS } from '../js/diagram-model.js';
import { TABS } from '../js/state.js';
import { LANE_MODES, EVENT_KINDS } from '../js/lane-state.js';
import { LANE_ERRORS } from '../js/model.js';
import { HEALTH_NAMES } from '../js/health.js';

const root = fileURLToPath(new URL('..', import.meta.url));
const en = JSON.parse(readFileSync(join(root, 'i18n/en.json'), 'utf8'));

function sources(dir) {
  return readdirSync(dir).flatMap((name) => {
    const p = join(dir, name);
    return statSync(p).isDirectory() ? sources(p) : p.endsWith('.js') ? [p] : [];
  });
}
const code = [join(root, 'main.js'), ...sources(join(root, 'js'))].map((p) => readFileSync(p, 'utf8')).join('\n');
const literal = new Set([...code.matchAll(/\bt\(\s*'([a-z0-9_.]+)'/g)].map((m) => m[1]));
// index.html names each section by a catalogue key (data-label). main.js sets the aria-label from it.
const page = readFileSync(join(root, 'index.html'), 'utf8');
for (const m of page.matchAll(/\bdata-label="([a-z0-9_.]+)"/g)) literal.add(m[1]);

// Keys that the literal scan cannot see, because they are built from a variable or chosen by a ternary or a map.
const dynamic = [
  ...CHIP_KINDS.map((k) => `chip.${k}`), // lane-state.js chipText
  ...ACTION_IDS.filter((a) => a !== 'ff').map((a) => `action.${a}`),
  'action.ff_on',
  'action.ff_off', // BayCard.js / Sensors aria-label
  ...BUFFER_KINDS.map((k) => `buffer.${k}`), // BayCard.js gauge
  ...DRYER_STATES.map((s) => `dryer.state.${s}`), // dryer-form.js stateText
  ...FAULTS.map((f) => `dryer.fault.${f}`), // dryer-form.js faultText
  ...WINDOWS.map((w) => `chart.window_${w}h`), // HistoryView.js window buttons
  ...FILTERS.filter((f) => !f.startsWith('lane')).map((f) => `events.filter.${f}`), // HistoryView.js filters
  ...REASONS.map((r) => `reason.${r}`), // BayCard.js button title (laneActions reasons)
  ...FLAP_WHICH.map((w) => `dryer.flap.${w}`), // DryerView.js flap names
  ...CONFIRM.map((a) => `action.confirm_${a}`), // actions.js confirm before a lane action
  ...EVENT_KINDS.map((k) => `lane.kind.${k}`), // lane-state.js eventText
  ...LANE_MODES.map((m) => `lane.mode.${m}`),
  ...LANE_ERRORS.map((e) => `lane.error_short.${e}`), // Diagram.js errorShort
  ...LANE_ERRORS.map((e) => `lane.error.${e}`), // lane-state.js chipText
  'lane.kind.unknown',
  'lane.mode.unknown',
  ...HEALTH_NAMES.map((n) => `health.${n}`), // health.js healthWords
  'health.unknown',
  'dryer.state.unknown',
  ...[
    'spool_inserted',
    'spool_removed',
    'error_cleared',
    'no_tag',
    'tag_read',
    'tag_read_who',
    'error',
    'event',
    'event_mm',
  ].map((k) => `events.lane_${k}`), // log-text.js recorderWords
  'events.dryer_change',
  ...REFUSALS.map((r) => `refuse.${r}`), // model.js RefusedError
  'tag.pending',
  'tag.reading',
  'tag.read',
  'tag.read_who',
  'tag.no_tag',
  'tag.forgotten', // lane-state.js tagText
  'reason.stop_in_use',
  'reason.unavailable',
  'top.stop_all_print', // model.js stopAllWords (ternary keys)
  ...STOP_ALL_KEYS, // model.js stopAllWords: the Stop all button and its confirmation
  'toast.tag_rearmed', // actions.js readTag
  'conn.demo',
  'conn.demo_missing',
  'conn.offline',
  ...KLIPPY_STATES.map((s) => `klippy.state.${s}`), // conn.js bannerView (its key, t() in main.js)
  'klippy.unknown',
  'klippy.checking',
  'conn.page_error',
  'app.title',
  'app.title_unit', // state.js titleView (its key, t() in main.js)
  'dryer.open',
  'dryer.closed',
  ...DRYER_MODES.map((m) => `dryer.mode_${m}`), // DryerView.js mode buttons
  ...[1, 2, 3, 4, 5, 6].map((k) => `dryer.warn_${k}`), // DryerView.js warning items
  'dryer.warn_4_unknown', // DryerView.js warnKey, before the host reports the count
  'top.link_ok',
  'top.link_down',
  'lane.spool_unknown',
  'lane.no_spool_in_bay',
  'dryer.fans_running',
  'dryer.fans_stopped', // DryerView.js ternaries
  'top.theme_system',
  'top.theme_light',
  'top.theme_dark', // TopBar.js ThemeButton: the key is built from the mode
  ...['top.offline', 'top.idle', 'top.printing', 'top.printing_bare', 'top.paused', 'top.paused_only'], // TopBar.js pillView
  ...TABS.map((tab) => `tab.${tab}`), // TopBar.js tabs and App.js placeholder: the key is built from the tab
  ...Object.values(MARK_WORDS), // Diagram.js / MobileLists.js: the printing view's bay marks
];

test('the scan finds the literal keys', () => {
  assert.ok(literal.has('top.status') && literal.has('dryer.fault.unknown'));
});

test('every used key exists', () => {
  for (const key of [...literal, ...dynamic]) assert.ok(Object.hasOwn(en, key), `missing ${key}`);
});

test('no unused key', () => {
  const used = new Set([...literal, ...dynamic]);
  const unused = Object.keys(en).filter((k) => !used.has(k));
  assert.deepEqual(unused, []);
});
