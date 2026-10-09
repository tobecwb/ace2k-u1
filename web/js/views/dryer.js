import { esc } from './dom.js';
import { t } from '../i18n.js';
import { temp, pct, duration } from '../format.js';
import { FLAP_WHICH } from '../model.js';

export const FAN_SECONDS = 60;
// the dryer states the strip's chip names (t(`dryer.state.${state}`)) and the faults faultText knows
export const DRYER_STATES = ['idle', 'starting', 'heating', 'cooldown', 'fault'];
export const FAULTS = ['not_heating', 'response', 'sides', 'chamber_over', 'chamber_stale', 'ntc', 'mains', 'cutout', 'heat', 'fans', 'flaps'];

export function faultText(name) {
  return FAULTS.includes(name) ? t(`dryer.fault.${name}`) : t('dryer.fault.unknown', { name: name || '?' });
}

function summary(d) {
  if (d.state === 'heating' || d.state === 'starting') return t('dryer.summary_heating', { target: temp(d.target), left: duration(d.remaining) });
  if (d.state === 'cooldown') return t('dryer.summary_cooldown');
  if (d.state === 'fault') return faultText(d.fault);
  return t('dryer.summary_idle', { temp: temp(d.chamber), rh: pct(d.humidity) });
}

function busyBtn(pending, key, action, cls, label, online) {
  const busy = pending.has(key);
  return `<button class="btn${cls ? ` ${cls}` : ''}" data-action="${action}"${online && !busy ? '' : ' disabled'}>${esc(busy ? t('action.sending') : label)}</button>`;
}

function idlePanel(form, presets, online, pending) {
  return `<div class="label">${esc(t('dryer.preset').toUpperCase())}</div>
    ${presets.map((p, k) => `<button class="pre${form.preset === k ? ' sel' : ''}" data-action="preset" data-k="${k}">${esc(t('dryer.preset_label', p))}</button>`).join('')}
    <div style="margin:8px 0">
      <span class="num"><input id="dry-temp" type="number" inputmode="numeric" pattern="[0-9]*" min="15" max="65" value="${esc(form.temp)}"> ${esc(t('dryer.temp_unit'))}</span>
      <span class="num"><input id="dry-h" type="number" inputmode="numeric" pattern="[0-9]*" min="0" max="24" value="${esc(form.h)}"> ${esc(t('dryer.hours'))}
        <input id="dry-m" type="number" inputmode="numeric" pattern="[0-9]*" min="0" max="59" value="${esc(form.m)}"> ${esc(t('dryer.minutes'))}</span>
    </div>
    ${busyBtn(pending, 'dry:', 'dry', 'pri', t('dryer.start'), online)}
    <div class="note">${esc(t('dryer.limits'))}</div>`;
}

function runningPanel(d, progress, online, pending) {
  const total = progress?.total;
  const width = total ? Math.min(100, Math.max(0, ((total - d.remaining) / total) * 100)) : 0;
  const flaps = FLAP_WHICH.map((w) => `${t(`dryer.flap.${w}`)} ${t(d.flaps[w] === 'open' ? 'dryer.open' : 'dryer.closed')}`).join(', ');
  return `<span class="big">${esc(duration(d.remaining))}</span> ${total ? esc(t('dryer.left_of', { total: duration(total) })) : ''}
    <div class="prog"><i style="width:${width.toFixed(0)}%"></i></div>
    <div class="kv">
      <span>${esc(t('dryer.target'))}</span><b>${esc(temp(d.target))}</b>
      <span>${esc(t('dryer.chamber'))}</span><b>${esc(`${temp(d.chamber)} · ${pct(d.humidity)}`)}</b>
      <span>${esc(t('dryer.outlets'))}</span><b>${esc(t('dryer.outlets_value', { l: temp(d.outletL), r: temp(d.outletR) }))}</b>
      <span>${esc(t('dryer.fans_flaps'))}</span><b>${esc(t('dryer.fans_flaps_value', { fans: t(d.fansOn ? 'on' : 'off'), flaps }))}</b>
    </div>
    ${busyBtn(pending, 'dry_stop:', 'dry_stop', 'red', t('dryer.stop'), online)}
    <div class="note">${esc(t('dryer.after_stop'))}</div>`;
}

function faultPanel(d, online, pending) {
  return `<b>${esc(faultText(d.fault))}</b><br><span class="sub">${esc(t('dryer.fault_cooling'))}</span>
    <div class="kv"><span>${esc(t('dryer.outlets'))}</span><b>${esc(t('dryer.outlets_value', { l: temp(d.outletL), r: temp(d.outletR) }))}</b></div>
    ${busyBtn(pending, 'dry_clear:', 'dry_clear', '', t('dryer.clear'), online)}${busyBtn(pending, 'dry_log:', 'dry_log', '', t('dryer.log'), online)}`;
}

function manualFold(d, online, folds) {
  if (!d.manual) return '';
  const dis = online ? '' : ' disabled';
  const flap = (w) => `<button class="btn" data-action="flap" data-which="${w}" data-open="1"${dis}>${esc(t('dryer.flap_open', { which: t(`dryer.flap.${w}`) }))}</button><button class="btn" data-action="flap" data-which="${w}" data-open="0"${dis}>${esc(t('dryer.flap_close', { which: t(`dryer.flap.${w}`) }))}</button>`;
  return `<details id="manual" style="margin-top:6px"${folds.manual ? ' open' : ''}><summary class="label">${esc(t('dryer.manual'))}</summary>
    <button class="btn" data-action="fans_on"${dis}>${esc(t('dryer.fans_on', { s: FAN_SECONDS }))}</button><button class="btn" data-action="fans_off"${dis}>${esc(t('dryer.fans_off'))}</button><br>
    ${FLAP_WHICH.map(flap).join('<br>')}</details>`;
}

export function dryerHtml(d, { open, form, presets, progress, online, folds = {}, pending = new Set() }) {
  const bar = `<div class="bar${open ? ' open' : ''}" role="button" tabindex="0" data-action="dryer_toggle"><b>${esc(t('dryer.title'))}</b>
    <span class="chip c-${esc(d.state)}">${esc(t(`dryer.state.${d.state}`))}</span> ${esc(summary(d))}<span class="spacer"></span><span class="pill">${esc(t(open ? 'dryer.close_panel' : 'dryer.open_panel'))}</span></div>`;
  if (!open) return bar;
  let body;
  if (d.state === 'fault') body = faultPanel(d, online, pending);
  else if (d.state === 'heating' || d.state === 'starting') body = runningPanel(d, progress, online, pending);
  else if (d.state === 'cooldown') body = `<div class="note">${esc(t('dryer.cooldown_rule'))}</div>`;
  else body = idlePanel(form, presets, online, pending);
  return `${bar}<div class="panel">${body}${manualFold(d, online, folds)}</div>`;
}
