import { Connection } from './js/conn.js';
import { loadCatalog, t } from './js/i18n.js';
import { topbarView, stopAllLanes, allowedNow, gcodeFor, laneOf, dryerView, editPrefill, editAction, CONFIRM } from './js/model.js';
import { nearest } from './js/chartsvg.js';
import { parseJsonl, mergeDay, filterEvents, gcodeEvents, appendEvent, appendSample, liveSample, transitionEvents, snapshot, cycleProgress } from './js/history.js';
import { historyDays, clock, temp, pct } from './js/format.js';
import { setHtml } from './js/views/dom.js';
import { topbarHtml } from './js/views/topbar.js';
import { unitHtml } from './js/views/unit.js';
import { laneHtml, ffKey } from './js/views/lane.js';
import { dryerHtml, FAN_SECONDS } from './js/views/dryer.js';
import { chartHtml, defaultWindow } from './js/views/chart.js';
import { eventsHtml, anchoredTop } from './js/views/events.js';

const demo = new URLSearchParams(location.search).has('demo');
const app = {
  status: {}, online: false, klippy: null, selected: null, samples: [], events: [], snap: null,
  days: [], daysLoaded: 0, loaded: new Set(), gen: 0, pending: new Set(), conn: null, folds: {}, moveMm: '10',
  dryerOpen: false, presets: [], form: { preset: 1, temp: 60, h: 6, m: 0 }, edit: null,
  hours: null, hidden: readHidden(), filter: 'all', limit: 200,
};
function readHidden() {
  try { const v = JSON.parse(localStorage.getItem('ace2k.hidden') || '[]'); return Array.isArray(v) ? v : []; } catch { return []; }
}
function saveHidden() {
  try { localStorage.setItem('ace2k.hidden', JSON.stringify(app.hidden)); } catch { /* private mode */ }
}
const $ = (id) => document.getElementById(id);
const DEFAULT_KEEP_DAYS = 7;
const keepDays = () => {
  const k = app.status.ace2k_u1_history?.keep_days;
  return Number.isInteger(k) && k > 0 ? k : DEFAULT_KEEP_DAYS;
};
// An older listed day not loaded yet (or whose load failed) is what "Show more" can fetch.
const olderDays = () => !demo && app.days.slice(0, keepDays()).some((n) => !app.loaded.has(n));

// Redraw el; if one of its inputs had the focus, give the focus and the caret back to the new
// one (its value is rendered from app state, which every keystroke updates).
let restoring = false; // the focus given back after a redraw is not a user focus
// A real user focus on a number field selects its whole text, so typing replaces the value.
// Deferred: a mouseup or iOS Safari would undo a select() made inside the focus event.
document.addEventListener('focusin', (e) => {
  const el = e.target;
  if (restoring || !el || el.tagName !== 'INPUT' || el.type !== 'number') return;
  setTimeout(() => { if (document.activeElement === el) { try { el.select(); } catch { /* no selection */ } } }, 0);
});

function redrawKeepingFocus(el, html) {
  const active = document.activeElement;
  // an open colour picker or drop-down would close on a redraw: leave the element alone while it has the focus
  if (active && el.contains(active) && (active.tagName === 'SELECT' || active.type === 'color')) return;
  const id = active && active.tagName === 'INPUT' && el.contains(active) ? active.id : null;
  let caret = null;
  try { caret = id ? [active.selectionStart, active.selectionEnd] : null; } catch { /* no caret on a number input */ }
  setHtml(el, html);
  // the live element goes back into the new HTML in place of its fresh copy: a number input has no
  // caret to save and restore, so it must keep its own editing state
  const fresh = id ? el.querySelector(`#${CSS.escape(id)}`) : null;
  if (!fresh) return;
  if (fresh === active) return; // setHtml skipped the redraw: nothing detached
  fresh.replaceWith(active);
  if (document.activeElement === active) return;
  restoring = true;
  try { active.focus(); } finally { restoring = false; }
  try { if (caret && caret[0] !== null) active.setSelectionRange(caret[0], caret[1]); } catch { /* number input */ }
}

// The chart and the event list are rebuilt only when what they show changed.
const memo = { chart: { key: null, html: '' }, events: { key: null, html: '', filtered: [] } };
const sameKey = (a, b) => a !== null && a.length === b.length && a.every((v, i) => v === b[i]);

const lastFilter = new WeakMap();
// the events list's scroll state: its rows' keys and where they sit within the scrolled content
function readList(sec) {
  const list = sec.querySelector('.evlist');
  if (!list) return null;
  const rows = [...list.querySelectorAll('.ev')].map((r) => ({ k: r.dataset.k, offset: r.offsetTop, height: r.offsetHeight }));
  return { list, top: list.scrollTop, rows };
}

// The chart is drawn at its container's pixel width (one SVG unit = one CSS pixel).
let lastChartWidth = 320;
function chartWidth() {
  const el = $('chart');
  const cs = el && getComputedStyle(el);
  const w = el ? el.clientWidth - parseFloat(cs.paddingLeft || 0) - parseFloat(cs.paddingRight || 0) : 0;
  if (w > 0) lastChartWidth = Math.round(w);
  return lastChartWidth;
}
let remeasured = false;
let resizeTimer = null;
window.addEventListener('resize', () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(render, 150);
});

function render() {
  const online = app.online && app.klippy === 'ready';
  // a form for a lane that is no longer selected, or no longer offers Edit spool, is dropped
  if (app.edit && (app.selected !== app.edit.lane - 1 || !editAction(app.status, app.edit.lane - 1, online))) app.edit = null;
  const banner = $('banner');
  if (demo) { banner.hidden = false; banner.className = 'banner demo'; banner.textContent = t('conn.demo'); }
  else if (!app.online) { banner.hidden = false; banner.className = 'banner'; banner.textContent = t('conn.offline'); }
  else if (app.klippy !== 'ready') { banner.hidden = false; banner.className = 'banner'; banner.textContent = t('conn.klippy', { state: app.klippy || '…' }); }
  else banner.hidden = true;
  setHtml($('topbar'), topbarHtml(topbarView(app.status, online)));
  setHtml($('unit'), unitHtml(app.status, app.selected));
  redrawKeepingFocus($('lane'), laneHtml(app.status, app.selected, online, app.pending, app.folds, app.moveMm, app.edit));
  const nowS = Date.now() / 1000;
  const d = dryerView(app.status);
  const progress = cycleProgress(app.samples, nowS, d.remaining);
  redrawKeepingFocus($('dryer'), dryerHtml(d, { open: app.dryerOpen, form: app.form, presets: app.presets, progress, online, folds: app.folds, pending: app.pending }));
  const hours = app.hours ?? defaultWindow(progress, nowS);
  // rounded to 30 s so the chart is rebuilt at most once per sample interval
  const nowBucket = Math.floor(nowS / 30) * 30;
  const last = app.samples[app.samples.length - 1];
  const width = chartWidth();
  const heating = d.state === 'starting' || d.state === 'heating';
  const live = heating && typeof d.target === 'number' ? { from: progress?.start ?? null, value: d.target } : null;
  const chartKey = [app.samples, app.samples.length, last?.t, hours, app.hidden.join(','), nowBucket, width, live?.from, live?.value];
  if (!sameKey(memo.chart.key, chartKey)) {
    memo.chart = { key: chartKey, html: chartHtml(app.samples, { hours, hidden: app.hidden, nowS: nowBucket, width, live }) };
  }
  setHtml($('chart'), memo.chart.html);
  // the section was empty (hidden) when measured the first time: draw once more at its real width
  if (!remeasured && chartWidth() !== width) { remeasured = true; try { render(); } finally { remeasured = false; } return; }
  const more = olderDays();
  const eventsKey = [app.events, app.events.length, app.filter, app.limit, more];
  if (!sameKey(memo.events.key, eventsKey)) {
    const filtered = filterEvents(app.events, app.filter);
    memo.events = { key: eventsKey, filtered,
      html: eventsHtml(app.events, { filter: app.filter, limit: app.limit, filtered, more: filtered.length > app.limit || more }) };
  }
  const evEl = $('events');
  const before = readList(evEl);
  setHtml(evEl, memo.events.html);
  const after = readList(evEl);
  // a new filter starts at the top; anything else (new events, older days, a larger limit) keeps
  // the row the user is reading where it is
  if (after && lastFilter.get(evEl) === app.filter) {
    if (before) after.list.scrollTop = anchoredTop(before, after);
  } else if (after) after.list.scrollTop = 0;
  lastFilter.set(evEl, app.filter);
}

export function toast(msg) {
  const el = $('toast');
  el.textContent = msg;
  el.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { el.hidden = true; }, 6000);
}

// true when the command was handed over (or, in the demo, shown); false when refused or failed
// quiet(msg) true: a refusal the caller handles itself, shown nowhere
export async function send(id, n, args, key = `${id}:${n ?? ''}`, quiet = null) {
  let script;
  try {
    script = gcodeFor(id, n, args);
  } catch (e) {
    toast(t('toast.refused', { msg: e.message }));
    return false;
  }
  if (demo) { toast(`${t('conn.demo')}: ${script}`); return true; }
  app.pending.add(key);
  render();
  try {
    await app.conn.gcode(script);
    return true;
  } catch (e) {
    if (quiet?.(e.message)) return false;
    toast(t('toast.refused', { msg: e.message }));
    app.events = appendEvent(app.events, { t: Date.now() / 1000, k: 'e', lvl: 'err', src: n ? `lane${n}` : 'unit', msg: e.message });
    return false;
  } finally {
    app.pending.delete(key);
    render();
  }
}

// Read tag = forget: the lane goes back to pending and the unit reads its tag the next time the spool turns.
async function readTag(n) {
  if (await send('read_tag', n) && !demo) toast(t('toast.tag_rearmed', { n }));
}

function onStatus(status) {
  app.status = status;
  const now = Date.now() / 1000;
  if (status.ace2k) {
    app.samples = appendSample(app.samples, liveSample(status.ace2k, now));
    for (const ev of transitionEvents(app.snap, status.ace2k, now)) app.events = appendEvent(app.events, ev);
    app.snap = snapshot(status.ace2k, app.snap);
  }
  render();
}

// The recorder's day files as the printer names them (its own dates, not the browser's): read
// on every (re)connect. A missing directory is no history yet.
async function listDays() {
  const gen = app.gen;
  let files = [];
  try {
    const res = await fetch('/server/files/directory?path=logs/ace2k');
    if (res.ok) files = (await res.json())?.result?.files;
  } catch { /* no listing: no history */ }
  if (gen !== app.gen) return;
  app.days = historyDays(files, Infinity); // cut to keep_days where used: the status may come later
  loadDays(2);
}

async function loadDays(count) {
  const gen = app.gen;
  // the count newest listed days; one already loaded or in flight is never fetched again
  const names = app.days.slice(0, Math.min(count, keepDays())).filter((n) => !app.loaded.has(n));
  names.forEach((n) => app.loaded.add(n));
  app.daysLoaded = Math.max(app.daysLoaded, count);
  for (const name of names) {
    let day = null;
    try {
      const res = await fetch(`/server/files/logs/ace2k/${name}`);
      if (res.ok) day = parseJsonl(await res.text());
      else if (res.status !== 404) throw new Error(`HTTP ${res.status}`);
      // 404: the day was pruned meanwhile; an empty day, kept as loaded
    } catch {
      // a failed fetch is retried by a later call ("Show more", the next reconnect)
      if (gen === app.gen) app.loaded.delete(name);
      continue;
    }
    if (gen !== app.gen) return; // a reconnect reset the history meanwhile
    if (day) {
      ({ samples: app.samples, events: app.events } = mergeDay(app, day));
      render();
    }
  }
  if (gen === app.gen) render();
}

async function startDemo() {
  const [status, history] = await Promise.all([
    fetch('dev/demo-status.json').then((r) => r.json()),
    fetch('dev/demo-history.jsonl').then((r) => r.text()),
  ]);
  const day = parseJsonl(history);
  // the committed file ages: shift every record so the newest one is "now"
  const newest = Math.max(...day.samples.map((s) => s.t), ...day.events.map((e) => e.t));
  const shift = Math.floor(Date.now() / 1000) - newest;
  app.samples = day.samples.map((s) => ({ ...s, t: s.t + shift }));
  app.events = day.events.map((e) => ({ ...e, t: e.t + shift }));
  app.online = true;
  app.klippy = 'ready';
  app.status = status;
  render();
}

document.addEventListener('click', (ev) => {
  const bay = ev.target.closest('[data-bay]');
  if (bay) {
    const i = Number(bay.dataset.bay);
    app.selected = app.selected === i ? null : i;
    render();
    return;
  }
  const btn = ev.target.closest('[data-action]');
  if (!btn || btn.disabled) return;
  const id = btn.dataset.action;
  if (id === 'edit_open') { app.edit = editPrefill(app.status, Number(btn.dataset.lane) - 1); render(); return; }
  if (id === 'edit_cancel') { app.edit = null; render(); return; }
  if (id === 'edit_save') {
    const n = Number(btn.dataset.lane);
    const e = app.edit;
    if (!e || e.lane !== n || app.pending.has(`edit_spool:${n}`)) return;
    const ea = editAction(app.status, n - 1, app.online && app.klippy === 'ready');
    if (!ea?.enabled) { toast(t('toast.refused', { msg: t(`reason.${ea?.reason || 'offline'}`) })); return; }
    send('edit_spool', n, { type: e.type, vendor: e.vendor, rgb: String(e.color).replace('#', '') }).then((ok) => {
      if (ok && app.edit === e) { app.edit = null; render(); }
    });
    return;
  }
  if (id === 'dryer_toggle') { app.dryerOpen = !app.dryerOpen; render(); return; }
  if (id === 'preset') {
    const k = Number(btn.dataset.k);
    const p = app.presets[k];
    app.form = { preset: k, temp: p.temp, h: p.hours, m: 0 };
    document.activeElement?.blur();
    render();
    return;
  }
  if (id === 'dry') {
    if (app.pending.has('dry:')) return;
    // the raw strings stay in app.form: an emptied input is refused here, never read as 0
    const raw = [app.form.temp, app.form.h, app.form.m].map((v) => String(v ?? '').trim());
    const [temp, h, m] = raw.map((v) => (v === '' ? NaN : Number(v)));
    const minutes = h * 60 + m;
    if (![temp, h, m].every(Number.isInteger) || !(temp >= 15 && temp <= 65 && minutes >= 1 && minutes <= 1440)) {
      toast(t('toast.refused', { msg: t('dryer.limits') }));
      return;
    }
    send('dry', null, { temp, minutes });
    return;
  }
  if (['dry_stop', 'dry_clear', 'dry_log', 'fans_off'].includes(id)) { send(id); return; }
  if (id === 'fans_on') { send('fans_on', null, { seconds: FAN_SECONDS }); return; }
  if (id === 'flap') { send('flap', null, { which: btn.dataset.which, open: btn.dataset.open === '1' }); return; }
  if (id === 'stop_all') {
    if (!(app.online && app.klippy === 'ready')) { toast(t('toast.refused', { msg: t('reason.offline') })); return; }
    // during a print only the lanes not in use are stopped (a followed lane must keep its follow)
    const asked = stopAllLanes(app.status);
    if (asked && !asked.length) { toast(t('top.stop_all_print')); return; }
    if (!confirm(t(asked ? 'top.confirm_stop_all_print' : 'top.confirm_stop_all'))) return;
    // the status moved while the confirmation was open: choose the lanes now, never fall back to a plain stop in a print
    const lanes = stopAllLanes(app.status);
    if (lanes && !lanes.length) { toast(t('top.stop_all_print')); return; }
    send('stop_all', null, lanes ? { lanes } : {});
    return;
  }
  if (id === 'lane') {
    const n = Number(btn.dataset.lane);
    let action = btn.dataset.id;
    const now = allowedNow(app.status, n - 1, action, app.online && app.klippy === 'ready');
    if (!now.ok) { toast(t('toast.refused', { msg: t(`reason.${now.reason}`) })); return; }
    if (action === 'read_tag') { readTag(n); return; }
    const ff = action === 'ff';
    if (ff) {
      if (app.pending.has(ffKey(n))) return;
      action = laneOf(app.status, n - 1)?.ff_on ? 'ff_off' : 'ff_on';
    }
    if (CONFIRM.includes(action) && !confirm(t(`action.confirm_${action}`, { n }))) return;
    // an empty or invalid length is passed on as NaN: gcodeFor refuses it and the toast says so
    // outside 1-500 mm is refused the same way (NaN -> gcodeFor throws -> toast)
    const len = app.moveMm === '' ? NaN : Number(app.moveMm);
    const args = ['feed', 'rollback'].includes(action) ? { length: len >= 1 && len <= 500 ? len : NaN } : {};
    send(action, n, args, ff ? ffKey(n) : undefined);
    return;
  }
  if (id === 'window') { app.hours = Number(btn.dataset.h); render(); return; }
  if (id === 'series') {
    const k = btn.dataset.key;
    app.hidden = app.hidden.includes(k) ? app.hidden.filter((x) => x !== k) : [...app.hidden, k];
    saveHidden();
    render();
    return;
  }
  if (id === 'filter') { app.filter = btn.dataset.f; app.limit = 200; render(); return; }
  if (id === 'more') {
    // show the rows the limit hides; when there are none, fetch the next older day
    if (memo.events.filtered.length > app.limit) app.limit += 200;
    else if (olderDays()) loadDays(app.daysLoaded + 1);
    render();
  }
});

// The dryer strip is a div acting as a button: Enter and Space open it too.
document.addEventListener('keydown', (e) => {
  const bar = e.target.closest?.('[data-action="dryer_toggle"]');
  if (!bar || (e.key !== 'Enter' && e.key !== ' ')) return;
  e.preventDefault();
  app.dryerOpen = !app.dryerOpen;
  render();
  $('dryer').querySelector('[data-action="dryer_toggle"]')?.focus();
});

function showTip(ev) {
  const wrap = ev.target.closest?.('#chart-wrap');
  const tip = $('tip');
  if (!wrap || !tip) return;
  const svg = wrap.querySelector('svg');
  if (!svg) return;
  const box = svg.getBoundingClientRect();
  const W = Number(wrap.dataset.w);
  const L = Number(wrap.dataset.l);
  const R = Number(wrap.dataset.r);
  const fx = ((ev.clientX - box.left) / box.width) * W;
  const t0 = Number(wrap.dataset.t0);
  const t1 = Number(wrap.dataset.t1);
  const tAt = t0 + ((fx - L) / (R - L)) * (t1 - t0);
  const s = nearest(app.samples.filter((x) => x.t >= t0), tAt);
  if (!s) { tip.hidden = true; return; }
  tip.hidden = false;
  tip.textContent = `${clock(s.t)} · ${t('chart.chamber')} ${temp(s.ch)} · ${t('chart.heater_l')} ${temp(s.hl)} · ${t('chart.heater_r')} ${temp(s.hr)} · ${t('chart.humidity')} ${pct(s.rh)}${typeof s.tgt === 'number' ? ` · ${t('chart.target')} ${temp(s.tgt)}` : ''}`;
  tip.style.left = `${Math.max(0, Math.min(ev.clientX - box.left + 8, box.width - tip.offsetWidth))}px`;
  tip.style.top = `${ev.clientY - box.top + 8}px`;
}
document.addEventListener('pointermove', showTip);
document.addEventListener('pointerdown', (ev) => {
  // a tap outside the chart hides the tooltip a tap on it left shown
  if (!ev.target.closest?.('#chart-wrap')) { const tip = $('tip'); if (tip) tip.hidden = true; return; }
  showTip(ev);
});
// hide only when the pointer leaves the chart area itself (pointerout bubbles; check the target moved outside)
document.addEventListener('pointerout', (ev) => {
  const wrap = ev.target.closest?.('#chart-wrap');
  if (ev.pointerType === 'mouse' && wrap && !wrap.contains(ev.relatedTarget)) { const tip = $('tip'); if (tip) tip.hidden = true; }
});

document.addEventListener('input', (e) => {
  if (e.target.id === 'move-mm') app.moveMm = e.target.value;
  if (app.edit && ['edit-type', 'edit-vendor', 'edit-color'].includes(e.target.id)) {
    app.edit = { ...app.edit, [e.target.id.slice(5)]: e.target.value };
  }
  if (['dry-temp', 'dry-h', 'dry-m'].includes(e.target.id)) {
    app.form = { ...app.form, preset: null, temp: $('dry-temp').value, h: $('dry-h').value, m: $('dry-m').value };
  }
});

// A drop-down or colour picker that kept the focus after a choice would freeze its panel's redraws.
document.addEventListener('change', (e) => {
  if (e.target.id === 'edit-type' || e.target.id === 'edit-color') e.target.blur();
});

// Open <details> folds survive redraws: views render <details id="…"${folds[id] ? ' open' : ''}>.
document.addEventListener('toggle', (e) => { if (e.target.id) app.folds[e.target.id] = e.target.open; }, true);

async function main() {
  try { await loadCatalog('en'); } catch { /* t() falls back to the keys */ }
  document.title = t('app.title');
  try { app.presets = await fetch('presets.json').then((r) => r.json()); } catch { app.presets = []; }
  if (demo) { await startDemo(); return; }
  render();
  app.conn = new Connection({
    url: `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/websocket`,
    onStatus,
    onState: (st) => {
      app.online = st.online;
      app.klippy = st.online ? st.klippy : null;
      if (st.online && st.klippy === 'ready') {
        // (re)connected: the files are the history; live points resume from here
        app.gen += 1;
        app.days = [];
        app.daysLoaded = 0;
        app.loaded = new Set();
        app.samples = app.status.ace2k ? [liveSample(app.status.ace2k, Date.now() / 1000)] : [];
        app.events = [];
        app.snap = null;
        listDays();
      }
      render();
    },
    onGcode: (line) => {
      for (const ev of gcodeEvents(line, Date.now() / 1000)) app.events = appendEvent(app.events, ev);
      render();
    },
  });
  app.conn.start();
}

main();
export { app, render, loadDays };
