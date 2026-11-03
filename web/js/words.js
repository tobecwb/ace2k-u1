// The words the components write the same way, and the style that hands them a filament colour.
// Each takes the page's t(), so a component and its test read the same catalogue.

/** @typedef {import('./types.js').UiCtx['t']} Words */

// A head's name as the page writes it everywhere: T0-T3. h is the head's index (0-3), or its id as a panel names it.
/** @param {Words} t @param {number | string | null | undefined} h */
export const headName = (t, h) => t('units.head', { n: h });

// The values of a head_* reason's text: the head's name, and for head_hot the probe's cold limit in °C.
/** @param {Words} t @param {import('./types.js').Status | null | undefined} s @param {string | null} reason @param {number} h */
export const headVars = (t, s, reason, h) =>
  reason === 'head_hot'
    ? { head: headName(t, h), limit: s?.ace2k_u1?.probe_max_temp ?? '?' }
    : { head: headName(t, h) };

// A unit's name: "Unit a", or "Unit" for the unnamed [ace2k].
/** @param {Words} t @param {string | null | undefined} name */
export const unitLabel = (t, name) => (name ? t('units.name', { name }) : t('units.unnamed'));

// The lanes that feed a head, as a line: "1 lane: a:1" or "2 lanes: a:1 · a:2". list is the lanes already in words.
/** @param {Words} t @param {number} n @param {string} list */
export const lanesLine = (t, n, list) => (n === 1 ? t('units.lanes_one', { list }) : t('units.lanes', { n, list }));

// The U1's own spool of a head that no lane feeds: "U1 spool · PLA", or "U1 spool" with no material set.
/** @param {Words} t @param {string | null | undefined} material */
export const u1SpoolLine = (t, material) => (material ? `${t('units.u1_spool')} · ${material}` : t('units.u1_spool'));

// A spool's material, or "unknown" while none is known.
/** @param {Words} t @param {string | null | undefined} material */
export const materialWord = (t, material) => material ?? t('units.no_material');

// What a bay says to a screen reader: its spool, and its error after a comma while it is in error.
/** @param {string} what @param {string | null | undefined} error */
export const withError = (what, error) => (error ? `${what}, ${error}` : what);

// A filament colour as the style its element reads (--fil); no style while the colour is not known.
/** @param {string | null | undefined} c */
export const fil = (c) => (c ? `--fil:${c}` : undefined);
