import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

// No test loads main.js, and byId() throws on a missing element.
// So a call for an id that index.html does not have would fail only in the browser. This test reads both files as text.
const root = new URL('..', import.meta.url).pathname;
const read = (name) => readFileSync(`${root}${name}`, 'utf8');
const main = read('main.js');

// The literal argument of each call of fn: 'x', "x" or `x` without ${}.
// Anything else comes back as { bad }, so the test fails on a call that it cannot read.
function literalArgs(text, fn) {
  const out = [];
  for (const m of text.matchAll(new RegExp(`(?<![\\w.])(?<!function )${fn}\\(([^)]*)\\)`, 'g'))) {
    const lit = m[1]
      .trim()
      .replace(/,$/, '')
      .match(/^(?:'([^'$]*)'|"([^"$]*)"|`([^`$]*)`)$/);
    out.push(lit ? (lit[1] ?? lit[2] ?? lit[3]) : { bad: m[0] });
  }
  return out;
}

test('every byId() call names its id as a literal, and index.html has it', () => {
  const html = new Set([...read('index.html').matchAll(/\bid="([^"]+)"/g)].map((m) => m[1]));
  const asked = literalArgs(main, 'byId');
  assert.ok(asked.length > 0, 'byId calls found');
  assert.deepEqual(
    asked.filter((a) => typeof a !== 'string'),
    [],
  );
  assert.deepEqual(
    asked.filter((a) => typeof a === 'string' && !html.has(a)),
    [],
  );
});

test('no call looks an element up by a bare $()', () => {
  assert.equal(/\$\('/.test(main), false);
});
