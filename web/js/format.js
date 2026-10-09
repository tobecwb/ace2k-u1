// Text formats shared by the views.
const DASH = '—';
const isNum = (v) => typeof v === 'number' && Number.isFinite(v);

export function duration(minutes) {
  if (!isNum(minutes)) return DASH;
  const m = Math.max(0, Math.round(minutes));
  const h = Math.floor(m / 60);
  const rest = m % 60;
  if (h && rest) return `${h} h ${rest} min`;
  return h ? `${h} h` : `${rest} min`;
}

export const temp = (v) => (isNum(v) ? `${v.toFixed(1)} °C` : DASH);
export const pct = (v) => (isNum(v) ? `${Math.round(v)} %` : DASH);
export const mm = (v) => (isNum(v) ? `${v.toFixed(1)} mm` : DASH);

const pad = (n) => String(n).padStart(2, '0');

export function clock(t) {
  const d = new Date(t * 1000);
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

const DAY_FILE = /^\d{4}-\d{2}-\d{2}\.jsonl$/;

// The recorder's day files from a Moonraker directory listing (result.files), newest first: the
// names carry the printer's own dates, so the newest one is the printer's today.
export function historyDays(files, keepDays) {
  return (Array.isArray(files) ? files : [])
    .map((f) => f?.filename)
    .filter((n) => typeof n === 'string' && DAY_FILE.test(n))
    .sort()
    .reverse()
    .slice(0, keepDays);
}
