import { esc } from './dom.js';
import { t } from '../i18n.js';

export function topbarHtml(v) {
  const none = Array.isArray(v.stopLanes) && !v.stopLanes.length; // a print runs and every lane is in use
  const link = v.link ? `<span><i class="dot ok"></i>${esc(t('top.link_ok'))}</span>` : `<span><i class="dot bad"></i>${esc(t('top.link_down'))}</span>`;
  const health = v.health
    ? `<span><i class="dot ok"></i>${esc(t('top.health_ok'))}</span>`
    : `<span><i class="dot bad"></i>${esc(t('top.health_bad', { what: v.failing || '?' }))}</span>`;
  return `<b>${esc(t('app.title'))}</b>${v.version ? `<span class="label">${esc(t('top.version', { v: v.version }))}</span>` : ''}
    ${link}${health}${v.history ? `<span class="lvl-err">${esc(t('top.history_error', { e: v.history }))}</span>` : ''}<span class="spacer"></span>
    <button class="btn red" data-action="stop_all" ${v.online && !none ? '' : 'disabled'}${none ? ` title="${esc(t('top.stop_all_print'))}"` : ''}>${esc(t('top.stop_all'))}</button>${none ? `<span class="label">${esc(t('top.stop_all_print'))}</span>` : ''}`;
}
