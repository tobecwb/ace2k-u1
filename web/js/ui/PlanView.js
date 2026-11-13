import { html, useEffect, useRef } from './html.js';
import { ColourRows, swatch } from './ColourChips.js';
import { Timeline } from './Timeline.js';
import { Diagram } from './Diagram.js';
import { GuidedStep, overlayOf } from './GuidedStep.js';
import { PrintBar } from './PrintBar.js';
import { DASH, fmtHM } from '../format.js';
import { headOfIn, noFitWords, printGate, sentKinds, similarPairs, usedTest } from '../print-plan.js';
import { editAction, printing, printingNow, reasonKey } from '../model.js';
import { laneAt } from '../lane-state.js';
import { swapStops, timeKnown } from '../swap-time.js';
import { fileFrame, hintKept, planNow, ready } from '../state.js';
import { guideOf, stepId } from '../steps.js';

/** @typedef {import('../types.js').AppState} AppState */
/** @typedef {import('../types.js').UiCtx} UiCtx */
/** @typedef {import('../state.js').PlanNow} PlanNow */

// A count of swaps in words: "1 swap", "3 swaps".
/** @param {UiCtx['t']} t @param {number} n */
export const swapsWords = (t, n) => (n === 1 ? t('print.swap_one') : t('print.swaps_n', { n }));

/** @typedef {ReturnType<typeof printGate>} Gate */

// The print bar's summary in large type: the swaps of the bays Print sends (gate.choice), counted on the live lanes,
// and the time they stop the print; a dash while a colour has no bay on a head, or with no T order read (no
// placement): then the swaps are not known. With heads that swap in the background (now.bg), the swaps are the stops,
// and the swaps in the background follow when there are any. The swaps and the time both read now.changes (the
// changes the file's frame keeps), so they agree.
/** @param {UiCtx['t']} t @param {PlanNow} now @param {Gate} gate */
export function barSummary(t, now, gate) {
  const sent = sentStops(now, gate);
  if (sent === null) return DASH;
  const { kinds, seconds } = sent;
  const words = { swaps: swapsWords(t, kinds.stops), time: fmtHM(seconds), n: kinds.free };
  return kinds.free ? t('print.bar_bg', words) : t('print.bar', words);
}

// The swaps of the bays Print sends (gate.choice) and the seconds they stop the print, or null while they are not
// known (no placement, or a colour with no bay on a head).
/** @param {PlanNow} now @param {Gate} gate */
function sentStops(now, gate) {
  const order = now.changes.map((c) => c.tool);
  const kinds = now.place ? sentKinds(now.plan, gate.choice, now.lanes, now.bg, order) : null;
  if (kinds === null) return null;
  return { kinds, seconds: swapStops(now.changes, gate.choice, headOfIn(now.lanes), now.bg, now.times).total };
}

// The file's time in the header: the slicer's time plus the stops of the swaps Print sends, with the slicer's time
// beside it ("≈ 0:52 with the swaps (0:35 in the slicer)"). The slicer's time alone while the swaps are not known or
// stop the print for no time, or when the time is not one the estimates can use (0, not a number); nothing with no
// card.
/** @param {UiCtx['t']} t @param {PlanNow} now @param {Gate} gate @returns {string | null} */
export function planTimeWords(t, now, gate) {
  if (now.timeS === null) return null;
  const slicer = fmtHM(now.timeS);
  const sent = timeKnown(now.timeS) ? sentStops(now, gate) : null;
  if (sent === null || !(sent.seconds > 0)) return t('print.slicer_time', { time: slicer });
  return t('print.time_with_swaps', { total: fmtHM(now.timeS + sent.seconds), slicer });
}

// The colours the file prints whose pick has no bay on a head: "As it is" cannot count its swaps while there are any.
/** @param {PlanNow} now @returns {number} */
export function missingColours(now) {
  const headOf = headOfIn(now.lanes);
  const used = usedTest(now.plan.order);
  return now.plan.tools.filter((_, n) => used(n) && headOf(now.pick[n]) == null).length;
}

/**
 * One side of the comparison: a caption, the swaps in large type and what they cost, or a dash and why not. free is
 * the swaps in the background (none when absent or 0): a line of their own, under the cost.
 * @param {{ ctx: UiCtx, cap: string, swaps: number | null, seconds: number | null, why: string, good?: boolean,
 *   free?: number | null }} props
 */
function Side({ ctx, cap, swaps, seconds, why, good = false, free = null }) {
  const { t } = ctx;
  return html`<div class=${good ? 'cmp-side good' : 'cmp-side'}>
    <small>${cap}</small>
    <b>${swaps === null ? DASH : swapsWords(t, swaps)}</b>
    <small>${swaps === null || seconds === null ? why : t('print.stopped', { time: fmtHM(seconds) })}</small>
    ${swaps !== null && free ? html`<small>${t('print.background_n', { n: free })}</small>` : null}
  </div>`;
}

// The header: back to the files, the thumbnail, the name, the file's time (planTimeWords), and the comparison of the swaps
// "As it is" (the current pick) with the swaps "With the suggestion" (the best placement): two boxes, or one line on
// a phone. With heads that swap in the background, the swaps are the stops, and each side adds its swaps in the
// background.
/** @param {{ app: AppState, ctx: UiCtx, now: PlanNow, gate: Gate }} props */
function Header({ app, ctx, now, gate }) {
  const { t } = ctx;
  const file = now.plan.file;
  const card = app.print.cards?.[file]?.card ?? null;
  const thumb = card?.thumb ?? (app.print.thumb?.file === file ? app.print.thumb.url : null);
  const time = planTimeWords(t, now, gate);
  return html`<div class="plan-head">
    <button type="button" class="btn" onClick=${() => ctx.run('print_back', {})}>${t('print.back')}</button>
    <span class="plan-thumb">${thumb ? html`<img src=${thumb} alt=${t('print.thumb', { file })} />` : null}</span>
    <div class="plan-name">
      <b>${file.replace(/\.gcode$/i, '')}</b>
      ${time ? html`<span class="note">${time}</span>` : null}
      ${now.bg.length && !timeKnown(now.timeS) ? html`<p class="note">${t('print.no_time')}</p>` : null}
    </div>
    <${Compare} ctx=${ctx} now=${now} narrow=${Boolean(app.ui.narrow)} />
  </div>`;
}

// The comparison itself: two boxes, or one line on a phone.
/** @param {{ ctx: UiCtx, now: PlanNow, narrow: boolean }} props */
function Compare({ ctx, now, narrow }) {
  const { t } = ctx;
  const p = now.place;
  const best = p?.fits ? p.best : null;
  const free = p?.fits ? p.free : null;
  const bestS = p?.fits ? p.bestS : null;
  const noFit = p ? noFitWords(p, now.plan, now.merges) : DASH;
  if (narrow) return html`<p class="cmp line">${cmpLine(t, p?.now ?? null, best, noFit, free, p?.nowFree, bestS)}</p>`;
  return html`<div class="cmp">
    <${Side} ctx=${ctx} cap=${t('print.as_is')} swaps=${p?.now ?? null} seconds=${p?.nowS ?? null}
      why=${t('print.missing_n', { n: missingColours(now) })} free=${p?.nowFree} />
    <${Side} ctx=${ctx} cap=${t('print.with_suggestion')} swaps=${best} seconds=${bestS} why=${noFit} good=${true}
      free=${free} />
  </div>`;
}

// The comparison on a phone, in one line: the swaps as it is (a dash while they cannot be counted), and with the
// suggestion, with the seconds they stop the print (bestS), or why the suggestion does not fit. Each side adds its
// swaps in the background (asIsFree, free) when there are any, as the two boxes do. Without bestS the line says why
// the suggestion does not fit.
/**
 * @param {UiCtx['t']} t @param {number | null} asIs @param {number | null} best @param {string} noFit
 * @param {number | null} [free] @param {number | null} [asIsFree] @param {number | null} [bestS]
 */
export function cmpLine(t, asIs, best, noFit, free = null, asIsFree = null, bestS = null) {
  let as_is = asIs === null ? DASH : swapsWords(t, asIs);
  if (asIs !== null && asIsFree) as_is = t('print.as_is_bg', { swaps: as_is, n: asIsFree });
  if (best === null || bestS === null) return t('print.cmp_line_nofit', { as_is, why: noFit });
  const words = { as_is, best: swapsWords(t, best), time: fmtHM(bestS), n: free };
  return free ? t('print.cmp_line_bg', words) : t('print.cmp_line', words);
}

// One amber line for each bay whose spool has no material: the planner leaves it out until it is defined. A bay that
// Print sends (gate.choice) whose spool the page names only from its head's setting on the U1 (no tag read, no Edit
// spool record) has no material set for the adapter, which refuses it in a print: it gets a line too. Define opens
// the bay with its spool editor.
// Define is disabled with the bay's own Edit spool reason, as the bay panel shows it (editAction): while the print is
// printing, say.
/** @param {{ app: AppState, ctx: UiCtx, now: PlanNow, gate: Gate }} props */
function UnknownLines({ app, ctx, now, gate }) {
  const { t } = ctx;
  const used = usedTest(now.plan.order);
  const sent = new Set(now.plan.tools.flatMap((_, n) => (used(n) && gate.choice[n] ? [gate.choice[n]] : [])));
  return now.lanes
    .filter((l) => l.unknown || (!l.empty && l.materialSet === false && sent.has(l.key)))
    .map((l) => {
      const at = laneAt(app, l.key);
      // While the print is printing, closed with that reason first, whatever the bay's own state; else its own.
      const ea = at ? editAction(at.slice, at.i, ready(app)) : null;
      const why = printingNow(app.status)
        ? t('reason.print_running')
        : ea?.reason
          ? t(`reason.${reasonKey(ea.id, ea.reason)}`)
          : null;
      return html`<div class="plan-hint unk" key=${l.key}>
        <span class="q2" aria-hidden="true">${t('units.unknown_mark')}</span>
        <span class="grow">${l.unknown ? t('print.unknown_bay', { key: l.key }) : t('print.unset_bay', { key: l.key })}</span>
        <button type="button" class="btn warn" disabled=${Boolean(why)} title=${why ?? undefined}
          onClick=${() => ctx.run('define', { key: l.key })}>
          ${t('print.define', { key: l.key })}</button>
      </div>`;
    });
}

// The first pair of the file's colours that look the same, with Join and Keep apart. Keep apart hides it for
// this file.
/** @param {{ app: AppState, ctx: UiCtx, now: PlanNow }} props */
function SimilarHint({ app, ctx, now }) {
  const { t } = ctx;
  const pair = hintKept(app.print, now.plan.file) ? undefined : similarPairs(now.plan, now.merges)[0];
  if (!pair) return null;
  const [a, b] = pair;
  return html`<div class="plan-hint">
    ${swatch(now.plan.tools[a].colour)}${swatch(now.plan.tools[b].colour)}
    <span class="grow"><b>${t('print.similar', { a: a + 1, b: b + 1 })}</b> ${t('print.similar_note')}</span>
    <button type="button" class="btn warn" onClick=${() => ctx.run('merge', { n: String(b), into: String(a) })}>
      ${t('print.join')}</button>
    <button type="button" class="btn" onClick=${() => ctx.run('hint_keep', {})}>${t('print.keep_apart')}</button>
  </div>`;
}

// The bar's note when Print sends the bays as they are: the planner cannot plan the file, or the guided steps are
// only a suggestion. null otherwise.
/** @param {UiCtx['t']} t @param {PlanNow} now @param {Gate} gate @param {number} steps @returns {string | null} */
function asIsNote(t, now, gate, steps) {
  if (!gate.asIs || !now.place) return null;
  if (!now.place.fits) return t('print.as_is_nofit', { why: noFitWords(now.place, now.plan, now.merges) });
  return steps ? t('print.as_is_note') : null;
}

// What the page scrolls to when the plan view renders: on opening a file, the colours caption (the top of the screen
// is "File colours → spools", never the guided step); after that, on a step change, the step's lit bays on a desktop,
// and nothing on a phone (it has no diagram).
/** @param {{ opened: boolean, narrow: boolean }} s @returns {'colours' | 'step' | null} */
export function scrollTarget({ opened, narrow }) {
  if (opened) return 'colours';
  return narrow ? null : 'step';
}

// Scrolls the lit bays of the current step into view, as little as needed: the page's scroll padding keeps them
// above the sticky print bar. A phone has no diagram, so nothing moves there.
/** @param {Element | null} el */
export function showStep(el) {
  for (const bay of el?.querySelectorAll('.dg .bay.r-from, .dg .bay.r-to') ?? [])
    bay.scrollIntoView({ block: 'nearest' });
}

// The plan view's scroll after a render. seen holds the file it last rendered; app.print.scrolled the file the page
// already scrolled to. A file opened and not yet scrolled to: its colours caption goes to the top of the screen, and
// scrolled says so (no render needed). A file whose plan comes after that scroll does not move the page. The same
// file again (a step change): the step's lit bays, on a desktop only (scrollTarget).
/**
 * @param {AppState} app @param {Element | null} el @param {{ current: string | null }} seen
 * @param {string | null} file
 * @param {string} [to] the element the opening scroll puts at the top: the colours caption, unless the caller names
 *   another (the reading view: the top of its left column)
 */
export function scrollPlan(app, el, seen, file, to = '.plan-left > .cap') {
  const fresh = seen.current !== file;
  seen.current = file;
  if (file === null) return;
  const opened = fresh && app.print.scrolled !== file;
  if (fresh && !opened) return;
  const target = scrollTarget({ opened, narrow: Boolean(app.ui.narrow) });
  if (target === 'colours') {
    el?.querySelector(to)?.scrollIntoView({ block: 'start' });
    app.print = { ...app.print, scrolled: file };
  } else if (target === 'step') showStep(el);
}

// The plan of the chosen file, in layout A: the header with the comparison over the whole width; on the left the
// bays with no material, the colour rows and the similar-colour hint (a row opens the colour picker in the side
// panel), then the guided step; the diagram on the right (none on a phone); under both the timeline, then the print
// bar (on a phone its start options open from "Options ▾" in a sheet). With too little room for the two columns (a
// window under about 1100 px, or the side panel open beside it) it is one column in that order (print.css). The print
// gate is worked out once here: the bar, its summary, the start options, the bays with no material and the timeline
// all show the bays Print sends.
// On opening a file the page scrolls so the colours caption is at the top of the screen, once per file: app.print.
// scrolled names the file already scrolled to (here, or before its plan came), so a plan arriving later does not
// scroll again.
/** @param {{ app: AppState, ctx: UiCtx }} props */
export function PlanView({ app, ctx }) {
  const now = planNow(app);
  const g = guideOf(app);
  const root = useRef(/** @type {HTMLElement | null} */ (null));
  const seen = useRef(/** @type {string | null} */ (null));
  const at = g?.cur ? stepId(g.cur) : null;
  const file = now?.plan.file ?? null;
  useEffect(() => scrollPlan(app, root.current, seen, file), [at, file]);
  if (!now) return null;
  const gate = printGate(now, printing(app.status));
  const left = gate.asIs ? 0 : (g?.steps.length ?? 0);
  return html`<section class="plan" ref=${root}>
    <${Header} app=${app} ctx=${ctx} now=${now} gate=${gate} />
    <div class="plan-left">
      <${UnknownLines} app=${app} ctx=${ctx} now=${now} gate=${gate} />
      <${ColourRows} app=${app} ctx=${ctx} now=${now} />
      <${SimilarHint} app=${app} ctx=${ctx} now=${now} />
      ${g ? html`<${GuidedStep} app=${app} ctx=${ctx} g=${g} />` : null}
    </div>
    ${app.ui.narrow ? null : html`<div class="plan-dia"><${Diagram} app=${app} ctx=${ctx} overlay=${g ? overlayOf(app, g) : {}} /></div>`}
    <${Timeline} key=${now.plan.file} timeS=${app.print.cards?.[now.plan.file]?.card?.time ?? null} plan=${now.plan} choice=${gate.choice} headOf=${headOfIn(now.lanes)} bg=${now.bg}
      times=${now.times} frame=${fileFrame(app, now.plan.file)} t=${ctx.t} />
    <${PrintBar} app=${app} ctx=${ctx} now=${now} gate=${gate} summary=${barSummary(ctx.t, now, gate)} left=${left}
      note=${asIsNote(ctx.t, now, gate, g?.steps.length ?? 0)} />
  </section>`;
}
