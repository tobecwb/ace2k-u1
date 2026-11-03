import { test } from 'node:test';
import assert from 'node:assert/strict';
import { setCatalog, loadCatalog, t } from '../js/i18n.js';

test('t substitutes variables and falls back to the key', () => {
  setCatalog({ 'a.b': 'Lane {n} → head {n}' });
  assert.equal(t('a.b', { n: 3 }), 'Lane 3 → head 3');
  assert.equal(t('missing.key'), 'missing.key');
  assert.equal(t('a.b'), 'Lane {n} → head {n}');
});

test('loadCatalog fetches the language file and installs it; English by default', async () => {
  const urls = [];
  const fetchFn = async (url) => {
    urls.push(url);
    return { json: async () => ({ 'x.y': `from ${url}` }) };
  };
  await loadCatalog('pt', fetchFn);
  assert.equal(t('x.y'), 'from i18n/pt.json');
  await loadCatalog(undefined, fetchFn);
  assert.equal(t('x.y'), 'from i18n/en.json');
  assert.deepEqual(urls, ['i18n/pt.json', 'i18n/en.json']);
  await assert.rejects(
    loadCatalog('xx', async () => {
      throw new Error('offline');
    }),
    /offline/,
  );
  assert.equal(t('x.y'), 'from i18n/en.json', 'a failed load keeps the catalogue in use');
});
