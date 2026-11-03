// Fails when a text colour of the page is below 4.5:1 (WCAG 2.x) in the dark or the light theme.
//
// Usage: node check_contrast.mjs CSS_FILE
//        node check_contrast.mjs --self-test
//
// The colours are the custom properties of the two themes in CSS_FILE (web/css/tokens.css).
// The plain `:root` block is the dark theme, and the `:root[data-theme="light"]` block is the light one.
// A soft fill, an rgba colour, is blended on the surface it sits on before the ratio is taken.
// Text pairs need 4.5:1. The focus ring and the strong edge against the panel are not text, so they need 3:1.
// The script prints every pair that fails, and exits with 1 if there is any.

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const TEXT_MIN = 4.5;
const NON_TEXT_MIN = 3;

// Each text colour on its surface. A surface "x/y" is the soft fill x blended on y.
// The notes beside some pairs say where the page uses them.
const TEXT_PAIRS = [
  ['--ink', '--bg'],
  ['--ink', '--panel'],
  ['--ink', '--inset'],
  ['--ink', '--housing'],
  ['--ink-2', '--panel'],
  ['--ink-2', '--inset'],
  ['--ink-2', '--housing'], // .s-unknown and .s-parked on an unselected bay, and .hline on the unit box
  ['--ink-3', '--bg'],
  ['--ink-3', '--panel'],
  ['--ink-3', '--inset'],
  ['--ink-3', '--housing'],
  ['--ink-2', '--heat-soft/--panel'], // .pre span, .print .why on .pick.doubt
  ['--ink-2', '--heat-soft/--inset'],
  ['--heat', '--heat-soft/--panel'],
  ['--heat', '--heat-soft/--bg'],
  ['--bad', '--bad-soft/--panel'],
  ['--bad', '--bad-soft/--bg'],
  ['--water', '--panel'],
  ['--water', '--inset'],
  ['--ok', '--panel'],
  ['--ok', '--ok-soft/--panel'],
  ['--move', '--move-soft/--panel'],
  ['--on-heat', '--heat'],
  ['--ok', '--bg'], // the status pill while printing, in the top bar
  ['--warn', '--panel'], // also "lane ?" on a head card
  ['--warn', '--bg'],
  ['--err', '--panel'],
  ['--err', '--bg'],
  ['--err', '--bad-soft/--bg'], // Stop all in the top bar
  ['--err', '--bad-soft/--panel'], // a broken part
  ['--accent', '--panel'],
  ['--accent', '--bg'],
  ['--ink', '--unit-bg'], // the text on a unit's amber card
  ['--ink-3', '--unit-bg'],
  ['--ink', '--bay-bg'], // a bay of the Units diagram: its material, its number, its "?"
  ['--ink-3', '--bay-bg'],
  ['--warn', '--bay-bg'],
  ['--ink', '--foot-bg'], // a unit's footer strip: its name, its readings
  ['--ink-3', '--foot-bg'],
  ['--ink', '--chip'], // a bay's head chip
  ['--on-chip-on', '--chip-on'], // the chip of the bay loaded in its head
  ['--ink', '--foot-hover'], // a unit's footer under the pointer
  ['--ink-3', '--foot-hover'],
  ['--on-err', '--err'], // the "!" badge of a bay in error
  ['--err', '--bay-bg'], // the error word under a bay's material
  ['--ink', '--heat-soft/--bg'], // the guided step's card on the Print tab: its sentence, its caption, its buttons
  ['--ink-2', '--heat-soft/--bg'],
  ['--err', '--heat-soft/--bg'], // where eject all stopped, in the card
  ['--on-heat', '--warn'], // "Done, next →", the "take out" / "put in" labels
  ['--on-ok', '--ok'], // the Print button
];
const NON_TEXT_PAIRS = [
  ['--focus', '--panel'],
  ['--edge-strong', '--panel'], // the outline of a control with no fill of its own (.filters, .seg, .pre)
  ['--hover', '--panel'], // the outline of what the pointer is over
  ['--hover', '--unit-bg'],
  ['--hover', '--foot-bg'],
  ['--rail-empty', '--unit-bg'], // the dashed rail of an empty lane
];

/** The custom properties of every `selector { ... }` block of css, by selector. */
export function blocks(css) {
  const out = new Map();
  for (const [, selector, body] of css.replace(/\/\*[\s\S]*?\*\//g, '').matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    const props = out.get(selector.trim()) ?? {};
    for (const [, name, value] of body.matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g)) props[name] = value.trim();
    out.set(selector.trim(), props);
  }
  return out;
}

const SYSTEM_LIGHT = ':root:not([data-theme="dark"])';

/** The two themes. The light theme is the dark one, with the colours of the light block on top. */
export function themes(css) {
  const all = blocks(css);
  const dark = all.get(':root');
  const light = all.get(':root[data-theme="light"]');
  if (!dark || !light) throw new Error('tokens: need a `:root` block and a `:root[data-theme="light"]` block');
  return { dark, light: { ...dark, ...light } };
}

/** The light colours are written twice, for the light of the system and for the forced light. They must match. */
export function lightDiffers(css) {
  const all = blocks(css);
  const forced = all.get(':root[data-theme="light"]') ?? {};
  const system = all.get(SYSTEM_LIGHT);
  if (!system) return [`tokens: no ${SYSTEM_LIGHT} block`];
  return [...new Set([...Object.keys(forced), ...Object.keys(system)])]
    .filter((name) => forced[name] !== system[name])
    .map((name) => `light blocks differ: ${name} is ${forced[name]} (forced) and ${system[name]} (system)`);
}

/** @returns {[number, number, number, number]} r, g, b (0-255) and alpha */
export function parseColour(text) {
  const hex = /^#([0-9a-f]{6})$/i.exec(text);
  if (hex) return [0, 2, 4].map((i) => Number.parseInt(hex[1].slice(i, i + 2), 16)).concat(1);
  const rgba = /^rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([\d.]+)\s*)?\)$/.exec(text);
  if (rgba) return [Number(rgba[1]), Number(rgba[2]), Number(rgba[3]), rgba[4] === undefined ? 1 : Number(rgba[4])];
  throw new Error(`unreadable colour: ${text}`);
}

const blend = ([r, g, b, a], [r2, g2, b2]) => [r * a + r2 * (1 - a), g * a + g2 * (1 - a), b * a + b2 * (1 - a)];

function luminance(rgb) {
  const [r, g, b] = rgb.map((c) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** A colour of a theme, or a "soft/base" pair, as an opaque rgb. */
function resolve(theme, ref) {
  const [top, base] = ref.split('/');
  if (theme[top] === undefined) throw new Error(`missing token ${top}`);
  const colour = parseColour(theme[top]);
  return base ? blend(colour, resolve(theme, base)) : blend(colour, [0, 0, 0]);
}

export function ratio(a, b) {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

/** The pairs below their minimum, as "theme fg on bg: ratio (needs min)" lines. */
export function failures(css) {
  const out = lightDiffers(css);
  const set = themes(css);
  for (const [name, theme] of Object.entries(set)) {
    for (const [pairs, min] of [
      [TEXT_PAIRS, TEXT_MIN],
      [NON_TEXT_PAIRS, NON_TEXT_MIN],
    ]) {
      for (const [fg, bg] of pairs) {
        const r = ratio(resolve(theme, fg), resolve(theme, bg));
        if (r < min) out.push(`${name}: ${fg} on ${bg}: ${r.toFixed(2)} (needs ${min})`);
      }
    }
  }
  return out;
}

function selfTest() {
  const css = `:root { --ink: #000000; --bg: #ffffff; --soft: rgba(0, 0, 0, 0.5); }
    :root[data-theme="light"] { --bg: #000000; }`;
  const set = themes(css);
  const names = new Set([...TEXT_PAIRS, ...NON_TEXT_PAIRS].flat().flatMap((ref) => ref.split('/')));
  const flat = `:root { ${[...names].map((n) => `${n}: #777777;`).join(' ')} } :root[data-theme="light"] { --bg: #777777; }`;
  const same = `${css} :root:not([data-theme="dark"]) { --bg: #000000; }`;
  const apart = `${css} :root:not([data-theme="dark"]) { --bg: #111111; }`;
  const close = (x, y) => Math.abs(x - y) < 0.01;
  const checks = [
    close(ratio(resolve(set.dark, '--ink'), resolve(set.dark, '--bg')), 21),
    close(ratio(resolve(set.light, '--ink'), resolve(set.light, '--bg')), 1),
    resolve(set.dark, '--soft/--bg')[0] === 127.5,
    failures(flat).length === 2 * (TEXT_PAIRS.length + NON_TEXT_PAIRS.length) + 1,
    lightDiffers(same).length === 0,
    lightDiffers(apart).length === 1,
  ];
  if (!checks.every(Boolean)) throw new Error(`self-test failed: ${checks}`);
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const arg = process.argv[2];
  if (arg === '--self-test') selfTest();
  else {
    const bad = failures(readFileSync(arg, 'utf8'));
    for (const line of bad) console.log(line);
    process.exit(bad.length ? 1 : 0);
  }
}
