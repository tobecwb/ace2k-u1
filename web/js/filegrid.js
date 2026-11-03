// The file grid's pure part: which files show, in what order, and how well a file's colours fit the spools.
// The page keeps no job history, so there is no "Printed before" order.
import { fitsColour } from './print-plan.js';

/** @typedef {{ path: string, modified: number }} GridFile */
/** @typedef {import('./slicer-file.js').FileColour} FileColour */

// The files in the order asked: newest first, or by name without the case. The list given is not changed.
/** @param {GridFile[]} files @param {'new' | 'name'} sort @returns {GridFile[]} */
export function sortFiles(files, sort) {
  const list = [...files];
  if (sort === 'name') return list.sort((a, b) => a.path.localeCompare(b.path, undefined, { sensitivity: 'base' }));
  return list.sort((a, b) => b.modified - a.modified);
}

// The files whose name holds the text, without the case. Empty text keeps all.
/** @param {GridFile[]} files @param {string} q @returns {GridFile[]} */
export function filterFiles(files, q) {
  const needle = q.trim().toLowerCase();
  return needle ? files.filter((f) => f.path.toLowerCase().includes(needle)) : files;
}

// How many of the file's colours have no spool of the same material within the close distance in the
// live lanes. A lane with an empty bay or an unknown spool never matches.
/** @param {FileColour[]} colours @param {import('./types.js').PlanLane[]} lanes @returns {number} */
export function fitOf(colours, lanes) {
  const spools = lanes.filter((l) => !l.empty && !l.unknown && l.colour);
  return colours.filter((c) => !spools.some((l) => fitsColour(c, l))).length;
}
