import { html } from './html.js';
import { sliceFor } from '../model.js';
import { diagramModel, darkFil, envText, headView, MARK_WORDS, openBay, openHead } from '../diagram-model.js';
import { DASH } from '../format.js';
import { layout, pale, deep } from '../diagram-layout.js';
import { statusLevel, unitStatuses } from '../unit-status.js';
import { fil, headName, materialWord, unitLabel, withError } from '../words.js';

/** @typedef {import('../types.js').AppState} AppState */
/** @typedef {import('../types.js').UiCtx} UiCtx */
/** @typedef {import('../diagram-layout.js').DModel} DModel */
/** @typedef {import('../diagram-layout.js').DLane} DLane */
/** @typedef {import('../diagram-layout.js').DUnit} DUnit */
/** @typedef {import('../diagram-layout.js').Layout} Layout */
/** @typedef {import('../diagram-layout.js').Rail} Rail */
/** @typedef {import('../diagram-model.js').BayMark} BayMark */
/** @typedef {import('../diagram-layout.js').UnitBox} UnitBox */
/** @typedef {import('../unit-status.js').UnitStatus} UnitStatus */
/** @typedef {import('../diagram-layout.js').BayBox} BayBox */
/** @typedef {import('../diagram-layout.js').HeadBox} HeadBox */

/**
 * What the Print tab draws over the diagram. from and to are the two bays of the guided step ("take out" dashed,
 * "put in" solid with a ghost of the spool that goes in); while either is set, every other bay is dimmed but a bay
 * with a spool of no material, the bay whose panel is open and a bay in error. chips gives each bay the plan uses
 * its file colour (1-based), shown instead of the head chip. ejecting is the bay that eject all is ejecting.
 * @typedef {{ from?: string | null, to?: string | null, ghost?: { colour: string | null, material: string | null } | null,
 *   chips?: Record<string, number>, ejecting?: string | null }} Overlay
 */

// What makes an SVG group a button: a click, and Enter or Space from the keyboard.
/** @param {() => void} fn @param {string} label */
const press = (fn, label) => ({
  role: 'button',
  tabindex: '0',
  'aria-label': label,
  onClick: () => fn(),
  /** @param {{ key: string, preventDefault: () => void }} e */
  onKeyDown: (e) => {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    e.preventDefault();
    fn();
  },
});

// The marks for a colour that vanishes on the card (the style that paints it is words.js fil).
/** @param {string | null | undefined} c */
const lightFil = (c) => (pale(c) ? 'true' : undefined);

// The thin ring round the active bay of a colour that vanishes on its tile: grey round a pale one in the
// light theme, light round a deep one in the dark theme. Null for any other colour.
/** @param {string | null | undefined} c */
const ring = (c) => (pale(c) ? 'pale-ring' : deep(c) ? 'deep-ring' : null);

// A head card: the bar and swatch in the current filament's colour, the head's name, its two lines, a chevron.
// sel is true while the head's side panel is open: a ring in the accent colour inside the card's edge, as a bay's.
/** @param {{ box: HeadBox, view: ReturnType<typeof headView>, ctx: UiCtx, sel?: boolean }} props */
function HeadCard({ box, view, ctx, sel = false }) {
  const { x, y, w, h, head } = box;
  const name = headName(ctx.t, head);
  /** @returns {unknown} */
  const open = () => ctx.run('panel', { kind: 'head', id: String(head) });
  return html`<g class=${sel ? 'head sel' : 'head'} aria-current=${sel ? 'true' : undefined}
    ...${press(open, ctx.t('units.head_label', { head: name, what: view.line1 }))}>
    <rect class="head-card" x=${x} y=${y} width=${w} height=${h} rx="14" />
    ${sel ? html`<rect class="sel-ring" x=${x + 4} y=${y + 4} width=${w - 8} height=${h - 8} rx="10" />` : null}
    <rect class="bar" x=${x} y=${y} width="6" height=${h} rx="3" style=${fil(view.colour)}
      data-light-fil=${lightFil(view.colour)} data-dark-fil=${darkFil(view.colour)} />
    <text class="big" x=${x + 20} y=${y + 34}>${name}</text>
    <text class=${view.warn ? 'warn' : undefined} x=${x + 20} y=${y + 60}>${view.line1}</text>
    <text class="muted small" x=${x + 20} y=${y + 80}>${view.line2}</text>
    <circle class=${view.colour ? 'swatch cased' : 'swatch none'} cx=${x + 212} cy=${y + 36} r="22"
      style=${fil(view.colour)} data-dark-fil=${darkFil(view.colour)} />
    <circle class="hole" cx=${x + 212} cy=${y + 36} r="6" />
    <text class="chev" x=${x + w - 10} y=${y + 54} text-anchor="end">${ctx.t('units.chevron')}</text>
  </g>`;
}

// A head's nozzle, under its card, in the current filament's colour.
/** @param {{ box: HeadBox, colour: string | null }} props */
function Nozzle({ box, colour }) {
  const { cx } = box;
  return html`<g class="nozzle-at">
    <rect class="nblock" x=${cx - 14} y="118" width="28" height="12" rx="3" />
    <path class="nozzle cased" d=${`M${cx - 11} 130 L${cx + 11} 130 L${cx + 4} 144 L${cx - 4} 144 Z`} style=${fil(colour)}
      data-dark-fil=${darkFil(colour)} />
  </g>`;
}

// A lane's stub from its bay to the bus: dashed and grey while empty, else in its filament's colour.
/** @param {{ rail: Rail }} props */
function Stub({ rail }) {
  if (rail.dashed) return html`<path class="rail dashed" d=${rail.d} />`;
  return html`<g class="stub-at">
    <path class="stub-case cased" d=${rail.d} data-dark-fil=${darkFil(rail.colour)} />
    <path class="stub" d=${rail.d} style=${fil(rail.colour)} />
    <circle class="joint cased" cx=${rail.x} cy=${rail.y} r="4" style=${fil(rail.colour)}
      data-dark-fil=${darkFil(rail.colour)} />
  </g>`;
}

// The lane in a head: one rounded path from its bay to the nozzle, glowing in its colour.
/** @param {{ rail: Rail }} props */
function Live({ rail }) {
  return html`<g class="live-at">
    <path class="glow" d=${rail.d} style=${fil(rail.colour)} filter="url(#glow)" />
    <path class="live-case cased" d=${rail.d} data-dark-fil=${darkFil(rail.colour)} />
    <path class="rail live" d=${rail.d} style=${fil(rail.colour)} />
  </g>`;
}

// A bay's spool circle: dashed while empty, "?" in amber while its material is unknown, else its colour.
/** @param {{ lane: DLane, cx: number, cy: number, t: UiCtx['t'] }} props */
function Spool({ lane, cx, cy, t }) {
  if (!lane.spool) return html`<circle class="spool empty" cx=${cx} cy=${cy} r="19" />`;
  if (!lane.spool.material)
    return html`<circle class="spool unknown" cx=${cx} cy=${cy} r="19" />
      <text class="q" x=${cx} y=${cy + 6} text-anchor="middle">${t('units.unknown_mark')}</text>`;
  const c = lane.spool.colour;
  return html`<circle class="spool cased" cx=${cx} cy=${cy} r="19" style=${fil(c)} data-dark-fil=${darkFil(c)} />
    <circle class="hole" cx=${cx} cy=${cy} r="5" />`;
}

// What a bay says under its spool: the material, "unknown" or "empty".
/** @param {DLane} lane @param {UiCtx['t']} t */
const materialText = (lane, t) => (lane.spool ? materialWord(t, lane.spool.material) : t('units.empty'));

// Where the bay's spool came from: 🏷 from the tag, ✎ set by hand, nothing when nothing is known.
/** @param {DLane} lane @param {UiCtx['t']} t */
const originMark = (lane, t) => {
  if (lane.spool?.tag) return t('units.tag_mark');
  return lane.spool?.material ? t('units.hand_mark') : null;
};

// The width of a mark's pill from its word: about 6.8 px a character of the bold 11 px label, and 7 px each side.
/** @param {string} words */
export const pillWidth = (words) => Math.round(words.length * 6.8) + 14;

// A mark round a bay during a print, drawn over the rails: an outline, and a label on its top edge ("printing" in green for the bay
// that prints now, "in head" in blue for a bay loaded in another head).
/** @param {{ box: BayBox, mark: BayMark, t: UiCtx['t'] }} props */
function Mark({ box, mark, t }) {
  const { x, y, w, h, cx } = box;
  const words = t(MARK_WORDS[mark]);
  const pill = pillWidth(words);
  return html`<rect class=${`mk ${mark}`} x=${x - 3} y=${y - 3} width=${w + 6} height=${h + 6} rx="12" />
    <rect class=${`mk-lab ${mark}`} x=${cx - pill / 2} y=${y - 10} width=${pill} height="15" rx="7.5" />
    <text class=${`mk-text ${mark}`} x=${cx} y=${y + 1} text-anchor="middle">${words}</text>`;
}

// The tile's class: outlined in the filament's colour while loaded in a head, in red while in error.
/** @param {DLane} lane */
const tileClass = (lane) => ['bay-tile', lane.filled ? 'on' : '', lane.error ? 'err' : ''].filter(Boolean).join(' ');

// What the overlay makes of a bay: its role in the step (or the eject run) with its label, and whether it is dimmed.
// Null for a bay the overlay leaves alone.
/**
 * @param {DLane} lane @param {Overlay | null | undefined} o @param {string | null} open
 * @returns {{ role: 'from' | 'to' | 'ej' | null, dim: boolean }}
 */
export function bayRole(lane, o, open) {
  if (!o) return { role: null, dim: false };
  const role = lane.key === o.ejecting ? 'ej' : lane.key === o.to ? 'to' : lane.key === o.from ? 'from' : null;
  const kept = role !== null || lane.key === open || lane.error || (lane.spool !== null && !lane.spool.material);
  return { role, dim: Boolean(o.from || o.to) && !kept };
}

// The label over a bay of the step: "take out", "put in", "ejecting…".
/** @param {UiCtx['t']} t @param {'from' | 'to' | 'ej'} role */
const roleWords = (t, role) =>
  role === 'from' ? t('step.take_out') : role === 'to' ? t('step.put_in') : t('step.ejecting');

// The ghost of the spool that goes into the "put in" bay: a dashed circle in its colour and "+ PLA".
/** @param {{ ghost: { colour: string | null, material: string | null }, cx: number, y: number, t: UiCtx['t'] }} props */
function Ghost({ ghost, cx, y, t }) {
  return html`<circle class="spool ghost" cx=${cx} cy=${y + 50} r="19" style=${fil(ghost.colour)} />
    <text class="small b ghost-m" x=${cx} y=${y + 84} text-anchor="middle">${t('step.ghost', { material: ghost.material ?? t('step.spool') })}</text>`;
}

// A bay's spool and what it says under it.
/** @param {{ lane: DLane, cx: number, y: number, what: string, t: UiCtx['t'] }} props */
function BaySpool({ lane, cx, y, what, t }) {
  return html`<${Spool} lane=${lane} cx=${cx} cy=${y + 50} t=${t} />
    <text class=${lane.spool?.material ? 'small b' : 'small muted'} x=${cx} y=${y + 84} text-anchor="middle">${what}</text>`;
}

// A bay's chip: the head it feeds (filled while its lane is in the head), or on the Print tab the file colour the
// plan prints from it, "colour n".
/**
 * @param {{ lane: DLane, cx: number, y: number, chip: string, colourN: number | undefined, t: UiCtx['t'] }} props
 */
function BayChip({ lane, cx, y, chip, colourN, t }) {
  if (colourN !== undefined)
    return html`<rect class="chip colour" x=${cx - 26} y=${y + 104} width="52" height="18" rx="9" />
      <text class="tiny" x=${cx} y=${y + 117} text-anchor="middle">${t('print.colour_n', { n: colourN })}</text>`;
  return html`<rect class=${lane.filled ? 'chip on' : 'chip'} x=${cx - 17} y=${y + 104} width="34" height="18" rx="9" />
    <text class=${lane.filled ? 'tiny on' : 'tiny'} x=${cx} y=${y + 117} text-anchor="middle">${chip}</text>`;
}

// The labels over the bays of the step, in pills, drawn last so the rails of the heads never cover them.
/** @param {{ lay: Layout, model: DModel, overlay: Overlay | null, t: UiCtx['t'] }} props */
function RoleLabels({ lay, model, overlay, t }) {
  return lay.units.flatMap((box, i) =>
    model.units[i].lanes.map((lane, k) => {
      const { role } = bayRole(lane, overlay, null);
      if (!role) return null;
      const { cx, y } = box.bays[k];
      const words = roleWords(t, role);
      // The chip grows with its words (about 6.5 px a letter at this size), never narrower than before.
      const w = Math.max(54, Math.round(words.length * 6.5 + 14));
      return html`<g class=${`role-at r-${role}`}>
        <rect class="role-lab" x=${cx - w / 2} y=${y - 13} width=${w} height="17" rx="8.5" />
        <text class="role-word" x=${cx} y=${y - 1} text-anchor="middle">${words}</text>
      </g>`;
    }),
  );
}

// The red "!" badge in the top-left corner of a bay in error.
/** @param {{ box: BayBox, t: UiCtx['t'] }} props */
function ErrBadge({ box, t }) {
  const { x, y } = box;
  return html`<circle class="err-badge" cx=${x + 10} cy=${y + 11} r="7" />
    <text class="err-mark" x=${x + 10} y=${y + 15} text-anchor="middle">${t('units.err_mark')}</text>`;
}

// The short error word of a bay in error, under its material, clipped to the tile.
/** @param {{ box: BayBox, clip: string, word: string }} props */
function ErrWord({ box, clip, word }) {
  const { x, y, w, cx } = box;
  return html`<clipPath id=${clip}><rect x=${x + 2} y=${y + 86} width=${w - 4} height="16" /></clipPath>
    <text class="err-word" x=${cx} y=${y + 97} text-anchor="middle" clip-path=${`url(#${clip})`}>${word}</text>`;
}

// The tile has room for 8 letters at this size; a longer state word is squeezed to the tile's width.
const STATE_ROOM = 8;

// The lane's state word under its material ("Following", "In head?"), in the colour of the bay card's word.
/** @param {{ box: BayBox, clip: string, chip: string, word: string }} props */
function StateWord({ box, clip, chip, word }) {
  const { x, y, w, cx } = box;
  const fit = word.length > STATE_ROOM ? { textLength: w - 6, lengthAdjust: 'spacingAndGlyphs' } : {};
  return html`<clipPath id=${clip}><rect x=${x + 2} y=${y + 86} width=${w - 4} height="16" /></clipPath>
    <text class=${`state-word s-${chip}`} x=${cx} y=${y + 97} text-anchor="middle" ...${fit} clip-path=${`url(#${clip})`}>${word}</text>`;
}

// The class of a bay's group: dimmed, its role in the guided step, and selected (its panel open).
/** @param {boolean} dim @param {string | null} role @param {boolean} sel */
const bayClass = (dim, role, sel) =>
  ['bay', dim ? 'dim' : '', role ? `r-${role}` : '', sel ? 'sel' : ''].filter(Boolean).join(' ');

// A bay tile: its number, the spool, the material, the origin mark and a chip with the head it feeds.
// The bay loaded in a head is outlined in the filament's colour.
/** @param {{ box: BayBox, lane: DLane, clip: string, ctx: UiCtx, overlay?: Overlay | null, open?: string | null }} props */
function Bay({ box, lane, clip, ctx, overlay = null, open = null }) {
  const { t } = ctx;
  const { x, y, w, h, cx } = box;
  const { role, dim } = bayRole(lane, overlay, open);
  const ghost = role === 'to' ? (overlay?.ghost ?? null) : null;
  const colourN = overlay?.chips?.[lane.key];
  const c = lane.filled ? lane.pathColour : null;
  const what = materialText(lane, t);
  const mark = originMark(lane, t);
  const label = withError(what, lane.error);
  const chip = lane.head === null ? DASH : headName(t, lane.head);
  /** @returns {unknown} */
  const show = () => ctx.run('panel', { kind: 'bay', key: lane.key });
  const words = t('units.bay_label', { key: lane.key, what: label, head: chip });
  // The bay whose side panel is open is marked with a ring of its own in the accent colour, drawn inside the tile so
  // it never meets a neighbour or the colour and role rings outside it.
  const sel = open === lane.key;
  return html`<g class=${bayClass(dim, role, sel)}
    aria-current=${sel ? 'true' : undefined} ...${press(show, words)}>
    <title>${words}</title>
    ${ring(c) ? html`<rect class=${ring(c)} x=${x - 1.5} y=${y - 1.5} width=${w + 3} height=${h + 3} rx="11.5" />` : null}
    ${role ? html`<rect class="role-ring" x=${x - 4} y=${y - 4} width=${w + 8} height=${h + 8} rx="13" />` : null}
    <rect class=${tileClass(lane)} style=${fil(c)} x=${x} y=${y} width=${w} height=${h} rx="10" />
    ${sel ? html`<rect class="sel-ring" x=${x + 4} y=${y + 4} width=${w - 8} height=${h - 8} rx="6.5" />` : null}
    <text class="small muted" x=${cx} y=${y + 18} text-anchor="middle">${lane.n}</text>
    ${mark ? html`<text class="mark" x=${x + w - 4} y=${y + 15} text-anchor="end">${mark}</text>` : null}
    ${lane.error ? html`<${ErrBadge} box=${box} t=${t} />` : null}
    ${ghost ? html`<${Ghost} ghost=${ghost} cx=${cx} y=${y} t=${t} />` : html`<${BaySpool} lane=${lane} cx=${cx} y=${y} what=${what} t=${t} />`}
    ${lane.error ? html`<${ErrWord} box=${box} clip=${clip} word=${t(/** @type {string} */ (lane.errorShort))} />` : null}
    ${!lane.error && lane.state ? html`<${StateWord} box=${box} clip=${clip} chip=${lane.chip} word=${lane.state} />` : null}
    <${BayChip} lane=${lane} cx=${cx} y=${y} chip=${chip} colourN=${colourN} t=${t} />
  </g>`;
}

// The footer's room for the name: 9 characters before the status dot, so that the widest reading
// ("100 °C · 100 %") still ends left of "♨ dry". "Unit a" shows whole; a longer "Unit <name>" drops the
// prefix, and a name longer still ends in "…". The full "Unit <name>" is always the footer's title.
const NAME_ROOM = 9;
/** @param {string} full @param {string | null} name */
export const footerName = (full, name) => {
  if (full.length <= NAME_ROOM || !name) return full;
  return name.length > NAME_ROOM ? `${name.slice(0, NAME_ROOM - 1)}…` : name;
};
// Where the footer's parts sit, from the unit's left edge: the status dot after the name (about 7.2 px a
// character of the bold 13 px name, then 10 px), the readings 12 px after it, and the end of "♨ dry".
/** @param {string} shown */
export const footLayout = (shown) => {
  const dot = Math.round(18 + shown.length * 7.2 + 10);
  return { dot, readings: dot + 12, dryEnd: 238 };
};

// A unit tile: the amber card, its bays, and the footer strip (a button to the unit's panel) with the "♨ dry" shortcut.
/** @param {{ box: UnitBox, unit: DUnit, status: UnitStatus | undefined, clip: string, app: AppState, ctx: UiCtx, overlay?: Overlay | null }} props */
function UnitTile({ box, unit, status, clip, app, ctx, overlay = null }) {
  const { t } = ctx;
  const { x, y, w, h } = box;
  const label = unitLabel(t, unit.name);
  const shown = footerName(label, unit.name);
  const foot = footLayout(shown);
  const level = status ? statusLevel([status]) : 'err';
  const id = unit.name ?? '';
  /** @returns {unknown} */
  const open = () => ctx.run('panel', { kind: 'unit', id });
  /** @returns {unknown} */
  const dry = () => ctx.run('dry_shortcut', { unit: id });
  const sel = openBay(app); // the bay whose panel is open, once for every bay
  return html`<g class="unit-at">
    <rect class="unit" x=${x} y=${y} width=${w} height=${h} rx="14" />
    ${unit.lanes.map(
      (lane, i) =>
        html`<${Bay} box=${box.bays[i]} lane=${lane} clip=${`${clip}-${i}`} ctx=${ctx} overlay=${overlay} open=${sel} />`,
    )}
    <g class="foot-btn" ...${press(open, t('units.unit_label', { name: label }))}>
      <title>${label}</title>
      <rect class="foot" x=${x + 8} y=${y + 146} width=${w - 16} height="30" rx="9" />
      <text class="b" x=${x + 18} y=${y + 166}>${shown}</text>
      <circle class=${`dot-${level}`} cx=${x + foot.dot} cy=${y + 161} r="4.5" />
      <text class="small muted" x=${x + foot.readings} y=${y + 166}>${envText(sliceFor(app.status, unit.name))}</text>
      <text class="chev" x=${x + 250} y=${y + 167}>${t('units.chevron')}</text>
    </g>
    <g class="dry-btn" ...${press(dry, t('units.dry_label', { name: label }))}>
      <text class="small" x=${x + foot.dryEnd} y=${y + 166} text-anchor="end">${t('units.dry')}</text>
    </g>
  </g>`;
}

// The legend under the diagram.
/** @param {{ t: UiCtx['t'] }} props */
function Legend({ t }) {
  return html`<div class="legend">
    <span><i class="lg-live"></i>${t('units.legend_live')}</span>
    <span><i class="lg-wait"></i>${t('units.legend_waiting')}</span>
    <span><i class="lg-empty"></i>${t('units.legend_empty')}</span>
    <span><b class="lg-chip">${headName(t, 1)}</b>${t('units.legend_chip')}</span>
    <span>${t('units.tag_mark')} ${t('units.legend_tag')}</span>
    <span>${t('units.hand_mark')} ${t('units.legend_hand')}</span>
    <span><b class="lg-err">${t('units.err_mark')}</b>${t('units.legend_error')}</span>
  </div>`;
}

// A diagram wider than the standard 1200 keeps the standard's scale: a minimum width of one px per unit
// of the view (--dg-min, units.css), so its text stays legible and the tab scrolls sideways instead. The Print
// tab's two columns let it shrink to its column (print.css).
/** @param {number} width */
export const wide = (width) => (width > 1200 ? `--dg-min:${width}px` : undefined);

// The Units tab: the printer's heads on top, the rails, the units with their bays below, and the legend.
// marks outlines bays by key during a print (the printing view). The Print tab draws the same diagram with an
// overlay (the guided step), and without the legend.
/** @param {{ app: AppState, ctx: UiCtx, marks?: Record<string, BayMark>, overlay?: Overlay | null }} props */
export function Diagram({ app, ctx, marks = {}, overlay = null }) {
  const model = diagramModel(app);
  const lay = layout(model);
  const views = model.heads.map((h) => headView(model, h, ctx.t));
  const live = lay.rails.filter((r) => r.live);
  const statuses = unitStatuses(app); // once for every unit tile
  const selHead = openHead(app);
  return html`<div class="units-view">
    <svg class="dg" viewBox=${`0 0 ${lay.width} ${lay.height}`} style=${wide(lay.width)} role="group"
      aria-label=${ctx.t('units.diagram')}>
      <defs><filter id="glow" x="-20%" y="-20%" width="140%" height="140%"><feGaussianBlur stdDeviation="4" /></filter></defs>
      <text class="cap" x=${lay.heads[0].x - 4} y="16">${ctx.t('units.cap')}</text>
      ${lay.heads.map((box, i) => html`<${HeadCard} box=${box} view=${views[i]} ctx=${ctx} sel=${selHead === box.head} />`)}
      ${lay.segments.map((s) => html`<path class=${s.dashed ? 'rail dashed' : 'rail'} d=${s.d} />`)}
      ${lay.rails.filter((r) => !r.live).map((r) => html`<${Stub} rail=${r} />`)}
      ${lay.units.map(
        (box, i) =>
          html`<${UnitTile} box=${box} unit=${model.units[i]} status=${statuses.find((u) => u.name === model.units[i].name)} clip=${`err-clip-${i}`} app=${app} ctx=${ctx} overlay=${overlay} />`,
      )}
      ${live.map((r) => html`<${Live} rail=${r} />`)}
      ${lay.heads.map((box, i) => html`<${Nozzle} box=${box} colour=${views[i].colour} />`)}
      ${lay.units.flatMap((box, i) => model.units[i].lanes.map((lane, j) => (marks[lane.key] ? html`<${Mark} box=${box.bays[j]} mark=${marks[lane.key]} t=${ctx.t} />` : null)))}
      ${overlay ? html`<${RoleLabels} lay=${lay} model=${model} overlay=${overlay} t=${ctx.t} />` : null}
    </svg>
    ${overlay ? null : html`<${Legend} t=${ctx.t} />`}
  </div>`;
}
