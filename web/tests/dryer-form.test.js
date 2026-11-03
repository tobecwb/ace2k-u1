import { test } from 'node:test';
import assert from 'node:assert/strict';
import { parseHM, suggestPreset, faultText, stateText, dryerView, isRunning } from '../js/dryer-form.js';
import { loadEnglish, status } from './helpers.js';
import { t } from '../js/i18n.js';

loadEnglish();

test('parseHM: h:mm from 0:01 to 24:00, else null', () => {
  assert.equal(parseHM('2:30'), 150);
  assert.equal(parseHM(' 6:00 '), 360);
  assert.equal(parseHM('0:01'), 1);
  assert.equal(parseHM('24:00'), 1440);
  for (const bad of ['0:00', '24:01', '25:00', '2:60', '2:5', 'x', '', '2', '-1:30', null, undefined, 150])
    assert.equal(parseHM(bad), null, String(bad));
});

const presets = [
  { material: 'PLA', temp: 45, hours: 6 },
  { material: 'PETG', temp: 60, hours: 6 },
  { material: 'ABS', temp: 65, hours: 6 },
  { material: 'TPU', temp: 60, hours: 6 },
];

test('suggestPreset: the preset of the coolest material among the spools', () => {
  assert.equal(suggestPreset(presets, ['PETG', 'ABS', null, undefined]), presets[1]);
  assert.equal(suggestPreset(presets, ['abs', 'pla']), presets[0]);
  assert.equal(suggestPreset(presets, ['PETG', 'TPU']), presets[1]);
  assert.equal(suggestPreset(presets, ['PLA+', 'ABS']), presets[0], 'a suffix still names the material');
});

test('suggestPreset: null with no known material', () => {
  assert.equal(suggestPreset(presets, []), null);
  assert.equal(suggestPreset(presets, [null, 'NYLON', 'PLAX']), null, 'a longer word is another material');
  assert.equal(suggestPreset([], ['PLA']), null);
});

test('faultText and stateText name what they know and say so for the rest', () => {
  assert.equal(faultText('ntc'), t('dryer.fault.ntc'));
  assert.equal(faultText('lid'), t('dryer.fault.lid'));
  assert.notEqual(faultText('lid'), 'dryer.fault.lid', 'the i18n key exists');
  assert.equal(faultText('mystery'), t('dryer.fault.unknown', { name: 'mystery' }));
  assert.equal(faultText(null), t('dryer.fault.unknown', { name: '?' }));
  assert.equal(stateText('heating'), t('dryer.state.heating'));
  assert.equal(stateText('weird'), t('dryer.state.unknown'));
  assert.equal(stateText(null), t('dryer.state.unknown'));
});

test("dryerView: the waiting line reads the unit's idle RH, the live one only when absent", () => {
  const s = status();
  s.ace2k.humidity = 52;
  s.ace2k.dryer = { ...s.ace2k.dryer, rh: 38 };
  assert.equal(dryerView(s).rh, 38);
  assert.equal(dryerView(s).humidity, 52);
  s.ace2k.dryer = { ...s.ace2k.dryer, rh: null };
  assert.equal(dryerView(s).rh, 52);
});

test('isRunning: the dryer starting or heating', () => {
  assert.deepEqual(['starting', 'heating'].map(isRunning), [true, true]);
  assert.deepEqual(['idle', 'cooldown', 'fault', undefined].map(isRunning), [false, false, false, false]);
});
