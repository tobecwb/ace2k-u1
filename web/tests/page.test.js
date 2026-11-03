import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { elements } from './helpers.js';

// index.html's body, the frame main.js draws into, read as text (its script tag left out).
const page = readFileSync(new URL('../index.html', import.meta.url), 'utf8');
const html = page.slice(page.indexOf('<body>') + 6, page.indexOf('</body>')).replace(/<script\b[^>]*><\/script>/g, '');
const one = (selector) => {
  const found = elements(html, selector);
  assert.equal(found.length, 1, selector);
  return found[0].attrs;
};

test('the page is one root that main.js mounts the app into, with no panel of its own', () => {
  assert.deepEqual(one('div#app'), { id: 'app' });
  assert.equal(elements(html, 'section').length, 0);
  assert.equal(elements(html, 'main').length, 0);
});
