import { html, useMemo, useRef, useState } from './html.js';
import { segmentsOf, bands } from '../timeline.js';
import { SEEDS, changesOf, swapStops } from '../swap-time.js';
import { floorPct, fmtMS, fmtStopped, unit } from '../format.js';
import { headName } from '../words.js';
import {
  WHOLE,
  ZOOM_MIN,
  axisLabels,
  clampView,
  moveTo,
  pct,
  pickView,
  place,
  placeAt,
  resize,
  zoomIn,
  zoomOut,
} from '../zoom.js';

/** @typedef {import('../types.js').UiCtx} UiCtx */
/** @typedef {import('../types.js').Plan} Plan */
/** @typedef {import('../timeline.js').HeadOf} HeadOf */
/** @typedef {import('../zoom.js').View} View */

// Where a part from x to e sits on the band, in percent. Offsets out of 0..1, or an end before its start,
// are clamped, so a stray offset never draws outside the band. A start that is not a number draws from 0,
// and an end that is not a number to the end.
/** @param {number} x @param {number} e @returns {{ left: number, width: number }} */
export function span(x, e) {
  const a = unit(x);
  const b = Number.isFinite(e) ? unit(e) : 1;
  return { left: pct(a), width: pct(Math.max(0, b - a)) };
}

// A part's style: its place on the bars (percent) and the colour it draws (--fil).
/** @param {{ left: number, width: number }} at @param {string | null | undefined} colour */
const partStyleOf = ({ left, width }, colour) => `left:${left}%;width:${width}%${colour ? `;--fil:${colour}` : ''}`;

// A drag on the strip, as the zoom function it stands for: the window moved by the pointer's travel from x0 to x,
// one edge resized to x, or the stretch picked from x0 to x on the bare strip.
/** @param {'move' | 'a' | 'b' | 'pick'} kind @param {View} v @param {number} x0 @param {number} x @returns {View} */
export function stripDrag(kind, v, x0, x) {
  if (kind === 'move') return moveTo(v, v.a + (x - x0));
  if (kind === 'a' || kind === 'b') return resize(v, kind, x);
  return pickView(x0, x);
}

// A drag in progress on the strip: what it moves, where it began, and the stretch it began from.
/** @typedef {{ kind: 'move' | 'a' | 'b' | 'pick', x0: number, v: View, id: number }} Drag */

// The strip's pointer handlers. `ref` holds the strip's element and `drag` the drag in progress (both kept by the
// component between renders); `view` is the stretch now shown and `set` changes it. A drag starts on the primary
// button only, belongs to the pointer that started it (other pointers are ignored), is captured on the strip so it
// keeps working when it leaves it, and ends when the buttons are released out of sight (a move with none held).
/** @param {{ current: HTMLElement | null }} ref @param {{ current: Drag | null }} drag @param {View} view @param {(v: View) => void} set */
export function stripPointer(ref, drag, view, set) {
  // The pointer's place along the strip, 0..1.
  /** @param {PointerEvent} e */
  const at = (e) => {
    const r = /** @type {HTMLElement} */ (ref.current).getBoundingClientRect();
    return Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
  };
  return {
    /** @param {PointerEvent} e */
    down(e) {
      if (e.button !== 0) return;
      const cl = /** @type {HTMLElement} */ (e.target).classList;
      const kind = cl.contains('zl') ? 'a' : cl.contains('zr') ? 'b' : cl.contains('zwin') ? 'move' : 'pick';
      /** @type {HTMLElement} */ (ref.current).setPointerCapture(e.pointerId);
      drag.current = { kind, x0: at(e), v: view, id: e.pointerId };
    },
    /** @param {PointerEvent} e */
    move(e) {
      const d = drag.current;
      if (!d || e.pointerId !== d.id) return;
      if (e.buttons === 0) drag.current = null;
      else set(stripDrag(d.kind, d.v, d.x0, at(e)));
    },
    /** The pointer that began the drag is released or lost: the drag ends. @param {PointerEvent} e */
    up(e) {
      if (drag.current?.id === e.pointerId) drag.current = null;
    },
  };
}

// The zoom buttons' actions on the stretch now shown: − (twice as wide), + (half as wide), Whole print.
/** @param {View} view @param {(v: View) => void} set */
export const zoomActions = (view, set) => ({
  out: () => set(zoomOut(view)),
  in: () => set(zoomIn(view)),
  whole: () => set(WHOLE),
});

// A swap mark at x on the band (class cls: a stop, or a free swap), or nothing where x is out of the stretch shown.
/** @param {number} x @param {View} view @param {string} cls @param {string} [title] */
function markAt(x, view, cls, title) {
  const at = placeAt(x, view);
  return at === null ? null : html`<u class=${cls} title=${title} aria-hidden="true" style=${`left:${at}%`}></u>`;
}

// A swap's seconds as m:ss, rounded: the one figure of a mark's title and of the legend.
/** @param {number} s @returns {string} */
const swapTime = (s) => fmtMS(Math.round(s));

// The title of a mark: the time that swap stops the print (m:ss) when it is estimated, else what the mark is, so every
// mark has one. A free swap stops the print as its head arrives, and a late one stops it longer.
/** @param {UiCtx['t']} t @param {{ kind: string, s: number | null }} m */
export function titleOf(t, m) {
  if (m.s !== null) return t('timeline.stop_title', { time: swapTime(m.s) });
  if (m.kind === 'late') return t('timeline.late_title');
  return m.kind === 'free' ? t('timeline.free_title') : t('timeline.swap_title');
}

/**
 * One head's row: its name and the swap count of the whole print, then the band of the stretch shown. The band is
 * thick where the head prints, a thin line in the loaded colour where it waits, a ▼ where it changes spool and the
 * print stops, a hollow ▽ where a parked head changes it in the background, and,
 * during a print, the printed part dimmed and a "now" line (each only where it falls in the stretch).
 * @param {{ band: ReturnType<typeof bands>[number], plan: Plan, pos: number | null, view: View, t: UiCtx['t'] }} props
 */
function Row({ band, plan, pos, view, t }) {
  const past = pos === null ? null : place(0, pos, view);
  const cur = pos === null ? null : placeAt(pos, view);
  return html`<div class="tlr">
    <div class="lb"><b>${headName(t, band.head)}</b><span>${t('timeline.count', { n: band.swaps - band.free })}</span>
      ${band.free ? html`<small>${t('print.background_n', { n: band.free })}</small>` : null}
      ${band.stopS ? html`<small>${t('timeline.head_stopped', { time: fmtStopped(band.stopS) })}</small>` : null}</div>
    <div class="band">
      ${band.parts.map((p) => {
        const at = place(p.x, p.e, view);
        return at
          ? html`<span class=${p.active ? 'act' : 'idle'} style=${partStyleOf(at, plan.tools[p.tool]?.colour)}></span>`
          : null;
      })}
      ${band.drawn.map((m) => markAt(m.x, view, m.kind === 'stop' ? 'sw-mark' : `sw-mark free${m.kind === 'late' ? ' late' : ''}`, titleOf(t, m)))}
      ${past ? html`<span class="past" style=${`left:0%;width:${past.width}%`}></span>` : null}
      ${cur === null ? null : html`<span class="cur" style=${`left:${cur}%`}></span>`}
    </div>
  </div>`;
}

/**
 * The strip of the whole print under the bars: every head's printing parts in miniature, and the window on the
 * stretch shown. Drag the window to move it, its edges to resize it, the bare strip to pick a stretch. Beside it,
 * the buttons − + and Whole print.
 * @param {{ rows: ReturnType<typeof bands>, plan: Plan, view: View, set: (v: View) => void, t: UiCtx['t'] }} props
 */
function Strip({ rows, plan, view, set, t }) {
  const ref = useRef(/** @type {HTMLElement | null} */ (null));
  const drag = useRef(/** @type {Drag | null} */ (null));
  const { down, move, up } = stripPointer(ref, drag, view, set);
  const act = zoomActions(view, set);
  const n = rows.length;
  const full = view.a <= 0 && view.b >= 1;
  // The miniature does not depend on the stretch, so a drag redraws only the window.
  const mini = useMemo(
    () =>
      rows.map((band, i) =>
        band.parts
          .filter((p) => p.active)
          .map(
            (p) =>
              html`<i style=${`left:${pct(p.x)}%;width:${Math.max(0.2, pct(p.e - p.x))}%;top:${pct(i / n)}%;height:${pct(1 / n)}%${plan.tools[p.tool]?.colour ? `;--fil:${plan.tools[p.tool]?.colour}` : ''}`}></i>`,
          ),
      ),
    [rows, plan, n],
  );
  return html`<div class="zrow">
    <span class="note">${t('timeline.whole_print')}</span>
    <div class="zstrip" ref=${ref} onPointerDown=${down} onPointerMove=${move} onPointerUp=${up} onPointerCancel=${up}
      role="group" aria-label=${t('timeline.zoom_label')}>
      ${mini}
      <b class="zwin" style=${`left:${pct(view.a)}%;width:${pct(view.b - view.a)}%`}><i class="zl"></i><i class="zr"></i></b>
    </div>
    <div class="zbtns">
      <button type="button" class="btn" aria-label=${t('timeline.zoom_out')} disabled=${full} onClick=${act.out}>${t('timeline.zoom_out_mark')}</button>
      <button type="button" class="btn" aria-label=${t('timeline.zoom_in')} disabled=${view.b - view.a <= ZOOM_MIN + 1e-9} onClick=${act.in}>${t('timeline.zoom_in_mark')}</button>
      <button type="button" class="btn" disabled=${full} onClick=${act.whole}>${t('timeline.whole')}</button>
    </div>
  </div>`;
}

// The rows of the last plan drawn, with what they were built from (rowsOf).
/** @type {WeakMap<Plan, { key: string, rows: ReturnType<typeof bands> }>} */
const rowsCache = new WeakMap();

// The heads' rows of plan, built again only when what they show changes: the plan, the frame's bytes, the head
// each chosen spool is on, the heads that swap in the background (bg), the slicer's time and the swap averages. The
// page hands a fresh choice, head lookup and frame on every render (a pointer move on the strip renders), so they are
// compared by value, not by identity.
/**
 * @param {Plan} plan @param {Record<number, string>} choice @param {HeadOf} headOf
 * @param {import('../timeline.js').Frame} frame @param {number[]} [bg] @param {number | null} [timeS]
 * @param {import('../swap-time.js').SwapTimes} [times]
 * @returns {ReturnType<typeof bands>}
 */
export function rowsOf(plan, choice, headOf, frame, bg = [], timeS = null, times = SEEDS) {
  const picks = Object.entries(choice).map(([n, key]) => [n, key, headOf(key)]);
  const key = JSON.stringify([frame?.start, frame?.end, picks, bg, timeS, times]);
  const hit = rowsCache.get(plan);
  if (hit?.key === key) return hit.rows;
  const segs = segmentsOf(plan, frame);
  // Segments and changes are one to one, so a stop belongs to the segment at its index.
  const { stops } = swapStops(changesOf(plan, frame, timeS), choice, headOf, bg, times);
  const rows = bands(segs, choice, headOf, bg, stops);
  rowsCache.set(plan, { key, rows });
  return rows;
}

/**
 * "How the print uses each head": a row per head that prints, on one axis showing a stretch of the file (the
 * whole print at first), the strip that picks the stretch, and the legend of the marks (the background swap's only
 * when a row has one). With pos (the share of the file printed) a blue line marks now and the printed part is
 * dimmed. The count beside a head is always the whole print's: the swaps that stop the print, and under it those in
 * the background, when the head (in bg) has any. Nothing when no change of the file goes to a head. `view` fixes the
 * stretch (tests); the page leaves it out and the component keeps its own.
 * @param {{ plan: Plan, choice: Record<number, string>, headOf: HeadOf, pos?: number | null,
 *   frame?: import('../timeline.js').Frame, timeS?: number | null, view?: View | null, bg?: number[],
 *   times?: import('../swap-time.js').SwapTimes, t: UiCtx['t'] }} props
 */
export function Timeline({
  plan,
  choice,
  headOf,
  pos = null,
  frame = null,
  timeS = null,
  view: fixed = null,
  bg,
  times = SEEDS,
  t,
}) {
  const [own, setOwn] = useState(/** @type {View} */ (WHOLE));
  const view = clampView(fixed ?? own);
  const rows = rowsOf(plan, choice, headOf, frame, bg, timeS, times);
  if (!rows.length) return null;
  const [l0, l1, l2] = axisLabels(t, view, timeS);
  return html`<section class="tl">
    <div class="cap">${t('timeline.cap')}</div>
    ${rows.map((band) => html`<${Row} key=${band.head} band=${band} plan=${plan} pos=${pos} view=${view} t=${t} />`)}
    <div class="tlr ax"><div></div><div class="axis">
      <span>${l0}</span><span class="mid">${l1}</span><span>${l2}</span>
    </div></div>
    ${pos === null ? null : html`<p class="note">${t('timeline.now', { pct: floorPct(pos) })}</p>`}
    <${Strip} rows=${rows} plan=${plan} view=${view} set=${setOwn} t=${t} />
    <div class="leg">
      <span><i class="lg act"></i>${t('timeline.legend_active')}</span>
      <span><i class="lg idle"></i>${t('timeline.legend_idle')}</span>
      <span><b class="lg-mark">${t('timeline.mark')}</b> ${t('timeline.legend_swap', { time: swapTime(times.inline) })}</span>
      ${
        rows.some((b) => b.free)
          ? html`<span><b class="lg-mark free">${t('timeline.mark_bg')}</b> ${t('timeline.legend_bg', { time: swapTime(times.ready) })}</span>`
          : null
      }
      ${
        rows.some((b) => b.lateMarks.length)
          ? html`<span><b class="lg-mark free late">${t('timeline.mark_late')}</b> ${t('timeline.legend_late')}</span>`
          : null
      }
    </div>
  </section>`;
}
