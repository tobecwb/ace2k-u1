import { html } from './html.js';
import { headName } from '../words.js';
import { printing } from '../model.js';
import { DEFAULT_OPTIONS, flowHeadsOf, printGate, usedHeads } from '../print-plan.js';
import { ready } from '../state.js';

/** @typedef {import('../types.js').AppState} AppState */
/** @typedef {import('../types.js').UiCtx} UiCtx */
/** @typedef {import('../state.js').PlanNow} PlanNow */
/** @typedef {ReturnType<typeof printGate>} Gate */

// The line under it: why Print is disabled, else how many steps are left in the bays, else the note (Print sends the
// bays as they are), else that the spools are ready.
/** @param {UiCtx['t']} t @param {number} left @param {string | null} block @param {string | null} note */
const barNote = (t, left, block, note) => {
  if (block) return block;
  if (!left) return note ?? t('print.ready');
  return left === 1 ? t('print.step_left') : t('print.steps_left', { n: left });
};

/**
 * A start option as a large switch. data is what print_opt reads (the option, and the head for a flow head).
 * @param {{ ctx: UiCtx, on: boolean, text: string, data: Record<string, string> }} props
 */
function Toggle({ ctx, on, text, data }) {
  return html`<label class="tgl">
    <input type="checkbox" role="switch" checked=${on}
      onChange=${(/** @type {Event} */ e) => ctx.run('print_opt', data, '', /** @type {HTMLInputElement} */ (e.currentTarget).checked)} />
    <span class="tk" aria-hidden="true"></span>${text}
  </label>`;
}

// The start options: bed levelling, flow calibration (and, while it is on, the heads it runs on), time-lapse and
// the unload at the end.
// gate is the plan view's print gate; without one it is worked out here.
/** @param {{ app: AppState, ctx: UiCtx, now: PlanNow, gate?: Gate }} props */
export function Options({ app, ctx, now, gate = printGate(now, false) }) {
  const { t } = ctx;
  const o = { ...DEFAULT_OPTIONS, ...app.printOptions };
  const used = usedHeads(now.plan.order, gate.choice, now.lanes);
  const flowOn = flowHeadsOf(o, used);
  return html`<div class="opts" role="group" aria-label=${t('print.options')}>
    <${Toggle} ctx=${ctx} on=${o.bedLevel} text=${t('print.opt.bed_level')} data=${{ opt: 'bedLevel' }} />
    <${Toggle} ctx=${ctx} on=${o.flow} text=${t('print.opt.flow')} data=${{ opt: 'flow' }} />
    ${o.flow ? used.map((h) => html`<${Toggle} key=${h} ctx=${ctx} on=${flowOn.includes(h)} text=${t('panel.head', { head: headName(t, h) })} data=${{ opt: 'flow_head', head: String(h) }} />`) : null}
    <${Toggle} ctx=${ctx} on=${o.timeLapse} text=${t('print.opt.time_lapse')} data=${{ opt: 'timeLapse' }} />
    <${Toggle} ctx=${ctx} on=${o.endUnload} text=${t('print.opt.end_unload')} data=${{ opt: 'endUnload' }} />
  </div>`;
}

// The print bar, fixed at the bottom of the plan: the summary, the start options as large switches, and Print.
// summary is the swaps and the time stopped, in words (PlanView.js barSummary). left is the number of steps still
// pending in the bays for the bays Print sends; Print then asks first (printflow.js startPrint). note is said when
// nothing blocks and no step is left (Print sends the bays as they are). gate is the plan view's print gate; without
// one it is worked out here. On a phone (narrow) the bar is compact, above the tab bar: the summary, "Options ▾"
// (the start options in a bottom sheet, the side panel's kind opts) and Print.
/**
 * @param {{ app: AppState, ctx: UiCtx, now: PlanNow, gate?: Gate, summary: string, left: number, note?: string | null }} props
 */
export function PrintBar({ app, ctx, now, gate, summary, left, note = null }) {
  const narrow = Boolean(app.ui.narrow);
  const { t } = ctx;
  const { block } = gate ?? printGate(now, printing(app.status));
  const starting = app.pending.has('print:go');
  const off = Boolean(block) || starting || app.pending.has('print:file') || !ready(app) || !now.plan.tools.length;
  return html`<section class=${narrow ? 'pbar compact' : 'pbar'}>
    <div class="psum">
      <p class="big3">${summary}</p>
      <p class=${block ? 'note warn' : 'note'}>${barNote(t, left, block, note)}</p>
      ${starting ? html`<p class="note">${t('print.starting_note')}</p>` : null}
    </div>
    ${
      narrow
        ? html`<button type="button" class="btn" aria-haspopup="dialog" onClick=${() => ctx.run('panel', { kind: 'opts' })}>
          ${t('print.options_btn')}</button>`
        : html`<${Options} app=${app} ctx=${ctx} now=${now} gate=${gate} />`
    }
    <button type="button" class="btn go" disabled=${off} aria-busy=${starting ? 'true' : undefined}
      onClick=${() => ctx.run('print_go', {})}>${starting ? t('print.starting') : t('print.go_big')}</button>
  </section>`;
}
