import { test } from 'node:test';
import assert from 'node:assert/strict';
import { elements } from './helpers.js';

test('elements reads the markup the views emit', () => {
  const html =
    '<div class="a b"><button data-id="ff" disabled>Go &amp; <b>x</b></button><input id="q" value="1"/><p class="n">hi</p></div>';
  assert.deepEqual(elements(html, 'button[data-id=ff]'), [
    { tag: 'button', attrs: { 'data-id': 'ff', disabled: '' }, text: 'Go & x' },
  ]);
  assert.deepEqual(
    elements(html, '.a .n').map((e) => e.text),
    ['hi'],
  );
  assert.equal(elements(html, 'input#q')[0].attrs.value, '1');
  assert.equal(elements(html, 'span').length, 0);
});

test('elements throws on markup it cannot parse, so a negative check never passes vacuously', () => {
  for (const html of ["<b class='x'>y</b>", '<b class=x>y</b>', '<b>y</b><', '<b data-x="1>y</b>']) {
    assert.throws(() => elements(html, 'b'), /cannot parse/, html);
  }
});
