import { html } from './html.js';
import { headName, unitLabel } from '../words.js';
import { swatch } from './ColourChips.js';
import { bayOwner, bayRoots, materialMatch, mergeRoot, NEW, sameTool, usedTest } from '../print-plan.js';
import { pickCost } from '../planner.js';
import { planNow } from '../state.js';
import { swapsWords } from './PlanView.js';

/** @typedef {import('../types.js').AppState} AppState */
/** @typedef {import('../types.js').UiCtx} UiCtx */
/** @typedef {import('../types.js').PlanLane} PlanLane */
/** @typedef {import('../state.js').PlanNow} PlanNow */

// A swap cost as the picker shows it: +n (more swaps, amber), −n (fewer, green), +0. null is a choice that leaves
// more colours than bays: while the plan fits, it reads "does not fit"; while the plan does not fit yet, no number can
// be given, so it reads nothing (a join is how the user makes the file fit). While the plan does not fit, a choice
// that makes it fit has no delta to compare with: it reads "fits · n swaps", its swaps counted from nothing. No
// choice is ever disabled for its cost.
/** @param {UiCtx['t']} t @param {number | null} d @param {boolean} [fits] */
export function costWords(t, d, fits = true) {
  if (d === null) return fits ? { text: t('print.no_fit'), cls: 'c bad' } : { text: '', cls: 'c' };
  if (!fits) return { text: t('print.fits_swaps', { swaps: swapsWords(t, d) }), cls: 'c good' };
  if (d > 0) return { text: t('print.cost_more', { n: d }), cls: 'c bad' };
  return d < 0 ? { text: t('print.cost_less', { n: -d }), cls: 'c good' } : { text: t('print.cost_zero'), cls: 'c' };
}

// The unit part of a lane key: "a" for "a:2", "" for "2" (a single unit).
/** @param {string} key */
const unitOfKey = (key) => (key.includes(':') ? key.slice(0, key.lastIndexOf(':')) : '');

// A file colour in words: its material and colour code, "PETG #FFFFFF", as far as they are known.
/** @param {import('../types.js').Tool} tool */
export const toolWords = (tool) => `${tool.material ?? ''} ${String(tool.colour ?? '').toUpperCase()}`.trim();

// A unit's heading: its name and the heads its bays feed, "Unit a → T1". A unit whose bays feed no head has its name.
/** @param {UiCtx['t']} t @param {string} u @param {PlanLane[]} lanes */
export function unitHeading(t, u, lanes) {
  const unit = unitLabel(t, u);
  const heads = [...new Set(lanes.flatMap((l) => (l.head == null ? [] : [l.head])))].sort((a, b) => a - b);
  return heads.length ? t('print.unit_heads', { unit, heads: heads.map((h) => headName(t, h)).join(', ') }) : unit;
}

/**
 * One bay in the picker. An empty bay and a bay of another material are disabled and say why. A spool with no
 * material offers "? define and use". Any other bay shows the swaps it adds or saves, and picks that spool. A bay that
 * another colour already prints from says so ("shared with colour m"): its pick joins that colour, and its cost is
 * the join's.
 * @param {{ ctx: UiCtx, now: PlanNow, n: number, lane: PlanLane, roots: Map<string, number[]> }} props
 */
function Bay({ ctx, now, n, lane, roots }) {
  const { t } = ctx;
  const tool = now.plan.tools[n];
  const name = `${lane.key} ${lane.material ?? ''}`.trim();
  if (lane.empty)
    return html`<button type="button" class="opt" disabled>
      <span class="sp2 empty"></span><span>${lane.key}</span><span class="c">${t('units.empty')}</span></button>`;
  if (lane.unknown) {
    const data = { n: String(n), key: lane.key, material: tool.material ?? '', colour: tool.colour ?? '' };
    // The same action as the "Other options" row of this spool.
    return html`<button type="button" class="opt unk" onClick=${() => ctx.run('define_and_use', data)}>
      <span class="sp2 unk" aria-hidden="true">${t('units.unknown_mark')}</span><span>${lane.key}</span>
      <span class="c bad">${t('print.define_use')}</span></button>`;
  }
  if (materialMatch(tool.material, lane.material) === 'other')
    return html`<button type="button" class="opt" disabled title=${t('print.other_material')}>
      ${swatch(lane.colour)}<span>${name}</span><span class="c">${t('print.other_material')}</span></button>`;
  const owner = bayOwner(now.plan, roots, now.merges, n, lane.key);
  const d = pickCost(now.plan, now.pick, now.lanes, now.merges, n, owner === null ? lane.key : { merge: owner });
  const cost = costWords(t, d, now.place?.fits ?? true);
  // Marked when colour n prints from this bay: the pick of its root (its own, or the colour it really joins).
  const root = mergeRoot(now.plan, now.merges, n);
  const on = now.pick[root] === lane.key;
  const shared = owner ?? (on && root !== n ? root : null);
  return html`<button type="button" class=${on ? 'opt sel' : 'opt'} aria-pressed=${on}
    onClick=${() => ctx.run('pick', { n: String(n), key: lane.key })}>
    ${swatch(lane.colour)}<span>${name}</span>
    ${shared === null ? null : html`<span class="note">${t('print.shared_with', { m: shared + 1 })}</span>`}
    <span class=${cost.cls}>${cost.text}</span></button>`;
}

/**
 * A row of the other options: a new spool, or a join with another colour, with its swap cost.
 * @param {{ ctx: UiCtx, on: boolean, d: number | null, fits: boolean, run: () => unknown, children: unknown }} props
 */
function Row({ ctx, on, d, fits, run, children }) {
  const cost = costWords(ctx.t, d, fits);
  return html`<button type="button" class=${on ? 'row sel' : 'row'} aria-pressed=${on} onClick=${run}>
    ${children}<span class=${`r ${cost.cls}`}>${cost.text}</span></button>`;
}

// The colours that colour n may join: printed, not merged into another, of the same known material, and not the
// same colour and material already (those share a spool by themselves).
/** @param {PlanNow} now @param {number} n @returns {number[]} */
export function joinable(now, n) {
  const { plan, merges } = now;
  const used = usedTest(plan.order);
  const tool = plan.tools[n];
  return plan.tools.flatMap((other, j) =>
    j !== n &&
    used(j) &&
    mergeRoot(plan, merges, j) === j &&
    materialMatch(tool.material, other.material) === 'same' &&
    !sameTool(tool, other)
      ? [j]
      : [],
  );
}

// The colour picker of file colour n, in the side panel: the bays of every unit with each spool's swap cost,
// a new spool, a join with another colour of the same material, and "back to the suggestion" once the user
// picked or merged. A file colour that is gone (another file, a new plan) says so.
/** @param {{ app: AppState, ctx: UiCtx, n: number }} props */
export function ColourPicker({ app, ctx, n }) {
  const { t } = ctx;
  const now = planNow(app);
  const tool = now?.plan.tools[n];
  if (!now || !tool || !usedTest(now.plan.order)(n)) return html`<p class="note">${t('panel.gone')}</p>`;
  const units = [...new Set(now.lanes.map((l) => unitOfKey(l.key)))];
  const s = String(n);
  const fits = now.place?.fits ?? true;
  const words = toolWords(tool);
  const define = { n: s, material: tool.material ?? '', colour: tool.colour ?? '' };
  // The colours on each bay, worked out once for every bay of the picker.
  const roots = bayRoots(now.plan, now.pick, now.merges);
  // The swaps that colour n adds or saves by taking v: a bay, NEW or a merge.
  /** @param {string | { merge: number }} v */
  const cost = (v) => pickCost(now.plan, now.pick, now.lanes, now.merges, n, v);
  return html`<div class="card picker">
    <p class="t1">${swatch(tool.colour)} ${words}</p>
    <p class="note">${t('print.pick_note')}</p>
    <p class="cap">${t('print.pick_spools')}</p>
    ${units.map(
      (u) => html`<div class="pu" key=${u}>
        <p class="pn">${unitHeading(
          t,
          u,
          now.lanes.filter((l) => unitOfKey(l.key) === u),
        )}</p>
        <div class="pb">
          ${now.lanes
            .filter((l) => unitOfKey(l.key) === u)
            .map((l) => html`<${Bay} key=${l.key} ctx=${ctx} now=${now} n=${n} lane=${l} roots=${roots} />`)}
        </div>
      </div>`,
    )}
    <p class="cap">${t('print.pick_other')}</p>
    <${Row} ctx=${ctx} on=${now.pick[n] === NEW} d=${cost(NEW)} fits=${fits}
      run=${() => ctx.run('pick', { n: s, key: NEW })}>
      ${swatch(tool.colour, true)}<span>${words ? t('print.put_new', { what: words }) : t('print.put_new_any')}</span></${Row}>
    ${now.lanes
      .filter((l) => l.unknown)
      .map(
        (l) => html`<button type="button" class="row" key=${l.key}
          onClick=${() => ctx.run('define_and_use', { ...define, key: l.key })}>
          <span class="sp2 unk" aria-hidden="true">${t('units.unknown_mark')}</span>
          <span>${words ? t('print.unknown_is', { key: l.key, what: words }) : t('print.unknown_is_any', { key: l.key })}</span>
          <span class="r c bad">${t('print.define_use')}</span></button>`,
      )}
    ${joinable(now, n).map(
      (j) => html`<${Row} key=${j} ctx=${ctx} on=${now.merges[n] === j} d=${cost({ merge: j })} fits=${fits}
          run=${() => ctx.run('merge', { n: s, into: String(j) })}>
        ${swatch(now.plan.tools[j].colour)}<span>${t('print.join_with', { m: j + 1, what: toolWords(now.plan.tools[j]) })}</span></${Row}>`,
    )}
    ${
      now.user[n] != null || now.merges[n] != null
        ? html`<button type="button" class="link" onClick=${() => ctx.run('unpin', { n: s })}>${t('print.back_to_suggestion')}</button>`
        : null
    }
  </div>`;
}
