import { html, useEffect } from './html.js';
import { Diagram } from './Diagram.js';
import { diagramModel } from '../diagram-model.js';
import { fil, headName } from '../words.js';
import { MobileLists } from './MobileLists.js';
import { Timeline, span } from './Timeline.js';
import { liveAt, segmentsOf } from '../timeline.js';
import { etaTo, swapShareOf, totalOf } from '../clock.js';
import { headOfIn, laneByKey, liveLanes } from '../print-plan.js';
import { bgHeads, liveBg, livePlan, printClock } from '../state.js';
import { resuming } from '../model.js';
import { DASH, floorPct, fmtHM } from '../format.js';
import { timesOf } from '../swap-time.js';

/** @typedef {import('../types.js').AppState} AppState */
/** @typedef {import('../types.js').UiCtx} UiCtx */
/** @typedef {import('../types.js').Status} Status */
/** @typedef {import('../types.js').Plan} Plan */
/** @typedef {import('../types.js').PlanLane} PlanLane */
/** @typedef {import('../diagram-model.js').BayMark} BayMark */
/** @typedef {ReturnType<typeof liveAt>} Live */
/**
 * The running print as the view reads it. plan is the adapter's plan of the running file with the choice it
 * printed, or null (no plan, a plan of another file, or a file with no tool changes). clock is the one clock that
 * every time of the view reads (clock.js), frame the G-code's bytes it counts over.
 * @typedef {{ file: string, plan: (Plan & { choice: Record<number, string> }) | null,
 *   clock: import('../clock.js').Clock, thumb: string | null, timeS: number | null, frame: import('../timeline.js').Frame }} Job
 */

/** @param {AppState} app @returns {Job} */
export function printJob(app) {
  const { file, card, frame, clock } = printClock(app);
  const p = livePlan(app);
  const plan = p && p.file === file && p.choice && p.order?.length ? /** @type {Job['plan']} */ (p) : null;
  return { file, plan, clock, thumb: card?.thumb ?? null, timeS: card?.time ?? null, frame };
}

// The header's time line, on the clock: "1:12 of 1:36 · ≈ 0:24 left"; "finishing" at the last bytes; the time
// printed alone while no time left is known (no progress yet, a stopped print).
/** @param {UiCtx['t']} t @param {import('../clock.js').Clock} c */
function timeWords(t, c) {
  const done = fmtHM(c.done);
  if (c.phase === 'finishing') return t('printing.finishing', { done });
  const total = totalOf(c);
  if (total === null || c.left === null) return t('printing.elapsed', { done });
  return t('printing.time', { done, total: fmtHM(total), left: fmtHM(c.left) });
}

// The effect that asks for the running file's card (its thumbnail and slicer time) when the file changes.
/** @param {Pick<UiCtx, 'run'>} ctx @param {string} file */
export const cardEffect = (ctx, file) => () => void ctx.run('card_load', { file });

// The bays marked on the units: the bay that prints now, and the bays loaded in the other heads (read from the
// live status, so a bay the U1 reports in a head is the one marked).
/** @param {AppState} app @param {Live['now']} now @returns {Record<string, BayMark>} */
export function marksOf(app, now) {
  /** @type {Record<string, BayMark>} */
  const marks = now ? { [now.key]: 'printing' } : {};
  for (const lane of diagramModel(app).units.flatMap((u) => u.lanes)) {
    if (lane.filled && lane.head !== now?.head && !marks[lane.key]) marks[lane.key] = 'in_head';
  }
  return marks;
}

// A file colour's swatch.
/** @param {string | null | undefined} colour */
const swatch = (colour) => html`<i class="sw" style=${fil(colour)}></i>`;

/**
 * The header: thumbnail, name, %, the times, the progress bar, and Pause (Resume while paused) and Stop.
 * @param {{ app: AppState, ctx: UiCtx, job: Job }} props
 */
function Header({ app, ctx, job }) {
  const { t } = ctx;
  // No share reported yet (no progress from Klipper): a dash.
  const p = job.clock.p;
  const pct = p === null ? DASH : t('printing.pct', { pct: floorPct(p) });
  const time = timeWords(t, job.clock);
  const paused = app.status.print_stats?.state === 'paused';
  const busy = app.pending.has('print:pause') || app.pending.has('print:resume') || resuming(app.status);
  return html`<div class="pr-head">
    <span class="pr-thumb">${job.thumb ? html`<img src=${job.thumb} alt=${t('print.thumb', { file: job.file })} />` : null}</span>
    <div class="pr-main">
      <div class="pr-line">
        <b class="pr-name">${job.file.replace(/\.gcode$/i, '')}</b>
        <span class="pr-pct">${pct}</span>
        <span class="note">${time}</span>
      </div>
      <div class="prog"><i style=${`width:${span(0, p ?? 0).width}%`}></i></div>
    </div>
    <div class="pr-ctl">
      ${
        paused
          ? html`<button type="button" class="btn" disabled=${busy} onClick=${() => ctx.run('print_resume', {})}>${t('printing.resume')}</button>`
          : html`<button type="button" class="btn" disabled=${busy} onClick=${() => ctx.run('print_pause', {})}>${t('printing.pause')}</button>`
      }
      <button type="button" class="btn stop" onClick=${() => ctx.run('print_cancel', {})}>${t('printing.stop')}</button>
    </div>
  </div>`;
}

// "T1: colour 5 → colour 3" with the two swatches.
/** @param {{ plan: Plan, head: number, from: number, to: number, t: UiCtx['t'] }} props */
function Change({ plan, head, from, to, t }) {
  return html`<span class="sws">${swatch(plan.tools[from]?.colour)}${swatch(plan.tools[to]?.colour)}</span>
    <span class="txt">${t('printing.change', { head: headName(t, head), from: from + 1, to: to + 1 })}</span>`;
}

// The colour of the bay that prints, in words: its name from the host, else its code (#A1B2C3), else the file
// colour's code; empty when none is known.
/** @param {PlanLane | undefined} lane @param {import('../types.js').Tool | undefined} tool */
export const colourWords = (lane, tool) =>
  lane?.colour_name ?? String(lane?.colour ?? tool?.colour ?? '').toUpperCase();

// The spool in words: "red PLA"; the colour alone when the material is not known.
/** @param {string} colour @param {string | null | undefined} material @param {UiCtx['t']} t */
export const spoolWords = (colour, material, t) => (material ? t('step.what', { colour, material }) : colour);

/**
 * The Now card: the head, colour and bay that print; orange while a head changes spool; "finished" at the end of
 * the file, and "no colour yet" before its first change.
 * @param {{ job: Job, live: Live, lanes: PlanLane[], times: import('../swap-time.js').SwapTimes, t: UiCtx['t'] }} props
 */
function NowCard({ job, live, lanes, times, t }) {
  const plan = /** @type {Plan} */ (job.plan);
  // No share reported yet: which colour prints is not known.
  if (job.clock.p === null)
    return html`<div class="nowc"><div class="k">${t('printing.now_cap')}</div>
      <div class="v">${t('printing.now_unknown')}</div></div>`;
  const sw = live.swapping;
  if (sw)
    return html`<div class="nowc swap">
      <div class="k">${t('printing.swapping')}</div>
      <div class="v"><${Change} plan=${plan} head=${sw.head} from=${sw.from} to=${sw.to} t=${t} /></div>
      <div class="sub note">${t('printing.waits', { time: fmtHM(times.inline) })}</div>
    </div>`;
  const now = live.now;
  if (!now)
    return html`<div class="nowc"><div class="k">${t('printing.now_cap')}</div>
      <div class="v">${(job.clock.p ?? 0) >= 1 ? t('printing.finished') : t('printing.no_change')}</div></div>`;
  const tool = plan.tools[now.tool];
  const words = t('printing.now', {
    head: headName(t, now.head),
    n: now.tool + 1,
    spool: spoolWords(colourWords(laneByKey(lanes, now.key), tool), tool?.material, t),
    key: now.key,
  });
  return html`<div class="nowc"><div class="k">${t('printing.now_cap')}</div>
    <div class="v"><span class="sws">${swatch(tool?.colour)}</span><span class="txt">${words}</span></div></div>`;
}

// Under a next swap in the background: when the print stops next ("next stop in ≈ h:mm"), or that it never stops
// again. Nothing while the time to that stop is not known.
/** @param {UiCtx['t']} t @param {Job} job @param {Live['nextStop']} stop */
function stopWords(t, job, stop) {
  if (!stop) return t('printing.no_stop');
  const eta = etaTo(job.clock, stop.x);
  return eta === null ? null : t('printing.next_stop', { eta: fmtHM(eta) });
}

/**
 * The next spool swap: "Next spool swap · in ≈ h:mm", the head, the colours and the bays; "none until the end"
 * when no change loads another spool. A next swap in the background reads "Next swap in the background · in ≈ h:mm",
 * and a line under it says when the print stops next.
 * @param {{ job: Job, live: Live, t: UiCtx['t'] }} props
 */
function NextCard({ job, live, t }) {
  // No share reported yet: nothing is said about the next swap.
  if (job.clock.p === null) return null;
  const plan = /** @type {NonNullable<Job['plan']>} */ (job.plan);
  const next = live.next;
  if (!next)
    return html`<div class="nowc next"><div class="k">${t('printing.next_cap')}</div>
      <div class="v good">${t('printing.no_next')}</div></div>`;
  // The same clock as the header's times.
  const eta = etaTo(job.clock, next.x);
  const at = eta === null ? null : { eta: fmtHM(eta) };
  let cap = at ? t('printing.next', at) : t('printing.next_cap');
  if (next.free) cap = at ? t('printing.next_bg', at) : t('printing.next_bg_cap');
  const stop = next.free ? stopWords(t, job, live.nextStop) : null;
  return html`<div class="nowc next"><div class="k">${cap}</div>
    <div class="v"><${Change} plan=${plan} head=${next.head} from=${next.from} to=${next.to} t=${t} /></div>
    <div class="sub note">${t('printing.bays', { from: plan.choice[next.from], to: plan.choice[next.to] })}</div>
    ${stop ? html`<div class="sub note">${stop}</div>` : null}
  </div>`;
}

/**
 * The Print tab while a print runs: the header with the progress and the controls; with a plan of the running
 * file, also Now, the next spool swap, the units with the printing bay marked, and the timeline with "now".
 * @param {{ app: AppState, ctx: UiCtx }} props
 */
export function Printing({ app, ctx }) {
  const { t } = ctx;
  const job = printJob(app);
  useEffect(cardEffect(ctx, job.file), [job.file]);
  const plan = job.plan;
  if (!plan)
    return html`<section class="printing">
      <${Header} app=${app} ctx=${ctx} job=${job} />
    </section>`;
  const lanes = liveLanes(app.status, plan);
  const headOf = headOfIn(lanes);
  const times = timesOf(app.status);
  // The orange "swap in progress" lasts about one swap of print time, not a share of the change's part.
  const swapShare = swapShareOf(job.clock, times.inline);
  // The timeline shows the plan: a swap of a parked background head is free there. The Now and Next cards follow
  // the live status: a free swap is in progress while the print really waits for it (liveBg).
  const bg = bgHeads(app.status);
  const seen = liveBg(app.status);
  const live = liveAt(segmentsOf(plan, job.frame), plan.choice, headOf, job.clock.p ?? 0, swapShare, seen.free, seen);
  const marks = marksOf(app, live.now);
  return html`<section class="printing">
    <${Header} app=${app} ctx=${ctx} job=${job} />
    <div class="now2">
      <${NowCard} job=${job} live=${live} lanes=${lanes} times=${times} t=${t} />
      <${NextCard} job=${job} live=${live} t=${t} />
    </div>
    <${app.ui.narrow ? MobileLists : Diagram} app=${app} ctx=${ctx} marks=${marks} />
    <${Timeline} key=${job.file} timeS=${job.timeS} plan=${plan} choice=${plan.choice} headOf=${headOf} pos=${job.clock.p}
      bg=${bg} times=${times}
      frame=${job.frame} t=${t} />
  </section>`;
}
