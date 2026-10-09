import { test } from 'node:test';
import assert from 'node:assert/strict';
import { setCatalog, t } from '../js/i18n.js';

test('t substitutes variables and falls back to the key', () => {
  setCatalog({ 'a.b': 'Lane {n} → head {n}' });
  assert.equal(t('a.b', { n: 3 }), 'Lane 3 → head 3');
  assert.equal(t('missing.key'), 'missing.key');
  assert.equal(t('a.b'), 'Lane {n} → head {n}');
});
