// Fails when a page module has too many lines of code.
//
// Usage: node check_web_module_size.mjs --max-code-lines N --target-lines M PATH [PATH ...]
//        node check_web_module_size.mjs --self-test
//
// Each PATH is a file, or a directory that is searched for .js and .css files. A code line is a
// physical line that holds part of the code. Blank lines, lines with only a // comment and lines
// inside a /* ... */ block (JSDoc included) do not count. Template literals and strings are code.
// The script prints one line for each module over N code lines, and exits with 1 if there is any such module.
// A module over M lines in total only gets a warning, and the exit code does not change.
// It does for the page what check_module_size.py does for the Python modules.

import { mkdtempSync, mkdirSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const REGEX_MAY_START_AFTER = '(,=:[!&|?{};+-*%<>~^';
// A word after which a / starts a regex, not a division (`return /re/.test(x)`).
const REGEX_AFTER_WORDS = new Set(['return', 'typeof', 'case', 'in', 'of', 'delete', 'void', 'throw', 'yield', 'await']);
const isWordChar = (c) => /[\w$]/.test(c);

/** Count the lines of code in a JavaScript or CSS source. */
export function codeLines(source, css = false) {
  const lines = source.split('\n');
  const hasCode = new Array(lines.length).fill(false);
  let line = 0;
  let state = 'code';
  let quote = '';
  let last = ''; // the last significant character of code, to tell a regex from a division
  let word = ''; // the identifier being read, and the one just before the current position
  let prevWord = '';
  let inClass = false; // inside [...] of a regex, where / does not end it
  const templates = []; // brace depth of each open ${ } inside a template literal
  let depth = 0;
  for (let i = 0; i < source.length; i++) {
    const c = source[i];
    const next = source[i + 1];
    if (c === '\n') {
      line++;
      // A regex literal cannot span lines. A / read as a regex start that reaches the end of the
      // line was a division after all, so the next line is read as code.
      if (state === 'line' || state === 'regex') state = 'code';
      // A line inside a template literal is part of a string, so it is code, even when blank.
      if (state === 'template') hasCode[line] = true;
      continue;
    }
    if (state === 'line') continue;
    if (state === 'block') {
      if (c === '*' && next === '/') {
        i++;
        state = 'code';
      }
      continue;
    }
    if (c === ' ' || c === '\t' || c === '\r') {
      if (word) [prevWord, word] = [word, ''];
      continue;
    }
    if (state === 'string' || state === 'template' || state === 'regex') {
      hasCode[line] = true;
      if (c === '\\') {
        i++;
        if (source[i] === '\n') line++;
      } else if (state === 'regex' && (c === '[' || c === ']')) {
        inClass = c === '[';
      } else if (state === 'template' && c === '$' && next === '{') {
        templates.push(depth);
        depth++;
        i++;
        state = 'code';
        last = '{';
      } else if (state === 'template' ? c === '`' : state === 'regex' ? c === '/' && !inClass : c === quote) {
        state = 'code';
        last = ')';
      }
      continue;
    }
    // state === 'code'
    if (c === '/' && next === '*') {
      i++;
      state = 'block';
      continue;
    }
    if (c === '/' && next === '/' && !css) {
      i++;
      state = 'line';
      continue;
    }
    hasCode[line] = true;
    const before = prevWord || word;
    if (isWordChar(c)) {
      if (prevWord) prevWord = '';
      word += c;
    } else {
      prevWord = '';
      word = '';
    }
    if ((c === '+' || c === '-') && next === c) {
      // ++ or --. After an operand it is postfix and ends the operand, so a / after it divides.
      i++;
      last = isWordChar(last) || last === ')' || last === ']' ? ')' : c;
    } else if (c === '"' || c === "'") {
      state = 'string';
      quote = c;
    } else if (c === '`' && !css) {
      state = 'template';
    } else if (c === '/' && !css && (last === '' || REGEX_MAY_START_AFTER.includes(last) || REGEX_AFTER_WORDS.has(before))) {
      state = 'regex';
      inClass = false;
    } else if (c === '{') {
      depth++;
      last = c;
    } else if (c === '}') {
      depth--;
      if (templates.length && templates[templates.length - 1] === depth) {
        templates.pop();
        state = 'template';
      }
      last = c;
    } else {
      last = c;
    }
  }
  return { code: hasCode.filter(Boolean).length, total: source.endsWith('\n') ? lines.length - 1 : lines.length };
}

/** The .js and .css files under each path, sorted. */
export function modules(paths) {
  const found = [];
  const walk = (path) => {
    if (statSync(path).isFile()) {
      found.push(path);
      return;
    }
    for (const name of readdirSync(path)) {
      if (name !== 'node_modules') walk(join(path, name));
    }
  };
  for (const path of paths) walk(path);
  return found.filter((p) => p.endsWith('.js') || p.endsWith('.css')).sort();
}

/** Prints what is wrong with each module. Returns how many are over the code-line limit. */
export function check(paths, maxCodeLines, targetLines) {
  let over = 0;
  for (const path of modules(paths)) {
    const { code, total } = codeLines(readFileSync(path, 'utf8'), path.endsWith('.css'));
    if (total > targetLines) console.log(`${path}: ${total} lines (target ${targetLines}), warning only`);
    if (code > maxCodeLines) {
      console.log(`${path}: ${code} code lines (limit ${maxCodeLines})`);
      over++;
    }
  }
  return over;
}

/** Run check() and main() on a scratch tree: the file walk, the report and the exit codes. */
function checkAndMain() {
  const eq = (actual, expected, what) => {
    if (actual !== expected) throw new Error(`self-test: ${what}: got ${actual}, expected ${expected}`);
  };
  const dir = mkdtempSync(join(tmpdir(), 'web-size-'));
  const log = console.log;
  const err = console.error;
  const out = [];
  try {
    mkdirSync(join(dir, 'sub'));
    mkdirSync(join(dir, 'node_modules'));
    writeFileSync(join(dir, 'small.js'), '// c\nconst a = 1;\n');
    writeFileSync(join(dir, 'sub', 'big.css'), '.a { color: red; }\n.b { color: blue; }\n.c { color: green; }\n');
    writeFileSync(join(dir, 'node_modules', 'skipped.js'), 'a;\nb;\nc;\nd;\n');
    writeFileSync(join(dir, 'notes.txt'), 'a\nb\nc\nd\n');
    console.log = (line) => out.push(line);
    console.error = (line) => out.push(line);
    eq(modules([dir]).length, 2, 'modules: .js and .css only, no node_modules');
    eq(check([dir], 3, 10), 0, 'check: nothing over the limit');
    eq(out.length, 0, 'check: nothing printed');
    eq(check([dir], 2, 10), 1, 'check: one module over the limit');
    eq(out.length, 1, 'check: one line printed');
    eq(out[0].includes('big.css: 3 code lines (limit 2)'), true, 'check: names the module');
    eq(check([dir], 3, 2), 0, 'check: a long module only warns');
    eq(out.at(-1).includes('warning only'), true, 'check: the warning is printed');
    eq(main(['--max-code-lines', '3', '--target-lines', '3', dir]), 0, 'main: pass is 0');
    eq(main(['--max-code-lines', '2', '--target-lines', '3', dir]), 1, 'main: over the limit is 1');
    eq(main(['--max-code-lines', '3', dir]), 2, 'main: a missing option is 2');
    eq(main(['--max-code-lines', '3', '--target-lines', '3']), 2, 'main: no path is 2');
    eq(main(['--max-code-lines', 'x', '--target-lines', '3', dir]), 2, 'main: a bad number is 2');
  } finally {
    console.log = log;
    console.error = err;
    rmSync(dir, { recursive: true, force: true });
  }
}

function selfTest() {
  const eq = (actual, expected, what) => {
    if (actual !== expected) throw new Error(`self-test: ${what}: got ${actual}, expected ${expected}`);
  };
  const n = (src, css = false) => codeLines(src, css).code;
  eq(n(''), 0, 'empty');
  eq(n('\n\n  \n'), 0, 'blank lines');
  eq(n('// a\n  // b\nconst a = 1;\n'), 1, 'line comments');
  eq(n('const a = 1; // c\n'), 1, 'trailing comment');
  eq(n('/** one\n * two\n */\nconst a = 1;\n'), 1, 'block comment');
  eq(n('/* a */ const a = 1;\n'), 1, 'code after a block comment');
  eq(n('const a = 1; /* a\n b */\nconst b = 2;\n'), 2, 'block comment ending its line');
  eq(n('const a = "//";\nconst b = \'/*\';\n'), 2, 'comment marks in strings');
  eq(n('const a = `\n// not a comment\n\n/* nor this */\n`;\nconst b = 1;\n'), 6, 'template literal lines, the blank one too');
  eq(n('const a = `x ${b ? `// y` : "/*"} z`;\nconst c = 1;\n'), 2, 'nested template');
  eq(n('const a = `${ {a: 1}.a }`;\n// c\n'), 1, 'braces inside a substitution');
  eq(n('const r = /\\/\\*/;\nconst s = a / b; // c\n'), 2, 'regex and division');
  eq(n('/* a */\n.a { color: red; }\n/* b\n c */\n', true), 1, 'css');
  eq(n('.a { content: "//"; }\n// not a comment in css\n', true), 2, 'css has no line comments');
  eq(codeLines('a\nb\n').total, 2, 'total lines');
  eq(n('const r = () => { return /\\*/.test(x); };\nconst b = 1;\n'), 2, 'regex after return');
  eq(n('const r = /[/*]/;\nconst b = 1; // c\n'), 2, 'slash inside a regex class');
  eq(n('const a = b / c; // d\nconst e = f / g / h;\n'), 2, 'division after names');
  eq(n('const a = i++ / 2; // c\n// d\nconst b = 1;\n'), 2, 'division after a postfix ++');
  eq(n('const a = i-- / 2;\n/* c\n d */\nconst b = 1;\n'), 2, 'division after a postfix --');
  eq(n('const a = (b)++ / 2\n// c\nconst d = 1;\n'), 2, 'division after (b)++');
  eq(n('const a = ++b; const r = /\\//;\n// c\n'), 1, 'a regex after a prefix ++ statement');
  eq(n('const a = b ? /x\n// c\nconst d = 1;\n'), 2, 'a regex ends at its line');
  checkAndMain();
  console.log('check_web_module_size: self-test OK');
}

export function main(argv) {
  if (argv.includes('--self-test')) {
    selfTest();
    return 0;
  }
  let max = null;
  let target = null;
  const paths = [];
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === '--max-code-lines') max = Number(argv[++i]);
    else if (argv[i] === '--target-lines') target = Number(argv[++i]);
    else paths.push(argv[i]);
  }
  if (max === null || target === null || !paths.length || Number.isNaN(max + target)) {
    console.error('usage: check_web_module_size.mjs --max-code-lines N --target-lines M PATH [PATH ...]');
    return 2;
  }
  return check(paths, max, target) ? 1 : 0;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) process.exitCode = main(process.argv.slice(2));
