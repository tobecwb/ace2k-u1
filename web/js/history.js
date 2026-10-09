// The recorder's files and the live equivalents.
// The classification words are the recorder's (klippy/extras/ace2k_u1_history.py); the shared
// fixture web/tests/fixtures/event-lines.json pins both.
const PREFIXES = ['ace2k:', 'ace2k_u1:'];
const LANE_RE = /\blane (\d)\b/;
const DRYER_RE = /\b(dryer|drying|heater|fans?|flaps?)\b/;
const ERR_RE = /\b(refused|fault|stuck|tangled|motor_stalled|timeout|failed|assist_overrun|unload_incomplete|error)\b/;
const WARN_RE = /\b(runout|stopped_link|stopped_shutdown|lowered|interrupted|notices?)\b|not written|no answer/;
export const SAMPLE_GAP_S = 30;
const DUP_S = 2;

const RUNNING = ['starting', 'heating'];

const num = (v) => (typeof v === 'number' && Number.isFinite(v) ? Math.round(v * 100) / 100 : null);

export function parseJsonl(text) {
  const samples = [];
  const events = [];
  for (const line of text.split('\n')) {
    if (!line.trim()) continue;
    let rec;
    try { rec = JSON.parse(line); } catch { continue; }
    if (rec?.k === 's') samples.push(rec);
    else if (rec?.k === 'e') events.push(rec);
  }
  return { samples, events };
}

export function classify(text, error = false) {
  const low = text.toLowerCase();
  const lane = LANE_RE.exec(low);
  const src = lane ? `lane${lane[1]}` : DRYER_RE.test(low) ? 'dryer' : 'unit';
  const lvl = error || ERR_RE.test(low) ? 'err' : WARN_RE.test(low) ? 'warn' : 'info';
  return { lvl, src };
}

export function gcodeEvents(raw, t) {
  const out = [];
  for (let line of String(raw).split('\n')) {
    line = line.trim();
    const error = line.startsWith('!!');
    const body = error || line.startsWith('//') ? line.slice(2).trim() : line;
    if (PREFIXES.some((p) => body.startsWith(p))) {
      const { lvl, src } = classify(body, error);
      out.push({ t: Math.floor(t), k: 'e', lvl, src, msg: body });
    }
  }
  return out;
}

const ERR_KINDS = ['stuck', 'tangled', 'motor_stalled', 'timeout', 'assist_stall', 'assist_overrun', 'unload_incomplete'];
const WARN_KINDS = ['stopped_link', 'stopped_shutdown', 'runout', 'behind', 'snag', 'blocked', 'tail'];
const isObj = (v) => v !== null && typeof v === 'object' && !Array.isArray(v);

// filament_mm to one decimal, halves up (the recorder does the same arithmetic), or null
const mm = (v) => (typeof v === 'number' && Number.isFinite(v) ? (Math.floor(v * 10 + 0.5) / 10).toFixed(1) : null);

const text = (v) => (typeof v === 'string' && v ? v : null);

function laneSnapshot(l) {
  const lane = isObj(l) ? l : {};
  const ev = isObj(lane.last_event) ? lane.last_event : null;
  const tag = isObj(lane.tag) ? lane.tag : {};
  const rec = isObj(tag.record) ? tag.record : {};
  return {
    err: lane.mode === 'error' ? lane.error || 'error' : null,
    insert: typeof lane.insert === 'boolean' ? lane.insert : null,
    event: ev && { seq: ev.seq ?? null, kind: ev.kind ?? null, mode: ev.mode ?? null, mm: mm(ev.filament_mm) },
    tag: tag.state ?? null,
    brand: text(rec.brand),
    material: text(rec.material),
    seen: ev && { seq: ev.seq ?? null },
  };
}

// With prev (the previous snapshot) a lane whose last_event is missing this time keeps the last
// event seen, so a dropout does not replay it.
export function snapshot(status, prev = null) {
  const d = status?.dryer || {};
  const lanes = Array.isArray(status?.lanes) ? status.lanes : [];
  const snaps = lanes.map(laneSnapshot);
  if (prev) snaps.forEach((s, i) => { if (!s.seen && prev.lanes[i]) s.seen = prev.lanes[i].seen; });
  return { dryer: d.state ?? null, fault: d.fault ?? null, lanes: snaps };
}

export function transitionEvents(prev, status, t) {
  if (!prev) return [];
  const snap = snapshot(status, prev);
  const ev = (lvl, src, msg) => ({ t: Math.floor(t), k: 'e', lvl, src, msg });
  const out = [];
  // a state appearing from nothing (a status still filling in) or vanishing is not a transition
  if (snap.dryer !== prev.dryer && snap.dryer !== null && prev.dryer !== null) {
    out.push(snap.dryer === 'fault'
      ? ev('err', 'dryer', `dryer fault: ${snap.fault || '?'}`)
      : ev('info', 'dryer', `dryer ${prev.dryer} → ${snap.dryer}`));
  }
  snap.lanes.slice(0, prev.lanes.length).forEach((now, i) => {
    const was = prev.lanes[i];
    const n = i + 1;
    const src = `lane${n}`;
    if (was.insert !== null && now.insert !== null && was.insert !== now.insert) {
      out.push(ev('info', src, `lane ${n} spool ${now.insert ? 'inserted' : 'removed'}`));
    }
    const e = now.event;
    if (e && (!was.seen || was.seen.seq !== e.seq)) {
      const kind = e.kind ?? '?';
      const mode = e.mode ?? '?';
      const lvl = ERR_KINDS.includes(kind) ? 'err' : WARN_KINDS.includes(kind) ? 'warn' : 'info';
      out.push(ev(lvl, src, `lane ${n} ${kind} (${e.mm === null ? mode : `${mode}, ${e.mm} mm`})`));
    }
    if (was.err !== now.err) {
      out.push(now.err !== null
        ? ev('err', src, `lane ${n} error: ${now.err}`)
        : ev('info', src, `lane ${n} error cleared`));
    }
    if (was.tag !== null && now.tag !== was.tag) {
      if (now.tag === 'read') {
        const who = [now.brand, now.material].filter(Boolean).join(' ');
        out.push(ev('info', src, who ? `lane ${n} tag read: ${who}` : `lane ${n} tag read`));
      } else if (now.tag === 'no_tag') {
        out.push(ev('info', src, `lane ${n} no tag`));
      }
    }
  });
  return out;
}

export function liveSample(status, t) {
  const d = status?.dryer || {};
  return { t: Math.floor(t), k: 's', ch: num(status?.chamber), rh: num(status?.humidity),
    hl: num(status?.ptc_left), hr: num(status?.ptc_right), dry: d.state ?? null, tgt: RUNNING.includes(d.state) ? num(d.target) : null, rem: num(d.remaining) };
}

export function appendSample(samples, sample) {
  const last = samples[samples.length - 1];
  if (last && sample.t - last.t < SAMPLE_GAP_S) return samples;
  return [...samples, sample];
}

export function appendEvent(events, event) {
  const dup = events.some((e) => e.msg === event.msg && Math.abs(e.t - event.t) <= DUP_S);
  return dup ? events : [...events, event];
}

// Merge one day file into the held history; merging the same day twice adds nothing. An event is
// a duplicate under appendEvent's rule (same message within DUP_S s), checked through a set of
// (message, whole second) keys so a large day merges in linear time.
const eventKey = (msg, t) => `${t}\u0000${msg}`;
export function mergeDay(state, day) {
  const seen = new Set(state.samples.map((x) => x.t));
  const fresh = day.samples.filter((x) => !seen.has(x.t) && seen.add(x.t));
  const samples = fresh.length ? [...state.samples, ...fresh].sort((a, b) => a.t - b.t) : state.samples;
  const keys = new Set(state.events.map((e) => eventKey(e.msg, Math.round(e.t))));
  const added = [];
  for (const e of day.events) {
    const t = Math.round(e.t);
    let dup = false;
    for (let d = -DUP_S; d <= DUP_S && !dup; d++) dup = keys.has(eventKey(e.msg, t + d));
    if (dup) continue;
    keys.add(eventKey(e.msg, t));
    added.push(e);
  }
  const events = added.length ? [...state.events, ...added].sort((a, b) => a.t - b.t) : state.events;
  return { samples, events };
}

export function filterEvents(events, filter, limit = Infinity) {
  const keep = (e) => (filter === 'all' ? true : filter === 'errors' ? e.lvl === 'err' : e.src === filter);
  return events.filter(keep).sort((a, b) => b.t - a.t).slice(0, limit);
}

// The running cycle: its start (unix s, from the samples) and total length in minutes: the largest
// remaining time any sample of the segment recorded (the cycle's first sample is at its start), or,
// for files without it, elapsed + remaining. null when the dryer is not running (remaining null).
export function cycleProgress(samples, nowS, remainingMin) {
  if (remainingMin === null || remainingMin === undefined) return null;
  let start = null;
  let maxRem = null;
  for (let i = samples.length - 1; i >= 0; i--) {
    if (!RUNNING.includes(samples[i].dry)) break;
    start = samples[i].t;
    const r = samples[i].rem;
    if (typeof r === 'number' && (maxRem === null || r > maxRem)) maxRem = r;
  }
  if (start === null) return { start: null, total: null };
  if (maxRem !== null) return { start, total: Math.round(Math.max(maxRem, remainingMin)) };
  return { start, total: Math.round((nowS - start) / 60) + remainingMin };
}
