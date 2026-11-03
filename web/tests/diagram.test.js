import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { html } from '../js/ui/html.js';
import { Diagram, footerName, footLayout, wide } from '../js/ui/Diagram.js';
import { diagramModel, headLanesText, openHead } from '../js/diagram-model.js';
import { chipText, laneChip } from '../js/lane-state.js';
import { LANE_ERRORS } from '../js/model.js';
import { laneOf, sliceFor } from '../js/model.js';
import { App } from '../js/ui/App.js';
import { ACTIONS } from '../js/actions.js';
import { renderHtml, textOf } from './ui-helpers.js';
import { loadEnglish } from './helpers.js';
import { t } from '../js/i18n.js';

loadEnglish();

const demo = (name) => JSON.parse(readFileSync(new URL(`../dev/demo-${name}.json`, import.meta.url), 'utf8'));
const appOf = (status) => ({ status, online: true, klippy: 'ready', ui: { tab: 'units', panel: null, sheet: false } });
const draw = (status, run = () => {}) => {
  const app = appOf(status);
  return renderHtml(html`<${Diagram} app=${app} ctx=${{ app, t, run }} />`);
};

// Every element vnode of a component tree that matches pred. Function components are called with their props.
function vnodes(node, pred, out = []) {
  if (Array.isArray(node)) {
    for (const n of node) vnodes(n, pred, out);
    return out;
  }
  if (!node || typeof node !== 'object') return out;
  if (typeof node.type === 'function') return vnodes(node.type(node.props), pred, out);
  if (pred(node)) out.push(node);
  vnodes(node.props?.children, pred, out);
  return out;
}
const calls = (status) => {
  const seen = [];
  const app = appOf(status);
  const tree = html`<${Diagram} app=${app} ctx=${{ app, t, run: (id, data) => seen.push([id, data]) }} />`;
  return { seen, nodes: vnodes(tree, (n) => n.props?.role === 'button') };
};

test("the operator's set-up: four heads, the U1 spool on T0, every lane of a, b and c", () => {
  const out = draw(demo('mine'));
  const text = textOf(out);
  for (const h of ['T0', 'T1', 'T2', 'T3']) assert.ok(text.includes(h), h);
  assert.ok(text.includes(t('units.u1_spool')));
  for (const u of 'abc') for (const n of [1, 2, 3, 4]) assert.ok(out.includes(`${u}:${n}`), `${u}:${n}`);
  // One live path per loaded head: T1, T2 and T3 hold a lane, T0 holds the U1's spool.
  assert.equal(out.match(/class="rail live"/g).length, 3);
  assert.ok(text.includes(t('units.lanes', { n: 4, list: 'a:1–4' })));
  // The legend sits under the diagram.
  assert.ok(text.includes(t('units.legend_live')) && text.includes(t('units.legend_empty')));
});

test('one unit: lanes by number, a bay with no spool drawn empty, the dashed rail', () => {
  const out = draw(demo('one'));
  assert.equal(out.match(/class="spool empty"/g).length, 1);
  assert.ok(out.includes('class="rail dashed"'));
  assert.ok(textOf(out).includes(t('units.empty')));
  assert.ok(textOf(out).includes(t('units.lanes_one', { list: '1' })));
  assert.ok(textOf(out).includes(t('units.unnamed')));
  // Head T2 has no lane in it: it says so with a dash.
  assert.match(textOf(out), /T2 — /);
});

test('the unknown set-up: "?" for a spool with no material, 🏷 for a tag, ✎ for a hand-set spool', () => {
  const out = draw(demo('unknown'));
  const text = textOf(out);
  assert.ok(out.includes('class="spool unknown"'));
  assert.ok(text.includes(t('units.unknown_mark')));
  assert.ok(text.includes(t('units.tag_mark')) && text.includes(t('units.hand_mark')));
  assert.ok(text.includes(t('units.no_material')));
});

test('the active bay and the head bar take the filament colour; pale and deep colours are marked', () => {
  const out = draw(demo('mine'));
  // a:2 holds black PLA: its spool carries data-dark-fil. T0 holds the U1's white PLA: its bar carries data-light-fil.
  assert.ok(out.includes('data-dark-fil'));
  assert.ok(out.includes('data-light-fil'));
  assert.match(out, /class="bay-tile on" style="--fil:#[0-9a-f]{6}"/);
  // No colour literal of the page's own: every colour in the markup is a spool's.
  const m = diagramModel(appOf(demo('mine')));
  const own = new Set([
    ...m.units.flatMap((u) => u.lanes.map((l) => l.spool?.colour)),
    ...m.heads.map((h) => h.u1Spool?.colour),
  ]);
  for (const [hex] of out.matchAll(/#[0-9a-f]{3,8}\b/gi)) assert.ok(own.has(hex), hex);
});

test('a head, a unit footer, a bay and "♨ dry" each open their panel', () => {
  const { seen, nodes } = calls(demo('mine'));
  for (const n of nodes) n.props.onClick();
  const kinds = seen.map(([id, d]) => (id === 'panel' ? d.kind : id));
  assert.equal(kinds.filter((k) => k === 'head').length, 4);
  assert.equal(kinds.filter((k) => k === 'unit').length, 3);
  assert.equal(kinds.filter((k) => k === 'bay').length, 12);
  assert.equal(kinds.filter((k) => k === 'dry_shortcut').length, 3);
  assert.deepEqual(seen.find(([id, d]) => id === 'panel' && d.kind === 'bay')[1], { kind: 'bay', key: 'a:1' });
  assert.deepEqual(seen.find(([id, d]) => id === 'panel' && d.kind === 'head')[1], { kind: 'head', id: '0' });
  assert.deepEqual(seen.find(([id, d]) => id === 'panel' && d.kind === 'unit')[1], { kind: 'unit', id: 'a' });
  assert.deepEqual(seen.find(([id]) => id === 'dry_shortcut')[1], { unit: 'a' });
});

test('Enter and Space press a button of the diagram; other keys do nothing', () => {
  const { seen, nodes } = calls(demo('one'));
  const bay = nodes.find((n) => n.props.class === 'bay');
  let prevented = 0;
  const key = (k) => bay.props.onKeyDown({ key: k, preventDefault: () => prevented++ });
  key('Enter');
  key(' ');
  key('a');
  assert.equal(seen.length, 2);
  assert.equal(prevented, 2);
  // One unit: its footer and dry shortcut name it by ''.
  const unit = calls(demo('one'));
  for (const n of unit.nodes) n.props.onClick();
  assert.deepEqual(unit.seen.find(([id, d]) => id === 'panel' && d.kind === 'unit')[1], { kind: 'unit', id: '' });
});

test('the model: lanes, heads, the U1 spool and the lane loaded in each head', () => {
  const m = diagramModel(appOf(demo('mine')));
  assert.deepEqual(
    m.units.map((u) => u.name),
    ['a', 'b', 'c'],
  );
  assert.deepEqual(m.heads[0].u1Spool, { colour: '#eeeeee', material: 'PLA' });
  assert.deepEqual(
    m.heads.map((h) => h.loaded),
    [null, 'a:1', 'b:1', 'c:1'],
  );
  assert.deepEqual(m.units[1].lanes[2].spool, { colour: '#111111', material: 'PETG', tag: false });
  // A head with no lane and no setting on the U1: a U1 spool of unknown colour and material.
  const bare = diagramModel(appOf({ ...demo('mine'), print_task_config: {} }));
  assert.deepEqual(bare.heads[0].u1Spool, { colour: null, material: null });
  // No status at all: one unnamed unit, its four empty lanes on their own heads.
  const none = diagramModel(appOf({}));
  assert.deepEqual(
    none.units.map((u) => [u.name, u.lanes.map((l) => [l.key, l.head, l.empty])]),
    [
      [
        null,
        [
          ['1', 0, true],
          ['2', 1, true],
          ['3', 2, true],
          ['4', 3, true],
        ],
      ],
    ],
  );
});

test("a head's lanes in runs: a:1–4, a:1 · b:1, 1–2 · 4", () => {
  const lane = (key, n) => ({ key, n, head: 0, empty: false, spool: null, current: false, filled: false });
  assert.equal(
    headLanesText([
      { unit: 'a', lane: lane('a:1', 1) },
      { unit: 'a', lane: lane('a:2', 2) },
      { unit: 'a', lane: lane('a:3', 3) },
      { unit: 'a', lane: lane('a:4', 4) },
    ]),
    'a:1–4',
  );
  assert.equal(
    headLanesText([
      { unit: 'a', lane: lane('a:1', 1) },
      { unit: 'b', lane: lane('b:1', 1) },
    ]),
    'a:1 · b:1',
  );
  assert.equal(
    headLanesText([
      { unit: null, lane: lane('1', 1) },
      { unit: null, lane: lane('2', 2) },
      { unit: null, lane: lane('4', 4) },
    ]),
    '1–2 · 4',
  );
});

test('a unit footer shows its link and self-test as a dot, and its temperature and humidity', () => {
  const s = demo('mine');
  s['ace2k b'].health = { ok: false, failing: ['ntc_left'] };
  s['ace2k c'].link_proven = false;
  delete s['ace2k a'].chamber;
  const out = draw(s);
  assert.deepEqual(
    [...out.matchAll(/class="(dot-\w+)"/g)].map((m) => m[1]),
    ['dot-ok', 'dot-warn', 'dot-err'],
  );
  assert.ok(textOf(out).includes('— · 20 %'));
  assert.ok(textOf(out).includes('27 °C · 52 %'));
});

test('the Units tab draws the diagram', () => {
  const app = appOf(demo('four'));
  const out = renderHtml(html`<${App} app=${app} ctx=${{ app, t, run: () => {} }} />`);
  assert.ok(out.includes('class="dg"'));
  assert.equal(out.match(/class="rail live"/g).length, 4);
});

test('dry_shortcut opens the unit panel on its dryer', () => {
  const app = appOf({});
  let renders = 0;
  ACTIONS.dry_shortcut({ app, render: () => renders++ }, { dataset: { unit: 'b' } });
  assert.deepEqual(app.ui.panel, { kind: 'unit', id: 'b', focus: 'dryer' });
  ACTIONS.dry_shortcut({ app, render: () => renders++ }, { dataset: {} });
  assert.deepEqual(app.ui.panel, { kind: 'unit', id: '', focus: 'dryer' });
  assert.equal(renders, 2);
});

test('the active bay of a pale or black filament gets a thin ring the theme can show', () => {
  // demo-one: lane 2 holds black PETG in T1, lane 4 light grey PLA in T3; lane 1's red needs no ring.
  // demo-printing: b:1 holds black PLA in T2.
  const one = draw(demo('one'));
  assert.equal(one.match(/class="deep-ring"/g).length, 1);
  assert.equal(one.match(/class="pale-ring"/g).length, 1);
  assert.equal(draw(demo('printing')).match(/class="deep-ring"/g).length, 1);
  assert.ok(draw(demo('mine')).match(/class="bar"[^>]*data-light-fil/));
});

test('a lane in error: its bay outlined in red, a "!" badge and the error word in words', () => {
  // demo-unknown: lane 1 is stuck (and loaded in T0).
  const out = draw(demo('unknown'));
  assert.equal(out.match(/class="bay-tile on err"/g).length, 1);
  assert.equal(out.match(/class="err-badge"/g).length, 1);
  assert.match(out, /<text class="err-word"[^>]*>Stuck<\/text>/);
  assert.ok(out.includes(`aria-label="Bay 1: PLA, Stuck, to T0"`));
  // An error the page does not know still says Error; a moving lane gets no mark.
  const odd = demo('unknown');
  odd.ace2k.lanes[0].error = 'brand_new';
  assert.match(draw(odd), /class="err-word"[^>]*>Error</);
  const moving = demo('unknown');
  moving.ace2k.lanes[0] = { ...moving.ace2k.lanes[0], mode: 'feeding', error: null };
  const calm = draw(moving);
  assert.ok(!calm.includes('err-badge') && !calm.includes('err-word') && !calm.includes(' err"'));
});

test('a head says "lane ?" in amber while its lane is unknown, and "—" only when nothing is loaded', () => {
  const s = demo('mine');
  s.ace2k_u1.current[1] = null;
  s['filament_motion_sensor e1_filament'] = { filament_detected: true };
  const unknown = draw(s);
  assert.match(unknown, /<text class="warn"[^>]*>lane \?<\/text>/);
  assert.equal(diagramModel(appOf(s)).heads[1].unknown, true);
  s['filament_motion_sensor e1_filament'] = { filament_detected: false };
  const empty = draw(s);
  assert.ok(!empty.includes('class="warn"'));
  assert.match(textOf(empty), /T1 — 4 lanes/);
});

test('the footer shows "Unit <name>" while it fits, else the name alone, capped; the full name is the title', () => {
  assert.equal(footerName('Unit', null), 'Unit');
  assert.equal(footerName('Unit ab', 'ab'), 'Unit ab');
  assert.equal(footerName('Unit abcd', 'abcd'), 'Unit abcd');
  assert.equal(footerName('Unit abcde', 'abcde'), 'abcde');
  assert.equal(footerName('Unit abcdefghi', 'abcdefghi'), 'abcdefghi');
  assert.equal(footerName('Unit abcdefghij', 'abcdefghij'), 'abcdefgh…');
  const named = (name) => {
    const s = demo('mine');
    s.ace2k_units.units = [`ace2k ${name}`, 'ace2k b', 'ace2k c'];
    s[`ace2k ${name}`] = s['ace2k a'];
    s.ace2k_u1.heads[1] = s.ace2k_u1.heads[1].map((x) => ({ ...x, unit: name }));
    return draw(s);
  };
  const long = named('abcdefghij');
  assert.ok(long.includes('<title>Unit abcdefghij</title>'));
  assert.match(textOf(long), /Unit abcdefghij abcdefgh… \d+ °C/);
  const short = named('ab');
  assert.ok(short.includes('<title>Unit ab</title>'));
  assert.match(textOf(short), /Unit ab Unit ab \d+ °C/);
});

test('with the longest shown name and the widest reading, the readings end left of "♨ dry"', () => {
  // 11 px text is about 6.5 px a character. The widest reading the footer writes is "100 °C · 100 %".
  const SMALL = 6.5;
  const widest = '100 °C · 100 %';
  for (const shown of ['Unit', 'Unit abcd', footerName('Unit abcdefghij', 'abcdefghij')]) {
    const f = footLayout(shown);
    const readingsEnd = f.readings + widest.length * SMALL;
    const dryStart = f.dryEnd - t('units.dry').length * SMALL;
    assert.ok(readingsEnd + 4 <= dryStart, `${shown}: readings end ${readingsEnd}, dry starts ${dryStart}`);
  }
  assert.ok(footLayout('Unit a').dot < footLayout('abcdefgh…').dot);
});

test("every error's tile word is one word of at most 8 characters; the full words stay in the title", () => {
  for (const e of LANE_ERRORS) {
    const word = t(`lane.error_short.${e}`);
    assert.ok(word.length <= 8 && !word.includes(' ') && word !== `lane.error_short.${e}`, `${e}: ${word}`);
  }
  const s = demo('unknown');
  s.ace2k.lanes[0].error = 'motor_stalled';
  const out = draw(s);
  assert.match(out, /<text class="err-word"[^>]*clip-path="url\(#err-clip-0-0\)"[^>]*>Stalled<\/text>/);
  assert.ok(out.includes('<clipPath id="err-clip-0-0">'));
  assert.ok(out.includes('<title>Bay 1: PLA, Motor stalled, to T0</title>'));
  assert.equal(diagramModel(appOf(s)).units[0].lanes[0].errorShort, 'lane.error_short.motor_stalled');
});

test('a diagram wider than 1200 keeps its scale: the svg gets a minimum width and the tab scrolls', () => {
  assert.equal(wide(1200), undefined);
  assert.equal(wide(2360), '--dg-min:2360px');
  const s = demo('four');
  for (const n of 'efgh') {
    s.ace2k_units.units.push(`ace2k ${n}`);
    s[`ace2k ${n}`] = s['ace2k a'];
  }
  assert.match(draw(s), /<svg class="dg" viewBox="0 0 2360 520" style="--dg-min:2360px"/);
  assert.match(draw(demo('four')), /<svg class="dg" viewBox="0 0 1200 520" role="group"/);
});

test("every bay shows its lane's state in the bay card's word, between the material and the head", () => {
  // demo-one: lane 1 follows, lane 2 is in the bay, lane 3 is empty, lane 4 is stuck.
  const s = demo('one');
  const out = draw(s);
  const re = /<text class="state-word s-([a-z_]+)"[^>]*>([^<]*)<\/text>/g;
  const words = [...out.matchAll(re)].map((m) => [m[1], m[2]]);
  const slice = sliceFor(s, null);
  const expected = [0, 1].map((i) => [laneChip(slice, i), chipText(laneOf(slice, i), laneChip(slice, i))]);
  assert.deepEqual(words, expected);
  assert.equal(words[0][1], 'Following');
  // The empty bay says only "empty"; the stuck bay keeps its short red error word.
  assert.equal(out.match(/class="state-word/g).length, 2);
  assert.match(out, /<text class="err-word"[^>]*>Stuck<\/text>/);
  // The word sits under the material and above the head chip.
  const y = Number(/<text class="state-word[^"]*" x="[^"]*" y="([0-9.]+)"/.exec(out)[1]);
  const mat = Number(/<text class="small b" x="[^"]*" y="([0-9.]+)"/.exec(out)[1]);
  const chip = Number(/<rect class="chip[^"]*" x="[^"]*" y="([0-9.]+)"/.exec(out)[1]);
  assert.ok(mat < y && y < chip);
});

test('a state word too long for the bay is squeezed to the tile, never wider', () => {
  const s = demo('one');
  const out = draw(s);
  // "Following" has 9 letters: it is fitted to the tile's width. Lane 2's shorter word keeps its own width.
  assert.match(out, /<text class="state-word s-following"[^>]*textLength="50"[^>]*>Following</);
  const second = [...out.matchAll(/<text class="state-word[^>]*>/g)][1][0];
  assert.ok(!second.includes('textLength'));
});

test('the head whose panel is open has the selected ring and aria-current, gone when it closes', () => {
  const app = { ...appOf(demo('mine')), ui: { tab: 'units', panel: { kind: 'head', id: '1' }, sheet: false } };
  const out = renderHtml(html`<${Diagram} app=${app} ctx=${{ app, t, run: () => {} }} />`);
  assert.equal((out.match(/class="sel-ring"/g) ?? []).length, 1);
  assert.match(out, /<g class="head sel" aria-current="true"[^>]*aria-label="T1/);
  assert.equal((out.match(/aria-current="true"/g) ?? []).length, 1);
  assert.ok(!draw(demo('mine')).includes('sel-ring'));
  // A bay's panel rings the bay, never a head.
  const bay = { ...app, ui: { ...app.ui, panel: { kind: 'bay', key: 'a:2' } } };
  assert.ok(
    !/class="head sel"/.test(renderHtml(html`<${Diagram} app=${bay} ctx=${{ app: bay, t, run: () => {} }} />`)),
  );
  const css = readFileSync(new URL('../css/units.css', import.meta.url), 'utf8');
  assert.match(css, /\.dg \.head\.sel \.sel-ring \{/);
});

test('openHead reads the head panel id as the side panel does: 0-3, else none', () => {
  const at = (panel) => openHead({ ui: { panel } });
  assert.deepEqual(
    [at({ kind: 'head', id: '0' }), at({ kind: 'head', id: '3' }), at({ kind: 'bay', key: '1' }), at(null)],
    [0, 3, null, null],
  );
  for (const id of ['4', '-1', '1.5', '', 'x', undefined]) assert.equal(at({ kind: 'head', id }), null, String(id));
});
