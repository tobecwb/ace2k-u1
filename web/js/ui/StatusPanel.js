import { html } from './html.js';
import { healthText, linkWords, unitStatuses } from '../unit-status.js';

/** @typedef {import('../types.js').AppState} AppState */
/** @typedef {import('../types.js').UiCtx} UiCtx */

// The status card of the side panel: each unit's version, link and self-test in words.
/** @param {{ app: AppState, ctx: UiCtx }} props */
export function StatusCard({ app, ctx }) {
  const units = unitStatuses(app);
  return units.map(
    (u) => html`<section class="status-unit">
      ${units.length > 1 ? html`<h3>${ctx.t('units.name', { name: u.name })}</h3>` : null}
      ${u.version ? html`<p class="note">${ctx.t('top.version', { v: u.version })}</p>` : null}
      <p class=${u.link ? 'light ok' : 'light err'}>${linkWords(u, ctx.t)}</p>
      <p class=${u.health ? 'light ok' : 'light warn'}>${healthText(u, ctx.t)}</p>
    </section>`,
  );
}
