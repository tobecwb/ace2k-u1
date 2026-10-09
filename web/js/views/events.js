import { esc } from './dom.js';
import { t } from '../i18n.js';
import { clock } from '../format.js';
import { filterEvents } from '../history.js';

export const FILTERS = ['all', 'errors', 'lane1', 'lane2', 'lane3', 'lane4', 'dryer'];
export const SOURCES = ['dryer', 'unit']; // the non-lane sources: t(`events.src.${src}`)

function filterLabel(f) {
  if (f.startsWith('lane')) return t('events.filter.lane', { n: f.slice(4) });
  return t(`events.filter.${f}`);
}

function sourceLabel(src) {
  if (src?.startsWith('lane')) return t('events.src.lane', { n: src.slice(4) });
  return t(`events.src.${SOURCES.includes(src) ? src : 'unit'}`);
}

const rowKey = (e) => `${e.t}|${e.msg}`;

// Scroll anchoring for the event list. before: { top, rows } read from the list before a redraw
// (rows: [{ k, offset }], offset = the row's top within the scrolled content); after: the same rows
// read after it. The first row that was visible keeps its place; when it is gone, the scroll
// position stays; at the very top the list stays at the top.
export function anchoredTop(before, after) {
  if (before.top <= 0) return 0;
  const anchor = before.rows.find((r) => r.offset + r.height > before.top);
  const now = anchor && after.rows.find((r) => r.k === anchor.k);
  return now ? Math.max(0, now.offset - (anchor.offset - before.top)) : before.top;
}

// filtered: the events already through filterEvents(events, filter) (newest first), when the
// caller has them; they are not filtered and sorted a second time.
export function eventsHtml(events, { filter, limit, more, filtered = null }) {
  const rows = filtered ? filtered.slice(0, limit) : filterEvents(events, filter, limit);
  const seen = new Map(); // equal t+msg rows get their occurrence number, so every key is unique
  const keyOf = (e) => { const k = rowKey(e); const n = seen.get(k) ?? 0; seen.set(k, n + 1); return `${k}|${n}`; };
  const list = rows.length
    ? rows.map((e) => `<div class="ev" data-k="${esc(keyOf(e))}"><span class="t">${esc(clock(e.t))}</span><span class="lvl-${esc(e.lvl)}">●</span><span class="w">${esc(sourceLabel(e.src))}</span><span>${esc(e.msg)}</span></div>`).join('')
    : `<div class="note">${esc(t('events.empty'))}</div>`;
  return `<div class="sec-head"><b>${esc(t('events.title'))}</b><span class="label">${esc(t('events.subtitle'))}</span></div>
    <div class="fl">${FILTERS.map((f) => `<button data-action="filter" data-f="${f}" class="${f === filter ? 'on' : ''}">${esc(filterLabel(f))}</button>`).join('')}</div>
    <div class="evlist">${list}${more ? `<button class="more" data-action="more">${esc(t('events.more'))}</button>` : ''}</div>`;
}
