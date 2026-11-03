import { html } from './html.js';
import { swatch } from './ColourChips.js';
import { headName } from '../words.js';
import { ejectList, ejectSkipped, loadedHead } from '../steps.js';

/** @typedef {import('../types.js').AppState} AppState */
/** @typedef {import('../types.js').UiCtx} UiCtx */
/** @typedef {import('../steps.js').Guide} Guide */
/** @typedef {import('../steps.js').Step} Step */

// The step that empties a bay: its take-out, or the move whose spool it holds.
/** @param {Step[]} steps @param {string} key @returns {Step | undefined} */
const stepFrom = (steps, key) =>
  steps.find((s) => (s.kind === 'out' && s.key === key) || (s.kind === 'move' && s.from === key));

// One bay that eject all empties: its spool's swatch, the bay, its material, and why it comes out.
/** @param {{ ctx: UiCtx, s: Step | undefined, k: string }} props */
function Row({ ctx, s, k }) {
  const { t } = ctx;
  const spool = s && (s.kind === 'out' || s.kind === 'move') ? s.spool : null;
  return html`<li class="er">
    ${swatch(spool?.colour)}<b>${k}</b><span>${spool?.material ?? ''}</span>
    <span class="note push">${s?.kind === 'move' ? t('eject_all.goes_to', { key: s.key }) : t('eject_all.unused')}</span>
  </li>`;
}

// The confirmation of eject all: the bays it empties with their spools, the bays it leaves (no material), and a
// warning for each bay loaded in a head, which the U1 heats to unload it first. Go carries the list it showed, and
// the run checks it against the live bays.
/** @param {{ app: AppState, ctx: UiCtx, g: Guide }} props */
export function EjectAll({ app, ctx, g }) {
  const { t } = ctx;
  const keys = ejectList(g.steps, g.stays);
  const heated = keys.flatMap((k) => {
    const h = loadedHead(app, k);
    return h === null ? [] : [t('eject_all.heat', { key: k, head: headName(t, h) })];
  });
  return html`<div class="modal">
    <div class="mbox" role="dialog" aria-modal="true" aria-labelledby="eject-all-title">
      <h3 id="eject-all-title">${t('eject_all.title')}</h3>
      <ul class="erows">
        ${keys.map((k) => html`<${Row} key=${k} ctx=${ctx} s=${stepFrom(g.steps, k)} k=${k} />`)}
        ${ejectSkipped(g.steps, g.stays).map((k) => html`<li key=${`skip-${k}`} class="er skip">${t('eject_all.skipped', { key: k })}</li>`)}
      </ul>
      <ul class="ewarn">
        ${heated.map((w) => html`<li key=${w} class="ew heat"><span class="ei" aria-hidden="true">🔥</span>${w}</li>`)}
        <li class="ew note"><span class="ei" aria-hidden="true">ℹ️</span>${t('eject_all.note')}</li>
      </ul>
      <div class="bt">
        <button type="button" class="btn" onClick=${() => ctx.run('eject_all_no', {})}>${t('eject_all.cancel')}</button>
        <button type="button" class="btn y" onClick=${() => ctx.run('eject_all_go', { keys: keys.join(',') })}>${t('eject_all.go')}</button>
      </div>
    </div>
  </div>`;
}

// The line of a run in the guided card: the bay being ejected (or its tail to take out by hand) with Stop the run,
// or where the run stopped and the host's message.
/** @param {{ app: AppState, ctx: UiCtx }} props */
export function EjectLine({ app, ctx }) {
  const { t } = ctx;
  const run = app.print.eject;
  if (run?.phase === 'run')
    return html`<p class="eline">
      ${run.tail ? t('eject_all.tail', { key: run.key }) : t('eject_all.running', { key: run.key })}
      <button type="button" class="btn" onClick=${() => ctx.run('eject_all_stop', {})}>${t('eject_all.stop')}</button>
    </p>`;
  if (run?.phase !== 'failed') return null;
  return html`<p class="eline bad">
    ${t('eject_all.failed', { key: run.key, msg: run.msg })}
    <button type="button" class="btn" onClick=${() => ctx.run('eject_all_no', {})}>${t('eject_all.close')}</button>
  </p>`;
}
