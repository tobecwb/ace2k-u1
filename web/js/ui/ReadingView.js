import { html, useEffect, useRef, useState } from './html.js';
import { swatch } from './ColourChips.js';
import { Diagram } from './Diagram.js';
import { scrollPlan } from './PlanView.js';
import { fmtHM, fmtMS, isNum } from '../format.js';
import { readView } from '../reading.js';
import { readingOf } from '../state.js';

/** @typedef {import('../types.js').AppState} AppState */
/** @typedef {import('../types.js').UiCtx} UiCtx */

// The bar's redraw while it shows: a fifth of a second reads as smooth and costs nothing.
export const READ_TICK_MS = 200;

// The bar's words elapsed ms after the click, or null while it does not show yet (READ_SHOW_MS in reading.js).
/** @param {UiCtx['t']} t @param {number | null | undefined} size @param {number} elapsed @returns {string | null} */
export function barWords(t, size, elapsed) {
  const v = readView(size, elapsed);
  if (!v.show) return null;
  if (v.leftS === null) return t('print.reading_elapsed', { time: fmtMS(v.elapsedS) });
  if (v.still) return t('print.still_reading');
  return t('print.reading_left', { time: fmtMS(v.leftS) });
}

// Starts the bar's redraw: calls tick every READ_TICK_MS. Returns the function that stops it.
/** @param {() => void} tick @returns {() => void} */
export function startTick(tick) {
  const id = setInterval(tick, READ_TICK_MS);
  return () => clearInterval(id);
}

// The file's size in bytes: from Moonraker's list, else its card's G-code bytes (the file up to the end of its
// G-code), else null.
/** @param {AppState} app @param {string} file @returns {number | null} */
export function sizeOf(app, file) {
  const listed = app.print.files.find((f) => f.path === file)?.size;
  if (isNum(listed)) return /** @type {number} */ (listed);
  const end = app.print.cards?.[file]?.card?.gcodeEnd;
  return isNum(end) && /** @type {number} */ (end) > 0 ? /** @type {number} */ (end) : null;
}

// Where the opening scroll goes: the top of the left column. It starts with the colours caption when the card has
// colours, else with the bar's place; a card whose colours come after the scroll puts them below that top edge, in
// view, never above the screen.
const TO_LEFT = '.plan-left';

// The view's two effects, apart so a test can run them: the bar's redraw (none after a refusal) and the one scroll to
// the top of the left column (PlanView's scrollPlan: once per file).
/**
 * @param {AppState} app @param {{ current: HTMLElement | null }} root @param {{ current: string | null }} seen
 * @param {import('../types.js').Reading} r @param {(update: (n: number) => number) => void} setTick
 */
export function readingEffects(app, root, seen, r, setTick) {
  return {
    tick: () => (r.error ? undefined : startTick(() => setTick((n) => n + 1))),
    scroll: () => scrollPlan(app, root.current, seen, r.file, TO_LEFT),
  };
}

// The file's colours from its card, one row each; the spool each comes from arrives with the plan. A card with no
// colours shows nothing here (the header and the bar only).
/** @param {{ ctx: UiCtx, colours: NonNullable<NonNullable<import('../types.js').CardEntry['card']>['colours']> }} props */
function Colours({ ctx, colours }) {
  const { t } = ctx;
  if (!colours.length) return null;
  return html`<p class="cap">${t('print.colours_cap')}</p>
    <div class="crows">
      ${colours.map(
        (c, n) => html`<div class="crow wait" key=${n}>
          <b>${t('print.colour_n', { n: n + 1 })}</b>${swatch(c.colour)}<span class="note">${c.material ?? ''}</span>
          <span class="ar" aria-hidden="true">${t('print.arrow')}</span>
          <span class="cto note">${t('print.spool_pending')}</span>
        </div>`,
      )}
    </div>`;
}

// The host's refusal in place of the bar; else the estimated bar (without a size, the time elapsed only: no track,
// nothing moving); else nothing yet.
/** @param {{ ctx: UiCtx, r: import('../types.js').Reading, words: string | null, share: number | null }} props */
function Bar({ r, words, share }) {
  if (r.error) return html`<div class="plan-hint err"><span class="grow">${r.error}</span></div>`;
  if (!words) return null;
  return html`<div class="rbar" role="status">
    <span>${words}</span>
    ${share === null ? null : html`<span class="track"><i style=${`width:${Math.round(share * 1000) / 10}%`}></i></span>`}
  </div>`;
}

/**
 * The plan view while the host reads the chosen file: the header and the file's colours from its card (their spools
 * come with the plan), the diagram, and the estimated reading bar; or the host's refusal in place of the bar. The
 * page scrolls to the top of the left column once (PlanView's scroll), so the plan arriving later does not scroll again.
 * nowMs is the clock (the tests pass it; the page uses Date.now()).
 * @param {{ app: AppState, ctx: UiCtx, nowMs?: number }} props
 */
export function ReadingView({ app, ctx, nowMs }) {
  const { t } = ctx;
  const r = /** @type {import('../types.js').Reading} */ (readingOf(app));
  const file = r.file;
  const [, setTick] = useState(0);
  const root = useRef(/** @type {HTMLElement | null} */ (null));
  const seen = useRef(/** @type {string | null} */ (null));
  const card = app.print.cards?.[file]?.card ?? null;
  const colours = card?.colours ?? [];
  const fx = readingEffects(app, root, seen, r, setTick);
  useEffect(fx.tick, [file, r.error]);
  useEffect(fx.scroll, [file]);
  const elapsed = (nowMs ?? Date.now()) - r.at;
  const size = sizeOf(app, file);
  const view = readView(size, elapsed);
  const words = r.error ? null : barWords(t, size, elapsed);
  const back = html`<button type="button" class="btn" onClick=${() => ctx.run('print_back', {})}>${t('print.back')}</button>`;
  return html`<section class="plan reading" ref=${root}>
    <div class="plan-head">
      ${back}
      <span class="plan-thumb">${card?.thumb ? html`<img src=${card.thumb} alt=${t('print.thumb', { file })} />` : null}</span>
      <div class="plan-name">
        <b>${file.replace(/\.gcode$/i, '')}</b>
        ${card?.time != null ? html`<span class="note">${t('print.slicer_time', { time: fmtHM(card.time) })}</span>` : null}
      </div>
    </div>
    <div class="plan-left">
      <${Colours} ctx=${ctx} colours=${colours} />
      <div class="rslot"><${Bar} ctx=${ctx} r=${r} words=${words} share=${view.share} /></div>
    </div>
    ${app.ui.narrow ? null : html`<div class="plan-dia"><${Diagram} app=${app} ctx=${ctx} overlay=${{}} /></div>`}
    ${
      r.error
        ? null
        : html`<section class=${app.ui.narrow ? 'pbar compact' : 'pbar'}>
            <div class="psum"><p class="note">${t('print.reading_block')}</p></div>
            <button type="button" class="btn go" disabled>${t('print.go_big')}</button>
          </section>`
    }
  </section>`;
}

/**
 * The notice when the host's plan names another file than this page's (another device planned it): this page does
 * not plan its own file back by itself (two pages would plan each other's file away); it asks.
 * @param {{ ctx: UiCtx, mine: string, theirs: string }} props
 */
export function OtherDevice({ ctx, mine, theirs }) {
  const { t } = ctx;
  /** @param {string} f */
  const name = (f) => f.replace(/\.gcode$/i, '');
  return html`<section class="plan other">
    <div class="plan-hint">
      <span class="grow">${t('print.other_device', { file: name(theirs) })}</span>
      <button type="button" class="btn pri" onClick=${() => ctx.run('plan_again', {})}>${t('print.plan_again', { file: name(mine) })}</button>
      <button type="button" class="btn" onClick=${() => ctx.run('print_back', {})}>${t('print.back')}</button>
    </div>
  </section>`;
}
