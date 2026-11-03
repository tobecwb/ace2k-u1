import { after, test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';

// No test loads main.js. So a name that it imports from a module that does not export it
// would fail only in the browser.
// That happens when a move forgot the export, or when an import got lost.
// This test reads the import lists and the export lists as text, with no DOM and no execution.
const root = new URL('..', import.meta.url).pathname;

function sources(dir) {
  return readdirSync(dir).flatMap((n) => {
    const p = join(dir, n);
    return statSync(p).isDirectory() ? sources(p) : n.endsWith('.js') ? [p] : [];
  });
}

// Every top-level import and export statement is read. A form that this cannot read fails the test,
// so the check never skips a statement silently. The formatter breaks long lists over several lines.
const DECL = /^export (?:async )?(?:const|let|function|class) (\w+)/;
const LIST = /^(import|export) \{([^}]*)\}(?: from '([^']+)')?;?/;

/** Each name of a `{ a, b as c }` list as [name, alias?]. */
function listNames(raw) {
  return raw
    .split(',')
    .map((x) => x.trim())
    .filter(Boolean)
    .map((x) => x.split(/\s+as\s+/));
}

function statements(file) {
  const text = readFileSync(file, 'utf8');
  const out = { exports: new Set(), imports: [], unreadable: [] };
  const lines = text.split('\n');
  for (let i = 0; i < lines.length; i += 1) {
    if (!/^(import|export)\b/.test(lines[i])) continue;
    const decl = lines[i].match(DECL);
    if (decl) {
      out.exports.add(decl[1]);
      continue;
    }
    const rest = lines.slice(i).join('\n');
    const list = rest.match(LIST);
    if (!list) {
      out.unreadable.push(`${file.slice(root.length)}:${i + 1}: ${lines[i]}`);
      continue;
    }
    const names = listNames(list[2]);
    if (list[1] === 'export') {
      for (const n of names) out.exports.add(n.pop());
    } else if (list[3]?.startsWith('.')) {
      out.imports.push({ from: list[3], names: names.map((n) => n[0]) });
    } else {
      out.unreadable.push(`${file.slice(root.length)}:${i + 1}: ${lines[i]}`);
    }
  }
  return out;
}

function exportsOf(file) {
  return statements(file).exports;
}

const files = [join(root, 'main.js'), ...sources(join(root, 'js'))];

test('every name a page module imports is exported by the module it comes from', () => {
  let checked = 0;
  const unreadable = [];
  for (const file of files) {
    const st = statements(file);
    unreadable.push(...st.unreadable);
    for (const imp of st.imports) {
      const have = exportsOf(resolve(dirname(file), imp.from));
      for (const name of imp.names) {
        assert.ok(have.has(name), `${file.slice(root.length)} imports ${name}, which ${imp.from} does not export`);
        checked += 1;
      }
    }
  }
  assert.deepEqual(unreadable, [], 'import/export statements this check cannot read');
  assert.ok(checked > 100);
});

test('a multi-line list is read and an unknown form is reported', () => {
  const dir = mkdtempSync(join(tmpdir(), 'imports-'));
  after(() => rmSync(dir, { recursive: true, force: true }));
  const f = join(dir, 'x.js');
  writeFileSync(
    f,
    "import {\n  a,\n  b as c,\n} from './y.js';\nexport {\n  d,\n  e as f,\n};\nexport default 1;\nimport z from './y.js';\n",
  );
  const st = statements(f);
  assert.deepEqual(st.imports, [{ from: './y.js', names: ['a', 'b'] }]);
  assert.deepEqual([...st.exports], ['d', 'f']);
  assert.equal(st.unreadable.length, 2);
});

test('the check sees a missing export', () => {
  assert.ok(exportsOf(join(root, 'js/state.js')).has('readHidden'));
  assert.ok(!exportsOf(join(root, 'js/state.js')).has('noSuchName'));
});
