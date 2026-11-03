import { html } from './html.js';
import { fil } from '../words.js';
import { LANES, colorOf, laneOf, sliceFor, spoolOf } from '../model.js';
import { unitId } from '../lane-state.js';
import { faultText, formOf, stateText, dryerView, isRunning } from '../dryer-form.js';
import { DASH, fmtHM, isNum, pct, temp } from '../format.js';
import { linkWords, unitStatuses } from '../unit-status.js';

/** @typedef {import('../types.js').AppState} AppState */
/** @typedef {import('../types.js').UiCtx} UiCtx */
/** @typedef {import('../types.js').DryerView} DryerView */

// The dryer's line under its state: the target and the time left while it heats, what the cooldown and a fault
// mean, and the cycle that Dry starts while it is idle (the unit's dryer form: a preset, or the last values typed).
/** @param {DryerView} d @param {AppState} app @param {string | null} unit @param {UiCtx['t']} t */
export function dryerLine(d, app, unit, t) {
  if (isRunning(d.state)) return t('panel.dry_left', { target: temp(d.target), left: fmtHM((d.remaining ?? 0) * 60) });
  if (d.state === 'cooldown') return t('dryer.cooldown_rule');
  if (d.state === 'fault') return faultText(d.fault);
  const form = formOf(app, unit);
  return t('panel.dry_plan', { temp: form.temp, time: form.hm });
}

// The dryer's button: Dry while idle, Stop while it heats, Clear on a fault, none while it cools down.
// Each names this unit in its data, as the Dryer tab's cards do.
/** @param {DryerView} d @param {UiCtx['t']} t @returns {{ action: string, label: string } | null} */
export function dryerButton(d, t) {
  if (d.state === 'idle') return { action: 'dry', label: t('panel.dry') };
  if (isRunning(d.state)) return { action: 'dry_stop', label: t('dryer.stop') };
  return d.state === 'fault' ? { action: 'dry_clear', label: t('dryer.clear') } : null;
}

// The unit card: its four spools small, the dryer ring with its state and button, the firmware and the link.
// Opened from the footer's "♨ dry" (focus 'dryer'), the dryer's button takes the focus.
/** @param {{ app: AppState, ctx: UiCtx, unit: string | null, focus?: string }} props */
export function UnitCard({ app, ctx, unit, focus }) {
  const { t } = ctx;
  const slice = sliceFor(app.status, unit);
  const d = dryerView(slice);
  const status = unitStatuses(app).find((u) => u.name === unit);
  const btn = dryerButton(d, t);
  const id = unitId(unit);
  const link = linkWords(status, t);
  /** @returns {unknown} */
  const press = () => ctx.run(/** @type {{ action: string }} */ (btn).action, { unit: id });
  // Each bay's spool colour; an empty bay is dashed, a spool of no known colour is grey.
  const minis = Array.from({ length: LANES }, (_, i) =>
    laneOf(slice, i)?.insert ? { c: colorOf(spoolOf(slice, i)), cls: 'mini' } : { c: null, cls: 'mini empty' },
  );
  return html`<div class="card unit-card">
    <div class="minis">${minis.map(
      (m, i) => html`<span class=${m.c ? m.cls : `${m.cls} unknown`} style=${fil(m.c)}
        title=${String(i + 1)}></span>`,
    )}</div>
    <section class="dry">
      <div class="uring"><b>${isNum(d.chamber) ? t('panel.ring_temp', { v: Math.round(d.chamber) }) : DASH}</b>
        <small>${pct(d.humidity)}</small></div>
      <div>
        <div class="t1">${stateText(d.state)}</div>
        <div class="note">${dryerLine(d, app, unit, t)}</div>
        ${
          btn
            ? html`<button type="button" class=${btn.action === 'dry' ? 'btn pri' : 'btn'}
              data-autofocus=${focus === 'dryer' ? 'true' : undefined} onClick=${press}>${btn.label}</button>`
            : null
        }
      </div>
    </section>
    <p class="note">${status?.version ? t('panel.firmware', { v: status.version, link }) : link}</p>
  </div>`;
}
