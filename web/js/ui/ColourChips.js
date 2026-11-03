import { html } from './html.js';
import { CLOSE, colourDistance, laneByKey, mergeRoot, usedTest } from '../print-plan.js';
import { pale } from '../diagram-layout.js';

/** @typedef {import('../types.js').AppState} AppState */
/** @typedef {import('../types.js').UiCtx} UiCtx */
/** @typedef {import('../types.js').PlanLane} PlanLane */
/** @typedef {import('../state.js').PlanNow} PlanNow */

const HEX = /^#[0-9a-fA-F]{6}$/;

// True when a spool's colour is far enough from the file's colour to be named: at CLOSE or beyond, it no longer
// counts as the file's colour. A colour that is not known is never flagged.
/** @param {string | null | undefined} spool @param {string | null | undefined} file */
export const colourDiffers = (spool, file) =>
  HEX.test(spool ?? '') && HEX.test(file ?? '') && colourDistance(String(spool), String(file)) >= CLOSE;

// A spool's colour as the page names it: the host's name while the plan read it, else its code.
/** @param {PlanLane} lane */
const colourName = (lane) => lane.colour_name ?? String(lane.colour).toUpperCase();

// A colour swatch. dashed draws the outline of a spool that is not there yet. A pale colour (white, light grey,
// yellow) is marked pale: the light theme outlines its dashed ring in grey, as the diagram does.
/** @param {string | null | undefined} colour @param {boolean} [dashed] */
export const swatch = (colour, dashed = false) =>
  html`<span class=${['dot', dashed ? 'new' : '', pale(colour) ? 'pale' : ''].filter(Boolean).join(' ')}
    style=${HEX.test(colour ?? '') ? `--fil:${colour}` : undefined}></span>`;

/**
 * Where file colour n comes from, as its row shows it: "= colour m" when merged into another colour, "new in c:3"
 * for a spool to put, else the spool's swatch and the bay it prints from (its colour named in amber when it is
 * not the file's), and 📌 when the user picked it. A colour the placement could not seat says so.
 * @param {{ ctx: UiCtx, now: PlanNow, n: number }} props
 */
export function LaneChip({ ctx, now, n }) {
  const { t } = ctx;
  const { plan, lanes, user, merges, place } = now;
  const root = mergeRoot(plan, merges, n);
  if (root !== n)
    return html`<span class="mrg">${t('print.merged', { n: root + 1 })}</span>${swatch(plan.tools[root].colour)}`;
  const line = place?.lines.find((l) => l.tools.includes(n));
  if (!line) return html`<span class="lk alt">${t('print.no_place')}</span>`;
  const pin = user[n] != null ? html`<span class="pin" title=${t('print.picked')}>${t('print.pin')}</span>` : null;
  if (line.act === 'put')
    return html`${swatch(plan.tools[n].colour, true)}<span class="lk">${t('print.new_in', { key: line.key })}</span>${pin}`;
  const lane = /** @type {PlanLane} */ (laneByKey(lanes, line.act === 'move' ? line.from : line.key));
  const differs = colourDiffers(lane.colour, plan.tools[n].colour);
  const where = differs ? t('print.key_colour', { key: line.key, colour: colourName(lane) }) : line.key;
  return html`${swatch(lane.colour)}<span class=${differs ? 'lk alt' : 'lk'}>${where}</span>${pin}`;
}

// The file's colours, one row each, for the tools the file prints: "colour n", its swatch and material, and the
// spool it comes from. A row opens the colour picker for that colour in the side panel.
/** @param {{ app: AppState, ctx: UiCtx, now: PlanNow }} props */
export function ColourRows({ app, ctx, now }) {
  const { t } = ctx;
  const used = usedTest(now.plan.order);
  const open = app.ui.panel?.kind === 'pick' ? Number(app.ui.panel.n) : null;
  return html`<p class="cap">${t('print.colours_cap')} <span class="note">${t('print.colours_note')}</span></p>
    <div class="crows">
      ${now.plan.tools.map((tool, n) =>
        used(n)
          ? html`<button type="button" key=${n} class=${open === n ? 'crow open' : 'crow'} aria-pressed=${open === n}
              onClick=${() => ctx.run('panel', { kind: 'pick', n: String(n) })}>
              <b>${t('print.colour_n', { n: n + 1 })}</b>${swatch(tool.colour)}<span class="note">${tool.material ?? ''}</span>
              <span class="ar" aria-hidden="true">${t('print.arrow')}</span>
              <span class="cto"><${LaneChip} ctx=${ctx} now=${now} n=${n} /></span>
            </button>`
          : null,
      )}
    </div>`;
}
