import { html } from './html.js';
import { TABS, printClock, ready } from '../state.js';
import { floorPct } from '../format.js';
import { paused, printingNow, sliceFor, topbarView } from '../model.js';
import { statusLevel, statusWords, unitStatuses } from '../unit-status.js';

/** @typedef {import('../types.js').AppState} AppState */
/** @typedef {import('../types.js').UiCtx} UiCtx */

// The status pill says the print's state: its progress while it prints, why it waits while paused (the
// adapter's prefix left out), offline while the printer is not ready, else idle.
/** @param {AppState} app @returns {{ cls: string, key: string, vars: Record<string, unknown> }} */
function pillView(app) {
  if (!ready(app)) return { cls: 'pill off', key: 'top.offline', vars: {} };
  const s = app.status;
  if (printingNow(s)) {
    // The print view's own clock: the same share, floored as the header's; no % while none is reported.
    const p = printClock(app).clock.p;
    return p === null
      ? { cls: 'pill printing', key: 'top.printing_bare', vars: {} }
      : { cls: 'pill printing', key: 'top.printing', vars: { pct: floorPct(p) } };
  }
  if (!paused(s)) return { cls: 'pill', key: 'top.idle', vars: {} };
  const why = topbarView(s, true).paused;
  if (!why) return { cls: 'pill paused', key: 'top.paused_only', vars: {} };
  return { cls: 'pill paused', key: 'top.paused', vars: { why: why.replace(/^ace2k_u1: /, '') } };
}

// The pill. A click opens the Print tab, where the print is followed and resumed.
/** @param {{ app: AppState, ctx: UiCtx }} props */
function Pill({ app, ctx }) {
  const v = pillView(app);
  return html`<button type="button" class=${v.cls} onClick=${() => ctx.run('tab', { tab: 'print' })}>
    ${ctx.t(v.key, v.vars)}
  </button>`;
}

// The health dot: green, amber when a unit's self-test fails, red when a link is down. Its name says the state
// in words, and a click opens the status panel.
/** @param {{ app: AppState, ctx: UiCtx }} props */
function HealthDot({ app, ctx }) {
  const units = unitStatuses(app);
  const words = statusWords(units, ctx.t);
  return html`<button type="button" class=${`health-dot ${statusLevel(units)}`} aria-label=${words} title=${words}
    onClick=${() => ctx.run('panel', { kind: 'status' })}></button>`;
}

// Stop all, the emergency control: always shown, in every tab. During a print with every lane in use there is
// nothing to stop, so it is disabled and its title says how to stop the print instead. On a phone the CSS shows
// the short word; the full label stays the button's name.
/** @param {{ app: AppState, ctx: UiCtx }} props */
function StopAll({ app, ctx }) {
  const online = ready(app);
  const v = topbarView(sliceFor(app.status, null), online);
  const none = Array.isArray(v.stopLanes) && !v.stopLanes.length;
  const label = ctx.t(v.stopWords.label, v.stopWords.vars);
  return html`<button type="button" class="btn stop" disabled=${!online || none} aria-label=${label}
    title=${none ? ctx.t('top.stop_all_print') : undefined} onClick=${() => ctx.run('stop_all', {})}>
    <span class="stop-full">${label}</span><span class="stop-short" aria-hidden="true">${ctx.t('top.stop_short')}</span>
  </button>`;
}

// The tab that an arrow key, Home or End moves to from tab, round at both ends. Null for any other key.
/** @param {string} tab @param {string} key @returns {import('../state.js').Tab | null} */
export function tabKey(tab, key) {
  const i = TABS.indexOf(/** @type {import('../state.js').Tab} */ (tab));
  const n = TABS.length;
  const to = { ArrowRight: i + 1, ArrowLeft: i - 1 + n, Home: 0, End: n - 1 }[key];
  return to === undefined ? null : TABS[to % n];
}

// One icon per theme mode, drawn in the text colour, so it reads in both themes.
const THEME_ICONS = {
  system: html`<rect x="3" y="4" width="18" height="12" rx="2" /><path d="M8 20h8M12 16v4" />`,
  light: html`<circle cx="12" cy="12" r="4" /><path
      d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1 7 17M17 7l2.1-2.1"
    />`,
  dark: html`<path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z" />`,
};

// The theme button. It shows the icon of the mode in use, and is named for it. A click asks for the next mode.
/** @param {{ app: AppState, ctx: UiCtx }} props */
function ThemeButton({ app, ctx }) {
  const mode = app.theme ?? 'system';
  const name = ctx.t(`top.theme_${mode}`);
  return html`<button type="button" class="btn icon" aria-label=${name} title=${name} onClick=${() => ctx.run('theme', {})}>
    <svg data-theme-icon=${mode} viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor"
      stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${THEME_ICONS[mode]}</svg>
  </button>`;
}

// The top bar: the title, the four tabs, the status pill, the theme button, the health dot and Stop all.
// The tabs follow the ARIA tabs pattern: only the active tab is in the Tab order, the arrow keys move between them.
/** @param {{ app: AppState, ctx: UiCtx }} props */
export function TopBar({ app, ctx }) {
  const tab = app.ui.tab;
  /** @param {{ key: string, preventDefault: () => void }} e */
  const onKey = (e) => {
    const next = tabKey(tab, e.key);
    if (!next) return;
    e.preventDefault();
    ctx.run('tab', { tab: next });
    globalThis.document?.getElementById(`tab-${next}`)?.focus();
  };
  return html`<header class="top">
    <span class="brand">${ctx.t('app.title')}</span>
    <nav class="tabs" role="tablist" onKeyDown=${onKey}>
      ${TABS.map(
        (id) =>
          html`<button type="button" role="tab" id=${`tab-${id}`} aria-controls="tab-body"
            class=${tab === id ? 'tab on' : 'tab'} aria-selected=${tab === id ? 'true' : 'false'}
            tabindex=${tab === id ? '0' : '-1'} onClick=${() => ctx.run('tab', { tab: id })}>${ctx.t(`tab.${id}`)}</button>`,
      )}
    </nav>
    <span class="grow"></span>
    <${Pill} app=${app} ctx=${ctx} />
    <${ThemeButton} app=${app} ctx=${ctx} />
    <${HealthDot} app=${app} ctx=${ctx} />
    <${StopAll} app=${app} ctx=${ctx} />
  </header>`;
}
