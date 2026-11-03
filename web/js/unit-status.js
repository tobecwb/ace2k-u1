import { sliceFor, topbarView } from './model.js';
import { ready, unitNames } from './state.js';
import { healthWords } from './health.js';

// Each unit's link and self-test as the status dot, the status panel and the unit card show them. It has no markup.

/** @typedef {import('./types.js').AppState} AppState */
/** @typedef {import('./types.js').UiCtx} UiCtx */
/**
 * One unit's link and self-test, as the status dot and the status panel show them.
 * @typedef {{ name: string | null, version: string | null, link: boolean, health: boolean,
 *   failing: string[] | null }} UnitStatus
 */

// Every unit's link and self-test. A unit's link counts as down while the page is not ready.
/** @param {AppState} app @returns {UnitStatus[]} */
export function unitStatuses(app) {
  const online = ready(app);
  return unitNames(app).map((name) => {
    const v = topbarView(sliceFor(app.status, name), online);
    return { name, version: v.version, link: online && v.link, health: v.health, failing: v.failing };
  });
}

// The status dot's level: err when a link is down, warn when a self-test fails, else ok.
/** @param {UnitStatus[]} units @returns {'ok' | 'warn' | 'err'} */
export function statusLevel(units) {
  if (units.some((u) => !u.link)) return 'err';
  return units.some((u) => !u.health) ? 'warn' : 'ok';
}

// The link's words and the self-test's words of one unit.
/** @param {UnitStatus | undefined} u @param {UiCtx['t']} t */
export const linkWords = (u, t) => t(u?.link ? 'top.link_ok' : 'top.link_down');
/** @param {UnitStatus} u @param {UiCtx['t']} t */
export const healthText = (u, t) =>
  u.health ? t('top.health_ok') : t('top.health_bad', { what: healthWords(u.failing) });

// The whole status in one line, for the dot's name: each unit's link and self-test, the unit named when
// there are several.
/** @param {UnitStatus[]} units @param {UiCtx['t']} t */
export function statusWords(units, t) {
  return units
    .map((u) => {
      const words = `${linkWords(u, t)}, ${healthText(u, t)}`;
      return units.length > 1 ? `${t('units.name', { name: u.name })}: ${words}` : words;
    })
    .join('; ');
}
