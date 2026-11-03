import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { t } from '../js/i18n.js';
import {
  sliceFor,
  chipOf,
  chipFor,
  sharedHeadsView,
  headOf,
  unitsOf,
  unitObj,
  isCurrent,
  parkedOf,
  sharedWith,
  spoolOf,
  editSave,
  editScript,
  gcodeFor,
  laneActions,
  CONFIRM,
  REASONS,
} from '../js/model.js';
import { chipText, laneAt } from '../js/lane-state.js';
import { LANE_ERRORS } from '../js/model.js';
import { html } from '../js/ui/html.js';
import { BayCard } from '../js/ui/BayCard.js';
import { renderHtml } from './ui-helpers.js';
import { filterEvents, transitionEvents, snapshot, liveSample, appendSample, mergeDay } from '../js/history.js';
import { loadEnglish, lane, twoHeadedUnits, elements } from './helpers.js';

loadEnglish();

// The bay card of the lane with this key, as the side panel draws it.
const bayCard = (status, key) => {
  const app = { status, online: true, klippy: 'ready', pending: new Set(), folds: {}, moveMm: '10', edit: null };
  return renderHtml(html`<${BayCard} app=${app} ctx=${{ app, t, run: () => {} }} at=${laneAt(app, key)} />`);
};

test('a lane waiting outside its shared head is parked', () => {
  const s = twoHeadedUnits();
  assert.equal(chipOf(sliceFor(s, 'a').ace2k.lanes[1], false, { parked: true }), 'parked');
  assert.equal(headOf(s, 1, 'a'), 0);
});

test("the demo status is the adapter's current form: a list of lanes per head", () => {
  const s = JSON.parse(readFileSync(new URL('../dev/demo-status.json', import.meta.url), 'utf8'));
  assert.ok(s.ace2k_u1.heads.every((h) => Array.isArray(h)));
  assert.ok(Array.isArray(s.ace2k_u1.current));
});

test("sliceFor: the unit's object as ace2k; unitObj names it", () => {
  const s = twoHeadedUnits();
  assert.equal(unitObj('a'), 'ace2k a');
  assert.equal(unitObj(null), 'ace2k');
  assert.equal(sliceFor(s, 'b').ace2k, s['ace2k b']);
  assert.equal(sliceFor(s, null).ace2k, s['ace2k a']); // no name: the first unit
  assert.equal(sliceFor(s, 'b').ace2k_u1, s.ace2k_u1);
  const one = { ace2k: { lanes: [] } };
  assert.equal(sliceFor(one, null).ace2k, one.ace2k);
  assert.deepEqual(unitsOf(s), ['ace2k a', 'ace2k b']);
});

test("headOf: the lane's head in the list form, for the unit asked or the slice's", () => {
  const s = twoHeadedUnits();
  assert.deepEqual(
    [0, 1, 2, 3].map((i) => headOf(s, i, 'a')),
    [0, 0, 1, 2],
  );
  assert.deepEqual(
    [0, 1, 2, 3].map((i) => headOf(s, i, 'b')),
    [3, null, null, null],
  );
  assert.deepEqual(
    [0, 1, 2, 3].map((i) => headOf(sliceFor(s, 'b'), i)),
    [3, null, null, null],
  );
  assert.deepEqual(
    [0, 1, 2, 3].map((i) => headOf(s, i)),
    [0, 0, 1, 2],
  ); // the first unit
  // a head left to the U1 (null) holds no lane
  const s2 = twoHeadedUnits();
  s2.ace2k_u1.heads[1] = null;
  assert.equal(headOf(s2, 2, 'a'), null);
});

test('isCurrent and parkedOf read ace2k_u1.current', () => {
  const s = twoHeadedUnits();
  assert.ok(isCurrent(s, 0, 'a', 0));
  assert.ok(!isCurrent(s, 0, 'a', 1));
  assert.ok(!isCurrent(s, 0, 'b', 0));
  const a = sliceFor(s, 'a');
  assert.deepEqual(
    [0, 1, 2, 3].map((i) => parkedOf(a, i)),
    [false, true, false, false],
  );
  assert.deepEqual(
    [0, 1, 2, 3].map((i) => parkedOf(sliceFor(s, 'b'), i)),
    [false, false, false, false],
  );
  // When each head has a single lane, no lane is parked, whatever current says.
  const single = twoHeadedUnits();
  single.ace2k_u1.current = [null, null, null, null];
  assert.ok(!parkedOf(sliceFor(single, 'a'), 2));
});

test('no Parked while the adapter is not hooked or the shared head has no current lane', () => {
  const off = twoHeadedUnits();
  off.ace2k_u1.hooked = false;
  off.ace2k_u1.current = [null, null, null, null];
  assert.deepEqual(
    [0, 1].map((i) => parkedOf(sliceFor(off, 'a'), i)),
    [false, false],
  );
  const offNamed = twoHeadedUnits();
  offNamed.ace2k_u1.hooked = false; // a stale current while not hooked is not read either
  assert.ok(!parkedOf(sliceFor(offNamed, 'a'), 1));
  const empty = twoHeadedUnits();
  empty.ace2k_u1.current[0] = null;
  assert.deepEqual(
    [0, 1].map((i) => parkedOf(sliceFor(empty, 'a'), i)),
    [false, false],
  );
  // The head sensor sees filament and no lane is named. The chip shows In head?, not Loaded.
  empty['filament_motion_sensor e0_filament'] = { filament_detected: true };
});

test("an empty lane's hint names it as the console does", () => {
  const s = twoHeadedUnits();
  s['ace2k a'].lanes[2] = lane({ insert: false });
  assert.ok(elements(bayCard(s, 'a:3'), 'div.note').some((n) => n.text === t('lane.empty_hint', { n: 'a:3' })));
  const one = { ace2k: { lanes: [lane(), lane(), lane({ insert: false }), lane()] } };
  assert.ok(elements(bayCard(one, '3'), 'div.note').some((n) => n.text === t('lane.empty_hint', { n: 3 })));
});

test('a lane of a shared head without a tag names its ACEK_SPOOL record before the head setting', () => {
  const s = twoHeadedUnits();
  s.print_task_config = {
    filament_exist: [true, true, true, true],
    filament_vendor: ['V', 'V', 'V', 'V'],
    filament_type: ['PLA', 'PLA', 'PLA', 'PLA'],
    filament_sub_type: ['', '', '', ''],
    filament_color_rgba: ['111111FF', '111111FF', '111111FF', '111111FF'],
  };
  s.ace2k_u1.spools = {
    'a:2': { vendor: 'Acme', type: 'PETG', color: '#1a2b3c' },
    'a:1': { vendor: 'Acme', type: 'ABS', color: '#ffffff' },
  };
  assert.deepEqual(spoolOf(sliceFor(s, 'a'), 1), {
    vendor: 'Acme',
    type: 'PETG',
    subtype: null,
    rgba: '1a2b3cff',
    fromTag: false,
  });
  assert.equal(spoolOf(sliceFor(s, 'a'), 0).type, 'ABS'); // the head's current lane also takes its record first
  assert.equal(spoolOf(sliceFor(s, 'a'), 2).type, 'PLA'); // with no record, the head's setting
  assert.equal(spoolOf(sliceFor(s, 'b'), 1), null); // b:2: no record, no head
  // a tag read wins over the record
  s['ace2k a'].lanes[1] = lane({
    tag: { state: 'read', record: { brand: 'Bambu', material: 'PLA', color_rgba: '00FF00FF' } },
  });
  assert.equal(spoolOf(sliceFor(s, 'a'), 1).type, 'PLA');
  // One unnamed unit. The record's key is -:n.
  const one = {
    ace2k: { lanes: [lane(), lane(), lane(), lane()] },
    ace2k_u1: {
      hooked: true,
      heads: [[{ unit: null, lane: 1 }], null, null, null],
      current: [null, null, null, null],
      spools: { '-:1': { vendor: 'Generic', type: 'TPU', color: '#000000' } },
    },
  };
  assert.equal(spoolOf(sliceFor(one, null), 0).type, 'TPU');
});

test('a shared head with no lane known in it lends its setting to none of its lanes', () => {
  // After a restart, the head's setting is the one of the last loaded lane (yellow).
  const s = twoHeadedUnits();
  s.ace2k_u1.current[0] = null;
  s.print_task_config = {
    filament_exist: [true, false, false, false],
    filament_vendor: ['Generic', 'NONE', 'NONE', 'NONE'],
    filament_type: ['PLA', 'NONE', 'NONE', 'NONE'],
    filament_sub_type: ['', '', '', ''],
    filament_color_rgba: ['FFFF00FF', 'FFFFFFFF', 'FFFFFFFF', 'FFFFFFFF'],
  };
  // A read tag belongs to the lane, not to the head's setting.
  s['ace2k a'].lanes[0] = lane({
    tag: { state: 'read', record: { brand: 'Bambu Lab', material: 'PLA', color_rgba: '000000FF' } },
  });
  assert.deepEqual(spoolOf(sliceFor(s, 'a'), 0), {
    vendor: 'Bambu Lab',
    type: 'PLA',
    subtype: null,
    rgba: '000000FF',
    fromTag: true,
  });
  // With no tag and no record, the spool is not known, and it is not the head's setting.
  assert.equal(spoolOf(sliceFor(s, 'a'), 1), null);
  // The lane in the head is known, but its setting was written for another lane. The spool is still not known.
  s.ace2k_u1.current[0] = { unit: 'a', lane: 2 };
  s.ace2k_u1.setting_for = [{ unit: 'a', lane: 4 }, null, null, null];
  assert.equal(spoolOf(sliceFor(s, 'a'), 1), null);
  // The setting written for a:2 belongs to a:2.
  s.ace2k_u1.setting_for[0] = { unit: 'a', lane: 2 };
  assert.equal(spoolOf(sliceFor(s, 'a'), 1).rgba, 'FFFF00FF');
});

test('chipOf: parked only for an idle lane with filament in its bay', () => {
  assert.equal(chipOf(lane(), true, { parked: true }), 'parked');
  assert.equal(chipOf(lane({ insert: false }), false, { parked: true }), 'empty');
  assert.equal(chipOf(lane({ mode: 'error' }), false, { parked: true }), 'error');
  assert.equal(chipOf(lane({ mode: 'feeding' }), false, { parked: true }), 'moving');
  assert.equal(chipOf(lane(), true), 'loaded');
  assert.equal(t('chip.parked'), 'Parked');
});

test('the lane card with several units: Lane a:2, its head and the lanes sharing it', () => {
  const s = twoHeadedUnits();
  const card = bayCard(s, 'a:2');
  assert.deepEqual(
    elements(card, 'div.route b').map((e) => e.text),
    ['a:2 → T0'],
  );
  assert.deepEqual(
    elements(card, 'div.route span.state.s-parked').map((e) => e.text),
    [t('chip.parked')],
  );
  assert.deepEqual(
    elements(card, 'p.shared').map((e) => e.text),
    [t('panel.shared_with', { lanes: 'a:1' })],
  );
  assert.equal(elements(card, 'p.shared svg.merge[aria-hidden=true]').length, 1);
  assert.deepEqual(sharedWith(sliceFor(s, 'a'), 0), ['a:2']);
  assert.deepEqual(sharedWith(sliceFor(s, 'a'), 2), []);
  assert.deepEqual(
    elements(bayCard(s, 'b:1'), 'div.route b').map((e) => e.text),
    ['b:1 → T3'],
  );
  assert.equal(elements(bayCard(s, 'b:1'), 'p.shared').length, 0);
});

test('one unit with a shared head: the lanes are named by number', () => {
  const s = {
    ace2k: { lanes: [lane(), lane(), lane(), lane()] },
    ace2k_u1: {
      hooked: true,
      heads: [
        [
          { unit: null, lane: 1 },
          { unit: null, lane: 2 },
        ],
        null,
        null,
        null,
      ],
      current: [{ unit: null, lane: 1 }, null, null, null],
    },
  };
  assert.deepEqual(sharedWith(sliceFor(s, null), 1), ['1']);
  assert.deepEqual(
    elements(bayCard(s, '2'), 'p.shared').map((e) => e.text),
    [t('panel.shared_with', { lanes: '1' })],
  );
  assert.equal(elements(bayCard(s, '2'), 'p.shared svg.merge[aria-hidden=true]').length, 1);
});

test("a parked lane does not take the head's setting as its spool", () => {
  const s = twoHeadedUnits();
  s.print_task_config = {
    filament_exist: [true, true, true, true],
    filament_vendor: ['V', 'V', 'V', 'V'],
    filament_type: ['PETG', 'PLA', 'PLA', 'PLA'],
    filament_sub_type: ['', '', '', ''],
    filament_color_rgba: ['111111FF', '111111FF', '111111FF', '111111FF'],
  };
  s.ace2k_u1.setting_for = [{ unit: 'a', lane: 1 }, null, null, null]; // written for a:1, in the head
  assert.equal(spoolOf(sliceFor(s, 'a'), 0).type, 'PETG');
  assert.equal(spoolOf(sliceFor(s, 'a'), 1), null);
});

test("the dryer commands carry the unit's name", () => {
  assert.equal(gcodeFor('dry', null, { temp: 50, minutes: 60 }, 'b'), 'ACEK_DRY TEMP=50 DURATION=60 UNIT=b');
  assert.equal(gcodeFor('dry_stop', null, {}, 'a'), 'ACEK_DRY_STOP UNIT=a');
});

test("the events: filtering by the unit's text", () => {
  const ev = [
    { t: 1, lvl: 'info', src: 'lane2', msg: 'lane a:2 spool inserted' },
    { t: 2, lvl: 'info', src: 'unit', msg: 'lane b:3 spool removed' },
    { t: 3, lvl: 'info', src: 'unit', msg: 'ace2k b: dryer idle → heating' },
    { t: 4, lvl: 'info', src: 'dryer', msg: 'ace2k a: dryer heating → cooldown' },
    { t: 5, lvl: 'info', src: 'unit', msg: 'ace2k_u1: head 1: swapped a:1 → a:2' },
  ];
  assert.deepEqual(
    filterEvents(ev, 'unit:b').map((e) => e.t),
    [3, 2],
  );
  assert.deepEqual(
    filterEvents(ev, 'unit:a').map((e) => e.t),
    [4, 1],
  );
});

test("Edit spool: ACEK_SPOOL always, the head's setting for a one-lane head or the head's current lane", () => {
  const s = twoHeadedUnits();
  assert.equal(
    gcodeFor('spool', 2, { type: 'PLA', rgb: 'ffffff', vendor: 'Generic' }, 'a'),
    'ACEK_SPOOL LANE=2 TYPE=PLA COLOR=ffffff VENDOR=Generic UNIT=a',
  );
  assert.equal(
    gcodeFor('spool', 2, { type: 'PLA', rgb: 'ffffff', vendor: 'Generic' }),
    'ACEK_SPOOL LANE=2 TYPE=PLA COLOR=ffffff VENDOR=Generic',
  );
  assert.throws(() => gcodeFor('spool', 2, { type: 'X;Y', rgb: 'ffffff', vendor: 'Generic' }));
  assert.throws(() => gcodeFor('spool', 5, { type: 'PLA', rgb: 'ffffff', vendor: 'Generic' }));
  const a = sliceFor(s, 'a');
  const edit = (n, head) => ({ lane: n, head, type: 'PETG', vendor: 'Generic', color: '#1a2b3c' });
  const parked = editSave(a, edit(2, 0), true);
  assert.equal(parked.ok, true);
  assert.equal(editScript(parked, 2, 'a'), 'ACEK_SPOOL LANE=2 TYPE=PETG COLOR=1a2b3c VENDOR=Generic UNIT=a');
  const current = editSave(a, edit(1, 0), true);
  assert.equal(
    editScript(current, 1, 'a'),
    "ACEK_SPOOL LANE=1 TYPE=PETG COLOR=1a2b3c VENDOR=Generic UNIT=a\nSET_PRINT_FILAMENT_CONFIG CONFIG_EXTRUDER=0 VENDOR=Generic FILAMENT_TYPE=PETG FILAMENT_SUBTYPE='' FILAMENT_COLOR_RGBA=1A2B3CFF FORCE=1",
  );
  const own = editSave(a, edit(3, 1), true);
  assert.ok(
    editScript(own, 3, 'a').endsWith(
      "\nSET_PRINT_FILAMENT_CONFIG CONFIG_EXTRUDER=1 VENDOR=Generic FILAMENT_TYPE=PETG FILAMENT_SUBTYPE='' FILAMENT_COLOR_RGBA=1A2B3CFF FORCE=1",
    ),
  );
  // one unnamed unit: no UNIT=
  const one = {
    ace2k: { lanes: [lane(), lane(), lane(), lane()] },
    ace2k_u1: {
      hooked: true,
      heads: [[{ unit: null, lane: 1 }], [{ unit: null, lane: 2 }], null, null],
      current: [null, null, null, null],
    },
  };
  assert.equal(
    editScript(editSave(sliceFor(one, null), edit(2, 1), true), 2, null),
    "ACEK_SPOOL LANE=2 TYPE=PETG COLOR=1a2b3c VENDOR=Generic\nSET_PRINT_FILAMENT_CONFIG CONFIG_EXTRUDER=1 VENDOR=Generic FILAMENT_TYPE=PETG FILAMENT_SUBTYPE='' FILAMENT_COLOR_RGBA=1A2B3CFF FORCE=1",
  );
});

test("the live transitions with several units read as the recorder's", () => {
  const st = (dryer, mode = 'idle') => ({
    dryer: { state: dryer },
    lanes: [lane(), lane(), lane({ mode, error: mode === 'error' ? 'stuck' : null }), lane()],
  });
  const b = transitionEvents(snapshot(st('idle')), st('heating', 'error'), 10, 'b', false);
  assert.deepEqual(
    b.map((e) => [e.src, e.msg]),
    [
      ['unit', 'ace2k b: dryer idle → heating'],
      ['unit', 'lane b:3 error: stuck'],
    ],
  );
  const a = transitionEvents(snapshot(st('heating')), st('fault', 'error'), 10, 'a', true);
  assert.deepEqual(
    a.map((e) => [e.src, e.msg]),
    [
      ['dryer', 'ace2k a: dryer fault: ?'],
      ['lane3', 'lane a:3 error: stuck'],
    ],
  );
  // One unit: the texts name no unit.
  assert.deepEqual(
    transitionEvents(snapshot(st('idle')), st('heating', 'error'), 10).map((e) => e.msg),
    ['dryer idle → heating', 'lane 3 error: stuck'],
  );
});

test('the live samples with several units carry their unit, and keep their own pace', () => {
  assert.equal(liveSample({ chamber: 20 }, 100).u, undefined);
  assert.equal(liveSample({ chamber: 20 }, 100, 'b').u, 'b');
  let samples = appendSample([], liveSample({}, 100, 'a'));
  samples = appendSample(samples, liveSample({}, 101, 'b'));
  samples = appendSample(samples, liveSample({}, 102, 'a'));
  assert.deepEqual(
    samples.map((x) => [x.t, x.u]),
    [
      [100, 'a'],
      [101, 'b'],
    ],
  );
  // A day merge keeps two units' samples in the same second.
  const day = {
    samples: [
      { t: 10, k: 's', u: 'a' },
      { t: 10, k: 's', u: 'b' },
    ],
    events: [],
  };
  assert.equal(mergeDay({ samples: [], events: [] }, day).samples.length, 2);
  assert.equal(mergeDay(mergeDay({ samples: [], events: [] }, day), day).samples.length, 2);
});

test("a lane alone on its head shows the U1's head setting before its ACEK_SPOOL record, as before the records", () => {
  const s = twoHeadedUnits();
  s.print_task_config = {
    filament_exist: [true, true, true, true],
    filament_vendor: ['V', 'V', 'V', 'V'],
    filament_type: ['PLA', 'PETG', 'PLA', 'PLA'],
    filament_sub_type: ['', '', '', ''],
    filament_color_rgba: ['111111FF', '222222FF', '111111FF', '111111FF'],
  };
  s.ace2k_u1.spools = {
    'a:3': { vendor: 'Acme', type: 'ABS', color: '#ffffff' },
    'a:1': { vendor: 'Acme', type: 'ABS', color: '#ffffff' },
  };
  // a:3 alone feeds head 2. Its setting wins, and the record is only the fallback.
  assert.deepEqual(spoolOf(sliceFor(s, 'a'), 2), {
    vendor: 'V',
    type: 'PETG',
    subtype: null,
    rgba: '222222FF',
    fromTag: false,
  });
  // a:1 shares head 1, so its record comes first.
  assert.equal(spoolOf(sliceFor(s, 'a'), 0).type, 'ABS');
  // Without the setting of head 2, the record is used.
  s.print_task_config.filament_exist[1] = false;
  assert.equal(spoolOf(sliceFor(s, 'a'), 2).type, 'ABS');
});

const unknownHead = () => {
  const s = twoHeadedUnits();
  s['filament_motion_sensor e0_filament'] = { filament_detected: true };
  s.ace2k_u1.current[0] = null;
  return s;
};

test('a head several lanes feed has one line: its lane, or "lane unknown" with the probe button', () => {
  // The lane in the head is known. The line names it, with no button.
  const known = twoHeadedUnits();
  known['filament_motion_sensor e0_filament'] = { filament_detected: true };
  assert.deepEqual(sharedHeadsView(known, true), [
    { head: 0, lanes: ['a:1', 'a:2'], current: 'a:1', empty: false, unknown: false, probe: null, unit: 'a' },
  ]);
  // current[h] is null with the sensor set. The lane is unknown, and the button is on the line.
  const s = unknownHead();
  const [h] = sharedHeadsView(s, true);
  assert.deepEqual([h.unknown, h.current, h.probe], [true, null, { enabled: true, reason: null }]);
  // The sensor is empty and there is no current lane. The head is empty, with no button.
  const empty = twoHeadedUnits();
  empty.ace2k_u1.current[0] = null;
  // With no sensor reading in the status there is no line at all, never "empty".
  assert.deepEqual(sharedHeadsView(empty, true), []);
  empty['filament_motion_sensor e0_filament'] = { filament_detected: false };
  assert.equal(sharedHeadsView(empty, true)[0].unknown, false);
  // Not hooked, the adapter knows nothing, so there is no line.
  const unhooked = unknownHead();
  unhooked.ace2k_u1.hooked = false;
  assert.deepEqual(sharedHeadsView(unhooked, true), []);
});

// The adapter keeps the head's lane after the screen unloads the head. The sensor then reads it empty,
// and the line still names the lane, as empty.
test('a head line names its lane as empty while the head sensor reads no filament', () => {
  const unloaded = twoHeadedUnits();
  unloaded['filament_motion_sensor e0_filament'] = { filament_detected: false };
  const [h] = sharedHeadsView(unloaded, true);
  assert.deepEqual([h.current, h.empty, h.unknown, h.probe], ['a:1', true, false, null]);
  // With no sensor reading, the page cannot tell, so the line names the lane alone.
  const unread = twoHeadedUnits();
  assert.equal(sharedHeadsView(unread, true)[0].empty, false);
});

test('one unit: the shared head names its lane by number, and its lanes say "In head?" while it is unknown', () => {
  const one = {
    ace2k: { lanes: [lane({ insert: true }), lane({ insert: true }), lane(), lane()] },
    ace2k_u1: {
      hooked: true,
      heads: [
        [
          { unit: null, lane: 1 },
          { unit: null, lane: 2 },
        ],
        [{ unit: null, lane: 3 }],
        [{ unit: null, lane: 4 }],
        null,
      ],
      current: [null, null, null, null],
    },
    'filament_motion_sensor e0_filament': { filament_detected: true },
  };
  const page = sliceFor(one, null);
  assert.deepEqual(
    sharedHeadsView(page, true).map((h) => [h.head, h.lanes, h.current, h.unknown]),
    [[0, ['1', '2'], null, true]],
  );
  assert.deepEqual([chipFor(page, 0), chipFor(page, 1)], ['unknown', 'unknown']);
  one.ace2k_u1.current[0] = { unit: null, lane: 2 };
  assert.equal(sharedHeadsView(sliceFor(one, null), true)[0].current, '2');
});

test("the head's probe is disabled offline and during a print; the lane card does not offer it", () => {
  const s = unknownHead();
  assert.deepEqual(sharedHeadsView(s, false)[0].probe, { enabled: false, reason: 'offline' });
  for (const state of ['printing', 'paused']) {
    const p = { ...s, print_stats: { state } };
    assert.deepEqual(sharedHeadsView(p, true)[0].probe, { enabled: false, reason: 'print_on' }, state);
  }
  assert.ok(CONFIRM.includes('probe_head') && REASONS.includes('print_on'));
  // the lane card no longer offers it
  assert.ok(!laneActions(sliceFor(s, 'a'), 1, true).some((a) => a.id === 'probe_head'));
  assert.equal(elements(bayCard(s, 'a:2'), 'button[data-id=probe_head]').length, 0);
  // The G-code carries the head's number, and UNIT= with a named unit. A head out of range is refused.
  assert.equal(gcodeFor('probe_head', null, { head: 0 }, 'a'), 'ACEK_HEAD_PROBE HEAD=1 UNIT=a');
  assert.equal(gcodeFor('probe_head', null, { head: 3 }), 'ACEK_HEAD_PROBE HEAD=4');
  assert.throws(
    () => gcodeFor('probe_head', null, { head: null }),
    (e) => e.key === 'refuse.head',
  );
  assert.ok(t('action.confirm_probe_head', { head: 'T0' }).startsWith('Find which lane is in T0?'));
});

test('the probe waits while a lane of its head moves; Load into the head points at the probe while unknown', () => {
  const s = unknownHead();
  s['ace2k a'].lanes[1] = lane({ insert: true, mode: 'unloading' });
  assert.deepEqual(sharedHeadsView(s, true)[0].probe, { enabled: false, reason: 'head_moving' });
  // Printing comes first.
  const p = { ...s, print_stats: { state: 'printing' } };
  assert.equal(sharedHeadsView(p, true)[0].probe.reason, 'print_on');
  assert.equal(t('reason.head_moving', { head: 'T0' }), "one of T0's lanes is moving: wait for it to end");
  // The lane in the head is unknown: Load into T0 says to find it, not to unload it.
  const load = (st) => laneActions(sliceFor(st, 'a'), 1, true).find((a) => a.id === 'load_head');
  const u = unknownHead();
  u['gcode_macro AUTO_FEEDING_BATCH'] = { doing: false };
  assert.deepEqual([load(u).enabled, load(u).reason], [false, 'head_unknown']);
  assert.equal(
    t('reason.head_unknown', { head: 'T0' }),
    'T0 holds a filament the page cannot name: Find the lane in T0 first',
  );
  // With a lane known, the full head is the reason.
  u.ace2k_u1.current[0] = { unit: 'a', lane: 1 };
  assert.equal(load(u).reason, 'head_loaded');
  assert.equal(sharedHeadsView(u, true)[0].probe, null);
  // A head load in progress comes before the head's own reasons.
  const b = unknownHead();
  b['gcode_macro AUTO_FEEDING_BATCH'] = { doing: true };
  assert.equal(load(b).reason, 'batch_open');
  // A following lane is at rest: the probe stops the follow itself.
  const f = unknownHead();
  f['ace2k a'].lanes[1] = lane({ insert: true, mode: 'following' });
  assert.deepEqual(sharedHeadsView(f, true)[0].probe, { enabled: true, reason: null });
});

test('the probe waits for a cold head, as the adapter publishes it', () => {
  const s = unknownHead();
  s.ace2k_u1.probe_cold = [true, null, null, null];
  s.ace2k_u1.probe_max_temp = 60;
  assert.deepEqual(sharedHeadsView(s, true)[0].probe, { enabled: true, reason: null });
  s.ace2k_u1.probe_cold[0] = false;
  assert.deepEqual(sharedHeadsView(s, true)[0].probe, { enabled: false, reason: 'head_hot' });
  // Unread (null) or not published: the page leaves it to the adapter's own refusal.
  s.ace2k_u1.probe_cold[0] = null;
  assert.equal(sharedHeadsView(s, true)[0].probe.enabled, true);
  assert.equal(t('reason.head_hot', { head: 'T0', limit: 60 }), 'T0 is hot: let it cool below 60 °C');
});

test('a shared head whose lane is unknown: its lanes with a spool say "In head?", not Loaded', () => {
  const s = unknownHead();
  s['ace2k a'].lanes[0] = lane({ insert: true });
  s['ace2k a'].lanes[1] = lane({ insert: true });
  const a = sliceFor(s, 'a');
  assert.deepEqual([chipFor(a, 0), chipFor(a, 1)], ['unknown', 'unknown']);
  assert.equal(t('chip.unknown'), 'In head?');
  // The lane that the adapter names shows Loaded.
  const known = twoHeadedUnits();
  known['filament_motion_sensor e0_filament'] = { filament_detected: true };
  known['ace2k a'].lanes[0] = lane({ insert: true });
  assert.equal(chipFor(sliceFor(known, 'a'), 0), 'loaded');
  // With the sensor empty, the lane waits in its bay. With the adapter not hooked, the chip is the plain one.
  const bay = twoHeadedUnits();
  bay.ace2k_u1.current[0] = null;
  bay['ace2k a'].lanes[1].insert = true;
  assert.equal(chipFor(sliceFor(bay, 'a'), 1), 'in_bay');
  const unhooked = unknownHead();
  unhooked.ace2k_u1.hooked = false;
  unhooked['ace2k a'].lanes[1].insert = true;
  assert.equal(chipFor(sliceFor(unhooked, 'a'), 1), 'loaded');
  assert.equal(chipOf({ mode: 'idle', insert: true }, true, { unknown: true }), 'unknown');
  assert.equal(chipOf({ mode: 'idle', insert: false }, true, { unknown: true }), 'empty');
  assert.equal(chipOf({ mode: 'idle', insert: true }, true, { unknown: true, parked: true }), 'parked');
});

test('the error chip says each known error in words and an unknown error as Error', () => {
  const errored = (error) => lane({ mode: 'error', error });
  for (const e of LANE_ERRORS) assert.doesNotMatch(chipText(errored(e), 'error'), /_/, e);
  assert.equal(chipText(errored('motor_stalled'), 'error'), 'Motor stalled');
  assert.equal(chipText(errored('some_new_error'), 'error'), 'Error');
  assert.equal(chipText(errored(null), 'error'), 'Error');
  assert.equal(chipText(lane(), 'loaded'), 'Loaded');
});
