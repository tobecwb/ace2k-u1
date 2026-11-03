import { Connection, bannerView, socketUrl } from './js/conn.js';
import { loadCatalog, t } from './js/i18n.js';
import { eventCount, listDays, loadDays } from './js/history.js';
import { connectionChange, ingestGcode, ingestStatus, loadDemo } from './js/live.js';
import { send, sendScript } from './js/send.js';
import {
  openPrint,
  planFile,
  startPrint,
  keepChoice,
  loadCard,
  ensureRunningCard,
  lostEjectRun,
  leaveEjectRun,
  dropEjectNote,
} from './js/printflow.js';
import { demoFiles, demoOtherPlan } from './js/demo.js';
import {
  readHidden,
  loadTheme,
  storeTheme,
  loadTab,
  tabOf,
  storeTab,
  unitNames,
  keepDays,
  titleView,
  storageOr,
  initialPrint,
} from './js/state.js';
import { runAction } from './js/actions.js';
import { autoAdvance } from './js/steps.js';
import { DEFAULT_OPTIONS } from './js/print-plan.js';
import { html, render as mount } from './js/ui/html.js';
import { makeRenderer } from './js/render-queue.js';
import { App } from './js/ui/App.js';
import { autoPanel, keptEdit } from './js/lane-state.js';

/** @typedef {import('./js/types.js').AppState} AppState */
/** @typedef {import('./js/types.js').Status} Status */
/** @typedef {import('./js/types.js').ActionCtx} ActionCtx */
/** @typedef {import('./js/types.js').UiCtx} UiCtx */
/** @typedef {import('./js/types.js').DayDeps} DayDeps */

// The page's query, read once: ?demo and what goes with it (tab, panel, file, other), for screenshots.
const params = new URLSearchParams(location.search);
const demo = params.has('demo');
// The tab that ?tab= names, in the demo only.
/** @returns {import('./js/state.js').Tab | null} */
function demoTab() {
  const name = params.get('tab');
  return demo && name ? tabOf(name) : null;
}
// The side panel that ?panel= names, in the demo only: bay:2, bay:a:2, unit:a, unit: (the unnamed unit), head:1,
// status, pick:1 (the colour picker of tool 1, with ?file=) (for screenshots).
/** @returns {Record<string, string> | null} */
function demoPanel() {
  const spec = params.get('panel');
  if (!demo || spec === null) return null;
  const at = spec.indexOf(':');
  const kind = at < 0 ? spec : spec.slice(0, at);
  const rest = at < 0 ? '' : spec.slice(at + 1);
  if (kind === 'pick') return { kind, n: rest };
  return kind === 'bay' ? { kind, key: rest } : kind === 'status' ? { kind } : { kind, id: rest };
}
// The phone's width, where the side panel is a bottom sheet.
const NARROW = window.matchMedia('(max-width: 700px)');
/** @type {AppState} */
const app = {
  status: {},
  online: false,
  klippy: null,
  selected: null,
  samples: [],
  events: [],
  snaps: {},
  // dryerUnit and chartUnit name the unit that the dryer panel and the chart show.
  // Null means the first unit.
  dryerUnit: null,
  chartUnit: null,
  days: [],
  daysLoaded: 0,
  loaded: new Set(),
  gen: 0,
  pending: new Set(),
  conn: null,
  folds: {},
  moveMm: '10',
  modeWarn: false,
  dryerLog: null,
  presets: [],
  form: { preset: 1, temp: 60, hm: '6:00' },
  forms: {},
  edit: null,
  hours: null,
  hidden: readHidden(storedHidden()),
  theme: loadTheme(storageOrNull()),
  filter: 'all',
  limit: 200,
  print: initialPrint(),
  printOptions: { ...DEFAULT_OPTIONS },
  // The page's own state: the tab shown (kept across visits), the side panel, and the bottom sheet.
  // With ?demo&tab=print the demo opens on that tab (for screenshots).
  ui: { tab: demoTab() ?? loadTab(storageOrNull()), panel: demoPanel(), sheet: false, narrow: NARROW.matches },
};
// localStorage, or null when the browser blocks it.
/** @returns {Storage | null} */
function storageOrNull() {
  return storageOr(() => localStorage);
}
// The chart series the user hid, as the browser stored them. Null when nothing is stored or storage is blocked.
/** @returns {string | null} */
function storedHidden() {
  try {
    return localStorage.getItem('ace2k.hidden');
  } catch {
    return null;
  }
}
// Stores the hidden chart series, so they stay hidden after a reload.
/** @returns {void} */
function saveHidden() {
  try {
    localStorage.setItem('ace2k.hidden', JSON.stringify(app.hidden));
  } catch {
    /* Private mode blocks storage. The choice lasts until the page is reloaded. */
  }
}
// A theme chosen with the top bar's button is set on <html>.
// The system theme leaves the attribute off.
/** @returns {void} */
function applyTheme() {
  const root = document.documentElement;
  if (app.theme === 'system') root.removeAttribute('data-theme');
  else root.setAttribute('data-theme', app.theme);
}
// Applies the chosen theme and stores it for the next visit.
/** @returns {void} */
function saveTheme() {
  applyTheme();
  storeTheme(storageOrNull(), app.theme);
}
// Stores the tab shown, so the page opens on it at the next visit.
/** @returns {void} */
function saveTab() {
  storeTab(storageOrNull(), app.ui.tab);
}
applyTheme();
// Returns the element of index.html with this id.
// tests/main-ids.test.js checks that every id the page asks for is in index.html.
// So a missing element means a broken page, and this throws.
/** @param {string} id @returns {HTMLElement} */
function byId(id) {
  const el = document.getElementById(id);
  if (!el) throw new Error(`missing #${id}`);
  return el;
}
// When the user focuses a number field, its whole text is selected. So typing replaces the value.
// select() runs on the next turn, because a mouseup or iOS Safari would undo it inside the focus event.
document.addEventListener('focusin', (e) => {
  const el = e.target;
  if (!(el instanceof HTMLInputElement) || el.type !== 'number') return;
  setTimeout(() => {
    if (document.activeElement === el) {
      try {
        el.select();
      } catch {
        /* no selection */
      }
    }
  }, 0);
});

// Shows the banner above the page, or hides it when there is nothing to say.
/** @returns {void} */
function renderBanner() {
  const v = bannerView({ demo, online: app.online, klippy: app.klippy, message: app.linkMessage });
  const banner = byId('banner');
  if (v.hidden) {
    banner.hidden = true;
    banner.className = '';
    banner.textContent = '';
    return;
  }
  banner.hidden = false;
  banner.className = v.className ?? '';
  banner.textContent = t(v.key, v.vars);
}

const root = byId('app');
// Draws the whole page from the app state: the banner, then the Preact tree in #app.
/** @returns {void} */
const render = makeRenderer(() => {
  renderBanner();
  mount(html`<${App} app=${app} ctx=${ctx} />`, root);
});

/** @type {number | undefined} */
let toastTimer;
/** @param {string} msg */
function toast(msg) {
  const el = byId('toast');
  el.textContent = msg;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    el.hidden = true;
  }, 6000);
}

// After a status update: a lane that newly went into error opens its bay card while no panel is open, and an
// open spool editor whose lane no longer offers Edit spool closes.
/** @param {Status} prev */
function openOnError(prev) {
  const panel = autoPanel(app, prev);
  if (panel) app.ui = { ...app.ui, panel };
  app.edit = keptEdit(app);
}

/** @param {Status} status */
function onStatus(status) {
  const prev = app.status;
  ingestStatus(app, status, Date.now() / 1000);
  openOnError(prev);
  ensureRunningCard(ctx); // The G-code bytes its progress is counted over.
  keepChoice(ctx); // When a plan is made again, the choice in the Print panel carries over.
  autoAdvance(app); // A guided step whose change is certainly done moves on by itself.
  render();
}

// What js/history.js needs from the page to load the history.
/** @type {DayDeps} */
const historyDeps = { fetch: (...args) => fetch(...args), render: () => render(), keepDays: () => keepDays(app) };

// With ?demo, the page loads the demo files.
// A release has no dev/ folder. There the banner says that the demo files are not installed.
/** @returns {Promise<void>} */
async function startDemo() {
  const demoName = params.get('demo') || null;
  const prev = app.status;
  await loadDemo((...args) => fetch(...args), app, Date.now() / 1000, demoName);
  openOnError(prev);
  ensureRunningCard(ctx);
  render();
  // ?file= opens the plan of that demo file (for screenshots).
  const file = params.get('file');
  if (file) {
    // The file list first, as the Print tab's opening brings it: the reading bar's estimate comes from a file's size.
    app.print = { ...app.print, files: (await demoFiles()).files };
    await loadCard(ctx, file); // And its card, as the grid loaded it: the colour rows and the slicer's time.
    planFile(ctx, file);
  }
  // ?other= then makes the live plan another file's, as when another device plans it (the notice, for screenshots).
  const other = params.get('other');
  if (file && other) await demoOtherPlan(ctx, file, other);
}

// The page state and the effects that the actions of js/actions.js use.
/** @type {ActionCtx & UiCtx} */
const ctx = {
  app,
  demo,
  t,
  toast,
  render,
  saveHidden,
  saveTheme,
  saveTab,
  now: () => Date.now() / 1000,
  session: storageOr(() => sessionStorage),
  loadDays: (count) => loadDays(historyDeps, app, count),
  send: (...args) => send(ctx, ...args),
  sendScript: (...args) => sendScript(ctx, ...args),
  openPrint: () => openPrint(ctx),
  planFile: (file) => planFile(ctx, file),
  loadCard: (file) => loadCard(ctx, file),
  startPrint: () => startPrint(ctx),
  confirm: (msg) => window.confirm(msg),
  sleep: (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
  blur: () => {
    const active = document.activeElement;
    if (active instanceof HTMLElement) active.blur();
  },
  eventCount: () => eventCount(app),
  // Components call the page's actions by id with a data object.
  run: (id, data, value, checked) => runAction(ctx, id, data, value, checked),
};

// Open <details> folds stay open across redraws. The views read app.folds to draw the open attribute.
document.addEventListener(
  'toggle',
  (e) => {
    const el = e.target;
    if (el instanceof HTMLDetailsElement && el.id) app.folds[el.id] = el.open;
  },
  true,
);

// A page going away with an eject all run on notes its bay for the page loaded next (leaveEjectRun). A page the
// browser brings back from its cache still runs it, so the note goes.
window.addEventListener('pagehide', () => leaveEjectRun(ctx));
window.addEventListener('pageshow', (e) => {
  if (e.persisted) dropEjectNote(ctx);
});

NARROW.addEventListener('change', () => {
  app.ui = { ...app.ui, narrow: NARROW.matches };
  render();
});

// The browser tab's title. It names the unit when the printer has only one, once it is known.
/** @returns {void} */
function setTitle() {
  const v = titleView(unitNames(app));
  document.title = t(v.key, v.vars);
}

// Loads the catalogue and the presets, then starts the demo or the link to the printer.
/** @returns {Promise<void>} */
async function main() {
  try {
    await loadCatalog('en');
  } catch {
    /* t() falls back to the keys */
  }
  setTitle();
  try {
    app.presets = await fetch('presets.json').then((r) => r.json());
  } catch {
    app.presets = [];
  }
  lostEjectRun(ctx); // An eject all run that a reload of this tab ended says where it stopped.
  if (demo) {
    await startDemo();
    return;
  }
  render();
  app.conn = new Connection({
    url: socketUrl(location.protocol, location.host),
    onStatus,
    onState: (st) => {
      const reconnected = connectionChange(app, st, Date.now() / 1000);
      setTitle();
      if (reconnected) listDays(historyDeps, app);
      render();
    },
    onGcode: (line) => {
      ingestGcode(app, line, Date.now() / 1000);
      render();
    },
  });
  app.conn.start();
}

main();
