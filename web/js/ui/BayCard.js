import { html, useLayoutEffect, useRef } from './html.js';
import { SpoolEditor } from './SpoolEditor.js';
import { focusEditor } from './focus.js';
import { fil, headName } from '../words.js';
import {
  BUFFER_KINDS,
  REASONS,
  bufferOf,
  colorOf,
  headOf,
  laneActions,
  laneUnknownIn,
  laneOf,
  reasonKey,
  sharedWith,
  spoolIn,
  tagForgotten,
} from '../model.js';
import { chipText, encoderSpeed, eventText, ffKey, laneChip, tagText, unitId } from '../lane-state.js';
import { PAGE_WIDE_KEYS, ready, scoped } from '../state.js';
import { DASH, mm } from '../format.js';

/** @typedef {import('../types.js').AppState} AppState */
/** @typedef {import('../types.js').UiCtx} UiCtx */
/** @typedef {import('../types.js').Lane} Lane */
/** @typedef {import('../types.js').LaneAction} LaneAction */
/** @typedef {import('../types.js').Spool} Spool */
/** @typedef {import('../lane-state.js').LaneAt} LaneAt */
/** @typedef {import('../lane-state.js').SpeedMemo} SpeedMemo */
/**
 * What a lane button needs besides its action: the lane, the unit's pending keys, the action's data, and the
 * values of its words ({ head } the head's name, T0-T3).
 * @typedef {{ at: LaneAt, pending: Set<string>, data: Record<string, string>, vars: { head: string } }} ButtonCtx
 */

// The rows of the card's buttons: the moves, then Read tag and Edit spool, then the Advanced fold.
const MAIN = ['load', 'eject', 'stop', 'clear', 'load_head'];
const SMALL = ['read_tag', 'edit_spool'];
const ADVANCED = ['feed', 'rollback', 'forget_tag'];
const MOVES = ['feed', 'rollback'];
// The buffer's three positions, from the pulled end to the pushed end.
const GAUGE = ['taut', 'at_rest', 'pushed'].filter((k) => BUFFER_KINDS.includes(k));
// The reasons in the order of their notes. Stop locked by the print has its own words.
const NOTE_ORDER = [...REASONS, 'stop_in_use'];

// Eject is the card's main button; Clear error is red like Stop, because it ends the lane's error.
/** @type {Record<string, string>} */
const BTN_STYLE = { eject: ' pri', clear: ' stop' };

// The pending key that a lane's button shows as busy. A page-wide key is the action's id itself.
/** @param {string} id @param {number} i */
export const busyKey = (id, i) => {
  if (id === 'ff') return ffKey(i + 1);
  return PAGE_WIDE_KEYS.has(id) ? id : `${id}:${i + 1}`;
};

// One of the card's buttons. It runs the lane action of its id on this lane, and Edit spool opens the editor.
// A disabled button carries its reason as a title; the card's note says it too.
/** @param {{ a: LaneAction, b: ButtonCtx, ctx: UiCtx, cls?: string }} props */
function LaneButton({ a, b, ctx, cls = '' }) {
  const { t } = ctx;
  const busy = b.pending.has(busyKey(a.id, b.at.i));
  const key = a.reason ? reasonKey(a.id, a.reason) : null;
  const label = busy ? t('action.sending') : t(`action.${a.id}`, b.vars);
  /** @returns {unknown} */
  const press = () => (a.id === 'edit_spool' ? ctx.run('edit_open', b.data) : ctx.run('lane', { ...b.data, id: a.id }));
  return html`<button type="button" class=${`btn${BTN_STYLE[a.id] ?? ''}${cls}`} data-id=${a.id}
    disabled=${!a.enabled || busy} title=${key ? t(`reason.${key}`, b.vars) : undefined} onClick=${press}>${label}</button>`;
}

// The spool: its colour, material and subtype and where they came from (the tag, or set by hand), its maker and
// colour code.
/** @param {{ lane: Lane, spool: Spool | null, at: LaneAt, t: UiCtx['t'] }} props */
function SpoolLine({ lane, spool, at, t }) {
  if (!lane.insert)
    return html`<div class="bay-spool"><span class="sp empty"></span><div>
      <div class="t1">${t('lane.no_spool_in_bay')}</div>
      <div class="note">${t('lane.empty_hint', { n: at.key })}</div></div></div>`;
  const colour = colorOf(spool);
  const origin = spool?.fromTag ? t('lane.from_tag') : spool?.type ? t('lane.by_hand') : null;
  const sub = [spool?.vendor, colour?.toUpperCase()].filter(Boolean).join(' · ');
  return html`<div class="bay-spool">
    <span class=${colour ? 'sp' : 'sp unknown'} style=${fil(colour)}></span>
    <div>
      <div class="t1">${spool?.type ? [spool.type, spool.subtype].filter(Boolean).join(' ') : t('lane.spool_unknown')}
        ${origin ? html`<span class=${spool?.fromTag ? 'origin tag' : 'origin hand'}>${origin}</span>` : null}</div>
      ${sub ? html`<div class="note">${sub}</div>` : null}
    </div>
  </div>`;
}

// Two lines that merge into one, for a head that several lanes feed. Decoration only: the text says the same.
/** @returns {unknown} */
const MergeIcon = () =>
  html`<svg class="merge" viewBox="0 0 12 12" aria-hidden="true"><path d="M1 2.5h2.5c2 0 2.5 3.5 4.5 3.5h3M1 9.5h2.5c2 0 2.5-3.5 4.5-3.5"
    fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" /></svg>`;

// Where the lane goes, "a:2 → T1", with its state; and the lanes that share its head.
/** @param {{ at: LaneAt, lane: Lane, colour: string | null, t: UiCtx['t'] }} props */
function Route({ at, lane, colour, t }) {
  const head = headOf(at.slice, at.i);
  // The same state as the lane's bay in the diagram (laneChip).
  const chip = laneChip(at.slice, at.i);
  const shared = sharedWith(at.slice, at.i);
  const route =
    head === null
      ? t('panel.route_none', { lane: at.key })
      : t('panel.route', { lane: at.key, head: headName(t, head) });
  return html`<div class="route">
      <span class="fdot" style=${fil(colour)}></span>
      <b>${route}</b>
      <span class=${`state s-${chip}`}>${chipText(lane, chip)}</span>
    </div>
    ${shared.length ? html`<p class="note shared"><${MergeIcon} />${t('panel.shared_with', { lanes: shared.join(', ') })}</p>` : null}`;
}

// The buffer's three positions with the one it is in lit, the filament speed, and the feed-forward toggle.
/** @param {{ lane: Lane, speed: number | null, ff: LaneAction | undefined, b: ButtonCtx, ctx: UiCtx }} props */
function Sensors({ lane, speed, ff, b, ctx }) {
  const { t } = ctx;
  const at = bufferOf(lane);
  const busy = b.pending.has(ffKey(b.at.i + 1));
  const reason = ff?.reason ? t(`reason.${reasonKey(ff.id, ff.reason)}`, b.vars) : undefined;
  return html`<div class="sens">
    <div><div class="note">${t('lane.buffer')}</div>
      <div class="gauge">${GAUGE.map(
        (k) =>
          html`<span class=${k === at ? 'z on' : 'z'} aria-current=${k === at ? 'true' : undefined}>${t(`buffer.${k}`)}</span>`,
      )}</div></div>
    <div><div class="note">${t('panel.speed')}</div>
      <div class="big2">${speed === null ? DASH : t('panel.speed_value', { v: speed.toFixed(1) })}</div></div>
    <div><div class="note">${t('lane.feed_forward')}</div>
      <button type="button" class=${lane.ff_on ? 'tog on' : 'tog'} role="switch" aria-checked=${lane.ff_on ? 'true' : 'false'}
        aria-label=${t(lane.ff_on ? 'action.ff_off' : 'action.ff_on')} title=${reason}
        disabled=${!ff?.enabled || busy} onClick=${() => ctx.run('lane', { ...b.data, id: 'ff' })}><i></i></button></div>
  </div>`;
}

// The Advanced fold: the tag and the encoder, and the manual moves with their length and Forget tag.
/** @param {{ adv: LaneAction[], lane: Lane, app: AppState, b: ButtonCtx, ctx: UiCtx }} props */
function Advanced({ adv, lane, app, b, ctx }) {
  const { t } = ctx;
  const forgotten = tagForgotten(b.at.slice, b.at.i);
  return html`<details id="adv" class="adv" open=${Boolean(app.folds.adv)}>
    <summary class="btn">${t('lane.advanced')}</summary>
    <div class="fact"><span>${t('lane.tag')}</span><b>${tagText(lane, forgotten)}</b></div>
    <div class="fact"><span>${t('lane.encoder')}</span><b>${mm(lane.encoder_mm)}</b></div>
    ${
      adv.some((a) => MOVES.includes(a.id))
        ? html`<label class="len">${t('lane.length')}
          <input type="number" inputmode="numeric" min="1" max="2000" step="1" value=${app.moveMm}
            onInput=${(/** @type {{ currentTarget: HTMLInputElement }} */ e) => ctx.run('move_mm', {}, e.currentTarget.value)} /></label>`
        : null
    }
    <div class="acts">${adv.map((a) => html`<${LaneButton} a=${a} b=${b} ctx=${ctx} />`)}</div>
  </details>`;
}

// While the lane's shared head holds a filament that no lane is known for (laneUnknownIn), right under the moves:
// a link to the head's card, where Find the lane in T<n> is. It opens the same panel as a click on the head in the
// diagram. It is the note of head_unknown too.
/** @param {{ head: number, ctx: UiCtx }} props */
function HeadUnknownLink({ head, ctx }) {
  return html`<p class="note why"><button type="button" class="link"
    onClick=${() => ctx.run('panel', { kind: 'head', id: String(head) })}>${ctx.t('panel.head_unknown_link', { head: headName(ctx.t, head) })}</button></p>`;
}

// Why the card's buttons are disabled, each reason once. A disabled button gets no focus and shows no title on a
// touch screen, so the reasons need their own notes.
// skip is a reason whose note is drawn elsewhere (head_unknown, by HeadUnknownLink).
/** @param {{ actions: LaneAction[], vars: ButtonCtx['vars'], t: UiCtx['t'], skip: string | null }} props */
function ReasonNotes({ actions, vars, t, skip }) {
  const keys = new Set(actions.flatMap((a) => (a.reason && a.reason !== skip ? [reasonKey(a.id, a.reason)] : [])));
  return NOTE_ORDER.filter((k) => keys.has(k)).map((k) => html`<p class="note why">${t(`reason.${k}`, vars)}</p>`);
}

// The open spool editor of this lane, when the form in the page state is this lane's and the lane still offers
// Edit spool. A lane whose tag now speaks, or that emptied, drops the form (keptEdit drops it from the state too).
/** @param {AppState} app @param {LaneAt} at @param {LaneAction[]} actions */
const editOf = (app, at, actions) =>
  app.edit &&
  app.edit.lane === at.i + 1 &&
  (app.edit.unit ?? null) === at.unit &&
  actions.some((a) => a.id === 'edit_spool')
    ? app.edit
    : null;

// The bay card: the spool, the route with its state, the buffer, speed and feed-forward, the last move,
// and the lane's buttons. Edit spool shows only while no tag speaks for the spool (laneActions offers it then).
/** @param {{ app: AppState, ctx: UiCtx, at: LaneAt }} props */
export function BayCard({ app, ctx, at }) {
  const { t } = ctx;
  const online = ready(app);
  const lane = laneOf(at.slice, at.i) || {};
  const spool = spoolIn(at.slice, at.i);
  const head = headOf(at.slice, at.i);
  const actions = laneActions(at.slice, at.i, online);
  const memo = useRef(/** @type {SpeedMemo | null} */ (null));
  memo.current = encoderSpeed(memo.current, lane.encoder_mm, Date.now(), !lane.mode || lane.mode === 'idle');
  /** @type {ButtonCtx} */
  const b = {
    at,
    pending: scoped(app, at.unit),
    data: { lane: String(at.i + 1), unit: unitId(at.unit) },
    vars: { head: head === null ? '' : headName(t, head) },
  };
  const edit = editOf(app, at, actions);
  const root = useRef(/** @type {HTMLElement | null} */ (null));
  const was = useRef(edit !== null);
  useLayoutEffect(() => focusEditor(root.current, edit !== null, was), [edit !== null]);
  /** @param {string[]} ids */
  const row = (ids) => actions.filter((a) => ids.includes(a.id) && !(edit && a.id === 'edit_spool'));
  const adv = row(ADVANCED);
  const unknown = head !== null && laneUnknownIn(at.slice, head);
  return html`<div class="card bay-card" ref=${root}>
    <${SpoolLine} lane=${lane} spool=${spool} at=${at} t=${t} />
    <${Route} at=${at} lane=${lane} colour=${colorOf(spool)} t=${t} />
    <${Sensors} lane=${lane} speed=${memo.current?.speed ?? null} ff=${actions.find((a) => a.id === 'ff')} b=${b} ctx=${ctx} />
    <p class="note">${t('panel.last_move', { what: eventText(lane.last_event) })}</p>
    <div class="acts">${row(MAIN).map((a) => html`<${LaneButton} a=${a} b=${b} ctx=${ctx} />`)}</div>
    ${unknown ? html`<${HeadUnknownLink} head=${/** @type {number} */ (head)} ctx=${ctx} />` : null}
    <div class="acts sm">
      ${row(SMALL).map((a) => html`<${LaneButton} a=${a} b=${b} ctx=${ctx} />`)}
      ${adv.length ? html`<${Advanced} adv=${adv} lane=${lane} app=${app} b=${b} ctx=${ctx} />` : null}
    </div>
    ${edit ? html`<${SpoolEditor} app=${app} ctx=${ctx} at=${at} edit=${edit} />` : null}
    <${ReasonNotes} actions=${actions} vars=${b.vars} t=${t} skip=${unknown ? 'head_unknown' : null} />
  </div>`;
}
