// Renders every demo scenario and tab of the page to PNGs, and fails on console errors or overflow.
//
// Usage: node visual_check.mjs OUT_DIR      (Chrome is CHROME, or the macOS default path)
//        node visual_check.mjs --self-test
//
// It serves web/ on a free port and starts headless Chrome with a fresh profile. For each
// scenario x tab x theme x width it opens the demo page through the DevTools protocol (node's own
// fetch and WebSocket) and writes <scenario>-<tab>-<theme>-<width>.png. A page fails when it logs a
// console error, throws, hits the render queue's runaway warning, mounts more or less than one
// .shell, or is wider than the window. Chrome and the server are stopped at the end, also on failure.
// A few more shots open a demo file's plan (&file=): a guided step, the eject-all confirmation (a click on the
// card's eject button) and the spools in place. Such a shot waits for its own element (wait) before it is taken.

import { spawn } from 'node:child_process';
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { createServer } from 'node:http';
import { tmpdir } from 'node:os';
import { extname, join, normalize } from 'node:path';
import { fileURLToPath } from 'node:url';

const WEB = fileURLToPath(new URL('../web/', import.meta.url));
const CHROME = process.env.CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
export const SCENARIOS = ['status', 'one', 'mine', 'four', 'unknown', 'printing', 'dryer-fault'];
export const TABS = ['units', 'print', 'dryer', 'history'];
export const THEMES = ['dark', 'light'];
export const WIDTHS = [1360, 375];
const RUNAWAY = 'render: stopped after';
const TYPES = {
  '.html': 'text/html',
  '.js': 'text/javascript',
  '.css': 'text/css',
  '.json': 'application/json',
  '.jsonl': 'text/plain',
  '.svg': 'image/svg+xml',
};

// The plan view's shots on demo-four: step 1 of phone-stand (wide and phone), its eject-all confirmation, the
// phone's start options open in their sheet, the colour picker open beside layout A, and calibration-cube, whose one
// colour is already in its bay. click is a selector clicked once the page is ready.
const PLAN_SHOTS = [
  { scenario: 'plan-step1', file: 'phone-stand.gcode', wait: '.guide', widths: [1440, 1100, 900, 375], height: 900 },
  { scenario: 'plan-reading', file: 'big-9-colours.gcode', wait: '.rbar', widths: [1440, 375], height: 900 },
  {
    scenario: 'plan-other',
    file: 'phone-stand.gcode',
    other: 'calibration-cube.gcode',
    wait: '.plan.other',
    widths: [1440, 375],
    height: 900,
  },
  { scenario: 'plan-eject', file: 'phone-stand.gcode', wait: '.mbox', click: '.btn.ej', widths: [1360] },
  {
    scenario: 'plan-opts',
    file: 'phone-stand.gcode',
    wait: '.side.sheet[data-kind="opts"] .opts',
    click: '.pbar.compact .btn[aria-haspopup="dialog"]',
    widths: [375],
  },
  { scenario: 'plan-in-place', file: 'calibration-cube.gcode', wait: '.guide.done', widths: [1360] },
  // Layout A with the colour picker open: the side panel takes its share, and the plan falls to one column when the
  // diagram's column would be too narrow to read.
  {
    scenario: 'plan-picker',
    file: 'phone-stand.gcode',
    wait: '.side[data-kind="pick"]',
    click: '.crow',
    widths: [1440, 1100],
    height: 900,
  },
];

/**
 * Every shot as { scenario, tab, theme, width, name, url }, the url relative to the server root; a plan shot also
 * has wait (a selector to wait for) and may have click (a selector to click first).
 */
export function plan() {
  const shots = [];
  for (const scenario of SCENARIOS)
    for (const tab of TABS)
      for (const theme of THEMES)
        for (const width of WIDTHS) {
          const demo = scenario === 'status' ? 'demo' : `demo=${scenario}`;
          const name = `${scenario}-${tab}-${theme}-${width}.png`;
          shots.push({ scenario, tab, theme, width, name, url: `/?${demo}&tab=${tab}` });
        }
  for (const { scenario, file, other, wait, click, widths, height } of PLAN_SHOTS)
    for (const theme of THEMES)
      for (const width of widths) {
        const name = `${scenario}-${theme}-${width}.png`;
        const url = `/?demo=four&tab=print&file=${file}${other ? `&other=${other}` : ''}`;
        const extra = { ...(click ? { click } : {}), ...(height ? { height } : {}) };
        shots.push({ scenario, tab: 'print', theme, width, name, url, wait, ...extra });
      }
  return shots;
}

/** The problems found on a page, as strings; empty when the page is fine. */
export function judge({ errors, shells, broken, tab, want, scrollWidth, innerWidth }) {
  const out = [...errors];
  if (shells !== 1) out.push(`${shells} .shell elements, expected 1`);
  if (broken > 0) out.push(`${broken} .broken element(s): a component threw while rendering`);
  if (tab !== want) out.push(`tab body shows "${tab}", expected "${want}"`);
  if (scrollWidth > innerWidth) out.push(`horizontal overflow: scrollWidth ${scrollWidth} > ${innerWidth}`);
  return out;
}

/** Serves WEB on a free port; resolves to { server, port }. */
function serve() {
  const server = createServer((req, res) => {
    const path = normalize(decodeURIComponent(new URL(req.url, 'http://x').pathname)).replace(/^(\.\.[/\\])+/, '');
    try {
      const file = path.endsWith('/') ? `${path}index.html` : path;
      const body = readFileSync(join(WEB, file));
      res.writeHead(200, { 'content-type': TYPES[extname(file)] || 'application/octet-stream' });
      res.end(body);
    } catch {
      res.writeHead(404).end();
    }
  });
  return new Promise((resolve) =>
    server.listen(0, '127.0.0.1', () => resolve({ server, port: server.address().port })),
  );
}

/** Starts headless Chrome; resolves to { child, port } once the DevTools port is announced. */
function launch(profile) {
  const args = ['--headless=new', '--remote-debugging-port=0', `--user-data-dir=${profile}`];
  const flags = [...args, '--no-first-run', '--disable-gpu', 'about:blank'];
  const child = spawn(CHROME, flags, { stdio: ['ignore', 'ignore', 'pipe'] });
  return new Promise((resolve, reject) => {
    let log = '';
    const timer = setTimeout(() => reject(new Error('Chrome gave no DevTools port in 20 s')), 20000);
    child.on('error', (e) => reject(new Error(`cannot start Chrome at ${CHROME} (set CHROME): ${e.message}`)));
    child.on('exit', (code) => reject(new Error(`Chrome exited early (${code})`)));
    child.stderr.on('data', (d) => {
      log += d;
      const m = log.match(/DevTools listening on ws:\/\/[^:]+:(\d+)\//);
      if (m) {
        clearTimeout(timer);
        resolve({ child, port: Number(m[1]) });
      }
    });
  });
}

/** One page: opens a target, runs shots through it, closes it. */
async function withPage(chromePort, fn) {
  const target = await (await fetch(`http://127.0.0.1:${chromePort}/json/new?about:blank`, { method: 'PUT' })).json();
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((ok, bad) => {
    ws.onopen = ok;
    ws.onerror = bad;
  });
  let id = 0;
  const pending = new Map();
  const listeners = [];
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.id && pending.has(msg.id)) {
      const { ok, bad } = pending.get(msg.id);
      pending.delete(msg.id);
      if (msg.error) bad(new Error(msg.error.message));
      else ok(msg.result);
    } else if (msg.method) for (const l of listeners) l(msg);
  };
  const send = (method, params = {}) =>
    new Promise((ok, bad) => {
      pending.set(++id, { ok, bad });
      ws.send(JSON.stringify({ id, method, params }));
    });
  try {
    return await fn(send, (l) => listeners.push(l));
  } finally {
    ws.close();
    await fetch(`http://127.0.0.1:${chromePort}/json/close/${target.id}`).catch(() => {});
  }
}

// Read in the page: the tab body shown, whether it has content (the wide Units tab draws an svg,
// the phone one lists), and what the checks count.
const PROBE = `(() => {
  const body = document.querySelector('#tab-body');
  return {
    shells: document.querySelectorAll('.shell').length,
    broken: document.querySelectorAll('.broken').length,
    tab: body ? body.dataset.tab : null,
    filled: !!body && body.children.length > 0 && body.querySelector('svg, li, button, .card, p') !== null,
    scrollWidth: document.documentElement.scrollWidth,
    innerWidth: window.innerWidth,
  };
})()`;

/** Renders one shot; resolves to its problems. */
async function shoot(send, listen, base, shot, out) {
  const errors = [];
  let loaded;
  const loadedP = new Promise((ok) => (loaded = ok));
  listen((msg) => {
    if (msg.method === 'Page.loadEventFired') loaded();
    else if (msg.method === 'Runtime.exceptionThrown')
      errors.push(
        `exception: ${msg.params.exceptionDetails.exception?.description ?? msg.params.exceptionDetails.text}`,
      );
    else if (msg.method === 'Runtime.consoleAPICalled') {
      const { type, args } = msg.params;
      const text = args.map((a) => a.value ?? a.description ?? '').join(' ');
      if (type === 'error') errors.push(`console.error: ${text}`);
      else if (type === 'warn' && text.startsWith(RUNAWAY)) errors.push(`console.warn: ${text}`);
    } else if (msg.method === 'Log.entryAdded' && msg.params.entry.level === 'error')
      errors.push(`log: ${msg.params.entry.text} ${msg.params.entry.url ?? ''}`);
  });
  await send('Page.enable');
  await send('Runtime.enable');
  await send('Log.enable');
  await send('Emulation.setDeviceMetricsOverride', {
    width: shot.width,
    height: shot.height ?? 900,
    deviceScaleFactor: 1,
    mobile: shot.width < 600,
  });
  await send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-color-scheme', value: shot.theme }] });
  await send('Page.navigate', { url: base + shot.url });
  await loadedP;
  const probe = async () => (await send('Runtime.evaluate', { returnByValue: true, expression: PROBE })).result.value;
  let state = await probe();
  for (let i = 0; i < 100 && !(state.tab === shot.tab && state.filled); i++) {
    await new Promise((r) => setTimeout(r, 100));
    state = await probe();
  }
  if (!(state.tab === shot.tab && state.filled)) errors.push(`script: tab "${shot.tab}" did not render within 10 s`);
  if (shot.wait) {
    const has = async (sel) =>
      (
        await send('Runtime.evaluate', {
          returnByValue: true,
          expression: `!!document.querySelector(${JSON.stringify(sel)})`,
        })
      ).result.value;
    const until = async (sel) => {
      for (let i = 0; i < 100 && !(await has(sel)); i++) await new Promise((r) => setTimeout(r, 100));
      return has(sel);
    };
    if (shot.click) {
      if (await until(shot.click))
        await send('Runtime.evaluate', { expression: `document.querySelector(${JSON.stringify(shot.click)}).click()` });
      else errors.push(`script: ${shot.click} did not render within 10 s`);
    }
    if (!(await until(shot.wait))) errors.push(`script: ${shot.wait} did not render within 10 s`);
  }
  await new Promise((r) => setTimeout(r, 300));
  state = await probe();
  const { data } = await send('Page.captureScreenshot', { format: 'png' });
  writeFileSync(join(out, shot.name), Buffer.from(data, 'base64'));
  return judge({ errors, ...state, want: shot.tab });
}

/** Runs the whole plan; resolves to the number of failed shots. */
async function main(out) {
  mkdirSync(out, { recursive: true });
  const profile = mkdtempSync(join(tmpdir(), 'ace2k-visual-'));
  let chrome;
  let web;
  let failed = 0;
  let count = 0;
  const stop = async () => {
    if (chrome) {
      const gone = new Promise((ok) => chrome.child.once('exit', ok));
      chrome.child.kill('SIGTERM');
      await Promise.race([gone, new Promise((ok) => setTimeout(ok, 5000))]);
      chrome.child.kill('SIGKILL');
    }
    web?.server.close();
    rmSync(profile, { recursive: true, force: true, maxRetries: 10, retryDelay: 300 });
  };
  for (const sig of ['SIGINT', 'SIGTERM']) process.once(sig, () => stop().finally(() => process.exit(130)));
  try {
    web = await serve();
    chrome = await launch(profile);
    const base = `http://127.0.0.1:${web.port}`;
    for (const shot of plan()) {
      const problems = await withPage(chrome.port, (send, listen) => shoot(send, listen, base, shot, out)).catch(
        (e) => [`script: ${e.message}`],
      );
      count++;
      if (problems.length) {
        failed++;
        console.log(`FAIL ${shot.name}`);
        for (const p of problems) console.log(`  ${p}`);
      }
    }
    console.log(`${count} shots, ${failed} failed, written to ${out}`);
  } finally {
    await stop();
  }
  return failed;
}

/** Checks plan() and judge() without a browser. */
function selfTest() {
  const shots = plan();
  const assert = (ok, what) => {
    if (!ok) throw new Error(`self-test: ${what}`);
  };
  const OK = { errors: [], shells: 1, broken: 0, tab: 'units', want: 'units', scrollWidth: 375, innerWidth: 375 };
  const grid = SCENARIOS.length * TABS.length * THEMES.length * WIDTHS.length;
  const total = grid + PLAN_SHOTS.reduce((n, p) => n + THEMES.length * p.widths.length, 0);
  assert(shots.length === total, `the plan has ${total} shots`);
  assert(new Set(shots.map((s) => s.name)).size === total, 'names are unique');
  assert(shots[0].url === '/?demo&tab=units' && shots[0].name === 'status-units-dark-1360.png', 'first shot');
  assert(shots[grid - 1].url === '/?demo=dryer-fault&tab=history', 'last scenario shot');
  const plans = shots.slice(grid);
  assert(
    plans.every((s) => s.tab === 'print' && s.url.startsWith('/?demo=four&tab=print&file=') && s.wait),
    'plan shots',
  );
  assert(plans.filter((s) => s.click === '.btn.ej').length === 2, 'the eject confirmation in both themes');
  assert(
    plans
      .filter((s) => s.scenario === 'plan-opts')
      .map((s) => s.name)
      .join() === 'plan-opts-dark-375.png,plan-opts-light-375.png',
    'the options sheet on a phone, in both themes',
  );
  assert(
    plans.some((s) => s.name === 'plan-step1-light-375.png'),
    'step 1 on a phone',
  );
  assert(
    ['1440', '1100', '900'].every((w) => plans.some((s) => s.name === `plan-step1-dark-${w}.png` && s.height === 900)),
    'layout A at 1440 x 900, 1100 and 900',
  );
  assert(
    plans.some(
      (s) => s.name === 'plan-reading-dark-1440.png' && s.wait === '.rbar' && s.url.endsWith('big-9-colours.gcode'),
    ),
    'the reading bar',
  );
  assert(
    plans.some(
      (s) =>
        s.name === 'plan-other-light-375.png' &&
        s.wait === '.plan.other' &&
        s.url.endsWith('&other=calibration-cube.gcode'),
    ),
    'the other-device notice',
  );
  assert(
    plans.some((s) => s.name === 'plan-in-place-dark-1360.png'),
    'the spools in place',
  );
  assert(
    ['1440', '1100'].every((w) =>
      plans.some((s) => s.name === `plan-picker-dark-${w}.png` && s.click === '.crow' && s.height === 900),
    ),
    'layout A with the colour picker open at 1440 x 900 and 1100',
  );
  assert(judge({ ...OK }).length === 0, 'clean page passes');
  assert(judge({ ...OK, errors: ['x'], shells: 2, scrollWidth: 400 }).length === 3, 'three problems');
  assert(judge({ ...OK, broken: 1 }).length === 1, 'a .broken element fails');
  assert(judge({ ...OK, tab: 'print' }).length === 1, 'the wrong tab fails');
  assert(judge({ ...OK, tab: null }).length === 1, 'a missing tab body fails');
  console.log('visual_check self-test: OK');
}

if (process.argv[2] === '--self-test') selfTest();
else if (!process.argv[2]) {
  console.error('usage: node visual_check.mjs OUT_DIR | --self-test');
  process.exit(2);
} else process.exitCode = (await main(process.argv[2])) ? 1 : 0;
