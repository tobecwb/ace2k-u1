import { html } from './html.js';
import {
  LANES,
  colorOf,
  headOf,
  headSetting,
  isCurrent,
  laneActions,
  laneText,
  reasonKey,
  laneUnknownIn,
  sharedHeadsView,
  sliceFor,
  spoolIn,
} from '../model.js';
import { unitId } from '../lane-state.js';
import { LOAD_HEAD_KEY, ready, scoped, unitNames } from '../state.js';
import { fil, headName, headVars, lanesLine, materialWord, u1SpoolLine } from '../words.js';

/** @typedef {import('../types.js').AppState} AppState */
/** @typedef {import('../types.js').UiCtx} UiCtx */
/** @typedef {import('../lane-state.js').LaneAt} LaneAt */
/** @typedef {import('../types.js').LaneAction} LaneAction */
/** @typedef {LaneAt & { current: boolean, colour: string | null, material: string | null }} HeadLane */

// The lanes that feed head h, of every unit, each with whether it is in the head now and its spool.
/** @param {AppState} app @param {number} h @returns {HeadLane[]} */
export function headLanesOf(app, h) {
  return unitNames(app).flatMap((unit) => {
    const slice = sliceFor(app.status, unit);
    return Array.from({ length: LANES }, (_, i) => i)
      .filter((i) => headOf(slice, i) === h)
      .map((i) => {
        const sp = spoolIn(slice, i);
        return {
          unit,
          i,
          slice,
          key: laneText(slice, i),
          current: isCurrent(slice, h, unit, i),
          colour: colorOf(sp),
          material: sp?.type ?? null,
        };
      });
  });
}

// A lane's Load into T<n>: disabled with its reason (a title and the card's note), sending while a head loads.
/** @param {{ lane: HeadLane, load: LaneAction, h: number, app: AppState, ctx: UiCtx }} props */
function LoadButton({ lane, load, h, app, ctx }) {
  const { t } = ctx;
  const busy = app.pending.has(LOAD_HEAD_KEY);
  const head = headName(t, h);
  const key = load.reason ? reasonKey(load.id, load.reason) : null;
  const data = { id: 'load_head', lane: String(lane.i + 1), unit: unitId(lane.unit) };
  return html`<button type="button" class="btn" disabled=${!load.enabled || busy}
    title=${key ? t(`reason.${key}`, { head }) : undefined}
    onClick=${() => ctx.run('lane', data)}>${busy ? t('action.sending') : t('action.load_head', { head })}</button>`;
}

// One lane of the head: its colour, key and material, and "in the head" or its Load into T<n> button.
// load is the lane's Load into T<n> action, or undefined when the lane does not offer it.
/** @param {{ lane: HeadLane, load: LaneAction | undefined, h: number, app: AppState, ctx: UiCtx }} props */
function LaneRow({ lane, load, h, app, ctx }) {
  const { t } = ctx;
  const what = `${lane.key} ${materialWord(t, lane.material)}`;
  return html`<li class=${lane.current ? 'row on' : 'row'}>
    <span class="fdot" style=${fil(lane.colour)}></span>
    <span>${what}</span>
    ${
      lane.current
        ? html`<span class="note">${t('panel.in_head')}</span>`
        : load
          ? html`<${LoadButton} lane=${lane} load=${load} h=${h} app=${app} ctx=${ctx} />`
          : null
    }
  </li>`;
}

// Find the lane in T<n>, while the adapter does not know which lane is in the head. It says sending while its
// request is out, and shows its reason as a title when it is disabled. reason is null while it is busy:
// the probe's own moves would read as a lane that moves. While it can be pressed, a note says what it moves.
/**
 * @param {{ enabled: boolean, reason: string | null, busy: boolean, head: number, app: AppState, ctx: UiCtx }} props
 */
function ProbeButton({ enabled, reason, busy, head, app, ctx }) {
  const { t } = ctx;
  const vars = headVars(t, app.status, reason, head);
  const ready = enabled && !busy;
  return html`<div class="acts sm"><button type="button" class="btn" disabled=${!ready}
    title=${reason ? t(`reason.${reason}`, vars) : undefined}
    onClick=${() => ctx.run('probe_head', { head: String(head + 1) })}>${busy ? t('action.sending') : t('action.probe_head', vars)}</button></div>
    ${ready ? html`<p class="note">${t('heads.probe_note', vars)}</p>` : null}`;
}

// The head card: the lane in the head now and its lanes, each with Load into T<n>; Find the lane in T<n> while the
// adapter does not know which lane is in it. A head that no lane feeds runs on the U1's own spool.
// Why a button is disabled is also a note below them, each reason once, for a touch screen shows no title.
/** @param {{ app: AppState, ctx: UiCtx, head: number }} props */
export function HeadCard({ app, ctx, head }) {
  const { t } = ctx;
  const lanes = headLanesOf(app, head);
  if (!lanes.length) {
    const own = headSetting(app.status, head, false);
    return html`<div class="card head-card-panel">
      <div class="bay-spool"><span class=${own ? 'sp' : 'sp unknown'} style=${own ? `--fil:${colorOf(own)}` : undefined}></span>
        <div><div class="t1">${u1SpoolLine(t, own?.type)}</div>
        <div class="note">${t('units.no_ace')}</div></div></div>
    </div>`;
  }
  const online = ready(app);
  const loads = lanes.map((l) => laneActions(l.slice, l.i, online).find((a) => a.id === 'load_head'));
  const cur = lanes.find((l) => l.current);
  const line = sharedHeadsView(app.status, online).find((x) => x.head === head);
  const probe = line?.probe;
  const probing =
    Boolean(line) && scoped(app, /** @type {{ unit: string | null }} */ (line).unit).has(`probe_head:${head + 1}`);
  const probeWhy = probing ? null : (probe?.reason ?? null);
  const list = lanes.map((l) => l.key).join(' · ');
  // The top line: the lane in the head; or that it holds a filament no lane is known for; or no lane known.
  const headLine = cur
    ? t('panel.head_now', { lane: cur.key, material: materialWord(t, cur.material) })
    : laneUnknownIn(app.status, head)
      ? t('panel.head_unknown', { head: headName(t, head) })
      : t('panel.head_none');
  const reasons = [
    ...new Set([
      ...loads.flatMap((a, k) => (a?.reason && !lanes[k].current ? [String(reasonKey(a.id, a.reason))] : [])),
      ...(probeWhy ? [probeWhy] : []),
    ]),
  ];
  return html`<div class="card head-card-panel">
    <div class="bay-spool">
      <span class=${cur?.colour ? 'sp' : 'sp unknown'} style=${fil(cur?.colour)}></span>
      <div>
        <div class="t1">${headLine}</div>
        <div class="note">${lanesLine(t, lanes.length, list)}</div>
      </div>
    </div>
    <ul class="rows">${lanes.map((l, k) => html`<${LaneRow} lane=${l} load=${loads[k]} h=${head} app=${app} ctx=${ctx} />`)}</ul>
    ${probe ? html`<${ProbeButton} enabled=${probe.enabled} reason=${probeWhy} busy=${probing} head=${head} app=${app} ctx=${ctx} />` : null}
    ${reasons.map((k) => html`<p class="note why">${t(`reason.${k}`, headVars(t, app.status, k, head))}</p>`)}
  </div>`;
}
