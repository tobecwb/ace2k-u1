import { html, useEffect, useRef } from './html.js';
import { liveLanes } from '../print-plan.js';
import { fmtHM } from '../format.js';
import { sortFiles, filterFiles, fitOf } from '../filegrid.js';

/** @typedef {import('../types.js').AppState} AppState */
/** @typedef {import('../types.js').UiCtx} UiCtx */

// Calls onSeen(path) once for each card of the grid that enters the viewport. Observer is the browser's
// IntersectionObserver (a test passes its own). Returns the function that stops watching.
/**
 * @param {ParentNode} root
 * @param {(path: string) => void} onSeen
 * @param {typeof IntersectionObserver} Observer
 * @returns {() => void}
 */
export function observeCards(root, onSeen, Observer) {
  const seen = new Observer((entries, obs) => {
    for (const e of entries) {
      if (!e.isIntersecting) continue;
      obs.unobserve(e.target);
      const path = /** @type {HTMLElement} */ (e.target).dataset.file;
      if (path) onSeen(path);
    }
  });
  for (const el of root.querySelectorAll('[data-file]')) seen.observe(el);
  return () => seen.disconnect();
}

// One card: the thumbnail, the name, the time and number of colours, the colour dots and the fit line.
// A file whose colours are unknown (or whose card has not loaded) shows no dots and no fit line.
/** @param {{ file: { path: string, modified: number }, app: AppState, ctx: UiCtx, lanes: import('../types.js').PlanLane[] }} props */
function Card({ file, app, ctx, lanes }) {
  const { t } = ctx;
  const card = app.print.cards?.[file.path]?.card ?? null;
  const colours = card?.colours ?? null;
  const missing = colours ? fitOf(colours, lanes) : 0;
  const meta = [
    card?.time != null ? fmtHM(card.time) : null,
    colours ? (colours.length === 1 ? t('print.n_colour') : t('print.n_colours', { n: colours.length })) : null,
  ]
    .filter(Boolean)
    .join(' · ');
  return html`<button type="button" class="fcard" data-file=${file.path}
      onClick=${() => ctx.run('file_pick', { file: file.path })}>
    <span class="fthumb">${card?.thumb ? html`<img loading="lazy" src=${card.thumb} alt=${t('print.thumb', { file: file.path })} />` : null}</span>
    <span class="fbody">
      <span class="fname">${file.path.replace(/\.gcode$/i, '')}</span>
      <span class="fmeta">${meta}</span>
      ${
        colours
          ? html`<span class="fdots">${colours.map(
              (c) =>
                html`<i style=${`background:${c.colour}`} title=${`${c.material ?? ''} ${c.colour.toUpperCase()}`.trim()}></i>`,
            )}</span>
            <span class=${`ffit ${missing ? 'no' : 'ok'}`}>${missing ? t('print.fit_missing', { n: missing }) : t('print.fit_ok')}</span>`
          : null
      }
    </span>
  </button>`;
}

// The Print tab's file grid: search, sort chips, the count, and the cards. A card loads when it scrolls into view.
/** @param {{ app: AppState, ctx: UiCtx }} props */
export function FileGrid({ app, ctx }) {
  const root = useRef(null);
  const key = cardsKey(shownFiles(app));
  useEffect(() => openLater(ctx, queueMicrotask), []); // The list is read when the tab opens.
  useEffect(() => watchCards(root.current, ctx, IntersectionObserver), [key]);
  return gridView(app, ctx, root);
}

// Opens the tab (print_open) once the effects that are running have run. Opening the tab draws the page, and a draw
// made inside Preact's effect flush re-runs the grid's effects (a double file list, and card observers that leak).
// later is queueMicrotask.
/** @param {UiCtx} ctx @param {(fn: () => void) => void} later */
export function openLater(ctx, later) {
  later(() => void ctx.run('print_open'));
}

// The effect's key: the files shown and their times, so a file that changed is watched again and its card reloads.
/** @param {{ path: string, modified: number }[]} files */
export const cardsKey = (files) => files.map((f) => `${f.path}@${f.modified}`).join('\n');

// Starts watching the cards under el (when the grid is drawn): a card that scrolls into view loads.
/** @param {HTMLElement | null} el @param {UiCtx} ctx @param {typeof IntersectionObserver} Observer */
export function watchCards(el, ctx, Observer) {
  return el ? observeCards(el, (path) => ctx.run('card_load', { file: path }), Observer) : undefined;
}

// The files that show: the search applied, then the sort.
/** @param {AppState} app */
const shownFiles = (app) => sortFiles(filterFiles(app.print.files, app.print.q ?? ''), app.print.sort ?? 'new');

// The grid's markup. root is the ref of the element that holds the cards.
/** @param {AppState} app @param {UiCtx} ctx @param {import('preact').RefObject<HTMLElement> | null} root */
export function gridView(app, ctx, root) {
  const { t } = ctx;
  const sort = app.print.sort ?? 'new';
  const shown = shownFiles(app);
  const lanes = liveLanes(app.status, null);
  return html`<section class="fgrid">
    <div class="ftools">
      <input type="search" class="fsearch" placeholder=${t('print.search')} aria-label=${t('print.search')}
        value=${app.print.q ?? ''} onInput=${(/** @type {Event} */ e) => ctx.run('print_q', {}, /** @type {HTMLInputElement} */ (e.target).value)} />
      <div class="fsorts" role="group" aria-label=${t('print.sort')}>
        ${[
          ['new', t('print.sort.new')],
          ['name', t('print.sort.name')],
        ].map(
          ([
            id,
            label,
          ]) => html`<button type="button" class=${`chip ${id === sort ? 'on' : ''}`} aria-pressed=${id === sort}
            onClick=${() => ctx.run('print_sort', { sort: id })}>${label}</button>`,
        )}
      </div>
      <span class="fcount">${t('print.count', { n: shown.length })}</span>
    </div>
    ${shown.length ? null : html`<p class="placeholder">${t('print.files_none')}</p>`}
    <div class="fcards" ref=${root}>
      ${shown.map((f) => html`<${Card} key=${f.path} file=${f} app=${app} ctx=${ctx} lanes=${lanes} />`)}
    </div>
  </section>`;
}
