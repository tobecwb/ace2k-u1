import { test } from 'node:test';
import assert from 'node:assert/strict';
import { lane, status, twoHeadedUnits } from './helpers.js';
import {
  CHIP_KINDS,
  ACTION_IDS,
  BUFFER_KINDS,
  REASONS,
  CONFIRM,
  laneInUse,
  chipOf,
  spoolOf,
  bufferOf,
  laneActions,
  stopAllLanes,
  allowedNow,
  gcodeFor,
  unitName,
  FILAMENT_TYPES,
  editPrefill,
  editAction,
  topbarView,
  colorOf,
  headOf,
  headHasFilament,
  editSave,
  stopAllWords,
  STOP_ALL_KEYS,
  stopAllPlan,
  stopAllConfirmed,
  reasonKey,
  headArg,
  tagSpool,
  recordSpool,
  headSetting,
  RefusedError,
  REFUSALS,
  loadHeadPlan,
  sliceFor,
  editableSpool,
  printingNow,
} from '../js/model.js';
import { dryerView } from '../js/dryer-form.js';

// a refusal by its catalogue name and values
const refusedWith =
  (name, vars = {}) =>
  (e) =>
    e instanceof RefusedError && e.key === `refuse.${name}` && JSON.stringify(e.vars) === JSON.stringify(vars);

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

test('spoolOf: a head setting with no material (NONE) does not hide the tag or the record', () => {
  // The U1 sets the heads a file does not use to NONE when a print starts, with filament still in them.
  const ptc = {
    filament_exist: [true, true, false, false],
    filament_vendor: ['NONE', 'NONE', 'NONE', 'NONE'],
    filament_type: ['NONE', 'NONE', 'NONE', 'NONE'],
    filament_sub_type: ['NONE', 'NONE', 'NONE', 'NONE'],
    filament_color_rgba: ['FFFFFFFF', 'FFFFFFFF', 'FFFFFFFF', 'FFFFFFFF'],
  };
  const lanes = [
    lane({ tag: { state: 'read', record: { brand: 'Bambu Lab', material: 'PLA', color_rgba: '000000FF' } } }),
    lane(),
    lane(),
    lane(),
  ];
  const s = status({ ptc, lanes });
  assert.equal(spoolOf(s, 0)?.type, 'PLA');
  assert.equal(headSetting(s, 1, false), null);
});

test('spoolOf: a tag pending again keeps its last record, as the adapter does', () => {
  const lanes = [
    lane({ tag: { state: 'pending', record: { brand: 'Bambu Lab', material: 'PLA', color_rgba: '000000FF' } } }),
    lane(),
    lane(),
    lane(),
  ];
  assert.equal(spoolOf(status({ lanes }), 0)?.type, 'PLA');
});

test('spoolOf: head setting first, then a read tag, else null', () => {
  const ptc = {
    filament_exist: [true, false, false, false],
    filament_vendor: ['Generic', 'NONE', 'NONE', 'NONE'],
    filament_type: ['PLA', 'NONE', 'NONE', 'NONE'],
    filament_sub_type: ['', 'NONE', 'NONE', 'NONE'],
    filament_color_rgba: ['1E88E5FF', 'FFFFFFFF', 'FFFFFFFF', 'FFFFFFFF'],
  };
  const lanes = [
    lane(),
    lane({ tag: { state: 'read', record: { brand: 'Bambu Lab', material: 'PETG', color_rgba: '000000FF' } } }),
    lane(),
    lane(),
  ];
  const s = status({ ptc, lanes });
  assert.deepEqual(spoolOf(s, 0), { vendor: 'Generic', type: 'PLA', subtype: null, rgba: '1E88E5FF', fromTag: false });
  assert.deepEqual(spoolOf(s, 1), {
    vendor: 'Bambu Lab',
    type: 'PETG',
    subtype: null,
    rgba: '000000FF',
    fromTag: true,
  });
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
  assert.deepEqual(ids(laneActions(status(), 0, true)), [
    'load',
    'eject',
    'read_tag',
    'load_head',
    'ff',
    'feed',
    'rollback',
    'edit_spool',
  ]);
  const following = status({ lanes: [lane({ mode: 'following' }), lane(), lane(), lane()] });
  assert.deepEqual(ids(laneActions(following, 0, true)), ['eject', 'stop', 'read_tag', 'ff', 'edit_spool']);
  const err = status({ lanes: [lane({ mode: 'error' }), lane(), lane(), lane()] });
  assert.deepEqual(ids(laneActions(err, 0, true)), ['eject', 'clear', 'read_tag', 'ff', 'edit_spool']);
  const empty = status({ lanes: [lane({ insert: false }), lane(), lane(), lane()] });
  assert.deepEqual(ids(laneActions(empty, 0, true)), ['ff']);
  const tagged = status({ lanes: [lane({ tag: { state: 'read', record: {} } }), lane(), lane(), lane()] });
  assert.ok(ids(laneActions(tagged, 0, true)).includes('read_tag'));
  // A tag read with no material does not speak for the spool, so Edit spool stays; one with a material hides it.
  assert.ok(ids(laneActions(tagged, 0, true)).includes('edit_spool'));
  const speaks = status({
    lanes: [lane({ tag: { state: 'read', record: { material: 'PLA' } } }), lane(), lane(), lane()],
  });
  assert.ok(!ids(laneActions(speaks, 0, true)).includes('edit_spool'));
  // Read tag forgets the tag. It shows only for a tag that is read or absent.
  // A lane that waits to be read has no button.
  for (const state of ['pending', 'searching', 'reading', 'unknown']) {
    const waiting = status({ lanes: [lane({ tag: { state } }), lane(), lane(), lane()] });
    assert.ok(!ids(laneActions(waiting, 0, true)).includes('read_tag'), state);
  }
  // Forget tag, without a new read, comes with a tag that was read. Once forgotten, Edit spool replaces it.
  assert.ok(ids(laneActions(tagged, 0, true)).includes('forget_tag'));
  // While the print is printing, Forget tag waits for a pause, as Read tag again and Edit spool do.
  const printingTagged = { ...tagged, print_stats: { state: 'printing' } };
  assert.equal(byId(laneActions(printingTagged, 0, true), 'forget_tag').reason, 'print_running');
  assert.equal(byId(laneActions({ ...tagged, print_stats: { state: 'paused' } }, 0, true), 'forget_tag').enabled, true);
  assert.ok(!ids(laneActions(status(), 0, true)).includes('forget_tag'));
  const forgotten = {
    ...tagged,
    ace2k_u1: { hooked: true, lanes: { '-:1': { tag_forgotten: true, push_end: null } } },
  };
  assert.deepEqual(ids(laneActions(forgotten, 0, true)), [
    'load',
    'eject',
    'read_tag',
    'load_head',
    'ff',
    'feed',
    'rollback',
    'edit_spool',
  ]);
  assert.equal(gcodeFor('read_tag', 1, { forgotten: true }), 'ACEK_TAG_FORGET LANE=1 READ=1');
  assert.equal(gcodeFor('read_tag', 1, { forgotten: true }, 'a'), 'ACEK_TAG_FORGET LANE=1 READ=1 UNIT=a');
  assert.equal(gcodeFor('read_tag', 1), 'ACEK_RFID_FORGET LANE=1');
  for (const id of [
    'load',
    'eject',
    'stop',
    'clear',
    'read_tag',
    'forget_tag',
    'ff',
    'feed',
    'rollback',
    'edit_spool',
  ]) {
    assert.ok(ACTION_IDS.includes(id));
  }
  // The page has no Follow off. With the follow off on a loaded head, the extruder grinds the filament.
  assert.ok(!ACTION_IDS.includes('follow_off'));
});

test('laneActions lock during a print and offline', () => {
  const s = status({ print: 'printing', lanes: [lane({ mode: 'following' }), lane(), lane(), lane()] });
  const acts = laneActions(s, 0, true);
  for (const id of ['eject', 'ff']) {
    assert.deepEqual([byId(acts, id).enabled, byId(acts, id).reason], [false, 'in_use'], id);
  }
  assert.deepEqual([byId(acts, 'stop').enabled, byId(acts, 'stop').reason], [false, 'in_use'], 'stop');
  // While the print is printing, a bay's spool is not changed: Read tag again and Edit spool wait for a pause.
  assert.deepEqual([byId(acts, 'read_tag').enabled, byId(acts, 'read_tag').reason], [false, 'print_running']);
  // Load into head N waits for the print's end on every lane: it heats the head.
  const spoolish = ['load_head', 'read_tag', 'edit_spool'];
  assert.ok(
    laneActions(s, 1, true).every((a) => a.enabled === !spoolish.includes(a.id)),
    'lane 2 not in use',
  );
  // Paused: they are offered again.
  const paused = status({ print: 'paused', lanes: [lane({ mode: 'following' }), lane(), lane(), lane()] });
  assert.deepEqual(
    ['read_tag', 'edit_spool'].map((id) => byId(laneActions(paused, 1, true), id).enabled),
    [true, true],
  );
  assert.ok(laneActions(s, 1, false).every((a) => !a.enabled && a.reason === 'offline'));
  assert.equal(byId(acts, 'eject').confirm, true);
  assert.deepEqual(CONFIRM, ['eject', 'probe_head']);
});

test('gcodeFor', () => {
  const table = [
    [['load', 2], 'ACEK_LOAD LANE=2 WAIT=0'],
    [['eject', 2], 'ACEK_EJECT LANE=2 WAIT=0'],
    [['stop', 2], 'ACEK_STOP LANE=2'],
    [['stop_all'], 'ACEK_STOP'],
    [['clear', 2], 'ACEK_CLEAR LANE=2'],
    [['read_tag', 2], 'ACEK_RFID_FORGET LANE=2'],
    [['ff_on', 2], 'ACEK_FEED_FORWARD LANE=2 ON=1'],
    [['ff_off', 2], 'ACEK_FEED_FORWARD LANE=2 OFF=1'],
    [['feed', 2, { length: 10 }], 'ACEK_FEED LANE=2 LENGTH=10 SPEED=20 WAIT=0'],
    [['rollback', 2, { length: 25.5 }], 'ACEK_ROLLBACK LANE=2 LENGTH=25.5 SPEED=20 WAIT=0'],
    [['dry', null, { temp: 55, minutes: 240 }], 'ACEK_DRY TEMP=55 DURATION=240'],
    [['dry_mode', null, { mode: 'manual' }], 'ACEK_DRY_MODE MODE=manual'],
    [['dry_mode', null, { mode: 'humidity' }], 'ACEK_DRY_MODE MODE=humidity'],
    [['dry_mode', null, { mode: 'print' }], 'ACEK_DRY_MODE MODE=print'],
    [['dry_stop'], 'ACEK_DRY_STOP'],
    [['dry_clear'], 'ACEK_DRY_CLEAR'],
    [['dry_log'], 'ACEK_DRYER_LOG'],
    [['fans_on', null, { seconds: 60 }], 'ACEK_FAN ON=1 SECONDS=60'],
    [['fans_off'], 'ACEK_FAN ON=0'],
    [['flap', null, { which: 'rear', open: true }], 'ACEK_FLAP WHICH=rear OPEN=1'],
    [['flap', null, { which: 'bottom', open: false }], 'ACEK_FLAP WHICH=bottom OPEN=0'],
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
  assert.deepEqual(topbarView(s, true), {
    version: '0.11.0',
    link: true,
    health: true,
    failing: null,
    history: null,
    paused: null,
    online: true,
    stopLanes: null,
    stopWords: { label: 'top.stop_all', confirm: 'top.confirm_stop_all', vars: { unit: '' } },
  });
  s.ace2k_u1_history = { error: 'No space left on device' };
  assert.equal(topbarView(s, true).history, 'No space left on device');
});

test('gcodeFor refuses malformed or injected arguments', () => {
  assert.throws(() => gcodeFor('feed', 2, { length: NaN }));
  assert.throws(() => gcodeFor('feed', 2, { length: '10\nACEK_STOP' }));
  assert.throws(() => gcodeFor('feed', 2, { length: 0 }));
  assert.throws(() => gcodeFor('feed', 2, { length: -5 }));
  assert.throws(() => gcodeFor('rollback', 2, { length: Infinity }));
  assert.throws(() => gcodeFor('dry', null, { temp: NaN, minutes: 240 }));
  assert.throws(() => gcodeFor('dry', null, { temp: 55, minutes: '240 ACEK_STOP' }));
  assert.throws(() => gcodeFor('fans_on', null, { seconds: 0 }));
  assert.throws(() => gcodeFor('flap', null, { which: 'x', open: true }));
  assert.throws(() => gcodeFor('dry_mode', null, { mode: 'manual\nACEK_STOP' }));
  assert.throws(() => gcodeFor('dry_mode', null, {}));
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
  assert.equal(byId(acts, 'read_tag').reason, 'print_running', 'read_tag');
});

test('laneActions on an error lane during a print: clear stays, read_tag waits for a pause, eject locked, no stop', () => {
  const s = status({ print: 'printing', lanes: [lane({ mode: 'error' }), lane(), lane(), lane()] });
  const acts = laneActions(s, 0, true);
  assert.equal(byId(acts, 'clear').enabled, true, 'clear');
  assert.equal(byId(acts, 'read_tag').reason, 'print_running', 'read_tag');
  assert.deepEqual([byId(acts, 'eject').enabled, byId(acts, 'eject').reason], [false, 'in_use']);
  assert.equal(byId(acts, 'stop'), undefined, 'no stop on an error lane');
});

test('laneActions: Eject works with a loaded head outside a print, locked only in use or offline', () => {
  const s = status({ heads: [true, false, false, false] });
  const acts = laneActions(s, 0, true);
  assert.deepEqual([byId(acts, 'eject').enabled, byId(acts, 'eject').reason], [true, null]);
  assert.equal(byId(acts, 'eject').confirm, true);
  for (const id of ['load', 'read_tag', 'feed', 'rollback']) assert.equal(byId(acts, id).enabled, true, id);
  const following = status({
    heads: [true, false, false, false],
    lanes: [lane({ mode: 'following' }), lane(), lane(), lane()],
  });
  assert.equal(byId(laneActions(following, 0, true), 'eject').enabled, true);
  assert.equal(byId(laneActions(s, 0, false), 'eject').reason, 'offline');
  const printing = status({ print: 'printing', heads: [true, false, false, false] });
  assert.equal(byId(laneActions(printing, 0, true), 'eject').reason, 'in_use');
  assert.deepEqual(REASONS, [
    'offline',
    'in_use',
    'no_head',
    'not_hooked',
    'print_on',
    'batch_open',
    'head_loaded',
    'head_unknown',
    'head_moving',
    'head_hot',
    'no_material',
    'print_running',
  ]);
  assert.equal(gcodeFor('eject', 1), 'ACEK_EJECT LANE=1 WAIT=0');
  // Eject all waits for each eject's decision.
  assert.equal(gcodeFor('eject', 3, { wait: true }, 'b'), 'ACEK_EJECT LANE=3 WAIT=1 UNIT=b');
});

test('gcodeFor takes a move up to 2000 mm and says the range past it', () => {
  assert.equal(gcodeFor('feed', 1, { length: 2000 }), 'ACEK_FEED LANE=1 LENGTH=2000 SPEED=20 WAIT=0');
  assert.equal(gcodeFor('rollback', 1, { length: 1 }), 'ACEK_ROLLBACK LANE=1 LENGTH=1 SPEED=20 WAIT=0');
  assert.throws(() => gcodeFor('feed', 1, { length: 2001 }), refusedWith('length_max', { max: 2000 }));
  assert.throws(() => gcodeFor('rollback', 1, { length: 2000.5 }), refusedWith('length_max', { max: 2000 }));
  assert.throws(() => gcodeFor('feed', 1, { length: 0 }), refusedWith('length_min'));
  assert.throws(() => gcodeFor('feed', 1, { length: NaN }), refusedWith('length_min')); // an emptied field
});

test('Forget tag forgets without reading again', () => {
  assert.equal(gcodeFor('forget_tag', 2), 'ACEK_TAG_FORGET LANE=2');
  assert.equal(gcodeFor('forget_tag', 2, {}, 'a'), 'ACEK_TAG_FORGET LANE=2 UNIT=a');
});

const spoolArgs = { type: 'PETG', vendor: 'Generic', rgb: '1a2b3c', head: 1 };

test('edit_spool: the exact G-code, on the head the lane feeds', () => {
  assert.equal(
    gcodeFor('edit_spool', 2, spoolArgs),
    "SET_PRINT_FILAMENT_CONFIG CONFIG_EXTRUDER=1 VENDOR=Generic FILAMENT_TYPE=PETG FILAMENT_SUBTYPE='' FILAMENT_COLOR_RGBA=1A2B3CFF FORCE=1",
  );
  assert.equal(
    gcodeFor('edit_spool', 1, { ...spoolArgs, head: 2 }),
    "SET_PRINT_FILAMENT_CONFIG CONFIG_EXTRUDER=2 VENDOR=Generic FILAMENT_TYPE=PETG FILAMENT_SUBTYPE='' FILAMENT_COLOR_RGBA=1A2B3CFF FORCE=1",
  );
  assert.equal(
    gcodeFor('edit_spool', 4, { type: 'PA6-CF', vendor: 'Esun_3+x-1', rgb: 'FFFFFF', head: 3 }),
    "SET_PRINT_FILAMENT_CONFIG CONFIG_EXTRUDER=3 VENDOR=Esun_3+x-1 FILAMENT_TYPE=PA6-CF FILAMENT_SUBTYPE='' FILAMENT_COLOR_RGBA=FFFFFFFF FORCE=1",
  );
  assert.equal(FILAMENT_TYPES.length, 16);
});

test('edit_spool refuses a bad type, vendor, colour or lane', () => {
  for (const vendor of ['', 'My Brand', "a'b", 'a"b', 'a;b', 'a#b', 'a\nACEK_STOP', 'x'.repeat(25), 'é', undefined]) {
    assert.throws(() => gcodeFor('edit_spool', 1, { ...spoolArgs, vendor }), String(vendor));
  }
  assert.doesNotThrow(() => gcodeFor('edit_spool', 1, { ...spoolArgs, vendor: 'x'.repeat(24) }));
  for (const type of ['pla', 'NYLON', '', undefined, 'PLA;X'])
    assert.throws(() => gcodeFor('edit_spool', 1, { ...spoolArgs, type }), String(type));
  for (const rgb of ['', '12345', '1234567', 'gggggg', '#1a2b3c', '1a2b3cff', undefined])
    assert.throws(() => gcodeFor('edit_spool', 1, { ...spoolArgs, rgb }), String(rgb));
  assert.throws(() => gcodeFor('edit_spool', 5, spoolArgs));
  assert.throws(() => gcodeFor('edit_spool', null, spoolArgs));
  for (const head of [undefined, null, -1, 4, 1.5, '1'])
    assert.throws(() => gcodeFor('edit_spool', 1, { ...spoolArgs, head }), String(head));
});

test('edit_spool is shown for a filament without a tag, locked like Load', () => {
  const noFilament = status({ lanes: [lane({ insert: false }), lane(), lane(), lane()] });
  assert.ok(!ids(laneActions(noFilament, 0, true)).includes('edit_spool'));
  const busy = status({ print: 'printing', lanes: [lane({ mode: 'following' }), lane(), lane(), lane()] });
  // While printing, the print's own reason comes first; paused, a lane in use is still locked by the print.
  assert.equal(byId(laneActions(busy, 0, true), 'edit_spool').reason, 'print_running');
  // Another lane: not while the print is printing; while it is paused, yes.
  assert.equal(byId(laneActions(busy, 1, true), 'edit_spool').reason, 'print_running');
  const paused = status({ print: 'paused', lanes: [lane({ mode: 'following' }), lane(), lane(), lane()] });
  assert.equal(byId(laneActions(paused, 1, true), 'edit_spool').enabled, true);
  assert.equal(byId(laneActions(paused, 0, true), 'edit_spool').reason, 'in_use');
  assert.equal(printingNow(busy), true);
  assert.equal(printingNow(paused), false);
  assert.equal(byId(laneActions(status(), 0, false), 'edit_spool').reason, 'offline');
});

test('editPrefill: the head setting, else defaults', () => {
  assert.deepEqual(editPrefill(status(), 1), { lane: 2, head: 1, type: 'PLA', vendor: 'Generic', color: '#ffffff' });
  const ptc = {
    filament_exist: [true, false, false, false],
    filament_vendor: ['Bambu Lab', 'NONE', 'NONE', 'NONE'],
    filament_type: ['PETG-CF', 'NONE', 'NONE', 'NONE'],
    filament_sub_type: ['', 'NONE', 'NONE', 'NONE'],
    filament_color_rgba: ['DD3333FF', 'FFFFFFFF', 'FFFFFFFF', 'FFFFFFFF'],
  };
  assert.deepEqual(editPrefill(status({ ptc }), 0), {
    lane: 1,
    head: 0,
    type: 'PETG-CF',
    vendor: 'Generic',
    color: '#dd3333',
  });
  ptc.filament_vendor[0] = 'Bambu';
  assert.equal(editPrefill(status({ ptc }), 0).vendor, 'Bambu');
});

test('editAction: the guard Save re-checks', () => {
  assert.equal(editAction(status(), 0, true).enabled, true);
  assert.equal(
    editAction(status({ print: 'printing', lanes: [lane({ mode: 'following' }), lane(), lane(), lane()] }), 0, true)
      .reason,
    'print_running',
  );
  assert.equal(editAction(status(), 0, false).reason, 'offline');
  assert.equal(
    editAction(
      status({ lanes: [lane({ tag: { state: 'read', record: { material: 'PLA' } } }), lane(), lane(), lane()] }),
      0,
      true,
    ),
    null,
  );
});

test('editableSpool: false while the tag speaks; true for a record, no spool info, or a tag with no material', () => {
  const at = (tag, extra = {}) => ({ ...status({ lanes: [lane({ tag }), lane(), lane(), lane()] }), ...extra });
  assert.equal(
    editableSpool(at({ state: 'read', record: { brand: 'Bambu', material: 'PLA', color_rgba: 'DD3333FF' } }), 0),
    false,
  );
  // After a restart the tag waits with its last record kept, and still speaks.
  assert.equal(editableSpool(at({ state: 'pending', record: { material: 'PETG' } }), 0), false);
  // A record by hand (ACEK_SPOOL) and no information at all are both editable.
  const record = at(
    { state: 'no_tag', record: null },
    { ace2k_u1: { hooked: true, spools: { '-:1': { type: 'PLA', color: 'FFFFFFFF' } } } },
  );
  assert.equal(editableSpool(record, 0), true);
  assert.equal(editableSpool(at({ state: 'no_tag', record: null }), 0), true);
  // A tag read with no usable material and colour counts as no tag.
  assert.equal(editableSpool(at({ state: 'read', record: { brand: 'Acme' } }), 0), true);
  assert.equal(editableSpool(at({ state: 'read', record: {} }), 0), true);
  // A tag forgotten by hand no longer speaks.
  const forgotten = at(
    { state: 'read', record: { material: 'PLA' } },
    { ace2k_u1: { hooked: true, lanes: { '-:1': { tag_forgotten: true } } } },
  );
  assert.equal(editableSpool(forgotten, 0), true);
  assert.equal(editableSpool(null, 0), true);
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
  // A moving lane that is not the follow of a print is still in use while printing. Offline wins over it.
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
  assert.equal(gcodeFor('stop_all', null, {}), 'ACEK_STOP');
  assert.equal(gcodeFor('stop_all', null, { lanes: [2, 3] }), 'ACEK_STOP LANE=2\nACEK_STOP LANE=3');
  assert.throws(() => gcodeFor('stop_all', null, { lanes: [] }));
  assert.throws(() => gcodeFor('stop_all', null, { lanes: [5] }));
  assert.throws(() => gcodeFor('stop_all', null, { lanes: ['1'] }));
});

test('Stop all is decided before its confirmation and sent exactly as decided', () => {
  const lanes = [lane({ mode: 'following' }), lane(), lane(), lane({ mode: 'feeding' })];
  const inPrint = status({ print: 'printing', lanes });
  const plan = stopAllPlan(inPrint, 'a');
  assert.deepEqual(plan, {
    ok: true,
    printing: true,
    lanes: [2, 3],
    script: 'ACEK_STOP LANE=2 UNIT=a\nACEK_STOP LANE=3 UNIT=a',
    words: stopAllWords(inPrint),
  });
  // A planned lane went into use while the dialog was open, as in a tool change. Nothing is sent.
  // The page never stops a lane that the print now uses.
  const moved = status({ print: 'printing', lanes: [lane(), lane({ mode: 'following' }), lane(), lane()] });
  assert.deepEqual(stopAllConfirmed(plan, moved), { ok: false, toast: 'top.stop_all_changed' });
  // The planned lanes are still idle, and another lane was freed meanwhile.
  // Exactly the lines that the dialog promised are sent.
  const freed = status({ print: 'printing', lanes: [lane(), lane(), lane(), lane({ mode: 'feeding' })] });
  assert.deepEqual(stopAllConfirmed(plan, freed), { ok: true, script: plan.script });
  // The print ended meanwhile, so nothing is sent.
  // The page never sends a plain ACEK_STOP that the dialog did not promise.
  assert.deepEqual(stopAllConfirmed(plan, status({ lanes })), { ok: false, toast: 'top.stop_all_changed' });
  // Outside a print it is the plain ACEK_STOP. A print that started meanwhile sends nothing either.
  const idle = stopAllPlan(status({ lanes }), 'a');
  assert.deepEqual([idle.ok, idle.printing, idle.script], [true, false, 'ACEK_STOP']);
  assert.deepEqual(stopAllConfirmed(idle, status({ lanes })), { ok: true, script: 'ACEK_STOP' });
  assert.deepEqual(stopAllConfirmed(idle, inPrint), { ok: false, toast: 'top.stop_all_changed' });
  assert.equal(stopAllPlan(inPrint, null).script, 'ACEK_STOP LANE=2\nACEK_STOP LANE=3');
  // A print with every lane in use shows no dialog at all.
  const all = status({ print: 'printing', lanes: [0, 1, 2, 3].map(() => lane({ mode: 'following' })) });
  assert.deepEqual(stopAllPlan(all, 'a'), { ok: false, toast: 'top.stop_all_print' });
  assert.ok(STOP_ALL_KEYS.includes('top.stop_all_changed') && STOP_ALL_KEYS.includes('top.stop_all_print'));
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

test('gcodeFor with a named unit adds UNIT= to every ACEK_ line', () => {
  assert.equal(gcodeFor('load', 1, {}, 'a'), 'ACEK_LOAD LANE=1 WAIT=0 UNIT=a');
  assert.equal(gcodeFor('stop_all', null, { lanes: [1, 2] }, 'a'), 'ACEK_STOP LANE=1 UNIT=a\nACEK_STOP LANE=2 UNIT=a');
  assert.equal(gcodeFor('load', 1, {}, null), 'ACEK_LOAD LANE=1 WAIT=0');
  assert.ok(!gcodeFor('edit_spool', 2, { type: 'PLA', vendor: 'X', rgb: 'FF0000', head: 1 }, 'a').includes('UNIT='));
});

test('Stop all outside a print is a plain ACEK_STOP, every lane of every unit', () => {
  assert.equal(gcodeFor('stop_all', null, {}, 'a'), 'ACEK_STOP');
  assert.equal(gcodeFor('stop_all', null, { lanes: null }, 'b'), 'ACEK_STOP');
  assert.equal(gcodeFor('stop', 3, {}, 'b'), 'ACEK_STOP LANE=3 UNIT=b');
});

test('unitName', () => {
  assert.equal(unitName('ace2k'), null);
  assert.equal(unitName('ace2k a'), 'a');
});

// The page's unit is the first in ace2k_units. The adapter's status says which head each lane feeds.
function onUnits(s, units, heads, hooked = true) {
  return {
    ...s,
    ace2k_units: { units, active_lanes: {} },
    ace2k_u1: heads === undefined ? undefined : { heads, hooked },
  };
}
const bare = [1, 2, 3, 4].map((lane) => ({ unit: null, lane }));
// lanes: b:1, b:2, a:1, a:2. The page follows a, the first in printer.cfg.
const crossed = [
  { unit: 'b', lane: 1 },
  { unit: 'b', lane: 2 },
  { unit: 'a', lane: 1 },
  { unit: 'a', lane: 2 },
];

test('headOf: one unnamed unit with bare lanes, lane n feeds head n-1', () => {
  for (const s of [status(), onUnits(status(), ['ace2k'], bare), onUnits(status(), ['ace2k'])]) {
    assert.deepEqual(
      [0, 1, 2, 3].map((i) => headOf(s, i)),
      [0, 1, 2, 3],
    );
  }
  // lanes: 2, 4, so only heads 1 and 3
  const some = onUnits(status(), ['ace2k'], [null, { unit: null, lane: 2 }, null, { unit: null, lane: 4 }]);
  assert.deepEqual(
    [0, 1, 2, 3].map((i) => headOf(some, i)),
    [null, 1, null, 3],
  );
});

test("headOf: the adapter's list form (a list of lanes per head) reads as the single form", () => {
  const listed = onUnits(
    status(),
    ['ace2k a', 'ace2k b'],
    crossed.map((x) => [x]),
  );
  assert.deepEqual(
    [0, 1, 2, 3].map((i) => headOf(listed, i)),
    [2, 3, null, null],
  );
  const bareList = onUnits(
    status(),
    ['ace2k'],
    bare.map((x) => [x]),
  );
  assert.deepEqual(
    [0, 1, 2, 3].map((i) => headOf(bareList, i)),
    [0, 1, 2, 3],
  );
});

test("headOf: several units, the page unit's lanes through the adapter's map", () => {
  const s = onUnits(status(), ['ace2k a', 'ace2k b'], crossed);
  assert.deepEqual(
    [0, 1, 2, 3].map((i) => headOf(s, i)),
    [2, 3, null, null],
  );
  const upper = onUnits(status(), ['ace2k A', 'ace2k b'], crossed); // names compare in any case
  assert.deepEqual(
    [0, 1, 2, 3].map((i) => headOf(upper, i)),
    [2, 3, null, null],
  );
  // No map from the adapter. With several units, no lane is known to feed a head.
  const unknown = onUnits(status(), ['ace2k a', 'ace2k b']);
  assert.deepEqual(
    [0, 1, 2, 3].map((i) => headOf(unknown, i)),
    [null, null, null, null],
  );
});

test("several units: head sensor, in use, spool and Edit spool read the lane's own head", () => {
  const ptc = {
    filament_exist: [true, false, true, false],
    filament_vendor: ['Bambu', 'NONE', 'Generic', 'NONE'],
    filament_type: ['PETG', 'NONE', 'PLA', 'NONE'],
    filament_sub_type: ['', 'NONE', '', 'NONE'],
    filament_color_rgba: ['000000FF', 'FFFFFFFF', '1E88E5FF', 'FFFFFFFF'],
  };
  // head 0 (b:1) holds filament, head 2 (a:1) does not
  const s = onUnits(
    status({ print: 'printing', heads: [true, false, false, false], ptc }),
    ['ace2k a', 'ace2k b'],
    crossed,
  );
  assert.equal(headHasFilament(s, 0), false);
  assert.equal(laneInUse(s, 0), false, 'b:1 holding head 0 is not a:1 in use');
  const held = onUnits(
    status({ print: 'printing', heads: [false, false, true, false], ptc }),
    ['ace2k a', 'ace2k b'],
    crossed,
  );
  assert.equal(headHasFilament(held, 0), true);
  assert.equal(laneInUse(held, 0), true);
  assert.deepEqual(spoolOf(s, 0), { vendor: 'Generic', type: 'PLA', subtype: null, rgba: '1E88E5FF', fromTag: false });
  const idle = onUnits(status({ ptc }), ['ace2k a', 'ace2k b'], crossed);
  assert.deepEqual(editPrefill(idle, 0), { lane: 1, head: 2, type: 'PLA', vendor: 'Generic', color: '#1e88e5' });
  assert.equal(editAction(idle, 0, true).enabled, true);
});

test('a lane of the page unit that feeds no head: no head sensor, no head setting, Edit spool disabled', () => {
  const ptc = {
    filament_exist: [true, true, true, true],
    filament_vendor: ['X', 'X', 'X', 'X'],
    filament_type: ['PLA', 'PLA', 'PLA', 'PLA'],
    filament_sub_type: ['', '', '', ''],
    filament_color_rgba: ['000000FF', '000000FF', '000000FF', '000000FF'],
  };
  const s = onUnits(
    status({ print: 'printing', heads: [true, true, true, true], ptc }),
    ['ace2k a', 'ace2k b'],
    crossed,
  );
  assert.equal(headHasFilament(s, 2), false);
  assert.equal(laneInUse(s, 2), false);
  assert.equal(spoolOf(s, 2), null);
  const idle = onUnits(status({ ptc }), ['ace2k a', 'ace2k b'], crossed);
  assert.equal(editPrefill(idle, 2).head, null);
  const ea = editAction(idle, 2, true);
  assert.equal(ea.enabled, false);
  assert.equal(ea.reason, 'no_head');
  assert.equal(allowedNow(idle, 2, 'edit_spool', true).ok, false);
});

test('the adapter not hooked: the configured map still locks a lane in use, Edit spool disabled', () => {
  for (const [units, map, lane] of [
    [['ace2k'], bare, 0],
    [['ace2k a', 'ace2k b'], crossed, 0],
  ]) {
    const head = headOf(onUnits(status(), units, map, false), lane);
    const sensors = [false, false, false, false];
    sensors[head] = true; // the print holds filament in the head this lane feeds
    const printing = onUnits(status({ print: 'printing', heads: sensors }), units, map, false);
    assert.equal(laneInUse(printing, lane), true, String(units));
    for (const id of ['load', 'eject', 'feed', 'rollback'])
      assert.equal(byId(laneActions(printing, lane, true), id).reason, 'in_use', id);
    const idle = onUnits(status(), units, map, false);
    const ea = editAction(idle, lane, true);
    assert.equal(ea.enabled, false);
    assert.equal(ea.reason, 'not_hooked');
    assert.equal(allowedNow(idle, lane, 'edit_spool', true).ok, false);
    assert.equal(editAction(onUnits(status(), units, map, true), lane, true).enabled, true);
  }
  // No adapter status yet, so Edit spool waits for it.
  assert.equal(editAction(status({ hooked: null }), 0, true).reason, 'not_hooked');
  assert.equal(editAction({ ...status(), ace2k_u1: undefined }, 0, true).reason, 'not_hooked');
});

test('Edit spool saves to the head the form was opened for; a changed map refuses the save', () => {
  const opened = onUnits(status(), ['ace2k a', 'ace2k b'], crossed);
  const e = { ...editPrefill(opened, 0), type: 'PETG', vendor: 'Acme', color: '#12AB34' };
  assert.equal(e.head, 2);
  assert.deepEqual(editSave(opened, e, true), {
    ok: true,
    writeHead: true,
    args: { type: 'PETG', vendor: 'Acme', rgb: '12AB34', head: 2 },
  });
  // The map changed while the form was open, and a:1 now feeds head 0. Nothing is written.
  const moved = onUnits(
    status(),
    ['ace2k a', 'ace2k b'],
    [
      { unit: 'a', lane: 1 },
      { unit: 'a', lane: 2 },
      { unit: 'b', lane: 1 },
      { unit: 'b', lane: 2 },
    ],
  );
  assert.deepEqual(editSave(moved, e, true), { ok: false, reason: 'head_changed' });
  // a:1 now feeds no head. The lock's reason comes first.
  const gone = onUnits(status(), ['ace2k a', 'ace2k b'], [{ unit: 'b', lane: 1 }, null, null, null]);
  assert.deepEqual(editSave(gone, e, true), { ok: false, reason: 'no_head' });
  assert.deepEqual(editSave(opened, e, false), { ok: false, reason: 'offline' });
  assert.deepEqual(editSave(onUnits(status(), ['ace2k a', 'ace2k b'], crossed, false), e, true), {
    ok: false,
    reason: 'not_hooked',
  });
});

test("Stop all says what it stops: this unit's idle lanes in a print, every unit's lanes outside", () => {
  const units = (names) => ({ ace2k_units: { units: names, active_lanes: {} } });
  const run = { print_stats: { state: 'printing' } };
  // One unnamed unit. The words name no unit.
  assert.deepEqual(stopAllWords({}), { label: 'top.stop_all', confirm: 'top.confirm_stop_all', vars: { unit: '' } });
  assert.deepEqual(stopAllWords(run), {
    label: 'top.stop_idle',
    confirm: 'top.confirm_stop_all_print',
    vars: { unit: '' },
  });
  // One named unit. The words of a print name it.
  assert.deepEqual(stopAllWords(units(['ace2k a'])), {
    label: 'top.stop_all',
    confirm: 'top.confirm_stop_all',
    vars: { unit: 'a' },
  });
  assert.deepEqual(stopAllWords({ ...units(['ace2k a']), ...run }), {
    label: 'top.stop_idle_unit',
    confirm: 'top.confirm_stop_idle_unit',
    vars: { unit: 'a' },
  });
  // Several units. In a print, only the idle lanes of the page unit stop, and the words say the others do not.
  // Outside a print, every unit stops.
  const two = units(['ace2k a', 'ace2k b']);
  assert.deepEqual(stopAllWords(two), {
    label: 'top.stop_all_units',
    confirm: 'top.confirm_stop_all_units',
    vars: { unit: 'a' },
  });
  assert.deepEqual(stopAllWords({ ...two, print_stats: { state: 'paused' } }), {
    label: 'top.stop_idle_unit',
    confirm: 'top.confirm_stop_idle_units',
    vars: { unit: 'a' },
  });
  for (const w of [stopAllWords(two), stopAllWords({ ...two, ...run }), stopAllWords(run), stopAllWords({})]) {
    assert.ok(STOP_ALL_KEYS.includes(w.label) && STOP_ALL_KEYS.includes(w.confirm));
  }
});

test('reasonKey: Stop locked by a print has its own words, every other reason is its own key', () => {
  assert.equal(reasonKey('stop', 'in_use'), 'stop_in_use');
  assert.equal(reasonKey('load', 'in_use'), 'in_use');
  assert.equal(reasonKey('stop', 'offline'), 'offline');
});

test('headArg: a head 0-3 as it is, anything else refused', () => {
  assert.deepEqual([0, 3].map(headArg), [0, 3]);
  for (const h of [-1, 4, 1.5, '1', null, undefined]) assert.throws(() => headArg(h), refusedWith('head'));
});

test("tagSpool: the tag's record while the tag speaks; forgotten, no record or no material: null", () => {
  const rec = { brand: 'Bambu Lab', material: 'PETG', color_rgba: '000000FF' };
  const s = status({
    lanes: [
      lane({ tag: { state: 'read', record: rec } }),
      lane({ tag: { state: 'read', record: null } }),
      lane(),
      lane({ tag: { state: 'read', record: {} } }),
    ],
  });
  assert.deepEqual(tagSpool(s, 0), {
    vendor: 'Bambu Lab',
    type: 'PETG',
    subtype: null,
    rgba: '000000FF',
    fromTag: true,
  });
  assert.equal(tagSpool(s, 1), null);
  assert.equal(tagSpool(s, 2), null);
  assert.equal(tagSpool(s, 3), null); // a record with no material does not count, as in the adapter
  s.ace2k_u1.lanes = { '-:1': { tag_forgotten: true } };
  assert.equal(tagSpool(s, 0), null);
});

test("recordSpool: the lane's ACEK_SPOOL record, its colour as RGBA; none or untyped: null", () => {
  const s = status();
  s.ace2k_u1.spools = {
    '-:1': { type: 'PLA', color: '#1E88E5', vendor: 'Generic' },
    '-:2': { type: 'PETG', color: 'red', vendor: '' },
    '-:3': { color: '#000000' },
  };
  assert.deepEqual(recordSpool(s, 0), {
    vendor: 'Generic',
    type: 'PLA',
    subtype: null,
    rgba: '1E88E5ff',
    fromTag: false,
  });
  assert.deepEqual(recordSpool(s, 1), { vendor: null, type: 'PETG', subtype: null, rgba: null, fromTag: false });
  assert.equal(recordSpool(s, 2), null);
  assert.equal(recordSpool(s, 3), null);
});

test("headSetting: a head's U1 setting, NONE read as unknown; no filament or no head: null", () => {
  const ptc = {
    filament_exist: [true, false, false, false],
    filament_vendor: ['NONE', 'NONE', 'NONE', 'NONE'],
    filament_type: ['PLA', 'NONE', 'NONE', 'NONE'],
    filament_sub_type: ['', 'NONE', 'NONE', 'NONE'],
    filament_color_rgba: ['1E88E5FF', 'FFFFFFFF', 'FFFFFFFF', 'FFFFFFFF'],
  };
  const s = status({ ptc });
  assert.deepEqual(headSetting(s, 0, true), {
    vendor: null,
    type: 'PLA',
    subtype: null,
    rgba: '1E88E5FF',
    fromTag: true,
  });
  assert.equal(headSetting(s, 1, false), null);
  assert.equal(headSetting(s, null, false), null);
  assert.equal(headSetting({}, 0, false), null);
});

test('gcodeFor refuses an id it has no G-code for, a prototype name included', () => {
  for (const id of ['nonsense', 'constructor', 'toString', '__proto__'])
    assert.throws(() => gcodeFor(id, 1), refusedWith('no_action'));
});

test('every refusal the model makes is a RefusedError with a catalogue name', () => {
  const cases = [
    [() => gcodeFor('dry', null, { temp: NaN, minutes: 240 }), 'temp'],
    [() => gcodeFor('dry', null, { temp: 55, minutes: 0 }), 'minutes'],
    [() => gcodeFor('fans_on', null, { seconds: 0 }), 'seconds'],
    [() => gcodeFor('edit_spool', 1, { ...spoolArgs, type: 'nope' }), 'type'],
    [() => gcodeFor('edit_spool', 1, { ...spoolArgs, vendor: 'a b' }), 'vendor'],
    [() => gcodeFor('edit_spool', 1, { ...spoolArgs, rgb: 'zz' }), 'colour'],
    [() => gcodeFor('edit_spool', 1, { ...spoolArgs, head: 9 }), 'head'],
    [() => gcodeFor('feed', 1, { length: 0 }), 'length_min'],
    [() => gcodeFor('feed', 1, { length: 5000 }), 'length_max'],
    [() => gcodeFor('stop_all', null, { lanes: [] }), 'stop_all'],
    [() => gcodeFor('flap', null, { which: 'x' }), 'which'],
    [() => gcodeFor('dry_mode', null, { mode: 'fast' }), 'mode'],
    [() => gcodeFor('load', 9), 'lane'],
    [() => gcodeFor('nope', 1), 'no_action'],
  ];
  assert.deepEqual(cases.map(([, name]) => name).sort(), [...REFUSALS].sort());
  for (const [fn, name] of cases) {
    assert.throws(fn, (e) => e instanceof RefusedError && e.key === `refuse.${name}` && e.message === e.key, name);
  }
});

// A lane of unit a that shares head 1 with a:2, both heads' sensors read, the head empty.
// The adapter names no lane in head 1, as after a restart.
const sharedA = (over = {}) => {
  const s = twoHeadedUnits();
  s['ace2k a'].lanes = [lane(), lane(), lane(), lane()];
  s.ace2k_u1.current[0] = null;
  for (const h of [0, 1, 2, 3]) s[`filament_motion_sensor e${h}_filament`] = { filament_detected: false };
  s['gcode_macro AUTO_FEEDING_BATCH'] = { doing: false };
  return { ...sliceFor(s, 'a'), ...over };
};

test("loadHeadPlan: the U1 screen's head load; a shared head first makes the lane its own", () => {
  // A head of its own: lane 4 of the single unit feeds head 4, the macro's EXTRUDER 3 (0-based).
  assert.deepEqual(loadHeadPlan(status(), 3, null), {
    head: 3,
    select: null,
    prepare: 'ACEK_HEAD_PREPARE LANE=4',
    start: 'AUTO_FEEDING_BATCH ACTION=START',
    doing: 'AUTO_FEEDING_BATCH ACTION=DOING EXTRUDER=3 LOAD=1',
    stop: 'AUTO_FEEDING_BATCH ACTION=STOP',
    spool: 'ACEK_HEAD_SPOOL LANE=4',
  });
  // A shared head: a:1 feeds head 1 with a:2. ACEK_LANE_ACTIVE names a:1 first.
  assert.deepEqual(loadHeadPlan(sharedA(), 0, 'a'), {
    head: 0,
    select: 'ACEK_LANE_ACTIVE LANE=1 UNIT=a',
    prepare: 'ACEK_HEAD_PREPARE LANE=1 UNIT=a',
    start: 'AUTO_FEEDING_BATCH ACTION=START',
    doing: 'AUTO_FEEDING_BATCH ACTION=DOING EXTRUDER=0 LOAD=1',
    stop: 'AUTO_FEEDING_BATCH ACTION=STOP',
    spool: 'ACEK_HEAD_SPOOL LANE=1 UNIT=a',
  });
  // The unnamed unit sends no UNIT=.
  const unnamed = status();
  unnamed.ace2k_u1.heads = [
    [
      { unit: null, lane: 1 },
      { unit: null, lane: 2 },
    ],
    [{ unit: null, lane: 3 }],
    [],
    [],
  ];
  assert.equal(loadHeadPlan(unnamed, 1, null).select, 'ACEK_LANE_ACTIVE LANE=2');
  assert.equal(loadHeadPlan(unnamed, 2, null).select, null);
  assert.equal(loadHeadPlan(unnamed, 1, null).spool, 'ACEK_HEAD_SPOOL LANE=2');
  // A head the page cannot name is refused, never sent.
  const fifth = status();
  fifth.ace2k_u1.heads = [[], [], [], [], [{ unit: null, lane: 1 }]];
  assert.throws(() => loadHeadPlan(fifth, 0, null), refusedWith('head'));
});

test('laneActions: Load into head N is offered on an idle lane with a spool whose head is read empty', () => {
  const load = (s, i = 0, online = true) => byId(laneActions(s, i, online), 'load_head');
  const state = (a) => a && [a.enabled, a.reason];
  assert.ok(ACTION_IDS.includes('load_head') && REASONS.includes('head_loaded') && REASONS.includes('batch_open'));
  // A head load is already running, from the screen or another page: the macro's batch is open.
  const open = status();
  open['gcode_macro AUTO_FEEDING_BATCH'] = { doing: true };
  assert.deepEqual(state(load(open)), [false, 'batch_open']);
  // Without the macro in the status, the page cannot tell: not offered.
  const noMacro = status();
  delete noMacro['gcode_macro AUTO_FEEDING_BATCH'];
  assert.equal(load(noMacro), undefined);
  assert.deepEqual(state(load(status())), [true, null]);
  assert.equal(load(status()).confirm, false); // The action asks its own question (actions.js).
  // Beside Eject and Read tag, before the Advanced actions.
  assert.deepEqual(ids(laneActions(status(), 0, true)).slice(0, 4), ['load', 'eject', 'read_tag', 'load_head']);
  // The head's sensor sees filament: refused, unload it first.
  assert.deepEqual(state(load(status({ heads: [true, false, false, false] }))), [false, 'head_loaded']);
  // Disabled while a print runs or is paused, on every lane, in use or not.
  for (const print of ['printing', 'paused']) assert.deepEqual(state(load(status({ print }), 1)), [false, 'print_on']);
  assert.deepEqual(state(load(status({ hooked: false }))), [false, 'not_hooked']);
  assert.deepEqual(state(load(status(), 0, false)), [false, 'offline']);
  // Not offered: a moving lane, an empty bay, a head with no sensor reading, a lane that feeds no head.
  assert.equal(load(status({ lanes: [lane({ mode: 'feeding' }), lane(), lane(), lane()] })), undefined);
  assert.equal(load(status({ lanes: [lane({ insert: false }), lane(), lane(), lane()] })), undefined);
  const noSensor = status();
  delete noSensor['filament_motion_sensor e0_filament'];
  assert.equal(load(noSensor), undefined);
  const noHead = status();
  noHead.ace2k_u1.heads = [[], [], [], []];
  assert.equal(load(noHead), undefined);
  // A shared head: the lane need not be the head's current one.
  assert.deepEqual(state(load(sharedA())), [true, null]);
  assert.deepEqual(state(load(sharedA(), 1)), [true, null]);
  assert.deepEqual(allowedNow(status({ heads: [true, false, false, false] }), 0, 'load_head', true), {
    ok: false,
    reason: 'head_loaded',
  });
});

test('laneActions: Eject is refused for a lane whose material is not set, when the adapter refuses it', () => {
  const s = status();
  // -:1 is in its head with no material set; -:2 has no material set but is out of its head
  s.ace2k_u1.lanes = {
    '-:1': { material_set: false, eject_no_material: true },
    '-:2': { material_set: false, eject_no_material: false },
    '-:3': { material_set: true, eject_no_material: false },
  };
  const eject = (/** @type {number} */ i) => byId(laneActions(s, i, true), 'eject');
  assert.deepEqual([eject(0).enabled, eject(0).reason], [false, 'no_material']);
  assert.equal(eject(1).enabled, true);
  assert.equal(eject(2).enabled, true);
  // an adapter that gives only material_set (an older one) refuses every lane with none set
  s.ace2k_u1.lanes = { '-:1': { material_set: false }, '-:2': { material_set: true } };
  assert.deepEqual([eject(0).enabled, eject(0).reason], [false, 'no_material']);
  assert.equal(eject(1).enabled, true);
  // an adapter that does not say (older still) refuses nothing
  delete s.ace2k_u1.lanes;
  assert.equal(eject(0).enabled, true);
});
