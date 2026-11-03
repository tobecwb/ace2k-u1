import { html } from './html.js';
import { sliceFor } from '../model.js';
import { DASH } from '../format.js';
import {
  diagramModel,
  headLanes,
  headView,
  envText,
  darkFil,
  MARK_WORDS,
  openBay,
  openHead,
} from '../diagram-model.js';
import { fil, headName, materialWord, unitLabel, withError } from '../words.js';

/** @typedef {import('../types.js').AppState} AppState */
/** @typedef {import('../types.js').UiCtx} UiCtx */
/** @typedef {import('../diagram-layout.js').DLane} DLane */
/** @typedef {import('../diagram-model.js').BayMark} BayMark */

// The grouping the Units tab list shows on a phone: 'head' unless the page was asked for 'unit'.
/** @param {AppState} app @returns {'head' | 'unit'} */
export const listMode = (app) => (app.ui.listMode === 'unit' ? 'unit' : 'head');

// A lane's key as an amber unit tag and its number ("a:2" is the tag a and 2); a key with no unit is the number.
/** @param {{ lane: DLane }} props */
function LaneKey({ lane }) {
  const at = lane.key.indexOf(':');
  return html`<span class="mkey">${at < 0 ? null : html`<span class="mut">${lane.key.slice(0, at)}</span>`}${lane.n}</span>`;
}

// A colour dot: dashed while there is no colour, else the spool's colour.
/** @param {{ colour: string | null | undefined, filled: boolean }} props */
function Dot({ colour, filled }) {
  return html`<i class=${filled ? 'msw' : 'msw none'} style=${fil(colour)} data-dark-fil=${darkFil(colour)}></i>`;
}

// A row's state: the print's mark when it has one, else in the head, waiting, or empty.
/** @param {DLane} lane @param {boolean} inHead @param {BayMark | undefined} mark @param {UiCtx['t']} t */
function stateWords(lane, inHead, mark, t) {
  if (mark) return t(MARK_WORDS[mark]);
  if (!lane.spool) return t('units.empty');
  return inHead ? t('panel.in_head') : t('list.waiting');
}

// One lane row: its key, material, (by unit) the head it feeds, its state, and a red mark with the short error word
// while it is in error. A tap opens the bay's panel, as the diagram's tile does.
// During a print (marks given) the marks speak for the heads: a lane with no mark waits, even one its head still
// reports as loaded while the printing bay takes over, so "in the head" never sits next to "in head".
// sel: the bay's side panel is open (the sheet on a phone): the row is marked as the diagram's tile is.
/** @param {{ lane: DLane, withHead: boolean, ctx: UiCtx, mark?: BayMark, marked: boolean, sel?: boolean }} props */
function LaneRow({ lane, withHead, ctx, mark, marked, sel = false }) {
  const { t } = ctx;
  const inHead = lane.filled && !marked;
  const state = stateWords(lane, inHead, mark, t);
  const material = lane.spool ? materialWord(t, lane.spool.material) : DASH;
  const cls = [
    'mrow',
    inHead ? 'on' : '',
    lane.spool ? '' : 'em',
    lane.error ? 'err' : '',
    mark ? `mk-${mark}` : '',
    sel ? 'sel' : '',
  ];
  const head = lane.head === null ? DASH : headName(t, lane.head);
  const what = withError(material, lane.error);
  return html`<button type="button" class=${cls.filter(Boolean).join(' ')} style=${fil(inHead ? lane.pathColour : null)}
    aria-current=${sel ? 'true' : undefined} aria-label=${t('units.bay_label', { key: lane.key, what, head })}
    onClick=${() => ctx.run('panel', { kind: 'bay', key: lane.key })}>
    <${Dot} colour=${lane.spool?.colour} filled=${lane.spool !== null} />
    <${LaneKey} lane=${lane} />
    <span class="mmat">${material}</span>
    ${withHead ? html`<span class="mhd">${t('list.to_head', { head })}</span>` : null}
    ${
      lane.error
        ? html`<span class="merr"><b class="merr-mark">${t('units.err_mark')}</b><span class="merr-text">${t(/** @type {string} */ (lane.errorShort))}</span></span>`
        : html`<span class="mstate">${state}</span>`
    }
    <span class="mchev">${t('units.chevron')}</span>
  </button>`;
}

// The By head list: a block per head, with the lanes that feed it (the lane in the head in blue).
/** @param {{ app: AppState, ctx: UiCtx, marks: Record<string, BayMark> | undefined }} props */
function ByHead({ app, ctx, marks }) {
  const open = openBay(app); // the bay whose side panel is open, once for every row
  const openH = openHead(app); // and the head whose side panel is open
  const { t } = ctx;
  const model = diagramModel(app);
  return model.heads.map((h) => {
    const view = headView(model, h, t);
    const lanes = headLanes(model, h.head).map((x) => x.lane);
    const name = headName(t, h.head);
    return html`<section class="mblock" key=${h.head}>
      <button type="button" class=${openH === h.head ? 'mheadrow sel' : 'mheadrow'}
        aria-current=${openH === h.head ? 'true' : undefined} aria-label=${t('units.head_label', { head: name, what: view.line1 })}
        onClick=${() => ctx.run('panel', { kind: 'head', id: String(h.head) })}>
        <b>${name}</b>
        <${Dot} colour=${view.colour} filled=${Boolean(view.colour)} />
        <span class=${view.warn ? 'mline warn' : 'mline'}>${view.line1}</span>
        ${lanes.length ? html`<span class="mmut">${lanes.length === 1 ? t('list.lane_one') : t('list.lanes', { n: lanes.length })}</span>` : null}
        <span class="mchev">${t('units.chevron')}</span>
      </button>
      ${lanes.map((lane) => html`<${LaneRow} lane=${lane} withHead=${false} ctx=${ctx} mark=${marks?.[lane.key]} marked=${Boolean(marks)} sel=${open === lane.key} />`)}
    </section>`;
  });
}

// The By unit list: an amber block per unit, with its reading, its dry shortcut and its lanes, each with the head it feeds.
/** @param {{ app: AppState, ctx: UiCtx, marks: Record<string, BayMark> | undefined }} props */
function ByUnit({ app, ctx, marks }) {
  const open = openBay(app); // the bay whose side panel is open, once for every row
  const { t } = ctx;
  const model = diagramModel(app);
  return model.units.map((unit) => {
    const label = unitLabel(t, unit.name);
    const id = unit.name ?? '';
    return html`<section class="mblock unit" key=${id}>
      <div class="munithead">
        <button type="button" class="munitopen" aria-label=${t('units.unit_label', { name: label })}
          onClick=${() => ctx.run('panel', { kind: 'unit', id })}>
          <b>${label}</b>
          <span class="mmut">${envText(sliceFor(app.status, unit.name))}</span>
        </button>
        <button type="button" class="mdry" aria-label=${t('units.dry_label', { name: label })}
          onClick=${() => ctx.run('dry_shortcut', { unit: id })}>${t('units.dry')}</button>
      </div>
      ${unit.lanes.map((lane) => html`<${LaneRow} lane=${lane} withHead=${true} ctx=${ctx} mark=${marks?.[lane.key]} marked=${Boolean(marks)} sel=${open === lane.key} />`)}
    </section>`;
  });
}

// One button of the switch.
/** @param {{ on: boolean, mode: string, label: string, ctx: UiCtx }} props */
function Toggle({ on, mode, label, ctx }) {
  return html`<button type="button" class=${on ? 'on' : ''} aria-pressed=${on ? 'true' : 'false'}
    onClick=${() => ctx.run('list_mode', { mode })}>${label}</button>`;
}

// The Units tab on a phone: lists instead of the diagram, with a switch between grouping by head or by unit.
// marks names bays by key during a print (the printing view), as the diagram outlines them.
/** @param {{ app: AppState, ctx: UiCtx, marks?: Record<string, BayMark> }} props */
export function MobileLists({ app, ctx, marks }) {
  const mode = listMode(app);
  return html`<div class="mlists">
    <div class="mseg" role="group" aria-label=${ctx.t('list.group')}>
      <${Toggle} on=${mode === 'head'} mode="head" label=${ctx.t('list.by_head')} ctx=${ctx} />
      <${Toggle} on=${mode === 'unit'} mode="unit" label=${ctx.t('list.by_unit')} ctx=${ctx} />
    </div>
    ${mode === 'head' ? html`<${ByHead} app=${app} ctx=${ctx} marks=${marks} />` : html`<${ByUnit} app=${app} ctx=${ctx} marks=${marks} />`}
  </div>`;
}
