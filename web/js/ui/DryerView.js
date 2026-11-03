import { html } from './html.js';
import { dryerOf, ready, unitNames, scoped } from '../state.js';
import { sliceFor, spoolOf, colorOf, FLAP_WHICH, DRYER_MODES, LANES } from '../model.js';
import { dryArgs } from '../actions.js';
import { fmtHM, temp, pct, DASH, isNum } from '../format.js';
import {
  parseHM,
  suggestPreset,
  faultText,
  stateText,
  formOf,
  FAN_SECONDS,
  autoCycle,
  isRunning,
} from '../dryer-form.js';

/** @typedef {import('../types.js').AppState} AppState */
/** @typedef {import('../types.js').UiCtx} UiCtx */
/** @typedef {import('../types.js').DryerView} DryerView */
/** @typedef {import('../types.js').Preset} Preset */
/** @typedef {import('../types.js').Progress} Progress */
/** @typedef {import('../types.js').SampleCache} SampleCache */
/** @typedef {import('../types.js').Spool} Spool */

const RING_R = 54;
const RING_LEN = 2 * Math.PI * RING_R;
const WARN_ITEMS = [1, 2, 3, 4, 5, 6];
// The per-unit split of app.samples that dryerOf (state.js) keeps between redraws. It lives here and not in app
// because it is worked out from app.samples (it is a cache, not state): it is dropped when app.samples is replaced.
/** @type {SampleCache} */
const samples = { src: null, by: new Map() };

// The Dryer tab: one card per unit, in printer.cfg order.
/** @param {{ app: AppState, ctx: UiCtx }} props */
export function DryerView({ app, ctx }) {
  return html`<div class="dryers">
    ${unitNames(app).map(
      (unit) => html`<${DryerCard} key=${unit ?? ''} app=${app} ctx=${ctx} unit=${unit} cache=${samples} />`,
    )}
  </div>`;
}

// The spool dots of a unit and the types they name, in bay order. A bay with no known spool is a dashed dot.
/** @param {AppState} app @param {string | null} unit */
function spoolsOf(app, unit) {
  const slice = sliceFor(app.status, unit);
  const spools = Array.from({ length: LANES }, (_, i) => spoolOf(slice, i));
  return { spools, types: spools.map((s) => s?.type) };
}

/** @param {{ colour: string | null, type: string | null | undefined }} props */
function Dot({ colour, type }) {
  return colour || type
    ? html`<i class="sp" title=${type ?? ''} style=${colour ? { background: colour } : undefined}></i>`
    : html`<i class="sp e"></i>`;
}

// The ring: the chamber's temperature and humidity, and what the unit does. The arc shows only while
// the dryer runs and some of the cycle has passed, so there is no dot at 0 %.
/** @param {{ d: DryerView, ctx: UiCtx, fraction: number | null }} props */
function Ring({ d, ctx, fraction }) {
  const running = isRunning(d.state);
  const arc = running && fraction !== null ? Math.min(1, Math.max(0, fraction)) * RING_LEN : 0;
  const note = running ? ctx.t('dryer.ring_target', { target: temp(d.target) }) : stateText(d.state);
  return html`<svg class=${`ring r-${d.state}`} viewBox="0 0 140 140">
    <circle class="track" cx="70" cy="70" r=${RING_R} />
    ${
      arc > 0
        ? html`<circle class="arc" cx="70" cy="70" r=${RING_R} stroke-dasharray=${`${arc.toFixed(1)} ${RING_LEN.toFixed(1)}`} transform="rotate(-90 70 70)" />`
        : null
    }
    <text class="big" x="70" y="66" text-anchor="middle">${temp(d.chamber)}</text>
    <text class="mid" x="70" y="86" text-anchor="middle">${pct(d.humidity)}</text>
    <text class="sm" x="70" y="104" text-anchor="middle">${note}</text>
  </svg>`;
}

// The strip under the ring: the drying time as h:mm, or the state.
/** @param {{ d: DryerView, ctx: UiCtx, progress: Progress | undefined }} props */
function Strip({ d, ctx, progress }) {
  if (isRunning(d.state)) {
    const total = progress?.total;
    const left = d.remaining ?? 0;
    const text = total
      ? ctx.t('dryer.drying_hm', { done: fmtHM(Math.max(0, total - left) * 60), total: fmtHM(total * 60) })
      : ctx.t('dryer.drying_left', { left: fmtHM(left * 60) });
    return html`<div class="stt heat">${text}</div>`;
  }
  return html`<div class=${`stt ${d.state === 'fault' ? 'fault' : 'idle'}`}>${stateText(d.state)}</div>`;
}

/** @param {number} n */
const failed = (n, /** @type {UiCtx} */ ctx) =>
  n === 1 ? ctx.t('dryer.mode_failed_one') : ctx.t('dryer.mode_failed_many', { n });

// The drying part of the mode's line: what the cycle does, or what the humidity mode waits for.
/** @param {DryerView} d @param {UiCtx} ctx @returns {string | null} */
function activityPart(d, ctx) {
  if (autoCycle(d) && isRunning(d.state)) {
    return ctx.t('dryer.mode_drying', { target: isNum(d.target) ? Math.round(d.target) : DASH });
  }
  if (d.mode !== 'humidity') return null;
  if (!d.cold) return ctx.t('dryer.mode_warming');
  return ctx.t('dryer.mode_waiting', { rh: isNum(d.rh) ? d.rh.toFixed(0) : DASH, limit: d.limit ?? DASH });
}

// The mode's state in one line, as the unit reports it. Manual has none.
/** @param {DryerView} d @param {UiCtx} ctx @returns {string} */
function modeLine(d, ctx) {
  if (d.mode === null || d.mode === 'manual') return '';
  if (d.mode === 'humidity' && d.stopped) return ctx.t('dryer.mode_stopped', { n: d.failures });
  const activity = activityPart(d, ctx);
  const parts = activity === null ? [] : [activity];
  if (d.mode === 'humidity' && d.failures > 0) parts.push(failed(d.failures, ctx));
  return parts.join(' · ');
}

// The item of the humidity warning. Before the host reports the count of failures, item 4 names none.
/** @param {number} k @param {DryerView} d */
const warnKey = (k, d) => (k === 4 && d.maxFailures == null ? 'dryer.warn_4_unknown' : `dryer.warn_${k}`);

// The warning that the humidity mode needs, with Enable and Cancel. It stands in place of the controls.
/** @param {{ d: DryerView, ctx: UiCtx, data: Record<string, string> }} props */
function ModeWarn({ d, ctx, data }) {
  const stops = autoCycle(d) && d.mode !== 'humidity';
  return html`<div class="mode-warn">
    <b>${ctx.t('dryer.warn_title')}</b>
    <ul>${WARN_ITEMS.map((k) => html`<li>${ctx.t(warnKey(k, d), { n: d.maxFailures })}</li>`)}</ul>
    ${stops ? html`<p class="warn-stops">${ctx.t('dryer.warn_stops')}</p>` : null}
    <div class="bts">
      <button type="button" class="btn go" onClick=${() => ctx.run('dry_mode_confirm', data)}>${ctx.t('dryer.warn_enable')}</button>
      <button type="button" class="btn" onClick=${() => ctx.run('dry_mode_cancel', data)}>${ctx.t('dryer.warn_cancel')}</button>
    </div>
  </div>`;
}

// The controls of an idle dryer: the presets, the temperature and the time, and Dry.
/** @param {{ app: AppState, ctx: UiCtx, unit: string | null, data: Record<string, string>, suggested: Preset | null, busy: boolean }} props */
function Controls({ app, ctx, unit, data, suggested, busy }) {
  const { presets } = app;
  const form = formOf(app, unit);
  const minutes = parseHM(form.hm);
  const args = dryArgs(form);
  const online = ready(app);
  /** @param {string} name */
  const field =
    (name) =>
    /** @param {{ target: HTMLInputElement }} ev */
    (ev) =>
      ctx.run('dry_field', { ...data, field: name }, ev.target.value);
  return html`<div class="pre">
      ${presets.map(
        (
          p,
          k,
        ) => html`<button type="button" class=${`chip${suggested === p ? ' sug' : ''}`} aria-pressed=${form.preset === k}
          onClick=${() => ctx.run('preset', { ...data, k: String(k) })}>
          ${ctx.t('dryer.preset_chip', { material: p.material, temp: p.temp, time: fmtHM(p.hours * 3600) })}
          ${suggested === p ? html`<small>${ctx.t('dryer.suggested')}</small>` : null}
        </button>`,
      )}
    </div>
    <div class="set">
      <label class="field"><span>${ctx.t('dryer.temp_label')}</span>
        <input type="text" inputmode="numeric" value=${String(form.temp)} onInput=${field('temp')} /></label>
      <label class="field"><span>${ctx.t('dryer.time_label')}</span>
        <input type="text" inputmode="numeric" value=${String(form.hm)} aria-invalid=${minutes === null}
          onInput=${field('hm')} /></label>
      <button type="button" class="btn go" disabled=${!online || busy || args === null} onClick=${() => ctx.run('dry', data)}>
        ${busy ? ctx.t('action.sending') : ctx.t('dryer.start')}
      </button>
    </div>
    ${minutes === null ? html`<p class="note bad">${ctx.t('dryer.bad_time')}</p>` : null}
    ${minutes !== null && args === null ? html`<p class="note bad">${ctx.t('dryer.bad_temp')}</p>` : null}`;
}

// The hardware readings, the by-hand fans and flaps (idle only) and the dryer log, folded.
// reading is true while the page waits for a dryer log, of any unit: one read at a time.
/** @param {{ app: AppState, ctx: UiCtx, d: DryerView, unit: string | null, data: Record<string, string>, off: boolean, reading: boolean }} props */
function Advanced({ app, ctx, d, unit, data, off, reading }) {
  const id = `adv-${unit ?? ''}`;
  /** @param {string} label @param {string} action @param {Record<string, string>} [more] */
  const button = (label, action, more = {}) =>
    html`<button type="button" class="btn" disabled=${off} onClick=${() => ctx.run(action, { ...data, ...more })}>${label}</button>`;
  return html`<details id=${id} class="adv" open=${app.folds[id] === true}>
    <summary>${ctx.t('dryer.advanced')}</summary>
    <dl class="status">
      <dt>${ctx.t('dryer.outlets')}</dt><dd>${ctx.t('dryer.outlets_value', { l: temp(d.outletL), r: temp(d.outletR) })}</dd>
      ${isRunning(d.state) && isNum(d.drive) ? html`<dt>${ctx.t('dryer.aim')}</dt><dd>${temp(d.drive)}</dd>` : null}
      <dt>${ctx.t('dryer.fans')}</dt><dd>${ctx.t(d.fansOn ? 'dryer.fans_running' : 'dryer.fans_stopped')}</dd>
      ${FLAP_WHICH.map(
        (w) => html`<dt>${ctx.t('dryer.flap_name', { which: ctx.t(`dryer.flap.${w}`) })}</dt>
          <dd>${ctx.t(d.flaps[w] === 'open' ? 'dryer.open' : 'dryer.closed')}</dd>`,
      )}
    </dl>
    ${
      d.manual
        ? html`<div class="pair"><span>${ctx.t('dryer.fans')}</span>
            ${button(ctx.t('dryer.fans_on', { s: FAN_SECONDS }), 'fans_on')}${button(ctx.t('dryer.fans_off'), 'fans_off')}</div>
          ${FLAP_WHICH.map(
            (w) => html`<div class="pair"><span>${ctx.t('dryer.flap_name', { which: ctx.t(`dryer.flap.${w}`) })}</span>
              ${button(ctx.t('dryer.open_verb'), 'flap', { which: w, open: '1' })}${button(ctx.t('dryer.close_verb'), 'flap', { which: w, open: '0' })}</div>`,
          )}`
        : null
    }
    <div class="pair"><button type="button" class="btn" disabled=${off || reading} aria-busy=${reading}
      onClick=${() => ctx.run('dry_log', data)}>${reading ? ctx.t('dryer.log_reading') : ctx.t('dryer.log')}</button></div>
  </details>`;
}

// The reply of ACEK_DRYER_LOG: the counters line, then each fault or interrupted cycle, and Close.
/** @param {{ ctx: UiCtx, unit: string | null, lines: string[], data: Record<string, string> }} props */
function LogPanel({ ctx, unit, lines, data }) {
  const title = unit ? ctx.t('dryer.log_title', { name: unit }) : ctx.t('dryer.log');
  return html`<div class="dlog">
    <b>${title}</b>
    <ul>${lines.map((l) => html`<li>${l}</li>`)}</ul>
    <div class="bts"><button type="button" class="btn" onClick=${() => ctx.run('dry_log_close', data)}>${ctx.t('dryer.log_close')}</button></div>
  </div>`;
}

// The three mode buttons, the current one pressed. Humidity shows only when printer.cfg allows it.
// Nothing is drawn until the unit has reported a mode.
/** @param {{ d: DryerView, ctx: UiCtx, data: Record<string, string>, off: boolean }} props */
function Modes({ d, ctx, data, off }) {
  if (d.mode === null) return null;
  return html`<div class="seg" role="group" aria-label=${ctx.t('dryer.mode')}>
    ${DRYER_MODES.filter((m) => m !== 'humidity' || d.humidityAllowed).map(
      (m) => html`<button type="button" aria-pressed=${d.mode === m} disabled=${off}
        onClick=${() => ctx.run('dry_mode', { ...data, mode: m })}>${ctx.t(`dryer.mode_${m}`)}</button>`,
    )}
  </div>`;
}

// How much of the cycle has passed, 0 to 1, or null when the total is not known.
/** @param {DryerView} d @param {Progress | null | undefined} progress */
function fractionOf(d, progress) {
  const total = progress?.total;
  return total ? (total - (d.remaining ?? 0)) / total : null;
}

// The body under the strip, by the dryer's state. types are the materials in the unit's bays.
/** @param {{ app: AppState, ctx: UiCtx, unit: string | null, d: DryerView, data: Record<string, string>, busy: boolean, off: boolean, types: (string | null | undefined)[] }} props */
function Body({ app, ctx, unit, d, data, busy, off, types }) {
  if (d.state === 'fault') {
    return html`<div class="fault">
      <b>${faultText(d.fault)}</b><p>${ctx.t('dryer.fault_cooling')}</p>
      <div class="bts"><button type="button" class="btn" disabled=${off} onClick=${() => ctx.run('dry_clear', data)}>${ctx.t('dryer.clear')}</button></div>
    </div>`;
  }
  if (isRunning(d.state)) {
    return html`<div class="bts">
        <button type="button" class="btn stop" disabled=${off || busy} onClick=${() => ctx.run('dry_stop', data)}>${ctx.t('dryer.stop')}</button>
        <span class="mu">${ctx.t('dryer.after_stop')}</span></div>`;
  }
  if (d.state === 'cooldown') return html`<p class="note">${ctx.t('dryer.cooldown_rule')}</p>`;
  const suggested = suggestPreset(app.presets, types);
  return html`<${Controls} app=${app} ctx=${ctx} unit=${unit} data=${data} suggested=${suggested} busy=${busy} />`;
}

// The card's head: the unit's name, the materials in its bays and one dot per bay.
/** @param {{ ctx: UiCtx, unit: string | null, spools: (import('../types.js').Spool | null)[], types: (string | undefined)[] }} props */
function Head({ ctx, unit, spools, types }) {
  const name = unit ? ctx.t('units.name', { name: unit }) : ctx.t('dryer.title');
  const kinds = [...new Set(types.filter((x) => typeof x === 'string'))];
  return html`<header class="uh"><b>${name}</b><span class="mu">${kinds.join(', ')}</span>
    <span class="spl">${spools.map((s) => html`<${Dot} colour=${colorOf(s)} type=${s?.type} />`)}</span></header>`;
}

// The humidity threshold and the mode's line, under the mode buttons.
/** @param {{ d: DryerView, ctx: UiCtx }} props */
function ModeInfo({ d, ctx }) {
  const line = modeLine(d, ctx);
  return html`${
    d.mode === 'humidity' && isNum(d.limit)
      ? html`<p class="hum">${ctx.t('dryer.threshold')} <b>${pct(d.limit)}</b></p>`
      : null
  }
    ${line ? html`<p class="mode-line">${line}</p>` : null}`;
}

// The warning stands on the card that asked for it, where printer.cfg allows the humidity mode.
/** @param {AppState} app @param {DryerView} d @param {string | null} unit */
const warnsHere = (app, d, unit) => app.modeWarn && d.humidityAllowed && (app.dryerUnit ?? unitNames(app)[0]) === unit;

// One unit's dryer card.
/** @param {{ app: AppState, ctx: UiCtx, unit: string | null, cache: SampleCache }} props */
function DryerCard({ app, ctx, unit, cache }) {
  const { d, progress } = dryerOf(app, unit, Date.now() / 1000, cache);
  const { spools, types } = spoolsOf(app, unit);
  const pending = scoped(app, unit);
  const data = { unit: unit ?? '' };
  const off = !ready(app);
  const running = isRunning(d.state);
  const warn = warnsHere(app, d, unit);
  const log = app.dryerLog && app.dryerLog.unit === unit ? app.dryerLog : null;
  return html`<section class=${`dcard${d.state === 'fault' ? ' bad' : ''}`}>
    <${Head} ctx=${ctx} unit=${unit} spools=${spools} types=${types} />
    <${Ring} d=${d} ctx=${ctx} fraction=${fractionOf(d, progress)} />
    <${Strip} d=${d} ctx=${ctx} progress=${progress ?? undefined} />
    <${Modes} d=${d} ctx=${ctx} data=${data} off=${off || pending.has('dry_mode:')} />
    <${ModeInfo} d=${d} ctx=${ctx} />
    ${warn ? html`<${ModeWarn} d=${d} ctx=${ctx} data=${data} />` : null}
    ${
      warn && !running
        ? null
        : html`<${Body} app=${app} ctx=${ctx} unit=${unit} d=${d} data=${data} off=${off}
          busy=${pending.has(running ? 'dry_stop:' : 'dry:')}
          types=${types} />`
    }
    <${Advanced} app=${app} ctx=${ctx} d=${d} unit=${unit} data=${data} off=${off} reading=${app.dryerLog?.lines === null} />
    ${log?.lines ? html`<${LogPanel} ctx=${ctx} unit=${unit} lines=${log.lines} data=${data} />` : null}
  </section>`;
}
