import { test } from 'node:test';
import assert from 'node:assert/strict';
import { html } from '../js/ui/html.js';
import { buttons, rendered, renderHtml, textIn, textOf } from './ui-helpers.js';

test('a component tree renders to escaped HTML text', () => {
  assert.equal(renderHtml(html`<p>${'a<b'}</p>`), '<p>a&lt;b</p>');
});

test('textOf keeps the words only', () => {
  assert.equal(textOf('<p>a <b>b</b>\n c</p>'), 'a b c');
});

test('rendered lists the element vnodes; textIn and buttons read them', () => {
  // The vnodes made while it renders: a component's, so they are made inside rendered.
  const Box = () => html`<div><button type="button">a <b>b</b></button><p>c</p></div>`;
  const { out, nodes } = rendered(html`<${Box} />`);
  assert.equal(out, '<div><button type="button">a <b>b</b></button><p>c</p></div>');
  assert.deepEqual(
    buttons(nodes).map((n) => textIn(n)),
    ['a b'],
  );
  assert.equal(textIn(null), '');
});
