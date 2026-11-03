// Fails when a function of the page has no JSDoc block right above it.
//
// Usage: node check_web_jsdoc.mjs PATH [PATH ...]   (no path, or a missing one: exit 2)
//        node check_web_jsdoc.mjs --self-test
//
// Each PATH is a file, or a directory that is searched for .js files. The script parses each file
// with oxc-parser (installed by `npm ci --prefix web`) and walks every function that has a name:
// a function declaration, an arrow or a function expression given to a const or a let (nested
// ones included) or exported as the default, a class or object method, getter or setter, and a
// class field that holds a function. Each one needs a /** ... */ block that ends right
// before it, with only white space between. For an exported or a const function, the block sits
// before the export or the const. A block that defines a type (@typedef, @callback) does not
// count. Two kinds need none, because tsc already knows their types: an anonymous callback (from
// the call it is given to) and an arrow given to an object key (from the object's declared type,
// as the ACTIONS table's Record<string, ActionFn>).
// The script prints one line for each function without a block, and exits with 1 if there is any.

import { createRequire } from 'node:module';
import { existsSync, mkdtempSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const WEB = fileURLToPath(new URL('../web/', import.meta.url));
const { parseSync } = createRequire(join(WEB, 'package.json'))('oxc-parser');

const FUNCTION = new Set(['FunctionExpression', 'ArrowFunctionExpression']);

/** The name of a key or an identifier, or '?' when it has none. */
const nameOf = (node) => node?.name ?? node?.value ?? '?';

/** Each named function of a source as { name, start }, start being where its JSDoc must end. */
function namedFunctions(program) {
  const out = [];
  const visit = (node, parent, grand) => {
    if (!node || typeof node.type !== 'string') return;
    const exported = parent?.type === 'ExportNamedDeclaration' || parent?.type === 'ExportDefaultDeclaration';
    if (node.type === 'FunctionDeclaration' && node.id)
      out.push({ name: node.id.name, start: exported ? parent.start : node.start });
    if (node.type === 'VariableDeclarator' && FUNCTION.has(node.init?.type)) {
      // A JSDoc goes before the whole declaration: `/** … */ export const f = () => …`.
      const decl = parent;
      const outer = grand?.type === 'ExportNamedDeclaration' ? grand : decl;
      out.push({ name: nameOf(node.id), start: decl.declarations.length === 1 ? outer.start : node.start });
    }
    if (node.type === 'ExportDefaultDeclaration') {
      const d = node.declaration;
      if (FUNCTION.has(d?.type) || (d?.type === 'FunctionDeclaration' && !d.id))
        out.push({ name: 'default', start: node.start });
    }
    // Class methods, getters and setters, and class fields that hold a function.
    if (node.type === 'MethodDefinition') out.push({ name: nameOf(node.key), start: node.start });
    if (node.type === 'PropertyDefinition' && FUNCTION.has(node.value?.type))
      out.push({ name: nameOf(node.key), start: node.start });
    // Object methods, getters and setters. An arrow given to a key takes the object's type.
    if (node.type === 'Property' && (node.method || node.kind === 'get' || node.kind === 'set'))
      out.push({ name: nameOf(node.key), start: node.start });
    for (const [key, child] of Object.entries(node)) {
      if (key === 'parent') continue;
      if (Array.isArray(child)) for (const c of child) visit(c, node, parent);
      else if (child && typeof child === 'object') visit(child, node, parent);
    }
  };
  visit(program, null, null);
  return out;
}

/** The named functions of source without a JSDoc block, as "line name". */
export function missingJsdoc(source, file = 'x.js') {
  const { program, comments, errors } = parseSync(file, source, { sourceType: 'module' });
  if (errors.length) throw new Error(`${file}: ${errors[0].message}`);
  // A block that defines a type (@typedef, @callback) documents that type, not the function after it.
  const blocks = comments.filter(
    (c) => c.type === 'Block' && c.value.startsWith('*') && !/@(typedef|callback)\b/.test(c.value),
  );
  const lineOf = (at) => source.slice(0, at).split('\n').length;
  return namedFunctions(program)
    .filter(({ start }) => !blocks.some((c) => c.end <= start && source.slice(c.end, start).trim() === ''))
    .map(({ name, start }) => `${lineOf(start)} ${name}`);
}

function sources(path) {
  if (!statSync(path).isDirectory()) return [path];
  return readdirSync(path)
    .sort()
    .flatMap((n) => (statSync(join(path, n)).isDirectory() || n.endsWith('.js') ? sources(join(path, n)) : []));
}

function selfTest() {
  const cases = [
    ['/** @returns {void} */\nfunction a() {}', []],
    ['function a() {}', ['1 a']],
    ['/** @param {number} x */\nexport const f = (x) => x;', []],
    ['export const f = (x) => x;', ['1 f']],
    ['// a line comment only\nconst g = function () {};', ['2 g']],
    ['/* not JSDoc */\nconst g = () => 1;', ['2 g']],
    ['/** @param {number} x\n */\nconst h = (\n  x,\n) => x;', []],
    ['class C {\n  /** @returns {void} */\n  a() {}\n  b() {}\n}', ['4 b']],
    ['const o = {\n  /** @returns {void} */\n  a() {},\n  b() {},\n  c: () => 1,\n};', ['4 b']],
    ['/** @returns {void} */\nfunction a() {\n  const inner = () => 1;\n  [1].map((x) => x);\n}', ['3 inner']],
    ['/** @returns {void} */\nconst a = 1;\nfunction b() {}', ['3 b']],
    ['/** @typedef {{ a: number }} T */\nfunction a() {}', ['2 a']],
    ['class C {\n  get a() { return 1; }\n  /** @param {number} v */\n  set a(v) {}\n}', ['2 a']],
    ['const o = {\n  get a() { return 1; },\n  /** @param {number} v */\n  set a(v) {},\n};', ['2 a']],
    ['class C {\n  f = () => 1;\n  /** @returns {number} */\n  g = function () { return 1; };\n  n = 1;\n}', ['2 f']],
    ['export default () => 1;', ['1 default']],
    ['export default function () {}', ['1 default']],
    ['/** @returns {number} */\nexport default function () { return 1; }', []],
    ['/** @returns {number} */\nexport default () => 1;', []],
    ['/** @callback F @param {number} x */\nconst f = (x) => x;', ['2 f']],
    ['/** @typedef {number} T */\n/** @param {T} x */\nconst f = (x) => x;', []],
  ];
  for (const [src, want] of cases) {
    const got = missingJsdoc(src);
    if (JSON.stringify(got) !== JSON.stringify(want)) throw new Error(`self-test: ${JSON.stringify(src)} gave ${got}`);
  }
  const dir = mkdtempSync(join(tmpdir(), 'jsdoc-'));
  try {
    writeFileSync(join(dir, 'a.js'), 'export function f() {}\n');
    if (run([dir]) !== 1) throw new Error('self-test: a file without JSDoc passed');
    writeFileSync(join(dir, 'a.js'), '/** @returns {void} */\nexport function f() {}\n');
    if (run([dir]) !== 0) throw new Error('self-test: a file with JSDoc failed');
  } finally {
    rmSync(dir, { recursive: true });
  }
  const said = [];
  const quiet = (line) => said.push(line);
  if (main([], quiet, quiet) !== 2 || !said.at(-1)?.startsWith('usage:'))
    throw new Error('self-test: no path did not print the usage and exit 2');
  if (main([join(dir, 'gone')], quiet, quiet) !== 2 || !said.at(-1)?.includes('no such file or directory'))
    throw new Error('self-test: a missing path did not say so and exit 2');
  console.log('check_web_jsdoc: self-test OK');
}

/** Prints the functions without JSDoc and returns the exit code. */
function run(paths, print = () => {}) {
  let bad = 0;
  for (const file of paths.flatMap(sources))
    for (const miss of missingJsdoc(readFileSync(file, 'utf8'), file)) {
      print(`${file}:${miss} has no JSDoc block`);
      bad += 1;
    }
  return bad ? 1 : 0;
}

/**
 * Runs the check on argv and returns the exit code: 0 clean, 1 a function without JSDoc,
 * 2 no path or a path that does not exist.
 */
export function main(argv, print = console.log, error = console.error) {
  if (argv[0] === '--self-test') {
    selfTest();
    return 0;
  }
  if (!argv.length) {
    error('usage: check_web_jsdoc.mjs PATH [PATH ...] | --self-test');
    return 2;
  }
  const missing = argv.find((p) => !existsSync(p));
  if (missing !== undefined) {
    error(`check_web_jsdoc: ${missing}: no such file or directory`);
    return 2;
  }
  return run(argv, print);
}

if (process.argv[1] === fileURLToPath(import.meta.url)) process.exitCode = main(process.argv.slice(2));
