import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  CHIP_KINDS, ACTION_IDS, BUFFER_KINDS, REASONS, CONFIRM,
  laneInUse, chipOf, spoolOf, bufferOf, laneActions, stopAllLanes, allowedNow, gcodeFor, FILAMENT_TYPES, editPrefill, editAction, dryerView, topbarView, colorOf,
} from '../js/model.js';

const lane = (over = {}) => ({ mode: 'idle', insert: true, rest: true, pushed: false, error: null,
  encoder_mm: 0, ff_on: true, last_event: null, tag: { state: 'no_tag', record: null }, ...over });

function status({ print = 'standby', lanes = [lane(), lane(), lane(), lane()], heads = [false, false, false, false], ptc } = {}) {
  const s = {
    ace2k: { version: '0.11.0', link_proven: true, health: { ok: true }, lanes,
      chamber: 27, humidity: 52, ptc_left: 29.1, ptc_right: 28.7,
      dryer: { state: 'idle', target: null, remaining: null, fans: { left: false, right: false },
        flaps: { bottom: 'closed', rear: 'closed' }, fault: null, notices: [] } },
    print_stats: { state: print },
    print_task_config: ptc || { filament_exist: [false, false, false, false],
      filament_vendor: ['NONE', 'NONE', 'NONE', 'NONE'], filament_type: ['NONE', 'NONE', 'NONE', 'NONE'],
      filament_sub_type: ['NONE', 'NONE', 'NONE', 'NONE'], filament_color_rgba: ['FFFFFFFF', 'FFFFFFFF', 'FFFFFFFF', 'FFFFFFFF'] },
  };
  heads.forEach((d, i) => { s[`filament_motion_sensor e${i}_filament`] = { filament_detected: d, enabled: true }; });
  return s;
}

test('laneInUse over print state × mode × head sensor', () => {
  for (const print of ['standby', 'printing', 'paused', 'complete']) {
    for (const mode of ['idle', 'following', 'error', 'feeding']) {
      for (const head of [false, true]) {
        const lanes = [lane({ mode }), lane(), lane(), lane()];
        const s = status({ print, lanes, heads: [head, false, false, false] });
        const expected = ['printing', 'paused'].includes(print) && (mode !== 'idle' || head);
        assert.equal(laneInUse(s, 0), expected, `${print} ${mode} ${head}`);
      }
    }
  }
});

test('chipOf precedence', () => {
  assert.equal(chipOf(lane({ mode: 'error' }), true), 'error');
  assert.equal(chipOf(lane({ mode: 'following' }), true), 'following');
  for (const m of ['feeding', 'rolling_back', 'unloading', 'loading', 'assisting', 'assisting_back']) {
    assert.equal(chipOf(lane({ mode: m }), false), 'moving');
  }
  assert.equal(chipOf(lane({ insert: false }), false), 'empty');
  assert.equal(chipOf(lane(), true), 'loaded');
  assert.equal(chipOf(lane(), false), 'in_bay');
  assert.equal(chipOf(undefined, false), 'empty');
  for (const k of ['error', 'following', 'moving', 'empty', 'loaded', 'in_bay']) assert.ok(CHIP_KINDS.includes(k));
});

test('spoolOf: head setting first, then a read tag, else null', () => {
  const ptc = { filament_exist: [true, false, false, false], filament_vendor: ['Generic', 'NONE', 'NONE', 'NONE'],
    filament_type: ['PLA', 'NONE', 'NONE', 'NONE'], filament_sub_type: ['', 'NONE', 'NONE', 'NONE'],
    filament_color_rgba: ['1E88E5FF', 'FFFFFFFF', 'FFFFFFFF', 'FFFFFFFF'] };
  const lanes = [lane(), lane({ tag: { state: 'read', record: { brand: 'Bambu Lab', material: 'PETG', color_rgba: '000000FF' } } }), lane(), lane()];
  const s = status({ ptc, lanes });
  assert.deepEqual(spoolOf(s, 0), { vendor: 'Generic', type: 'PLA', subtype: null, rgba: '1E88E5FF', fromTag: false });
  assert.deepEqual(spoolOf(s, 1), { vendor: 'Bambu Lab', type: 'PETG', subtype: null, rgba: '000000FF', fromTag: true });
  assert.equal(spoolOf(s, 2), null);
  assert.equal(colorOf({ rgba: '1E88E5FF' }), '#1e88e5');
  assert.equal(colorOf(null), null);
});

test('bufferOf', () => {
  assert.equal(bufferOf(lane({ rest: true })), 'at_rest');
  assert.equal(bufferOf(lane({ rest: false, pushed: true })), 'pushed');
  assert.equal(bufferOf(lane({ rest: false, pushed: false })), 'taut');
  assert.deepEqual(BUFFER_KINDS, ['at_rest', 'pushed', 'taut']);
});

const ids = (actions) => actions.map((a) => a.id);
const byId = (actions, id) => actions.find((a) => a.id === id);

test('laneActions visibility', () => {
  assert.deepEqual(ids(laneActions(status(), 0, true)), ['load', 'eject', 'read_tag', 'ff', 'feed', 'rollback', 'edit_spool']);
  const following = status({ lanes: [lane({ mode: 'following' }), lane(), lane(), lane()] });
  assert.deepEqual(ids(laneActions(following, 0, true)), ['eject', 'stop', 'read_tag', 'ff', 'edit_spool']);
  const err = status({ lanes: [lane({ mode: 'error' }), lane(), lane(), lane()] });
  assert.deepEqual(ids(laneActions(err, 0, true)), ['eject', 'clear', 'read_tag', 'ff', 'edit_spool']);
  const empty = status({ lanes: [lane({ insert: false }), lane(), lane(), lane()] });
  assert.deepEqual(ids(laneActions(empty, 0, true)), ['ff']);
  const tagged = status({ lanes: [lane({ tag: { state: 'read', record: {} } }), lane(), lane(), lane()] });
  assert.ok(ids(laneActions(tagged, 0, true)).includes('read_tag'));
  assert.ok(!ids(laneActions(tagged, 0, true)).includes('edit_spool'));
  // Read tag is the forget: only for a tag that is read or absent; a lane waiting to be read has no button
  for (const state of ['pending', 'searching', 'reading', 'unknown']) {
    const waiting = status({ lanes: [lane({ tag: { state } }), lane(), lane(), lane()] });
    assert.ok(!ids(laneActions(waiting, 0, true)).includes('read_tag'), state);
  }
  assert.ok(!ACTION_IDS.includes('forget_tag'));
  for (const id of ['load', 'eject', 'stop', 'clear', 'read_tag', 'ff', 'feed', 'rollback', 'edit_spool']) {
    assert.ok(ACTION_IDS.includes(id));
  }
  // no Follow off on the page: the follow switched off on a loaded head grinds the filament
  assert.ok(!ACTION_IDS.includes('follow_off'));
});

test('laneActions lock during a print and offline', () => {
  const s = status({ print: 'printing', lanes: [lane({ mode: 'following' }), lane(), lane(), lane()] });
  const acts = laneActions(s, 0, true);
  for (const id of ['eject', 'ff']) {
    assert.deepEqual([byId(acts, id).enabled, byId(acts, id).reason], [false, 'in_use'], id);
  }
  assert.deepEqual([byId(acts, 'stop').enabled, byId(acts, 'stop').reason], [false, 'in_use'], 'stop');
  assert.equal(byId(acts, 'read_tag').enabled, true, 'read_tag');
  assert.ok(laneActions(s, 1, true).every((a) => a.enabled), 'lane 2 not in use');
  assert.ok(laneActions(s, 1, false).every((a) => !a.enabled && a.reason === 'offline'));
  assert.equal(byId(acts, 'eject').confirm, true);
  assert.deepEqual(CONFIRM, ['eject']);
});

test('gcodeFor', () => {
  const table = [
    [['load', 2], 'ACE_LOAD LANE=2 WAIT=0'],
    [['eject', 2], 'ACE_EJECT LANE=2 WAIT=0'],
    [['stop', 2], 'ACE_STOP LANE=2'],
    [['stop_all'], 'ACE_STOP'],
    [['clear', 2], 'ACE_CLEAR LANE=2'],
    [['read_tag', 2], 'ACE_RFID_FORGET LANE=2'],
    [['ff_on', 2], 'ACE_FEED_FORWARD LANE=2 ON=1'],
    [['ff_off', 2], 'ACE_FEED_FORWARD LANE=2 OFF=1'],
    [['feed', 2, { length: 10 }], 'ACE_FEED LANE=2 LENGTH=10 SPEED=20 WAIT=0'],
    [['rollback', 2, { length: 25.5 }], 'ACE_ROLLBACK LANE=2 LENGTH=25.5 SPEED=20 WAIT=0'],
    [['dry', null, { temp: 55, minutes: 240 }], 'ACE_DRY TEMP=55 DURATION=240'],
    [['dry_stop'], 'ACE_DRY_STOP'],
    [['dry_clear'], 'ACE_DRY_CLEAR'],
    [['dry_log'], 'ACE_DRYER_LOG'],
    [['fans_on', null, { seconds: 60 }], 'ACE_FAN ON=1 SECONDS=60'],
    [['fans_off'], 'ACE_FAN ON=0'],
    [['flap', null, { which: 'rear', open: true }], 'ACE_FLAP WHICH=rear OPEN=1'],
    [['flap', null, { which: 'bottom', open: false }], 'ACE_FLAP WHICH=bottom OPEN=0'],
  ];
  for (const [[id, n, args], want] of table) assert.equal(gcodeFor(id, n, args), want, id);
  assert.throws(() => gcodeFor('nope', 1));
});

test('dryerView and topbarView', () => {
  const s = status();
  s.ace2k.dryer = { ...s.ace2k.dryer, state: 'heating', target: 55, remaining: 151, fans: { left: true, right: true } };
  const d = dryerView(s);
  assert.equal(d.state, 'heating');
  assert.equal(d.manual, false);
  assert.deepEqual([d.target, d.remaining, d.outletL, d.outletR, d.chamber, d.humidity], [55, 151, 29.1, 28.7, 27, 52]);
  assert.equal(d.fansOn, true);
  assert.equal(dryerView(status()).manual, true);
  assert.deepEqual(topbarView(s, true), { version: '0.11.0', link: true, health: true, failing: null, history: null, online: true, stopLanes: null });
  s.ace2k_u1_history = { error: 'No space left on device' };
  assert.equal(topbarView(s, true).history, 'No space left on device');
});

test('gcodeFor refuses malformed or injected arguments', () => {
  assert.throws(() => gcodeFor('feed', 2, { length: NaN }));
  assert.throws(() => gcodeFor('feed', 2, { length: '10\nACE_STOP' }));
  assert.throws(() => gcodeFor('feed', 2, { length: 0 }));
  assert.throws(() => gcodeFor('feed', 2, { length: -5 }));
  assert.throws(() => gcodeFor('rollback', 2, { length: Infinity }));
  assert.throws(() => gcodeFor('dry', null, { temp: NaN, minutes: 240 }));
  assert.throws(() => gcodeFor('dry', null, { temp: 55, minutes: '240 ACE_STOP' }));
  assert.throws(() => gcodeFor('fans_on', null, { seconds: 0 }));
  assert.throws(() => gcodeFor('flap', null, { which: 'x', open: true }));
  assert.throws(() => gcodeFor('load', 5));
  assert.throws(() => gcodeFor('load', 0));
  assert.throws(() => gcodeFor('load', 1.5));
  assert.throws(() => gcodeFor('stop', null));
});

test('laneActions lock on an idle lane with its head sensor during a print', () => {
  const s = status({ print: 'printing', heads: [true, false, false, false] });
  const acts = laneActions(s, 0, true);
  for (const id of ['load', 'eject', 'feed', 'rollback']) {
    assert.deepEqual([byId(acts, id).enabled, byId(acts, id).reason], [false, 'in_use'], id);
  }
  assert.equal(byId(acts, 'read_tag').enabled, true, 'read_tag');
});

test('laneActions on an error lane during a print: clear and read_tag stay, eject locked, no stop', () => {
  const s = status({ print: 'printing', lanes: [lane({ mode: 'error' }), lane(), lane(), lane()] });
  const acts = laneActions(s, 0, true);
  assert.equal(byId(acts, 'clear').enabled, true, 'clear');
  assert.equal(byId(acts, 'read_tag').enabled, true, 'read_tag');
  assert.deepEqual([byId(acts, 'eject').enabled, byId(acts, 'eject').reason], [false, 'in_use']);
  assert.equal(byId(acts, 'stop'), undefined, 'no stop on an error lane');
});

test('laneActions: Eject works with a loaded head outside a print, locked only in use or offline', () => {
  const s = status({ heads: [true, false, false, false] });
  const acts = laneActions(s, 0, true);
  assert.deepEqual([byId(acts, 'eject').enabled, byId(acts, 'eject').reason], [true, null]);
  assert.equal(byId(acts, 'eject').confirm, true);
  for (const id of ['load', 'read_tag', 'feed', 'rollback']) assert.equal(byId(acts, id).enabled, true, id);
  const following = status({ heads: [true, false, false, false], lanes: [lane({ mode: 'following' }), lane(), lane(), lane()] });
  assert.equal(byId(laneActions(following, 0, true), 'eject').enabled, true);
  assert.equal(byId(laneActions(s, 0, false), 'eject').reason, 'offline');
  const printing = status({ print: 'printing', heads: [true, false, false, false] });
  assert.equal(byId(laneActions(printing, 0, true), 'eject').reason, 'in_use');
  assert.deepEqual(REASONS, ['offline', 'in_use']);
  assert.equal(gcodeFor('eject', 1), 'ACE_EJECT LANE=1 WAIT=0');
});

test('gcodeFor refuses a move longer than 500 mm', () => {
  assert.equal(gcodeFor('feed', 1, { length: 500 }), 'ACE_FEED LANE=1 LENGTH=500 SPEED=20 WAIT=0');
  assert.throws(() => gcodeFor('feed', 1, { length: 500.5 }));
  assert.throws(() => gcodeFor('rollback', 1, { length: 501 }));
});

const spoolArgs = { type: 'PETG', vendor: 'Generic', rgb: '1a2b3c' };

test('edit_spool: the exact G-code, head = lane - 1', () => {
  assert.equal(gcodeFor('edit_spool', 2, spoolArgs),
    "SET_PRINT_FILAMENT_CONFIG CONFIG_EXTRUDER=1 VENDOR=Generic FILAMENT_TYPE=PETG FILAMENT_SUBTYPE='' FILAMENT_COLOR_RGBA=1A2B3CFF FORCE=1");
  assert.equal(gcodeFor('edit_spool', 4, { type: 'PA6-CF', vendor: 'Esun_3+x-1', rgb: 'FFFFFF' }),
    "SET_PRINT_FILAMENT_CONFIG CONFIG_EXTRUDER=3 VENDOR=Esun_3+x-1 FILAMENT_TYPE=PA6-CF FILAMENT_SUBTYPE='' FILAMENT_COLOR_RGBA=FFFFFFFF FORCE=1");
  assert.equal(FILAMENT_TYPES.length, 16);
});

test('edit_spool refuses a bad type, vendor, colour or lane', () => {
  for (const vendor of ['', 'My Brand', "a'b", 'a"b', 'a;b', 'a#b', 'a\nACE_STOP', 'x'.repeat(25), 'é', undefined]) {
    assert.throws(() => gcodeFor('edit_spool', 1, { ...spoolArgs, vendor }), String(vendor));
  }
  assert.doesNotThrow(() => gcodeFor('edit_spool', 1, { ...spoolArgs, vendor: 'x'.repeat(24) }));
  for (const type of ['pla', 'NYLON', '', undefined, 'PLA;X']) assert.throws(() => gcodeFor('edit_spool', 1, { ...spoolArgs, type }), String(type));
  for (const rgb of ['', '12345', '1234567', 'gggggg', '#1a2b3c', '1a2b3cff', undefined]) assert.throws(() => gcodeFor('edit_spool', 1, { ...spoolArgs, rgb }), String(rgb));
  assert.throws(() => gcodeFor('edit_spool', 5, spoolArgs));
  assert.throws(() => gcodeFor('edit_spool', null, spoolArgs));
});

test('edit_spool is shown for a filament without a tag, locked like Load', () => {
  const noFilament = status({ lanes: [lane({ insert: false }), lane(), lane(), lane()] });
  assert.ok(!ids(laneActions(noFilament, 0, true)).includes('edit_spool'));
  const busy = status({ print: 'printing', lanes: [lane({ mode: 'following' }), lane(), lane(), lane()] });
  assert.equal(byId(laneActions(busy, 0, true), 'edit_spool').reason, 'in_use');
  assert.equal(byId(laneActions(busy, 1, true), 'edit_spool').enabled, true);
  assert.equal(byId(laneActions(status(), 0, false), 'edit_spool').reason, 'offline');
});

test('editPrefill: the head setting, else defaults', () => {
  assert.deepEqual(editPrefill(status(), 1), { lane: 2, type: 'PLA', vendor: 'Generic', color: '#ffffff' });
  const ptc = { filament_exist: [true, false, false, false], filament_vendor: ['Bambu Lab', 'NONE', 'NONE', 'NONE'],
    filament_type: ['PETG-CF', 'NONE', 'NONE', 'NONE'], filament_sub_type: ['', 'NONE', 'NONE', 'NONE'],
    filament_color_rgba: ['DD3333FF', 'FFFFFFFF', 'FFFFFFFF', 'FFFFFFFF'] };
  assert.deepEqual(editPrefill(status({ ptc }), 0), { lane: 1, type: 'PETG-CF', vendor: 'Generic', color: '#dd3333' });
  ptc.filament_vendor[0] = 'Bambu';
  assert.equal(editPrefill(status({ ptc }), 0).vendor, 'Bambu');
});

test('editAction: the guard Save re-checks', () => {
  assert.equal(editAction(status(), 0, true).enabled, true);
  assert.equal(editAction(status({ print: 'printing', lanes: [lane({ mode: 'following' }), lane(), lane(), lane()] }), 0, true).reason, 'in_use');
  assert.equal(editAction(status(), 0, false).reason, 'offline');
  assert.equal(editAction(status({ lanes: [lane({ tag: { state: 'read', record: {} } }), lane(), lane(), lane()] }), 0, true), null);
});

test('gcodeFor has no Follow off', () => {
  assert.throws(() => gcodeFor('follow_off', 2));
});

test('Stop is enabled on a following lane outside a print, locked during one', () => {
  const lanes = [lane({ mode: 'following' }), lane(), lane(), lane()];
  const idle = byId(laneActions(status({ lanes }), 0, true), 'stop');
  assert.deepEqual([idle.enabled, idle.reason], [true, null]);
  for (const print of ['printing', 'paused']) {
    const st = byId(laneActions(status({ print, lanes }), 0, true), 'stop');
    assert.deepEqual([st.enabled, st.reason], [false, 'in_use'], print);
  }
  // a moving lane that is not a print's follow is still in use while printing; offline wins over it
  assert.equal(byId(laneActions(status({ print: 'printing', lanes }), 0, false), 'stop').reason, 'offline');
});

test('Stop all: lane selection and G-code', () => {
  const lanes = [lane({ mode: 'following' }), lane(), lane(), lane({ mode: 'feeding' })];
  assert.equal(stopAllLanes(status({ lanes })), null);
  assert.deepEqual(stopAllLanes(status({ print: 'printing', lanes })), [2, 3]);
  assert.deepEqual(stopAllLanes(status({ print: 'paused', lanes })), [2, 3]);
  assert.deepEqual(stopAllLanes(status({ print: 'printing', heads: [false, true, false, false], lanes })), [3]);
  const all = status({ print: 'printing', lanes: [0, 1, 2, 3].map(() => lane({ mode: 'following' })) });
  assert.deepEqual(stopAllLanes(all), []);
  assert.equal(topbarView(all, true).stopLanes.length, 0);
  assert.equal(gcodeFor('stop_all', null, {}), 'ACE_STOP');
  assert.equal(gcodeFor('stop_all', null, { lanes: [2, 3] }), 'ACE_STOP LANE=2\nACE_STOP LANE=3');
  assert.throws(() => gcodeFor('stop_all', null, { lanes: [] }));
  assert.throws(() => gcodeFor('stop_all', null, { lanes: [5] }));
  assert.throws(() => gcodeFor('stop_all', null, { lanes: ['1'] }));
});

test('allowedNow re-checks the action against the current status', () => {
  const lanes = [lane({ mode: 'following' }), lane(), lane(), lane()];
  const printing = status({ print: 'printing', lanes });
  assert.deepEqual(allowedNow(printing, 0, 'stop', true), { ok: false, reason: 'stop_in_use' });
  assert.deepEqual(allowedNow(printing, 0, 'eject', true), { ok: false, reason: 'in_use' });
  assert.deepEqual(allowedNow(printing, 0, 'stop', false), { ok: false, reason: 'offline' });
  assert.deepEqual(allowedNow(printing, 1, 'load', true), { ok: true, reason: null });
  assert.deepEqual(allowedNow(status({ lanes }), 0, 'stop', true), { ok: true, reason: null });
  // an action the status no longer offers at all is refused too
  assert.deepEqual(allowedNow(status({ lanes }), 0, 'load', true), { ok: false, reason: 'unavailable' });
  assert.equal(allowedNow(status({ lanes }), 0, 'load', false).reason, 'offline');
});

test('Stop all during a print with no lane telemetry sends nothing', () => {
  assert.deepEqual(stopAllLanes({ print_stats: { state: 'printing' } }), []);
  assert.deepEqual(stopAllLanes({ print_stats: { state: 'printing' }, ace2k: { lanes: [] } }), []);
  assert.equal(stopAllLanes({}), null);
});
