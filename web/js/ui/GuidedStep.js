import { html, useRef } from './html.js';
import { darkFil } from '../diagram-model.js';
import { fil } from '../words.js';
import { EjectAll, EjectLine } from './EjectAll.js';
import { ejectList, prevAt, stepId, stepSub, stepText, toolLook, wrongSpoolIn } from '../steps.js';
import { laneActions } from '../model.js';
import { laneAt, unitId } from '../lane-state.js';
import { ready, scoped } from '../state.js';
import { laneByKey } from '../print-plan.js';
import { swatch } from './ColourChips.js';
import { busyKey } from './BayCard.js';

/** @typedef {import('../types.js').AppState} AppState */
/** @typedef {import('../types.js').UiCtx} UiCtx */
/** @typedef {import('../steps.js').Guide} Guide */
/** @typedef {import('../steps.js').Step} Step */
/** @typedef {import('./Diagram.js').Overlay} Overlay */
/** @typedef {{ colour: string | null, material: string | null }} Look */

// The spool that goes into the step's "put in" bay: the spool moved or held, else the file colour of its tools.
// Null for a step that puts nothing in (a take-out, a check).
/** @param {Guide} g @param {Step} s @returns {Look | null} */
function ghostOf(g, s) {
  if (s.kind === 'move' || s.kind === 'putBack')
    return { colour: s.spool?.colour ?? null, material: s.spool?.material ?? null };
  if (s.kind === 'put') {
    const look = toolLook(g.now, s.tools);
    return { colour: look.colour, material: look.material };
  }
  return null;
}

// The two bays of a step and the ghost of what goes in: a take-out has only from, a put only to.
/** @param {Guide} g @param {Step | null} s @returns {{ from: string | null, to: string | null, ghost: Look | null }} */
export function stepBays(g, s) {
  return {
    from: s?.kind === 'out' ? s.key : s?.kind === 'move' ? s.from : null,
    to: s && s.kind !== 'out' ? s.key : null,
    ghost: s ? ghostOf(g, s) : null,
  };
}

// The overlay of the diagram for the guide: the two bays of the current step, the ghost of what goes in, the file
// colour of every bay the plan uses, and the bay that eject all is ejecting.
/** @param {AppState} app @param {Guide} g @returns {Overlay} */
export function overlayOf(app, g) {
  /** @type {Record<string, number>} */
  const chips = {};
  for (const line of g.now.place?.lines ?? []) chips[line.key] = toolLook(g.now, line.tools).n + 1;
  const run = app.print.eject;
  return { ...stepBays(g, g.cur), chips, ejecting: run?.phase === 'run' ? run.key : null };
}

// A bay's key as an amber unit tag and its number ("a:2" is the tag a and 2), as the phone's lists write it.
/** @param {{ k: string }} props */
function Key({ k }) {
  const at = k.indexOf(':');
  return html`<span class="mkey">${at < 0 ? null : html`<span class="mut">${k.slice(0, at)}</span>`}${at < 0 ? k : k.slice(at + 1)}</span>`;
}

/**
 * One bay of the step on a phone: its colour, its key, what it holds (or "+ PLA" for what goes in) and its label.
 * @param {{ ctx: UiCtx, k: string, look: Look | null, role: 'from' | 'to', ghost: boolean }} props
 */
function StepRow({ ctx, k, look, role, ghost }) {
  const { t } = ctx;
  const what = ghost
    ? t('step.ghost', { material: look?.material ?? t('step.spool') })
    : (look?.material ?? t('units.empty'));
  return html`<button type="button" class=${`srow ${role}`} onClick=${() => ctx.run('panel', { kind: 'bay', key: k })}>
    <i class=${ghost ? 'msw ghost' : look ? 'msw' : 'msw none'} style=${fil(look?.colour)} data-dark-fil=${darkFil(look?.colour)}></i>
    <${Key} k=${k} />
    <span class="mmat">${what}</span>
    <span class="smark">${role === 'from' ? t('step.take_out') : t('step.put_in')}</span>
  </button>`;
}

// The step on a phone: the bay to take out (dashed), an arrow, and the bay to put in (solid), each when there is one.
/** @param {{ ctx: UiCtx, g: Guide, s: Step }} props */
function StepRows({ ctx, g, s }) {
  const o = stepBays(g, s);
  /** @param {string} k */
  const lane = (k) => laneByKey(g.now.lanes, k);
  const from = o.from ? lane(o.from) : undefined;
  const fromLook = from && !from.empty ? { colour: from.colour, material: from.material } : null;
  const toLook = o.ghost ?? (o.to && !lane(o.to)?.empty ? (lane(o.to) ?? null) : null);
  return html`<div class="srows">
    ${o.from ? html`<${StepRow} ctx=${ctx} k=${o.from} look=${fromLook} role="from" ghost=${false} />` : null}
    ${o.from && o.to ? html`<span class="sdown" aria-hidden="true">${ctx.t('step.down')}</span>` : null}
    ${o.to ? html`<${StepRow} ctx=${ctx} k=${o.to} look=${toLook} role="to" ghost=${s.kind !== 'check'} />` : null}
  </div>`;
}

// The progress: one bar per step of the trail, done ones green, the current one amber.
/** @param {{ g: Guide }} props */
function Progress({ g }) {
  return html`<div class="pd">
    ${g.trail.map((id, i) => html`<i key=${id} class=${i === g.at ? 'on' : i < g.at || !g.live.has(id) ? 'ok' : ''}></i>`)}
  </div>`;
}

// "⏏ Eject a:1" on the card, when the step's bay holds a spool that is clearly not the colour asked (wrongSpoolIn):
// the bay card's own Eject (the host takes it out cold, or through its head), so the bay need not be opened. Once the
// bay empties, the step asks for the right spool again. Disabled with its reason as the bay card's is.
/** @param {{ app: AppState, ctx: UiCtx, g: Guide, s: Step | null }} props */
function EjectBay({ app, ctx, g, s }) {
  const at = s && wrongSpoolIn(g.now, s) ? laneAt(app, s.key) : null;
  if (!at) return null;
  const a = laneActions(at.slice, at.i, ready(app)).find((x) => x.id === 'eject');
  if (!a) return null;
  // The bay card's pending source and key, so both show the same eject as busy.
  const busy = scoped(app, at.unit).has(busyKey('eject', at.i));
  const data = { lane: String(at.i + 1), unit: unitId(at.unit), id: 'eject' };
  return html`<button type="button" class="btn pri" disabled=${!a.enabled || busy}
    onClick=${() => ctx.run('lane', data)}>${ctx.t('step.eject_bay', { key: at.key })}</button>`;
}

// The buttons of the card: eject all (while bays hold known spools out of place and no run is on), back, done and
// the list of every step.
/** @param {{ app: AppState, ctx: UiCtx, g: Guide }} props */
function Buttons({ app, ctx, g }) {
  const { t } = ctx;
  const ejects = ejectList(g.steps, g.stays);
  const prev = prevAt(g);
  const all = app.print.allSteps === true;
  const see = g.steps.length === 1 ? t('step.see_one') : t('step.see', { n: g.steps.length });
  return html`<div class="gb">
    ${
      ejects.length && app.print.eject?.phase !== 'run'
        ? html`<button type="button" class="btn ej" onClick=${() => ctx.run('eject_all', {})}>
            ${t('eject_all.button', { n: ejects.length })}</button>`
        : null
    }
    <button type="button" class="btn" disabled=${prev === null} aria-label=${t('step.prev_label')}
      onClick=${() => ctx.run('step_prev', {})}>${t('step.prev')}</button>
    ${g.cur ? html`<button type="button" class="btn y" onClick=${() => ctx.run('step_done', {})}>${t('step.next')}</button>` : null}
    <button type="button" class="link" onClick=${() => ctx.run('steps_all', {})}>
      ${all ? t('step.hide') : see}</button>
  </div>`;
}

// The step's words with the colour's swatch right after "colour N" (a step for a colour: its dot), on the card only.
/** @param {UiCtx['t']} t @param {string} words @param {Step | null} s @param {Guide} g */
function withDot(t, words, s, g) {
  if (!s || (s.kind !== 'put' && s.kind !== 'check')) return words;
  const look = toolLook(g.now, s.tools);
  const name = t('step.colour_n', { n: look.n + 1 });
  const at = words.indexOf(name);
  if (at < 0 || !look.colour) return words;
  const end = at + name.length;
  return html`${words.slice(0, end)}<span class="wdot">${swatch(look.colour)}</span>${words.slice(end)}`;
}
// The side a step's text comes from, decided once when the shown step changes, from the current trail: when the previous
// step is still in it, a later index slides from the right and an earlier one from the left; none on the first render,
// for a reset or a new plan (the previous step is not in the trail) and to or from the done card ('passed').
/** @param {string | null} prev @param {string} now @param {string[]} trail @returns {'left' | 'right' | null} */
export function stepTravel(prev, now, trail) {
  if (prev === null || prev === now || prev === 'passed' || now === 'passed') return null;
  const from = trail.indexOf(prev);
  const to = trail.indexOf(now);
  if (from < 0 || to < 0 || from === to) return null;
  return to > from ? 'right' : 'left';
}

// Keeps the direction for the step shown: it is computed when the step id changes and returned unchanged on every later
// render of that step, so a re-render within the slide does not drop it. The ref is written during render: Preact 10
// renders synchronously, there is no concurrent rendering to repeat or drop a render.
/** @param {{ current: { id: string, dir: 'left' | 'right' | null } | null }} ref @param {string} now @param {string[]} trail */
export function latchTravel(ref, now, trail) {
  if (ref.current === null || ref.current.id !== now)
    ref.current = { id: now, dir: stepTravel(ref.current?.id ?? null, now, trail) };
  return ref.current.dir;
}

// The guided card of the plan: the steps that put the spools in place, one at a time, with eject all; a green card
// once nothing is left. On a phone the card shows the step's bays as two rows instead of the diagram.
/** @param {{ app: AppState, ctx: UiCtx, g: Guide }} props */
export function GuidedStep({ app, ctx, g }) {
  const { t } = ctx;
  const last = useRef(/** @type {{ id: string, dir: 'left' | 'right' | null } | null} */ (null));
  const sid = g.cur ? stepId(g.cur) : 'passed';
  const dir = latchTravel(last, sid, g.trail);
  if (!g.steps.length)
    return html`<div class="guide done">
      <p class="gt">${t('step.in_place')}</p><p class="note">${t('step.in_place_sub')}</p>
    </div>`;
  const s = g.cur;
  const sub = s ? stepSub(t, s, g.now) : t('step.passed_sub');
  return html`<section class="guide">
      <div class="gmain">
        <p class="gcap">${t('step.head', { k: Math.min(g.at + 1, g.trail.length), n: g.trail.length })}</p>
        <${Progress} g=${g} />
        <div class="gwords">
          <div class="gtext" key=${sid} data-step=${sid} data-enter=${dir ?? undefined}>
            <p class="gt">${s ? withDot(t, stepText(t, s, g.now), s, g) : t('step.passed')}</p>
            ${sub ? html`<p class="note">${withDot(t, sub, s, g)}</p>` : null}
          </div>
        </div>
        ${s && app.ui.narrow ? html`<${StepRows} ctx=${ctx} g=${g} s=${s} />` : null}
        <${EjectBay} app=${app} ctx=${ctx} g=${g} s=${s} />
        <${EjectLine} app=${app} ctx=${ctx} />
      </div>
      <${Buttons} app=${app} ctx=${ctx} g=${g} />
    </section>
    ${
      app.print.allSteps
        ? html`<ol class="allsteps" aria-label=${t('step.all_label')}>
            ${g.steps.map((x) => html`<li key=${x.key + x.kind} class=${x === s ? 'cur' : ''}>${stepText(t, x, g.now)}</li>`)}
          </ol>`
        : null
    }
    ${app.print.eject?.phase === 'ask' ? html`<${EjectAll} app=${app} ctx=${ctx} g=${g} />` : null}`;
}
