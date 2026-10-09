import { esc } from './dom.js';
import { t } from '../i18n.js';
import { mm } from '../format.js';
import { FILAMENT_TYPES, laneOf, chipOf, headHasFilament, spoolOf, colorOf, bufferOf, laneActions } from '../model.js';
import { chipText } from './unit.js';

// the Advanced fold: the manual moves and the feed-forward toggle
const ADVANCED = ['feed', 'rollback', 'ff'];
const MOVES = ['feed', 'rollback'];

function label(a, lane) {
  if (a.id === 'ff') return t(lane.ff_on ? 'action.ff_off' : 'action.ff_on');
  return t(`action.${a.id}`);
}

// The lane's tag state in words: a pending tag is read by the unit by itself whenever the spool turns.
export function tagText(lane) {
  const tag = lane?.tag || {};
  switch (tag.state) {
    case 'pending': return t('tag.pending');
    case 'searching': case 'reading': return t('tag.reading');
    case 'read': {
      const who = [tag.record?.brand, tag.record?.material].filter(Boolean).join(' ');
      return t(who ? 'tag.read_who' : 'tag.read', { who });
    }
    case 'no_tag': return t('tag.no_tag');
    default: return t('lane.no_spool');
  }
}

// The feed-forward toggle's request is pending under one key whichever way it switches, so a
// status flip while it is in flight keeps the button busy.
export const ffKey = (n) => `ff:${n}`;

// Stop locked by the print has its own words: pausing the print is what lets the lane stop.
const reasonKey = (a) => (a.id === 'stop' && a.reason === 'in_use' ? 'reason.stop_in_use' : `reason.${a.reason}`);

function button(a, i, lane, pending) {
  const edit = a.id === 'edit_spool';
  const busy = pending.has(a.id === 'ff' ? ffKey(i + 1) : `${a.id}:${i + 1}`);
  const title = a.reason ? ` title="${esc(t(reasonKey(a)))}"` : '';
  return `<button class="btn" data-action="${edit ? 'edit_open' : 'lane'}" data-id="${a.id}" data-lane="${i + 1}"${a.enabled && !busy ? '' : ' disabled'}${title}>${esc(busy ? t('action.sending') : label(a, lane))}</button>`;
}

function editForm(edit, i, ea, pending) {
  const busy = pending.has(`edit_spool:${i + 1}`);
  return `<div class="edit">
    <label for="edit-type">${esc(t('lane.edit_type'))}</label>
    <select id="edit-type">${FILAMENT_TYPES.map((ty) => `<option${ty === edit.type ? ' selected' : ''}>${esc(ty)}</option>`).join('')}</select>
    <label for="edit-vendor">${esc(t('lane.edit_vendor'))}</label>
    <input type="text" id="edit-vendor" autocomplete="off" autocapitalize="off" spellcheck="false" value="${esc(edit.vendor)}">
    <label for="edit-color">${esc(t('lane.edit_colour'))}</label>
    <input type="color" id="edit-color" value="${esc(edit.color)}">
    <div class="edit-btns"><button class="btn pri" data-action="edit_save" data-lane="${i + 1}"${ea.enabled && !busy ? '' : ' disabled'}>${esc(busy ? t('action.sending') : t('action.save'))}</button><button class="btn" data-action="edit_cancel">${esc(t('action.cancel'))}</button></div>
    <div class="note">${esc(t('lane.edit_note'))}</div>${ea.reason ? `<div class="note">${esc(t(`reason.${ea.reason}`))}</div>` : ''}
  </div>`;
}

export function laneHtml(s, i, online, pending, folds = {}, moveMm = '10', edit = null) {
  if (i === null || i === undefined) return '';
  const lane = laneOf(s, i) || {};
  const chip = chipOf(lane, headHasFilament(s, i));
  const spool = lane.insert ? spoolOf(s, i) : null;
  const color = colorOf(spool) || 'var(--chip-empty-bg)';
  const spoolText = spool
    ? [spool.vendor, spool.type, spool.subtype].filter(Boolean).join(' · ') + (spool.fromTag ? ` ${t('lane.from_tag')}` : '')
    : t('lane.no_spool');
  const ev = lane.last_event;
  const evText = ev ? t('lane.event_text', { mode: ev.mode, kind: ev.kind, mm: mm(ev.filament_mm) }) : t('lane.no_spool');
  const actions = laneActions(s, i, online);
  const ea = actions.find((a) => a.id === 'edit_spool');
  const editing = Boolean(edit && edit.lane === i + 1 && ea);
  const inUse = (a) => a.reason === 'in_use';
  const reason = (actions.some((a) => inUse(a) && a.id !== 'stop') ? `<div class="note">${esc(t('reason.in_use'))}</div>` : '')
    + (actions.some((a) => inUse(a) && a.id === 'stop') ? `<div class="note">${esc(t('reason.stop_in_use'))}</div>` : '');
  const adv = actions.filter((a) => ADVANCED.includes(a.id));
  const advHtml = adv.length ? `<details id="adv"${folds.adv ? ' open' : ''}><summary>${esc(t('lane.advanced'))}</summary>
      ${adv.some((a) => MOVES.includes(a.id)) ? `<label>${esc(t('lane.length'))} <input type="number" id="move-mm" inputmode="numeric" pattern="[0-9]*" min="1" max="500" step="1" value="${esc(String(moveMm))}"></label><br>` : ''}
      ${adv.map((a) => button(a, i, lane, pending)).join('')}
    </details>` : '';
  return `<div class="ub-card" style="border-color:${color}">
    <b>${esc(t('lane.title', { n: i + 1 }))}</b> ${esc(t('lane.to_head', { n: i + 1 }))} <span class="chip c-${chip}">${esc(chipText(lane, chip))}</span>
    <div class="kv">
      <span>${esc(t('lane.spool'))}</span><b>${esc(spoolText)}</b>
      <span>${esc(t('lane.encoder'))}</span><b>${esc(mm(lane.encoder_mm))}</b>
      <span>${esc(t('lane.tag'))}</span><b>${esc(tagText(lane))}</b>
      <span>${esc(t('lane.buffer'))}</span><b>${esc(t(`buffer.${bufferOf(lane)}`))}</b>
      <span>${esc(t('lane.last_event'))}</span><b>${esc(evText)}</b>
      <span>${esc(t('lane.feed_forward'))}</span><b>${esc(t(lane.ff_on ? 'on' : 'off'))}</b>
    </div>
    ${actions.filter((a) => !ADVANCED.includes(a.id) && !(editing && a.id === 'edit_spool')).map((a) => button(a, i, lane, pending)).join('')}
    ${editing ? editForm(edit, i, ea, pending) : ''}${reason}${advHtml}
  </div>`;
}
