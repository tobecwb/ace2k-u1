import { esc } from './dom.js';
import { t } from '../i18n.js';
import { LANES, laneOf, chipOf, headHasFilament, spoolOf, colorOf } from '../model.js';

// An error chip names the error itself ("stuck", "tangled"), as the approved mock-ups do.
export function chipText(lane, chip) {
  return chip === 'error' && typeof lane?.error === 'string' && lane.error ? lane.error : t(`chip.${chip}`);
}

export function unitHtml(s, selected) {
  const bays = [];
  for (let i = 0; i < LANES; i++) {
    const lane = laneOf(s, i);
    const chip = chipOf(lane, headHasFilament(s, i));
    const spool = lane?.insert ? spoolOf(s, i) : null;
    const color = colorOf(spool);
    const ring = !lane?.insert ? '<span class="ua-spool ua-empty"></span>'
      : `<span class="ua-spool" style="${color ? `background:${color}` : ''}${color === '#111111' ? ';border-color:#333' : ''}"></span>`;
    bays.push(`<button class="ua-bay${selected === i ? ' sel' : ''}" data-bay="${i}">
      ${ring}<b>${esc(t('unit.lane', { n: i + 1 }))}</b> ${esc(spool?.type || t('lane.no_spool'))}<br>
      <span class="chip c-${chip}">${esc(chipText(lane, chip))}</span></button>`);
  }
  return `<div class="ua-box">${bays.join('')}</div>`;
}
