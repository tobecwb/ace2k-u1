import { esc } from './dom.js';
import { t } from '../i18n.js';
import { chartSvg, chartBox } from '../chartsvg.js';

export const WINDOWS = [1, 6, 24];
const LEGEND = [['ch', 'chart.chamber', '--line-ch'], ['hl', 'chart.heater_l', '--line-hl'], ['hr', 'chart.heater_r', '--line-hr'], ['rh', 'chart.humidity', '--line-rh'], ['tgt', 'chart.target', '--line-tgt']];

export function defaultWindow(progress, nowS) {
  if (!progress?.start) return 6;
  const hours = (nowS - progress.start) / 3600;
  return WINDOWS.find((w) => w >= hours) || 24;
}

export function chartHtml(samples, { hours, hidden, nowS, width, live = null }) {
  const t0 = nowS - hours * 3600;
  const box = chartBox(width);
  const shown = samples.filter((s) => s.t >= t0);
  const legend = LEGEND.map(([k, label, color]) => `<button data-action="series" data-key="${k}" class="${hidden.includes(k) ? 'off' : ''}"><i style="background:var(${color})${k === 'tgt' ? ';height:1px' : ''}"></i>${esc(t(label))}</button>`).join('');
  const body = shown.length > 1
    ? chartSvg(shown, { t0, t1: nowS, hidden, labels: { ago: t('chart.ago', { h: hours }), now: t('chart.now') }, width, live })
    : `<div class="note">${esc(t('chart.empty'))}</div>`;
  return `<div class="sec-head"><b>${esc(t('chart.title'))}</b><span class="rng">${WINDOWS.map((w) => `<button data-action="window" data-h="${w}" class="${w === hours ? 'on' : ''}">${esc(t(`chart.window_${w}h`))}</button>`).join('')}</span></div>
    <div class="lg">${legend}</div>
    <div class="chart-wrap" id="chart-wrap" data-t0="${t0}" data-t1="${nowS}" data-w="${box.W}" data-l="${box.L}" data-r="${box.R}">${body}<div class="tip" id="tip" hidden></div></div>
    <div class="note">${esc(t('chart.hint'))}</div>`;
}
