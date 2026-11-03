import { html } from './html.js';
import { FILAMENT_TYPES, SWATCHES, editAction, reasonKey } from '../model.js';
import { planNow, ready, scoped } from '../state.js';
import { normalColour, usedTest } from '../print-plan.js';

/** @typedef {import('../types.js').AppState} AppState */
/** @typedef {import('../types.js').UiCtx} UiCtx */
/** @typedef {import('../types.js').EditForm} EditForm */
/** @typedef {import('../lane-state.js').LaneAt} LaneAt */

/** @typedef {{ currentTarget: HTMLInputElement }} InputEv  An input event, read by its field's value. */

// Why Save is disabled, as a catalogue key, or null while it is enabled.
/** @param {AppState} app @param {LaneAt} at */
function saveReason(app, at) {
  const ea = editAction(at.slice, at.i, ready(app));
  if (!ea) return 'unavailable';
  return ea.reason ? reasonKey(ea.id, ea.reason) : null;
}

// The file's colours while the Print tab shows a file's plan: each colour the file prints, once, in tool order, with
// the colours that print it ("color 1, color 8") as its title. None outside a plan.
/** @param {AppState} app @param {UiCtx['t']} t @returns {{ c: string, title: string }[]} */
function fileColours(app, t) {
  const now = app.ui.tab === 'print' ? planNow(app) : null;
  if (!now) return [];
  const used = usedTest(now.plan.order);
  /** @type {Map<string, number[]>} */
  const seen = new Map();
  now.plan.tools.forEach((tool, n) => {
    const c = normalColour(tool.colour);
    if (!used(n) || !c) return;
    seen.set(c, [...(seen.get(c) ?? []), n]);
  });
  return [...seen].map(([c, ns]) => ({ c, title: ns.map((n) => t('print.colour_n', { n: n + 1 })).join(', ') }));
}

// The spool editor of a bay with no tag: the material as buttons (the U1's filament table), the colour as swatches
// plus a free colour, and an optional vendor. While the Print tab shows a file's plan, the file's colours come first
// (fileColours), then the usual swatches. Save sends the lane's spool record (ACEK_SPOOL) through edit_save.
// Opened from the colour picker (edit.use, the tool), Save also makes this bay that colour's pick, and the material
// is locked to the colour's own when the U1 lists it (edit.lock). A disabled Save says why in a note it points at.
/** @param {{ app: AppState, ctx: UiCtx, at: LaneAt, edit: EditForm }} props */
export function SpoolEditor({ app, ctx, at, edit }) {
  const { t } = ctx;
  const types = edit.lock ? [edit.type] : FILAMENT_TYPES.map(String);
  const why = saveReason(app, at);
  const busy = scoped(app, at.unit).has(`edit_spool:${at.i + 1}`);
  const colour = edit.color.toLowerCase();
  const fromFile = fileColours(app, t);
  const swatches = [
    ...fromFile,
    ...SWATCHES.filter((c) => !fromFile.some((f) => f.c === c.toLowerCase())).map((c) => ({ c, title: null })),
  ];
  const label = edit.use !== undefined ? t('spool.save_use', { n: edit.use + 1 }) : t('action.save');
  /** @param {{ preventDefault: () => void }} e */
  const save = (e) => {
    e.preventDefault();
    if (!busy) ctx.run('edit_save', { lane: String(at.i + 1) });
  };
  return html`<form class="spool-editor" onSubmit=${save}>
    <fieldset class="mats">
      <legend>${t('lane.edit_type')}</legend>
      ${types.map(
        (ty, k) => html`<button type="button" class=${ty === edit.type ? 'mat on' : 'mat'}
          data-autofocus=${ty === edit.type || (k === 0 && !types.includes(edit.type)) ? 'true' : undefined}
          aria-pressed=${ty === edit.type ? 'true' : 'false'} onClick=${() => ctx.run('spool_edit_type', { type: ty })}>${ty}</button>`,
      )}
    </fieldset>
    <fieldset class="cols">
      <legend>${t('lane.edit_colour')}</legend>
      ${swatches.map(
        ({ c, title }) => html`<button type="button" class=${c === colour ? 'sw on' : 'sw'} style=${`--fil:${c}`}
          title=${title ?? undefined} aria-pressed=${c === colour ? 'true' : 'false'}
          aria-label=${title ? t('spool.colour_titled', { title, c: c.toUpperCase() }) : t('spool.colour_value', { c: c.toUpperCase() })}
          onClick=${() => ctx.run('spool_edit_colour', {}, c)}></button>`,
      )}
      <input type="color" value=${colour} aria-label=${t('spool.free_colour')}
        onInput=${(/** @type {InputEv} */ e) => ctx.run('spool_edit_colour', {}, e.currentTarget.value)} />
    </fieldset>
    <label class="vendor">${t('spool.vendor')}
      <input type="text" autocomplete="off" autocapitalize="off" spellcheck="false" maxlength="24" value=${edit.vendor}
        onInput=${(/** @type {InputEv} */ e) => ctx.run('spool_edit_vendor', {}, e.currentTarget.value)} /></label>
    <p class="note">${t('lane.edit_note')}</p>
    <div class="acts">
      <button type="submit" class="btn pri" disabled=${why !== null} aria-disabled=${busy ? 'true' : undefined}
        title=${why ? t(`reason.${why}`) : undefined}>
        ${busy ? t('action.sending') : label}</button>
      <button type="button" class="btn" onClick=${() => ctx.run('edit_cancel', {})}>${t('action.cancel')}</button>
    </div>
    ${why ? html`<p class="note why">${t(`reason.${why}`)}</p>` : null}
  </form>`;
}
