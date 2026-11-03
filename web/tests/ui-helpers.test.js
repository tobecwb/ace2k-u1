import { test } from 'node:test';
import assert from 'node:assert/strict';
import { html } from '../js/ui/html.js';
import { renderHtml, textOf } from './ui-helpers.js';

test('a component tree renders to escaped HTML text', () => {
  assert.equal(renderHtml(html`<p>${'a<b'}</p>`), '<p>a&lt;b</p>');
});

test('textOf keeps the words only', () => {
  assert.equal(textOf('<p>a <b>b</b>\n c</p>'), 'a b c');
});
