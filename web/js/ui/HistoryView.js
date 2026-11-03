import { html, useEffect, useMemo, useRef, useState } from './html.js';
import { chartBox, chartSvg, tooltipAt, tooltipPlace, tooltipSample, tooltipText } from '../chartsvg.js';
import { clock, hasTarget, pct, temp, tempWithTarget } from '../format.js';
import { filterEvents } from '../history.js';
import { logLineText, sourceOf } from '../log-text.js';
import { isRunning } from '../dryer-form.js';
import { topbarView } from '../model.js';
import { dryerOf, known, olderDays, samplesOf, unitNames } from '../state.js';

/** @typedef {import('../types.js').AppState} AppState */
/** @typedef {import('../types.js').UiCtx} UiCtx */
/** @typedef {import('../types.js').Event} LogEvent */
/** @typedef {import('../types.js').Sample} Sample */
/** @typedef {import('../types.js').SampleCache} SampleCache */
/** @typedef {import('../chartsvg.js').ChartEvent} ChartEvent */
/**
 * The pointer's tooltip: what it says and where it sits in the plot box.
 * @typedef {{ text: string, left: number, top: number }} Tip
 */
/** @typedef {{ current: HTMLElement | null }} ElRef */

export const WINDOWS = [1, 6, 24];
export const FILTERS = ['all', 'errors', 'lane1', 'lane2', 'lane3', 'lane4', 'dryer'];
const LEGEND = [
  ['ch', 'chart.chamber', '--line-ch'],
  ['hl', 'chart.heater_l', '--line-hl'],
  ['hr', 'chart.heater_r', '--line-hr'],
  ['rh', 'chart.humidity', '--line-rh'],
  ['tgt', 'chart.target', '--line-ch'], // the chamber's target, dashed in the chamber's colour
];
const DEFAULT_WIDTH = 960; // the plot's width until the page has measured it
const SOURCE_COLOURS = 4; // the colour classes src-0 to src-3, one per unit in turn

// The window that a running cycle shows: the shortest one that holds the cycle, else 6 hours.
/** @param {{ start?: number | null } | null | undefined} progress @param {number} nowS */
export function defaultWindow(progress, nowS) {
  if (!progress?.start) return 6;
  const hours = (nowS - progress.start) / 3600;
  return WINDOWS.find((w) => w >= hours) || 24;
}

// The colour class of a source's dot: the unit's place in the list, or the U1's own.
/** @param {{ u1: boolean, name: string | null }} source @param {(string | null)[]} units */
export function sourceClass(source, units) {
  if (source.u1) return 'src-u1';
  return `src-${Math.max(0, units.indexOf(source.name)) % SOURCE_COLOURS}`;
}

// The colour of an event's dot on the chart: red for an error, the dryer's own, else the neutral one.
/** @param {LogEvent} e */
export const eventTone = (e) => (e.lvl === 'err' ? 'err' : e.src === 'dryer' ? 'dryer' : 'info');

// The events as the chart draws them.
/** @param {LogEvent[]} events @returns {ChartEvent[]} */
export const chartEvents = (events) => events.map((e) => ({ t: e.t, tone: eventTone(e) }));

// The table's rows with a key each. Rows with the same time and text get their occurrence number, so every key is unique.
/** @param {LogEvent[]} rows @returns {{ key: string, e: LogEvent }[]} */
export function keyedRows(rows) {
  /** @type {Map<string, number>} */
  const seen = new Map();
  return rows.map((e) => {
    const base = `${e.t}|${e.msg}`;
    const n = seen.get(base) ?? 0;
    seen.set(base, n + 1);
    return { key: `${base}|${n}`, e };
  });
}

// The tooltip under the pointer. pointer is relative to the plot box and box is that box's width.
// Null when no sample is in the window.
/**
 * @param {{ x: number, y: number }} pointer
 * @param {number} boxW
 * @param {number} tipW
 * @param {{ win: { w: number, l: number, r: number, t0: number, t1: number }, samples: Sample[],
 *   fmt: Parameters<typeof tooltipText>[1] }} view
 * @returns {Tip | null}
 */
export function tipFor(pointer, boxW, tipW, { win, samples, fmt }) {
  const s = tooltipSample(samples, win.t0, tooltipAt(pointer.x, boxW, win));
  return s ? { text: tooltipText(s, fmt), ...tooltipPlace(pointer, boxW, tipW) } : null;
}

// The pointer handlers of the plot. refs holds the plot's wrapper and the tooltip element. view is what tipFor reads.
// A mouse that leaves the plot hides the tooltip. A tap leaves it shown until a tap elsewhere (hideOnOutsideTap).
/** @param {{ wrap: ElRef, tip: ElRef }} refs @param {Parameters<typeof tipFor>[3]} view */
export function tipHandlers(refs, view) {
  /** @param {PointerEvent} ev */
  const show = (ev) => {
    const tip = refs.tip.current;
    const svg = refs.wrap.current?.querySelector('svg');
    if (!tip || !svg) return;
    const box = svg.getBoundingClientRect();
    const at = tipFor({ x: ev.clientX - box.left, y: ev.clientY - box.top }, box.width, tip.offsetWidth, view);
    tip.hidden = !at;
    if (!at) return;
    tip.textContent = at.text;
    tip.style.left = `${at.left}px`;
    tip.style.top = `${at.top}px`;
  };
  return {
    move: show,
    down: show,
    /** @param {PointerEvent} ev */
    leave: (ev) => {
      if (refs.tip.current && ev.pointerType === 'mouse') refs.tip.current.hidden = true;
    },
  };
}

// A tap anywhere outside the plot hides the tooltip that an earlier tap left. Returns the cleanup.
/** @param {Document} doc @param {{ wrap: ElRef, tip: ElRef }} refs */
export function hideOnOutsideTap(doc, { wrap, tip }) {
  /** @param {Event} ev */
  const on = (ev) => {
    if (tip.current && !wrap.current?.contains(/** @type {Node} */ (ev.target))) tip.current.hidden = true;
  };
  doc.addEventListener('pointerdown', on);
  return () => doc.removeEventListener('pointerdown', on);
}

// Tells set the plot's width now and whenever it changes. Returns the cleanup, or nothing before the plot exists.
/** @param {HTMLElement | null} el @param {(w: number) => void} set @param {typeof ResizeObserver} Observer */
export function watchWidth(el, set, Observer) {
  if (!el) return undefined;
  const observer = new Observer(() => set(el.clientWidth));
  observer.observe(el);
  return () => observer.disconnect();
}

/** @param {string} key @param {string} colour */
const Swatch = (key, colour) =>
  key === 'tgt'
    ? html`<i class="dashed" style=${`color:var(${colour})`}></i>`
    : html`<i style=${`background:var(${colour})`}></i>`;

// The filters, in one row: All, Errors, the units (with several), the four lanes, and Dryer.
/** @param {{ app: AppState, ctx: UiCtx, units: (string | null)[] }} props */
function Filters({ app, ctx, units }) {
  const { t } = ctx;
  /** @param {string} f @param {string} text @param {string} [label] */
  const btn = (f, text, label) =>
    html`<button type="button" aria-pressed=${f === app.filter} aria-label=${label}
      onClick=${() => ctx.run('filter', { f })}>${text}</button>`;
  return html`<div class="filters">
    ${btn('all', t('events.filter.all'))}${btn('errors', t('events.filter.errors'))}
    ${
      units.length > 1
        ? html`<span class="lanes" role="group" aria-label=${t('events.filter.units')}>
          <span aria-hidden="true">${t('events.filter.units')}</span>
          ${units.map((name) => btn(`unit:${name}`, String(name), t('units.name', { name })))}
        </span>`
        : null
    }
    <span class="lanes" role="group" aria-label=${t('events.filter.lanes')}>
      <span aria-hidden="true">${t('events.filter.lanes')}</span>
      ${[1, 2, 3, 4].map((n) => btn(`lane${n}`, String(n), t('events.filter.lane', { n })))}
    </span>
    ${btn('dryer', t('events.filter.dryer'))}
  </div>`;
}

// The source cell: the unit's dot and name, or the U1's.
/** @param {{ e: LogEvent, units: (string | null)[], t: UiCtx['t'] }} props */
function Source({ e, units, t }) {
  const source = sourceOf(e);
  const name = source.u1
    ? t('history.src_u1')
    : source.name
      ? t('history.src_ace', { name: source.name })
      : t('history.src_ace_one');
  return html`<span class="src"><i class=${`hdot ${sourceClass(source, units)}`}></i>${name}</span>`;
}

// The event table: time, source and text, newest first, full width. rows are already filtered and cut to the limit.
/** @param {{ rows: LogEvent[], more: boolean, units: (string | null)[], ctx: UiCtx }} props */
function EventTable({ rows, more, units, ctx }) {
  const { t } = ctx;
  if (!rows.length) return html`<p class="note">${t('events.empty')}</p>`;
  return html`<div class="evscroll" role="region" tabindex="0" aria-label=${t('events.title')}><table class="evtable">
    <thead><tr><th scope="col">${t('history.col_time')}</th><th scope="col">${t('history.col_source')}</th><th scope="col">${t('history.col_event')}</th></tr></thead>
    <tbody>
      ${keyedRows(rows).map(
        ({ key, e }) => html`<tr key=${key} class=${`lvl-${e.lvl}`}>
          <td class="tm"><time>${clock(e.t)}</time></td>
          <td><${Source} e=${e} units=${units} t=${t} /></td>
          <td class="tx">${logLineText(e.msg)}</td>
        </tr>`,
      )}
    </tbody>
  </table></div>
  ${more ? html`<button type="button" class="more" onClick=${() => ctx.run('more')}>${t('events.more')}</button>` : null}`;
}

// The History tab: the chart on top and the event table below, both across the whole width. The events are dots
// on the chart's time axis. The filter picks the events of both.

/** @param {{ app: AppState, ctx: UiCtx }} props */
export function HistoryView({ app, ctx }) {
  const { t } = ctx;
  const wrap = useRef(/** @type {HTMLElement | null} */ (null));
  const tip = useRef(/** @type {HTMLElement | null} */ (null));
  // The per-unit split that samplesOf keeps until app.samples is replaced. Each page instance holds its own.
  const cache = useRef(/** @type {SampleCache} */ ({ src: null, by: new Map() })).current;
  const [measured, setMeasured] = useState(0);
  useEffect(() => watchWidth(wrap.current, setMeasured, ResizeObserver), []);
  useEffect(() => hideOnOutsideTap(document, { wrap, tip }), []);
  const units = unitNames(app);
  const unit = known(app, app.chartUnit);
  const nowS = Math.floor(Date.now() / 1000 / 30) * 30; // rounded to 30 s, the sample interval
  const { d, progress } = dryerOf(app, unit, nowS, cache);
  const unitSamples = samplesOf(app, unit, cache);
  const hours = app.hours ?? defaultWindow(progress, nowS);
  const width = measured || DEFAULT_WIDTH;
  const t0 = nowS - hours * 3600;
  const shown = unitSamples.filter((s) => s.t >= t0);
  const heating = isRunning(d.state);
  const live = heating && typeof d.target === 'number' ? { from: progress?.start ?? null, value: d.target } : null;
  const filtered = useMemo(() => filterEvents(app.events, app.filter), [app.events, app.filter]);
  const dots = useMemo(() => chartEvents(filtered), [filtered]);
  const svg = useMemo(
    () =>
      shown.length > 1
        ? chartSvg(shown, {
            t0,
            t1: nowS,
            hidden: app.hidden,
            labels: { ago: t('chart.ago', { h: hours }), now: t('chart.now') },
            width,
            live,
            events: dots,
          })
        : '',
    [unitSamples, hours, app.hidden.join(','), nowS, width, live?.from, live?.value, dots],
  );
  const box = chartBox(width);
  const reading =
    heating && hasTarget(d.target) && shown.length ? ` ${tempWithTarget(shown[shown.length - 1].ch, d.target)}` : '';
  const handlers = tipHandlers(
    { wrap, tip },
    {
      win: { w: box.W, l: box.L, r: box.R, t0, t1: nowS },
      samples: unitSamples,
      fmt: { clock, temp, pct, t },
    },
  );
  const error = topbarView(app.status, true).history;
  return html`<section class="history">
    ${error ? html`<p class="history-error">${t('top.history_error', { e: error })}</p>` : null}
    <div class="hbox">
      <div class="head">
        <h2>${t('chart.title')}</h2>
        ${
          units.length > 1
            ? html`<span class="seg" role="group" aria-label=${t('chart.unit')}>
              ${units.map(
                (u) => html`<button type="button" aria-pressed=${u === unit}
                  onClick=${() => ctx.run('chart_unit', { unit: String(u) })}>${t('units.name', { name: u })}</button>`,
              )}
            </span>`
            : null
        }
        <span class="grow"></span>
        <span class="seg" role="group" aria-label=${t('chart.window')}>
          ${WINDOWS.map(
            (w) => html`<button type="button" aria-pressed=${w === hours}
              onClick=${() => ctx.run('window', { h: String(w) })}>${t(`chart.window_${w}h`)}</button>`,
          )}
        </span>
      </div>
      <div class="hlegend">
        ${LEGEND.map(
          ([k, label, colour]) => html`<button type="button" aria-pressed=${!app.hidden.includes(k)}
            onClick=${() => ctx.run('series', { key: k })}>${Swatch(k, colour)}${t(label) + (k === 'ch' ? reading : '')}</button>`,
        )}
        <span class="hlegend-ev"><i class="hdot ev-info"></i>${t('history.legend_events')}</span>
      </div>
      <div class="chart-wrap" ref=${wrap} onPointerMove=${handlers.move} onPointerDown=${handlers.down}
        onPointerLeave=${handlers.leave}>
        ${
          svg
            ? html`<div class="plot" dangerouslySetInnerHTML=${{ __html: svg }}></div>`
            : html`<div class="note">${t('chart.empty')}</div>`
        }
        <div class="tip" ref=${tip} hidden></div>
      </div>
    </div>
    <div class="hbox">
      <div class="head">
        <h2>${t('events.title')}</h2><span class="grow"></span><span class="note">${t('events.subtitle')}</span>
      </div>
      <${Filters} app=${app} ctx=${ctx} units=${units} />
      <${EventTable} rows=${filtered.slice(0, app.limit)} more=${filtered.length > app.limit || olderDays(app, false)}
        units=${units} ctx=${ctx} />
    </div>
  </section>`;
}
