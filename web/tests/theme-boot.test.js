import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { THEME_KEY, readTheme } from '../js/state.js';

const BOOT = readFileSync(new URL('../theme-boot.js', import.meta.url), 'utf8');
const INDEX = readFileSync(new URL('../index.html', import.meta.url), 'utf8');

// Runs the boot script against a fake page. Returns the data-theme it set, or null for none.
function boot(localStorage) {
  const attrs = {};
  const document = { documentElement: { setAttribute: (k, v) => (attrs[k] = v) } };
  vm.runInNewContext(BOOT, { localStorage, document });
  return attrs['data-theme'] ?? null;
}

test('theme-boot.js: sets what state.js keeps under the same key, for the values readTheme accepts', () => {
  for (const stored of ['light', 'dark', 'system', 'blue', null]) {
    const got = boot({ getItem: (k) => (k === THEME_KEY ? stored : 'x') });
    assert.equal(got, readTheme(stored) === 'system' ? null : readTheme(stored));
  }
});

test('theme-boot.js: a blocked storage sets nothing and does not throw', () => {
  const blocked = {
    getItem: () => {
      throw new Error('blocked');
    },
  };
  assert.equal(boot(blocked), null);
  assert.doesNotThrow(() =>
    vm.runInNewContext(BOOT, {
      document: {},
      get localStorage() {
        throw new Error('blocked');
      },
    }),
  );
});

test('index.html loads theme-boot.js as a plain script before the styles', () => {
  const at = INDEX.indexOf('<script src="theme-boot.js"></script>');
  assert.ok(at > 0 && at < INDEX.indexOf('<link rel="stylesheet"'));
});
