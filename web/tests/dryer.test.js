import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { html } from '../js/ui/html.js';
import { DryerView } from '../js/ui/DryerView.js';
import { runAction } from '../js/actions.js';
import { renderHtml, textOf } from './ui-helpers.js';
import { loadEnglish } from './helpers.js';
import { t } from '../js/i18n.js';

loadEnglish();

const read = (name) => JSON.parse(readFileSync(new URL(`../${name}`, import.meta.url), 'utf8'));
const presets = read('presets.json');

const lane = (material, rgba) => ({
  mode: 'idle',
  insert: true,
  error: null,
  last_event: null,
  tag: material
    ? { state: 'read', record: { brand: 'X', material, color_rgba: rgba } }
    : { state: 'unknown', record: null },
});
const unit = (dryer, lanes, over = {}) => ({
  chamber: 49,
  humidity: 28,
  ptc_left: 30,
  ptc_right: 31,
  lanes,
  dryer: { fans: {}, flaps: {}, ...dryer },
  ...over,
});
const FOUR = [lane('PLA', 'DD3333FF'), lane('PETG', '2288CCFF'), lane(null), lane('ABS', 'EEEEEEFF')];
const app = (status, over = {}) => ({
  status,
  online: true,
  klippy: 'ready',
  pending: new Set(),
  folds: {},
  presets,
  form: { preset: 1, temp: 60, hm: '6:00' },
  forms: {},
  dryerUnit: null,
  modeWarn: false,
  samples: [],
  conn: { unit: 'ace2k' },
  ...over,
});
const view = (a, calls = []) => {
  const ctx = { t, app: a, run: (...args) => calls.push(args) };
  return renderHtml(html`<${DryerView} app=${a} ctx=${ctx} />`);
};

const drying = (extra = {}) => ({ state: 'heating', target: 50, remaining: 79, mode: 'manual', ...extra });

test('a drying unit: the arc, the h:mm strip, Stop and the target in the ring', () => {
  const a = app(
    { ace2k: unit(drying(), FOUR) },
    {
      samples: [
        { t: 100, dry: 'heating', rem: 240 },
        { t: 160, dry: 'heating', rem: 239 },
      ],
    },
  );
  const out = view(a);
  assert.match(out, /class="arc"/);
  assert.ok(
    textOf(out).includes(t('dryer.drying_hm', { done: '2:41', total: '4:01' })) ||
      /Drying · \d:\d\d \/ \d:\d\d/.test(textOf(out)),
  );
  assert.ok(textOf(out).includes(t('dryer.stop')));
  assert.ok(textOf(out).includes('target 50.0'));
  assert.doesNotMatch(out, />Start drying</);
});

test('the strip reads done / total as h:mm', () => {
  const a = app(
    { ace2k: unit(drying({ remaining: 79 }), FOUR) },
    {
      samples: [
        { t: 0, dry: 'heating', rem: 240 },
        { t: 60, dry: 'heating', rem: 239 },
      ],
    },
  );
  // total = 241 min, 79 left: 162 min done
  assert.ok(textOf(view(a)).includes('Drying · 2:42 / 4:01'));
});

test('a drying unit whose total is unknown shows the time left', () => {
  const a = app({ ace2k: unit(drying({ remaining: 90 }), FOUR) });
  const out = textOf(view(a));
  assert.ok(out.includes(t('dryer.drying_left', { left: '1:30' })));
  assert.doesNotMatch(view(a), /class="arc"/, 'no arc without a known total');
});

test('an idle unit: no arc and no dot, presets with the suggestion, a h:mm field', () => {
  const a = app({ ace2k: unit({ state: 'idle', mode: 'manual' }, FOUR) });
  const out = view(a);
  assert.doesNotMatch(out, /class="arc"/);
  assert.match(out, /class="track"/);
  const text = textOf(out);
  assert.ok(text.includes('PLA · 45 °C · 6:00'));
  // PLA is the coolest material of the spools, so its preset carries the mark.
  assert.match(out, /class="chip sug"[^>]*>\s*PLA · 45 °C · 6:00/);
  assert.equal(out.match(/class="chip sug"/g).length, 1);
  assert.match(out, /value="6:00"/);
  assert.ok(text.includes(t('dryer.start')));
  assert.ok(text.includes('Idle'));
});

test('no known material, no suggestion', () => {
  const a = app({ ace2k: unit({ state: 'idle', mode: 'manual' }, [lane(null), lane(null), lane(null), lane(null)]) });
  assert.doesNotMatch(view(a), /chip sug/);
});

test('the spool dots follow the bays; an empty bay is dashed', () => {
  const out = view(app({ ace2k: unit({ state: 'idle' }, FOUR) }));
  assert.equal(out.match(/class="sp"/g).length, 3);
  assert.equal(out.match(/class="sp e"/g).length, 1);
  assert.ok(out.includes('background:#dd3333'));
  assert.ok(textOf(out).includes('PLA, PETG, ABS'));
});

test('a bad time disables Dry and says why; a bad temperature too', () => {
  const bad = view(
    app({ ace2k: unit({ state: 'idle' }, FOUR) }, { forms: { '': { preset: null, temp: 60, hm: '0:00' } } }),
  );
  assert.match(bad, /<button[^>]*class="btn go"[^>]*disabled/);
  assert.ok(textOf(bad).includes(t('dryer.bad_time')));
  assert.match(bad, /aria-invalid="true"/);
  const hot = view(
    app({ ace2k: unit({ state: 'idle' }, FOUR) }, { forms: { '': { preset: null, temp: 99, hm: '2:00' } } }),
  );
  assert.match(hot, /<button[^>]*class="btn go"[^>]*disabled/);
  assert.ok(textOf(hot).includes(t('dryer.bad_temp')));
  const ok = view(app({ ace2k: unit({ state: 'idle' }, FOUR) }));
  assert.doesNotMatch(ok, /<button[^>]*class="btn go"[^>]*disabled/);
  const offline = view(app({ ace2k: unit({ state: 'idle' }, FOUR) }, { online: false }));
  assert.match(offline, /<button[^>]*class="btn go"[^>]*disabled/);
  const busy = view(app({ ace2k: unit({ state: 'idle' }, FOUR) }, { pending: new Set(['dry:']) }));
  assert.ok(textOf(busy).includes(t('action.sending')));
});

test('the demo fault: a red card with the fault and Clear, and no arc', () => {
  const out = view(app(read('dev/demo-dryer-fault.json')));
  assert.match(out, /class="dcard bad"/);
  assert.match(out, /class="fault"/);
  assert.doesNotMatch(out, /class="arc"/);
  const text = textOf(out);
  assert.ok(text.includes(t('dryer.clear')));
  assert.doesNotMatch(out, />Start drying</);
});

test('a cooldown shows the rule and no controls', () => {
  const out = view(app({ ace2k: unit({ state: 'cooldown', mode: 'manual' }, FOUR) }));
  assert.ok(textOf(out).includes(t('dryer.cooldown_rule')));
  assert.doesNotMatch(out, />Start drying</);
});

test('three mode buttons; humidity only when allowed; the humidity threshold and line', () => {
  const dry = (over) =>
    app({
      ace2k: unit(
        { state: 'idle', mode: 'humidity', humidity_allowed: true, limit: 35, cold: true, rh: 41, ...over },
        FOUR,
      ),
    });
  const out = view(dry({}));
  assert.equal(out.match(/aria-pressed="(true|false)"[^>]*>(Manual|Humidity|During print)/g)?.length, 3);
  assert.ok(textOf(out).includes('35 %'));
  assert.ok(textOf(out).includes('waiting: box 41 %, limit 35 %'));
  const barred = view(dry({ humidity_allowed: false, mode: 'manual' }));
  assert.doesNotMatch(barred, />Humidity</);
  assert.doesNotMatch(
    view(app({ ace2k: unit({ state: 'idle' }, FOUR) })),
    /role="group"/,
    'no mode reported, no buttons',
  );
  const stopped = textOf(view(dry({ stopped: true, failures: 3 })));
  assert.ok(stopped.includes('stopped after 3 failed cycles'));
  const failures = textOf(view(dry({ failures: 1, cold: false })));
  assert.ok(failures.includes('box not steady yet') && failures.includes('1 failed cycle'));
  assert.ok(textOf(view(dry({ failures: 2 }))).includes('2 failed cycles'));
  const auto = textOf(view(app({ ace2k: unit(drying({ mode: 'print', bound: true }), FOUR) })));
  assert.ok(auto.includes('drying (auto) at 50 °C'));
});

test('the humidity warning stands on the clicked card and keeps Stop for a running cycle', () => {
  const st = (d) => ({
    ace2k: unit({ state: 'idle', mode: 'manual', humidity_allowed: true, max_failures: 4, ...d }, FOUR),
  });
  const out = view(app(st({}), { modeWarn: true }));
  assert.equal((out.match(/<li>/g) ?? []).length, 6);
  assert.ok(textOf(out).includes('in 4 cycles in a row'));
  assert.doesNotMatch(out, />Start drying</);
  const none = view(app(st({ max_failures: null }), { modeWarn: true }));
  assert.ok(textOf(none).includes('(dry_max_failures)'));
  const running = view(
    app(st({ state: 'heating', own: false, bound: true, mode: 'print', remaining: 10, target: 45 }), {
      modeWarn: true,
    }),
  );
  assert.ok(textOf(running).includes(t('dryer.warn_stops')));
  assert.ok(textOf(running).includes(t('dryer.stop')));
  assert.doesNotMatch(view(app(st({ humidity_allowed: false }), { modeWarn: true })), /<li>/);
});

test('several units: one card each, and the warning only on the unit it was asked on', () => {
  const status = {
    ace2k_units: { units: ['ace2k a', 'ace2k b'] },
    'ace2k a': unit({ state: 'idle', mode: 'manual', humidity_allowed: true }, FOUR),
    'ace2k b': unit(drying({ mode: 'manual', humidity_allowed: true }), FOUR),
  };
  const out = view(app(status, { modeWarn: true, dryerUnit: 'b' }));
  assert.equal(out.match(/<section/g).length, 2);
  assert.equal((out.match(/class="mode-warn"/g) ?? []).length, 1);
  assert.ok(textOf(out).includes('Unit a') && textOf(out).includes('Unit b'));
  const pend = view(app(status, { pending: new Set(['a|dry:']) }));
  assert.equal(textOf(pend).split(t('action.sending')).length - 1, 1);
});

test("the buttons call the actions with their card's unit", () => {
  const status = {
    ace2k_units: { units: ['ace2k a', 'ace2k b'] },
    'ace2k a': unit({ state: 'idle', mode: 'manual', humidity_allowed: true }, FOUR),
    'ace2k b': unit({ state: 'fault', fault: 'ntc' }, FOUR),
  };
  const calls = [];
  const a = app(status);
  const tree = html`<${DryerView} app=${a} ctx=${{ t, app: a, run: (...args) => calls.push(args) }} />`;
  const fire = (node, pred) => {
    const all = [];
    (function walk(n) {
      if (Array.isArray(n)) return n.forEach(walk);
      if (!n || typeof n !== 'object') return;
      if (typeof n.type === 'function') return walk(n.type(n.props));
      if (pred(n)) all.push(n);
      walk(n.props?.children);
    })(node);
    return all;
  };
  const buttons = fire(tree, (n) => n.type === 'button');
  const call = (label) =>
    buttons.filter((b) => textOf(renderHtml(html`<button>${b.props.children}</button>`)).startsWith(label));
  call(t('dryer.start'))[0].props.onClick();
  call(t('dryer.clear'))[0].props.onClick();
  call(t('dryer.mode_humidity'))[0].props.onClick();
  call(t('dryer.log'))[0].props.onClick();
  call(t('dryer.fans_off'))[0].props.onClick();
  call('PLA')[0].props.onClick();
  const inputs = fire(tree, (n) => n.type === 'input');
  inputs[0].props.onInput({ target: { value: '55' } });
  inputs[1].props.onInput({ target: { value: '3:00' } });
  assert.deepEqual(calls, [
    ['dry', { unit: 'a' }],
    ['dry_clear', { unit: 'b' }],
    ['dry_mode', { unit: 'a', mode: 'humidity' }],
    ['dry_log', { unit: 'a' }],
    ['fans_off', { unit: 'a' }],
    ['preset', { unit: 'a', k: '0' }],
    ['dry_field', { unit: 'a', field: 'temp' }, '55'],
    ['dry_field', { unit: 'a', field: 'hm' }, '3:00'],
  ]);
  assert.ok(runAction);
});

test('Advanced is folded by default, opens from app.folds, and offers fans and flaps only when idle', () => {
  const idle = view(app({ ace2k: unit({ state: 'idle', flaps: { bottom: 'open' }, fans: { left: true } }, FOUR) }));
  assert.match(idle, /<details id="adv-" class="adv">/);
  assert.ok(textOf(idle).includes(t('dryer.fans_on', { s: 60 })));
  assert.ok(textOf(idle).includes(t('dryer.log')));
  const open = view(
    app({ ace2k: unit({ state: 'heating', remaining: 5, target: 50, drive: 55 }, FOUR) }, { folds: { 'adv-': true } }),
  );
  assert.match(open, /<details id="adv-" class="adv" open/);
  assert.ok(!textOf(open).includes(t('dryer.fans_off')));
  assert.ok(textOf(open).includes(t('dryer.aim')));
  const off = view(app({ ace2k: unit({ state: 'idle' }, FOUR) }, { online: false }));
  assert.match(off, /<button[^>]*class="btn"[^>]*disabled[^>]*>Fans off/);
});

// Every button of a card with its click handler, found without a DOM. Card parts are plain functions, so they are called.
function clicks(a, calls) {
  const tree = html`<${DryerView} app=${a} ctx=${{ t, app: a, run: (...args) => calls.push(args) }} />`;
  const out = [];
  (function walk(n) {
    if (Array.isArray(n)) return n.forEach(walk);
    if (!n || typeof n !== 'object') return;
    if (typeof n.type === 'function') return walk(n.type(n.props));
    if (n.type === 'button') out.push([textOf(renderHtml(html`<b>${n.props.children}</b>`)), n.props.onClick]);
    walk(n.props?.children);
  })(tree);
  return out;
}

test('the warning, Stop, the flaps and the fans call their actions with the unit', () => {
  const warned = {
    ace2k: unit(
      { state: 'heating', mode: 'print', humidity_allowed: true, remaining: 5, target: 50, bound: true },
      FOUR,
    ),
  };
  const calls = [];
  const buttons = clicks(app(warned, { modeWarn: true }), calls);
  for (const label of [t('dryer.warn_enable'), t('dryer.warn_cancel'), t('dryer.stop')])
    buttons.find(([text]) => text === label)[1]();
  assert.deepEqual(calls, [
    ['dry_mode_confirm', { unit: '' }],
    ['dry_mode_cancel', { unit: '' }],
    ['dry_stop', { unit: '' }],
  ]);
  const idle = [];
  const manual = clicks(app({ ace2k: unit({ state: 'idle', mode: 'manual' }, FOUR) }), idle);
  for (const [text, click] of manual)
    if ([t('dryer.fans_on', { s: 60 }), t('dryer.open_verb'), t('dryer.close_verb')].includes(text)) click();
  assert.deepEqual(
    idle.map(([id, data]) => [id, data.which, data.open]),
    [
      ['fans_on', undefined, undefined],
      ['flap', 'bottom', '1'],
      ['flap', 'bottom', '0'],
      ['flap', 'rear', '1'],
      ['flap', 'rear', '0'],
    ],
  );
});

test('a card with no readings at all still draws', () => {
  const out = view(app({}, { presets: [] }));
  assert.match(out, /class="dcard"/);
  assert.match(out, /class="track"/);
});

test('the arc grows from the first minute and fills the ring at the end', () => {
  const start = app(
    { ace2k: unit(drying({ remaining: 241 }), FOUR) },
    { samples: [{ t: 0, dry: 'heating', rem: 240 }] },
  );
  assert.match(view(start), /stroke-dasharray="1\.4 339\.3"/);
  const over = app({ ace2k: unit(drying({ remaining: 0 }), FOUR) }, { samples: [{ t: 0, dry: 'heating', rem: 0 }] });
  assert.match(view(over), /class="arc"[^>]*stroke-dasharray="\d+\.\d [\d.]+"/);
});

test('with no spool anywhere, the dots are dashed and no material is named', () => {
  const out = view(app({ ace2k: unit({ state: 'idle' }, [{}, {}, {}, {}]) }));
  assert.equal(out.match(/class="sp e"/g).length, 4);
});

test('readings the unit has not reported show a dash, never NaN', () => {
  const noColour = view(
    app({ ace2k: unit({ state: 'idle' }, [lane('PLA', null), lane(null), lane(null), lane(null)]) }),
  );
  assert.match(noColour, /<i class="sp" title="PLA"><\/i>/);
  const running = textOf(view(app({ ace2k: unit({ state: 'heating', mode: 'print', bound: true }, FOUR) })));
  assert.ok(running.includes('drying (auto) at —') || running.includes('drying (auto) at -'), running);
  assert.doesNotMatch(running, /NaN|undefined|null/);
  const humid = textOf(
    view(
      app({
        ace2k: unit({ state: 'idle', mode: 'humidity', humidity_allowed: true, cold: true }, FOUR, { humidity: null }),
      }),
    ),
  );
  assert.ok(/waiting: box \S+ %, limit \S+ %/.test(humid));
  assert.doesNotMatch(humid, /NaN|undefined|null/);
  const printIdle = view(app({ ace2k: unit({ state: 'idle', mode: 'print' }, FOUR) }));
  assert.doesNotMatch(printIdle, /mode-line/);
});

test('a running cycle with no time left reported still shows the strip', () => {
  const a = app({ ace2k: unit({ state: 'heating', mode: 'manual', remaining: null }, FOUR) });
  assert.ok(textOf(view(a)).includes('Drying'));
});

test('each card shows its own form', () => {
  const status = {
    ace2k_units: { units: ['ace2k a', 'ace2k b'] },
    'ace2k a': unit({ state: 'idle', mode: 'manual' }, FOUR),
    'ace2k b': unit({ state: 'idle', mode: 'manual' }, FOUR),
  };
  const out = view(app(status, { forms: { a: { preset: null, temp: 52, hm: '1:15' } } }));
  const [cardA, cardB] = out.split('<section').slice(1);
  assert.ok(cardA.includes('value="52"') && cardA.includes('value="1:15"'));
  assert.ok(cardB.includes('value="60"') && cardB.includes('value="6:00"'), 'b keeps the starting values');
});

test('offline, Stop, Clear and the by-hand controls are disabled; the warning names no count before the host reports one', () => {
  const off = (status) => view(app(status, { online: false }));
  const disabledAfter = (out, label) => new RegExp(`<button[^>]*disabled[^>]*>${label}`).test(out);
  const run = off({ ace2k: unit(drying(), FOUR) });
  assert.ok(disabledAfter(run, t('dryer.stop')));
  const fault = off({ ace2k: unit({ state: 'fault', fault: 'ntc' }, FOUR) });
  assert.ok(disabledAfter(fault, t('dryer.clear')));
  const idle = off({ ace2k: unit({ state: 'idle', mode: 'manual' }, FOUR) });
  for (const label of [
    t('dryer.fans_on', { s: 60 }),
    t('dryer.fans_off'),
    t('dryer.open_verb'),
    t('dryer.close_verb'),
    t('dryer.log'),
  ])
    assert.ok(disabledAfter(idle, label), label);
  const online = view(app({ ace2k: unit({ state: 'idle', mode: 'manual' }, FOUR) }));
  assert.ok(!disabledAfter(online, t('dryer.fans_off')));
  const warned = view(
    app({ ace2k: unit({ state: 'idle', mode: 'manual', humidity_allowed: true }, FOUR) }, { modeWarn: true }),
  );
  assert.ok(textOf(warned).includes(t('dryer.warn_4_unknown')));
  assert.doesNotMatch(textOf(warned), /in \d+ cycles/);
});

// The buttons of a rendered tree, through its function components.
const buttonsOf = (node) => {
  const all = [];
  (function walk(n) {
    if (Array.isArray(n)) return n.forEach(walk);
    if (!n || typeof n !== 'object') return;
    if (typeof n.type === 'function') return walk(n.type(n.props));
    if (n.type === 'button') all.push(n);
    walk(n.props?.children);
  })(node);
  return all;
};

test("the dryer log: busy while it is read, then its lines under the unit's own card, with Close", () => {
  const status = {
    ace2k_units: { units: ['ace2k a', 'ace2k b'] },
    'ace2k a': unit({ state: 'idle', mode: 'manual' }, FOUR),
    'ace2k b': unit({ state: 'idle', mode: 'manual' }, FOUR),
  };
  const waiting = view(app(status, { dryerLog: { unit: 'b', lines: null, token: 1 } }));
  // One read at a time on the page: every card's button waits.
  assert.equal(textOf(waiting).split(t('dryer.log_reading')).length - 1, 2);
  assert.equal((waiting.match(/aria-busy="true"/g) ?? []).length, 2);
  assert.doesNotMatch(waiting, /class="dlog"/);
  const lines = [
    '3 cycles started, 2 completed, 1 fault; 0.4 h of heating, 36 Wh at 360 W',
    '#1 cycle 2 at 0.3 h: stopped',
  ];
  const a = app(status, { dryerLog: { unit: 'b', lines, token: 1 } });
  const out = view(a);
  assert.equal((out.match(/class="dlog"/g) ?? []).length, 1);
  const text = textOf(out);
  assert.ok(text.indexOf('Unit b') < text.indexOf(t('dryer.log_title', { name: 'b' })));
  assert.ok(text.includes(lines[0]) && text.includes(lines[1]));
  assert.ok(!text.includes(t('dryer.log_reading')));
  const calls = [];
  const tree = html`<${DryerView} app=${a} ctx=${{ t, app: a, run: (...args) => calls.push(args) }} />`;
  const close = buttonsOf(tree).filter((b) => b.props.children === t('dryer.log_close'));
  for (const b of close) b.props.onClick(); // the flaps' Close buttons too
  assert.deepEqual(
    calls.filter((c) => c[0] === 'dry_log_close'),
    [['dry_log_close', { unit: 'b' }]],
  );
});

test('the dryer log of the unnamed unit is titled Dryer log', () => {
  const out = view(
    app({ ace2k: unit({ state: 'idle' }, FOUR) }, { dryerLog: { unit: null, lines: ['0 cycles started'], token: 1 } }),
  );
  assert.match(out, new RegExp(`<b>${t('dryer.log')}</b>`));
});
