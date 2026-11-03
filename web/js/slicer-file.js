// Reads the colours of an OrcaSlicer file (and its derivatives) from two pieces of it.
// The header, near the start, lists the filaments the file uses ("; filament: 1,2,5"). The config
// block, near the end, lists every filament the slicer had configured, used or not. The module has
// no effects, so it runs under node.

/** @typedef {{ colour: string, material: string | null, vendor: string | null }} FileColour */

// How many bytes of a file the page reads for its colours: the header at the start, the config block at the end.
export const HEAD_BYTES = 8192;
export const TAIL_BYTES = 65536;

// The value of a "; key = a;b;c" line of the config block, split, or null without the line.
/** @param {string} text @param {string} key @returns {string[] | null} */
function listOf(text, key) {
  const m = text.match(new RegExp(`^; ${key} = (.*)$`, 'm'));
  return m ? m[1].split(';').map((x) => x.trim().replace(/^"|"$/g, '')) : null;
}

// The used filaments of the header, 1-based, in file order, or null without the line.
/** @param {string} head @returns {number[] | null} */
export function usedFilaments(head) {
  const m = head.match(/^; filament: ([\d,]+)\s*$/m);
  return m
    ? m[1]
        .split(',')
        .map(Number)
        .filter((n) => n > 0)
    : null;
}

// Every configured filament of the config block, or null without its colour line.
/** @param {string} tail @returns {FileColour[] | null} */
export function configColours(tail) {
  const colours = listOf(tail, 'filament_colour');
  if (!colours) return null;
  const types = listOf(tail, 'filament_type') ?? [];
  const vendors = listOf(tail, 'filament_vendor') ?? [];
  return colours.map((colour, i) => ({ colour, material: types[i] || null, vendor: vendors[i] || null }));
}

// The colours of the filaments the file uses. Without the header line, all configured ones.
// Null when the config block cannot be read.
/** @param {string} head @param {string} tail @returns {FileColour[] | null} */
export function fileColours(head, tail) {
  const all = configColours(tail);
  if (!all) return null;
  const used = usedFilaments(head);
  if (!used) return all;
  return used.map((n) => all[n - 1]).filter(Boolean);
}
