import { t as say } from './i18n.js';
import { kindWord, modeWord } from './lane-state.js';
import { faultText, stateText } from './dryer-form.js';

// The recorder's lines in words. history.js writes them and this module reads them back, so the two sides of the
// text sit in neighbouring files. It is not in history.js because lane-state.js (kindWord, modeWord) reads the page
// state, which already imports history.js.

/** @typedef {import('./types.js').Event} LogEvent */

// The recorder's texts (history.js, ace2k_u1_history.py) are shown in the catalogue's words.
// Any other line is a console line. It shows as it came, without the "ace2k:" or "ace2k_u1:" prefix,
// because the source column already says it.
// The event keeps the recorder's text, because that text is a contract.
/** @param {unknown} msg */
export function logLineText(msg) {
  const text = String(msg ?? '');
  const own = /^ace2k (\S+): (.*)$/s.exec(text);
  const body = own ? own[2] : text;
  const said = recorderWords(body);
  if (said === null) return text.replace(/^ace2k(_u1)?:\s*/, '');
  return own ? `${say('units.name', { name: own[1] })}: ${said}` : said;
}

/** @param {string} body @returns {string | null} */
function recorderWords(body) {
  let m = /^lane (\S+) (spool inserted|spool removed|error cleared|no tag)$/.exec(body);
  if (m) return say(`events.lane_${m[2].replace(' ', '_')}`, { n: m[1] });
  m = /^lane (\S+) tag read(?:: (.+))?$/.exec(body);
  if (m)
    return m[2] ? say('events.lane_tag_read_who', { n: m[1], who: m[2] }) : say('events.lane_tag_read', { n: m[1] });
  m = /^lane (\S+) error: (\S+)$/.exec(body);
  if (m) return say('events.lane_error', { n: m[1], error: kindWord(m[2]) });
  m = /^lane (\S+) (\S+) \((\S+?)(?:, ([\d.]+) mm)?\)$/.exec(body);
  if (m)
    return say(m[4] ? 'events.lane_event_mm' : 'events.lane_event', {
      n: m[1],
      kind: kindWord(m[2]),
      mode: modeWord(m[3]),
      mm: m[4],
    });
  m = /^dryer fault: (\S+)$/.exec(body);
  if (m) return faultText(m[1] === '?' ? null : m[1]);
  m = /^dryer (\S+) → (\S+)$/.exec(body);
  if (m) return say('events.dryer_change', { from: stateText(m[1]), to: stateText(m[2]) });
  return null;
}

// Who an event came from: one of the ACE units, by the name its text carries ("ace2k b: …", "lane b:3 …"), or the
// U1 itself. A unit that has no name in its text (a single unnamed unit) is { name: null }.
// Lines of the lanes and of the dryer belong to an ACE unit; any other console line comes from the U1.
/** @param {LogEvent} e @returns {{ u1: boolean, name: string | null }} */
export function sourceOf(e) {
  const msg = String(e.msg ?? '');
  const named = /^ace2k (\S+):/.exec(msg) ?? /\blane ([^\s:]+):\d/.exec(msg);
  if (named) return { u1: false, name: named[1] };
  const ace = /^ace2k:/.test(msg) || /^lane \d/.test(msg) || /^(lane\d|dryer)$/.test(e.src ?? '');
  return { u1: !ace, name: null };
}
