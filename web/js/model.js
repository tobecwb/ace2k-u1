// Pure: the merged Moonraker status → what the page shows and may do.
export const LANES = 4;
export const CHIP_KINDS = ['following', 'loaded', 'in_bay', 'moving', 'empty', 'error'];
export const BUFFER_KINDS = ['at_rest', 'pushed', 'taut'];
// No Follow off: the follow switched off on a loaded head leaves the lane idle against a pulling
// extruder, which grinds the filament (the console keeps ACE_ASSIST LANE=n OFF=1).
export const ACTION_IDS = ['load', 'eject', 'stop', 'clear', 'read_tag', 'ff', 'feed', 'rollback', 'edit_spool'];
const MOVING = ['feeding', 'rolling_back', 'unloading', 'loading', 'assisting', 'assisting_back'];
const PRINTING = ['printing', 'paused'];
// Stop is locked too: ACE_STOP on a following lane is the follow stopped by hand and left off, so the
// extruder would pull against a stopped lane mid-print and grind the filament.
const LOCKED = ['load', 'eject', 'stop', 'ff', 'feed', 'rollback', 'edit_spool'];
// The U1 filament table's types: what Edit spool may set.
export const FILAMENT_TYPES = ['ABS', 'ASA', 'PA', 'PA-CF', 'PA-GF', 'PA6-CF', 'PA6-GF', 'PC', 'PC-ABS', 'PEBA', 'PETG', 'PETG-CF', 'PLA', 'PLA-CF', 'PVA', 'TPU'];
export const VENDOR_RE = /^[A-Za-z0-9_+-]{1,24}$/;
export const CONFIRM = ['eject']; // app.js asks t(`action.confirm_${id}`) first
export const REASONS = ['offline', 'in_use']; // why an action is disabled: t(`reason.${r}`)
export const MOVE_SPEED = 20; // mm/s for the Advanced feed / rollback

const lanesOf = (s) => s?.ace2k?.lanes || [];
export const laneOf = (s, i) => lanesOf(s)[i];
export const headHasFilament = (s, i) => s?.[`filament_motion_sensor e${i}_filament`]?.filament_detected === true;
export const printing = (s) => PRINTING.includes(s?.print_stats?.state);

export function laneInUse(s, i) {
  const lane = laneOf(s, i);
  return printing(s) && ((lane?.mode && lane.mode !== 'idle') || headHasFilament(s, i));
}

export function chipOf(lane, headDetected) {
  if (!lane) return 'empty';
  if (lane.mode === 'error') return 'error';
  if (lane.mode === 'following') return 'following';
  if (MOVING.includes(lane.mode)) return 'moving';
  if (!lane.insert) return 'empty';
  return headDetected ? 'loaded' : 'in_bay';
}

const real = (v) => (v && v !== 'NONE' ? v : null);

export function spoolOf(s, i) {
  const ptc = s?.print_task_config;
  const lane = laneOf(s, i);
  const fromTag = lane?.tag?.state === 'read';
  if (ptc?.filament_exist?.[i]) {
    return { vendor: real(ptc.filament_vendor?.[i]), type: real(ptc.filament_type?.[i]),
      subtype: real(ptc.filament_sub_type?.[i]), rgba: real(ptc.filament_color_rgba?.[i]), fromTag };
  }
  const rec = fromTag ? lane.tag.record : null;
  if (rec) {
    return { vendor: rec.brand || null, type: rec.material || null, subtype: null, rgba: rec.color_rgba || null, fromTag: true };
  }
  return null;
}

export function colorOf(spool) {
  const rgba = spool?.rgba;
  return rgba && /^[0-9A-Fa-f]{8}$/.test(rgba) ? `#${rgba.slice(0, 6).toLowerCase()}` : null;
}

// The Edit spool action as laneActions has it (null when it is not offered), for the form and its Save guard.
export const editAction = (s, i, online) => laneActions(s, i, online).find((a) => a.id === 'edit_spool') || null;

// What the Edit spool form starts with: the head's current setting where it has one.
export function editPrefill(s, i) {
  const sp = spoolOf(s, i);
  return {
    lane: i + 1,
    type: FILAMENT_TYPES.includes(sp?.type) ? sp.type : 'PLA',
    vendor: sp?.vendor && VENDOR_RE.test(sp.vendor) ? sp.vendor : 'Generic',
    color: colorOf(sp) || '#ffffff',
  };
}

// The lanes Stop all may stop: during a print only those not in use (1-based, in order), else null
// (outside a print it is the plain ACE_STOP).
export function stopAllLanes(s) {
  if (!printing(s)) return null;
  if (!lanesOf(s).length) return []; // no lane telemetry: nothing is known to be idle
  const out = [];
  for (let i = 0; i < LANES; i++) if (!laneInUse(s, i)) out.push(i + 1);
  return out;
}

export function bufferOf(lane) {
  if (lane?.rest) return 'at_rest';
  if (lane?.pushed) return 'pushed';
  return 'taut';
}

export function laneActions(s, i, online) {
  const lane = laneOf(s, i) || {};
  const idle = !lane.mode || lane.mode === 'idle';
  const visible = [];
  if (lane.insert && idle) visible.push('load');
  if (lane.insert) visible.push('eject');
  if (!idle && lane.mode !== 'error') visible.push('stop');
  if (lane.mode === 'error') visible.push('clear');
  // Read tag = forget: the lane goes back to pending and the unit reads the tag on its next move
  if (lane.insert && ['read', 'no_tag'].includes(lane.tag?.state)) visible.push('read_tag');
  visible.push('ff');
  if (lane.insert && idle) visible.push('feed', 'rollback');
  if (lane.insert && lane.tag?.state !== 'read') visible.push('edit_spool');
  const inUse = laneInUse(s, i);
  return visible.map((id) => {
    let reason = null;
    if (!online) reason = 'offline';
    // Eject works whatever the head holds: ACE_EJECT unloads a loaded head on the U1 first
    else if (inUse && LOCKED.includes(id)) reason = 'in_use';
    return { id, enabled: reason === null, reason, confirm: CONFIRM.includes(id) };
  });
}

// The click-time check: the action as the current status offers it, never the DOM's disabled state.
// reason is the catalogue key's suffix (stop locked by a print has its own words).
export function allowedNow(s, i, id, online) {
  const a = laneActions(s, i, online).find((x) => x.id === id);
  if (a?.enabled) return { ok: true, reason: null };
  const reason = !a ? (online ? 'unavailable' : 'offline') : a.reason;
  return { ok: false, reason: id === 'stop' && reason === 'in_use' ? 'stop_in_use' : reason };
}

const LANE_ACTIONS = ['load', 'eject', 'stop', 'clear', 'read_tag', 'ff_on', 'ff_off', 'feed', 'rollback', 'edit_spool'];
export const FLAP_WHICH = ['bottom', 'rear'];
export const MOVE_MAX_MM = 500; // the Advanced feed / rollback length limit

// Refuse anything that is not a plain positive number: G-code is built from these values.
function posNum(v, name) {
  if (!Number.isFinite(v) || v <= 0) throw new Error(`${name} must be a positive number, got ${v}`);
  return v;
}

function moveLength(v) {
  if (posNum(v, 'length') > MOVE_MAX_MM) throw new Error(`length must be at most ${MOVE_MAX_MM} mm, got ${v}`);
  return v;
}

export function gcodeFor(id, n, args = {}) {
  if (LANE_ACTIONS.includes(id) && !(Number.isInteger(n) && n >= 1 && n <= LANES)) {
    throw new Error(`lane must be an integer 1-${LANES}, got ${n}`);
  }
  switch (id) {
    case 'load': return `ACE_LOAD LANE=${n} WAIT=0`;
    case 'eject': return `ACE_EJECT LANE=${n} WAIT=0`;
    case 'stop': return `ACE_STOP LANE=${n}`;
    case 'stop_all': {
      if (args.lanes === undefined || args.lanes === null) return 'ACE_STOP';
      const lanes = args.lanes;
      if (!Array.isArray(lanes) || !lanes.length || !lanes.every((l) => Number.isInteger(l) && l >= 1 && l <= LANES)) {
        throw new Error(`stop_all lanes must be a non-empty list of 1-${LANES}`);
      }
      return lanes.map((l) => `ACE_STOP LANE=${l}`).join('\n');
    }
    case 'clear': return `ACE_CLEAR LANE=${n}`;
    case 'read_tag': return `ACE_RFID_FORGET LANE=${n}`;
    case 'ff_on': return `ACE_FEED_FORWARD LANE=${n} ON=1`;
    case 'ff_off': return `ACE_FEED_FORWARD LANE=${n} OFF=1`;
    case 'feed': return `ACE_FEED LANE=${n} LENGTH=${moveLength(args.length)} SPEED=${MOVE_SPEED} WAIT=0`;
    case 'rollback': return `ACE_ROLLBACK LANE=${n} LENGTH=${moveLength(args.length)} SPEED=${MOVE_SPEED} WAIT=0`;
    case 'edit_spool': {
      if (!FILAMENT_TYPES.includes(args.type)) throw new Error(`type must be one of the filament table's, got ${args.type}`);
      if (typeof args.vendor !== 'string' || !VENDOR_RE.test(args.vendor)) throw new Error('vendor: 1-24 characters of A-Z a-z 0-9 _ + -');
      if (typeof args.rgb !== 'string' || !/^[0-9A-Fa-f]{6}$/.test(args.rgb)) throw new Error(`colour must be 6 hex digits, got ${args.rgb}`);
      return `SET_PRINT_FILAMENT_CONFIG CONFIG_EXTRUDER=${n - 1} VENDOR=${args.vendor} FILAMENT_TYPE=${args.type} FILAMENT_SUBTYPE='' FILAMENT_COLOR_RGBA=${args.rgb.toUpperCase()}FF FORCE=1`;
    }
    case 'dry': return `ACE_DRY TEMP=${posNum(args.temp, 'temp')} DURATION=${posNum(args.minutes, 'minutes')}`;
    case 'dry_stop': return 'ACE_DRY_STOP';
    case 'dry_clear': return 'ACE_DRY_CLEAR';
    case 'dry_log': return 'ACE_DRYER_LOG';
    case 'fans_on': return `ACE_FAN ON=1 SECONDS=${posNum(args.seconds, 'seconds')}`;
    case 'fans_off': return 'ACE_FAN ON=0';
    case 'flap': {
      if (!FLAP_WHICH.includes(args.which)) throw new Error(`which must be bottom or rear, got ${args.which}`);
      return `ACE_FLAP WHICH=${args.which} OPEN=${args.open ? 1 : 0}`;
    }
    default: throw new Error(`no G-code for ${id}`);
  }
}

export function dryerView(s) {
  const a = s?.ace2k || {};
  const d = a.dryer || {};
  return {
    state: d.state || 'idle',
    target: d.target ?? null,
    remaining: d.remaining ?? null,
    fault: d.fault ?? null,
    fansOn: Boolean(d.fans?.left || d.fans?.right),
    flaps: d.flaps || {},
    outletL: a.ptc_left ?? null,
    outletR: a.ptc_right ?? null,
    chamber: a.chamber ?? null,
    humidity: a.humidity ?? null,
    manual: (d.state || 'idle') === 'idle',
  };
}

export function topbarView(s, online) {
  const a = s?.ace2k || {};
  const failing = a.health?.failing;
  return {
    version: a.version || null,
    link: a.link_proven === true,
    health: a.health?.ok !== false,
    failing: failing && failing.length ? String(failing) : null,
    history: s?.ace2k_u1_history?.error || null,
    online,
    stopLanes: stopAllLanes(s),
  };
}
