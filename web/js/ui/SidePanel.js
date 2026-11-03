import { html, useEffect, useLayoutEffect, useRef } from './html.js';
import { focusBack, focusIn } from './focus.js';
import { StatusCard } from './StatusPanel.js';
import { BayCard } from './BayCard.js';
import { UnitCard } from './UnitCard.js';
import { HeadCard } from './HeadCard.js';
import { ColourPicker } from './ColourPicker.js';
import { Options } from './PrintBar.js';
import { escapeCloses, headAt, laneAt, unitAt } from '../lane-state.js';
import { headName, unitLabel } from '../words.js';
import { planNow } from '../state.js';
import { printGate } from '../print-plan.js';
import { printing } from '../model.js';

/** @typedef {import('../types.js').AppState} AppState */
/** @typedef {import('../types.js').UiCtx} UiCtx */
/** @typedef {import('./focus.js').FocusMemo} FocusMemo */
/** @typedef {import('./focus.js').FocusDoc} FocusDoc */
// The panel's title and its card, by the kind that app.ui.panel names: a bay, a unit, a head, the status, the
// colour picker of a file colour (pick, n the tool), or the start options of the plan's file on a phone (opts, with
// the bays Print sends, as the print bar shows them). A bay, unit or head that is gone (a unit unplugged, a key from
// an old link), or options with no plan, show the title and a line that says so.
/** @param {Record<string, string>} panel @param {AppState} app @param {UiCtx} ctx */
function panelBody(panel, app, ctx) {
  const { t } = ctx;
  if (panel.kind === 'status') return { title: t('top.status'), body: html`<${StatusCard} app=${app} ctx=${ctx} />` };
  if (panel.kind === 'pick') {
    const n = Number(panel.n);
    return { title: t('print.pick_title', { n: n + 1 }), body: html`<${ColourPicker} app=${app} ctx=${ctx} n=${n} />` };
  }
  if (panel.kind === 'opts') {
    const now = planNow(app);
    return {
      title: t('print.options'),
      body: now
        ? html`<${Options} app=${app} ctx=${ctx} now=${now} gate=${printGate(now, printing(app.status))} />`
        : gone(t),
    };
  }
  if (panel.kind === 'bay') {
    const at = laneAt(app, panel.key);
    const title = t('panel.bay', { key: panel.key });
    return { title, body: at ? html`<${BayCard} key=${panel.key} app=${app} ctx=${ctx} at=${at} />` : gone(t) };
  }
  if (panel.kind === 'unit') {
    const unit = unitAt(app, panel.id);
    return {
      title: unitLabel(t, panel.id),
      body:
        unit === undefined ? gone(t) : html`<${UnitCard} app=${app} ctx=${ctx} unit=${unit} focus=${panel.focus} />`,
    };
  }
  const head = headAt(panel.id);
  return {
    title: t('panel.head', { head: headName(t, panel.id) }),
    body: head === null ? gone(t) : html`<${HeadCard} app=${app} ctx=${ctx} head=${head} />`,
  };
}

/** @param {UiCtx['t']} t */
const gone = (t) => html`<p class="note">${t('panel.gone')}</p>`;

// What names the open panel, so the focus moves into it only when it shows something else.
/** @param {Record<string, string>} panel */
export const panelKey = (panel) =>
  `${panel.kind}|${panel.key ?? ''}|${panel.id ?? ''}|${panel.n ?? ''}|${panel.focus ?? ''}|${panel.auto ?? ''}`;

// Closes the panel on Escape. Returns the function that stops listening.
/** @param {FocusDoc} doc @param {() => void} close */
export function onEscape(doc, close) {
  /** @param {KeyboardEvent} e */
  const key = (e) => {
    if (e.key === 'Escape') close();
  };
  doc.addEventListener('keydown', key);
  return () => doc.removeEventListener('keydown', key);
}

// The panel's effects: the focus into the panel when it opens or changes, back to its opener when it closes, and
// Escape while nothing else is open over the page. auto is true for a panel that opened by itself.
// doc is the page's document; the string renderer has none, and runs no effect.
/**
 * @param {{ current: HTMLElement | null }} ref
 * @param {FocusMemo} memo
 * @param {UiCtx} ctx
 * @param {boolean} auto
 * @param {FocusDoc & { body?: Element | null }} [doc]
 */
export function panelEffects(ref, memo, ctx, auto, doc = globalThis.document) {
  return {
    focus: () => focusIn(ref.current, doc, memo, auto),
    // When the exit starts, the focus goes back to the opener, but only if it was lost (on the body) or is still in
    // the closing panel: a control the user focused since the click keeps it.
    restore: (/** @type {boolean} */ leaving) => {
      if (!leaving) return;
      const active = doc.activeElement;
      const lost = !active || active === doc.body || ref.current?.contains(active);
      if (lost) focusBack(memo);
    },
    escape: () =>
      onEscape(doc, () => {
        if (escapeCloses(ctx.app)) ctx.run('panel_close', {});
      }),
  };
}

/** @typedef {{ title: string, body: unknown }} PanelContent */

// The title and card the panel shows. While it leaves, it keeps the ones it showed last: its data may be gone by then
// (the colour picker or the start options after "← Files"), and its exit must not show "gone". last holds them.
/**
 * @param {Record<string, string>} panel @param {AppState} app @param {UiCtx} ctx @param {boolean} leaving
 * @param {{ current: PanelContent | null }} last
 * @returns {PanelContent}
 */
export function contentOf(panel, app, ctx, leaving, last) {
  if (leaving && last.current) return last.current;
  const content = panelBody(panel, app, ctx);
  if (!leaving) last.current = content;
  return content;
}

// The panel's frame: its title, the close button and the card. It is a bottom sheet on a phone's width.
/**
 * @param {{ panel: Record<string, string>, app: AppState, ctx: UiCtx,
 *   panelRef?: { current: HTMLElement | null }, leaving?: boolean, last?: { current: PanelContent | null } }} props
 */
export function PanelFrame({ panel, app, ctx, panelRef, leaving = false, last = { current: null } }) {
  const { title, body } = contentOf(panel, app, ctx, leaving, last);
  return html`<aside class=${[app.ui.narrow ? 'side sheet' : 'side', leaving ? 'leaving' : ''].filter(Boolean).join(' ')}
    aria-label=${title} tabindex="-1" ref=${panelRef} data-kind=${panel.kind} inert=${leaving ? true : undefined}>
    <div class="side-head">
      <h2>${title}</h2>
      <button type="button" class="btn icon" aria-label=${ctx.t('ui.close')} title=${ctx.t('ui.close')}
        onClick=${() => ctx.run('panel_close', {})}>×</button>
    </div>
    ${body}
  </aside>`;
}

// The side panel: a fixed panel on the right, a bottom sheet on a phone. It shows the card that app.ui.panel names,
// takes the focus when it opens or changes, gives it back when it closes, and closes on Escape.
// panel and leaving come from App's presence state, so a panel that closed stays for its exit, showing what it
// showed last (contentOf).
/** @param {{ app: AppState, ctx: UiCtx, panel?: Record<string, string> | null, leaving?: boolean }} props */
export function SidePanel({ app, ctx, panel: shown, leaving = false }) {
  const panel = /** @type {Record<string, string>} */ (shown ?? app.ui.panel);
  const ref = useRef(/** @type {HTMLElement | null} */ (null));
  const memo = useRef(/** @type {FocusMemo} */ ({ opener: null }));
  const last = useRef(/** @type {PanelContent | null} */ (null));
  const fx = panelEffects(ref, memo.current, ctx, panel.auto === '1');
  useLayoutEffect(fx.focus, [panelKey(panel)]);
  useEffect(() => fx.restore(leaving), [leaving]);
  useEffect(fx.escape, []);
  return html`<${PanelFrame} panel=${panel} app=${app} ctx=${ctx} panelRef=${ref} leaving=${leaving} last=${last} />`;
}
