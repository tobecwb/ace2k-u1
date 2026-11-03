import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

// The CSS of the page and main.js, read as text. No test runs a browser,
// so the breakpoints and the touch sizes are checked here.
const read = (name) => readFileSync(new URL(`../${name}`, import.meta.url), 'utf8');
const FILES = [
  'css/tokens.css',
  'css/frame.css',
  'css/units.css',
  'css/print.css',
  'css/printing.css',
  'css/dryer.css',
  'css/panel.css',
  'css/history.css',
  'css/mobile.css',
];
const css = Object.fromEntries(FILES.map((f) => [f, read(f).replace(/\/\*[\s\S]*?\*\//g, '')]));

// The bodies of every `@media <query> { … }` block of text, joined.
function media(text, query) {
  const out = [];
  let at = text.indexOf(`@media ${query}`);
  while (at >= 0) {
    let i = text.indexOf('{', at) + 1;
    const start = i;
    for (let depth = 1; depth > 0; i += 1) depth += text[i] === '{' ? 1 : text[i] === '}' ? -1 : 0;
    out.push(text.slice(start, i - 1));
    at = text.indexOf(`@media ${query}`, i);
  }
  return out.join('\n');
}

test('the page has a phone up to 700 px and one screen from 1000 px; the plan two columns from 1100 px, 1100 + the panel with it open', () => {
  const widths = new Set(
    Object.values(css).flatMap((text) =>
      [...text.matchAll(/@media[^{]*\((?:min|max)-width:\s*(\d+)px\)/g)].map((m) => m[1]),
    ),
  );
  const side = Number(/--side-w:\s*(\d+)px;/.exec(css['css/tokens.css'])[1]);
  const withPanel = 1100 + side;
  assert.equal(withPanel, 1530);
  assert.deepEqual([...widths].sort(), ['1000', '1100', String(withPanel), '700'].sort());
  // Layout A: no container query (a size container would hold the fixed Eject-all dialog), one media query for each
  // case: no panel from 1100 px, the panel open from 1100 px + its width. Both switch on the same grid.
  const all = Object.values(css).join('\n');
  assert.doesNotMatch(all, /@container|container-type|container:/);
  const print = css['css/print.css'];
  const bare = media(print, '(min-width: 1100px)');
  const open = media(print, `(min-width: ${withPanel}px)`);
  for (const [block, sel] of [
    [bare, '.shell:not(.has-panel)'],
    [open, '.shell.has-panel'],
  ]) {
    assert.equal(declared(block, `${sel} .plan`).display, 'grid');
    assert.equal(declared(block, `${sel} .plan`).gap, 'var(--wide-gap)');
    assert.equal(declared(block, `${sel} .plan-dia .dg`)['max-height'], 'var(--dia-max-h)');
  }
  const plan = declared(print, '.plan');
  assert.equal(plan['grid-template-columns'], '470px minmax(0, 1fr)');
  assert.match(plan['grid-template-areas'], /"head head"\s*"left dia"\s*"tl tl"\s*"bar bar"/);
  assert.equal(plan['--dia-max-h'], 'max(340px, calc(100vh - 560px))');
  assert.equal(plan.display, 'flex', 'one column below the breakpoint');
  assert.match(css['css/panel.css'], /\.shell\.has-panel main \{\s*margin-right: var\(--side-w\);\s*\}/);
  assert.match(declared(css['css/panel.css'], '.side').width, /var\(--side-w\)/);
  assert.ok(media(css['css/frame.css'], '(max-width: 700px)').includes('.shell'));
  assert.ok(media(print, '(max-width: 700px)').includes('.btn.go'));
});

test("the phone's sheet scrim fades in and out in var(--dur); the tab mark fades", () => {
  assert.match(css['css/panel.css'], /\.scrim \{[^}]*background: var\(--scrim\);[^}]*animation: scrim-in var\(--dur\)/);
  assert.match(css['css/panel.css'], /\.scrim\.leaving \{[^}]*opacity: 0;[^}]*transition: opacity var\(--dur\)/);
  assert.match(declared(css['css/frame.css'], '.tab').transition, /color var\(--dur\)/);
});

// The declarations that each selector gets in the flat rules of text. A selector list counts for each of its selectors.
function declared(text, selector) {
  const out = {};
  for (const [, sel, body] of text.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    if (!sel.split(',').some((s) => s.trim() === selector)) continue;
    for (const [, name, value] of body.matchAll(/([\w-]+)\s*:\s*([^;]+);/g)) out[name] = value.trim();
  }
  return out;
}

test("on a phone the print bar's switches and Print are 44 px high, as every other control", () => {
  const phone = media(css['css/print.css'], '(max-width: 700px)');
  for (const sel of ['.tgl', '.btn.go'])
    assert.equal(declared(phone, sel)['min-height'], 'var(--control-h-touch)', sel);
});

test('the print bar: compact and sticky above the tab bar on a phone, and the page keeps room for it below', () => {
  const phone = media(read('css/mobile.css').replace(/\/\*[\s\S]*?\*\//g, ''), '(max-width: 700px)');
  assert.match(declared(phone, '.pbar').bottom, /control-h-touch/);
  assert.match(declared(phone, 'html:has(.pbar)')['scroll-padding-bottom'], /control-h-touch/);
  assert.equal(declared(css['css/print.css'], '.pbar').position, 'sticky');
  assert.equal(declared(css['css/print.css'], '.pbar.compact').display, 'flex');
  assert.ok(declared(css['css/print.css'], 'html:has(.pbar)')['scroll-padding-bottom']);
});

test('no dead declaration: no fallback for a token tokens.css defines, one font size for .note and .hint', () => {
  const defined = new Set([...css['css/tokens.css'].matchAll(/(--[\w-]+)\s*:/g)].map((m) => m[1]));
  const all = Object.values(css).join('\n');
  const dead = [...all.matchAll(/var\((--[\w-]+)\s*,/g)].map((m) => m[1]).filter((n) => defined.has(n));
  assert.deepEqual(dead, []);
  for (const sel of ['.note', '.hint']) {
    const sizes = [...all.matchAll(/([^{}]+)\{([^{}]*)\}/g)].filter(
      ([, s, body]) => s.split(',').some((x) => x.trim() === sel) && /font-size/.test(body),
    );
    assert.equal(sizes.length, 1, sel);
  }
});

test('every colour is a token: no rgba or hex colour outside tokens.css, the spool strokes in both themes', () => {
  for (const f of FILES.filter((x) => x !== 'css/tokens.css')) {
    assert.ok(!css[f].includes('rgba('), f);
    assert.ok(!/#[0-9a-fA-F]{3,8}\b/.test(css[f]), f);
  }
  const tokens = css['css/tokens.css'];
  for (const name of ['--shadow', '--shadow-raised', '--spool-ring', '--spool-shine', '--spool-edge'])
    assert.equal(tokens.split(`${name}:`).length - 1, 3, `${name}: the dark block and the two light blocks`);
});

test('a control drawn by its outline alone has the stronger edge, checked at 3:1 on the panel', () => {
  assert.equal(css['css/tokens.css'].split('--edge-strong:').length - 1, 3);
  const script = readFileSync(new URL('../../scripts/check_contrast.mjs', import.meta.url), 'utf8');
  assert.ok(script.includes("['--edge-strong', '--panel']"));
  const all = Object.values(css).join('\n');
  for (const [sel, prop] of [
    ['.filters button', 'border'],
    ['.filters .lanes', 'border'],
    ['.filters .lanes button', 'border-left'],
    ['.history .seg', 'border'],
    ['.tgl .tk', 'border'],
  ])
    assert.match(declared(all, sel)[prop] ?? '', /var\(--edge-strong\)/, sel);
});

test('one input skin for the text fields and the menus, and their heights as tokens', () => {
  const skin = ['.len input', '.edit input', '.edit select'];
  for (const sel of skin) {
    const own = declared(css['css/tokens.css'], sel);
    assert.deepEqual(
      [own.background, own.border, own['border-radius'], own['min-height']],
      ['var(--inset)', '1px solid var(--edge)', 'var(--r-sm)', 'var(--control-h)'],
      sel,
    );
  }
});

test('the sizes come from a scale: radii, font sizes and control heights are tokens', () => {
  const rest = FILES.filter((f) => f !== 'css/tokens.css').map((f) => css[f]);
  const all = [...rest, css['css/tokens.css'].replace(/:root\s*\{[^}]*\}/g, '')].join('\n');
  const literal = (prop, allowed) =>
    [...all.matchAll(new RegExp(`(?<![(\\w-])${prop}:\\s*([^;]+);`, 'g'))]
      .map((m) => m[1].trim())
      .filter((v) => !v.includes('var(--') && !allowed.includes(v));
  assert.deepEqual(literal('border-radius', ['0', '2px', '50%']), []);
  assert.deepEqual(literal('font-size', []), []);
  // 34px: a row of the timeline (printing.css .tlr), not a control.
  assert.deepEqual(literal('min-height', ['0', '100dvh', '34px']), []);
});

// Browsers drop a whole rule whose :has() holds another :has(). Selectors Level 4 forbids it.
test('no :has() inside another :has()', () => {
  for (const f of FILES) assert.ok(!/:has\([^)]*:has\(/.test(css[f]), f);
});

test("the legend's target swatch is a dashed line in the chamber's colour; the target has no colour of its own", () => {
  assert.match(css['css/history.css'], /\.hlegend button i\.dashed \{[^}]*border-top: 1px dashed currentColor;/);
  for (const f of ['css/tokens.css', 'css/history.css']) assert.ok(!css[f].includes('--line-tgt'), f);
});

test('every animation and transition lasts var(--dur), so reduce motion reaches them all', () => {
  for (const f of FILES) {
    for (const m of css[f].matchAll(/(?:animation|transition)\s*:([^;]+);/g)) {
      assert.match(m[1], /var\(--dur\)/, `${f}: ${m[0]}`);
      assert.doesNotMatch(m[1].replace(/var\([^)]*\)/g, ''), /\d(?:ms|s)\b/, `${f}: literal duration in ${m[0]}`);
    }
  }
});

test('the print bar stacks in two rows in the one-column plan and is one row beside layout A', () => {
  const print = css['css/print.css'];
  assert.equal(declared(print, '.pbar')['grid-template-columns'], '1fr auto');
  assert.equal(declared(print, '.pbar .psum')['grid-column'], '1 / -1');
  assert.equal(declared(print, '.pbar .opts')['flex-wrap'], 'wrap');
  assert.equal(declared(print, '.pbar .opts')['grid-template-columns'], undefined);
  for (const [query, sel] of [
    ['(min-width: 1100px)', '.shell:not(.has-panel)'],
    ['(min-width: 1530px)', '.shell.has-panel'],
  ]) {
    const block = media(print, query);
    assert.equal(declared(block, `${sel} .pbar`)['grid-template-columns'], '1fr auto auto');
    assert.equal(declared(block, `${sel} .pbar .psum`)['grid-column'], 'auto');
    assert.equal(declared(block, `${sel} .pbar .opts`).display, 'grid');
    assert.equal(declared(block, `${sel} .pbar .opts`)['grid-template-columns'], 'auto auto');
  }
});
