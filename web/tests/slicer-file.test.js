import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { usedFilaments, configColours, fileColours } from '../js/slicer-file.js';

const head = readFileSync(new URL('./fixtures/orca-head.txt', import.meta.url), 'utf8');
const tail = readFileSync(new URL('./fixtures/orca-tail.txt', import.meta.url), 'utf8');

test('the header lists the filaments the file uses, 1-based, in file order', () => {
  assert.deepEqual(usedFilaments(head), [1, 2, 5, 7, 4]);
  assert.equal(usedFilaments('; nothing here\n'), null);
});

test('the config block gives every configured filament', () => {
  const all = configColours(tail);
  assert.equal(all?.length, 8);
  assert.deepEqual(all?.[0], { colour: '#FFFFFF', material: 'PLA', vendor: 'Generic' });
  assert.deepEqual(all?.[1], { colour: '#D32C34', material: 'PLA', vendor: 'Generic' });
  assert.equal(configColours('; other = 1\n'), null);
});

test('the file colours are the used filaments, in the header order', () => {
  const cs = fileColours(head, tail);
  assert.deepEqual(
    cs?.map((c) => c.colour),
    ['#FFFFFF', '#D32C34', '#FEC187', '#000000', '#5B3F27'],
  );
  assert.equal(fileColours('', tail)?.length, 8);
  assert.equal(fileColours(head, ''), null);
});

test('a short or odd list keeps what it can read', () => {
  const short = '; filament_colour = #112233\n; filament_type = PETG;PLA\n';
  assert.deepEqual(configColours(short), [{ colour: '#112233', material: 'PETG', vendor: null }]);
  assert.deepEqual(fileColours('; filament: 1,9\n', short), [{ colour: '#112233', material: 'PETG', vendor: null }]);
});
