// The reading bar of the plan view: what it shows while the host reads a G-code file to plan it. The host gives no
// progress, so the bar is an estimate from the file's size. Measured end to end on the U1 on 2026-11-02, click to plan
// on the page: 6.1 MB in 0.42 s and 258 MB in 12.6 s, i.e. about 0.15 s fixed plus 48 ms per MB.

// Milliseconds the host takes per MB (10^6 bytes) of G-code, from the measurement above.
export const READ_MS_PER_MB = 48;
// Milliseconds before the host starts reading (the round trip and the G-code queue), from the measurement above.
export const READ_BASE_MS = 150;
// A chosen threshold, not a measurement: the bar shows only when the plan has not come this long after the click, so
// a read under about 9 MB never shows it (see READ_MIN_SHOWN_MS for the cutoff in file size).
export const READ_SHOW_MS = 600;
// A chosen threshold, not a measurement: a bar shown for less than half a second reads as a flicker. A read whose
// estimate is under READ_SHOW_MS + this (a file under about 20 MB, (1100 - 150) / 48 MB) shows the bar only once it is
// this far past its estimate.
export const READ_MIN_SHOWN_MS = 500;
// The share the bar stops at once the estimate is used up ("Still reading…"): it never claims to be done.
export const READ_CAP = 0.95;

// The host's estimated reading time of a file of size bytes, in ms (the fixed start plus the size at the measured
// rate); null when the size is not a positive number.
/** @param {number | null | undefined} size @returns {number | null} */
export function estimateMs(size) {
  if (typeof size !== 'number' || !Number.isFinite(size) || size <= 0) return null;
  return READ_BASE_MS + Math.round((size / 1e6) * READ_MS_PER_MB);
}

// What the bar shows elapsed ms after the click: whether it shows, the share filled (null without a size), the whole
// seconds left (null without a size), the whole seconds elapsed, and still (the estimate is used up).
/**
 * @param {number | null | undefined} size
 * @param {number} elapsed
 * @returns {{ show: boolean, share: number | null, leftS: number | null, elapsedS: number, still: boolean }}
 */
export function readView(size, elapsed) {
  const est = estimateMs(size);
  const ms = Math.max(0, elapsed);
  const elapsedS = Math.floor(ms / 1000);
  // Monotone in time: a long read shows from READ_SHOW_MS; a short estimate only once well past it.
  const show = ms >= (est === null || est - READ_SHOW_MS >= READ_MIN_SHOWN_MS ? READ_SHOW_MS : est + READ_MIN_SHOWN_MS);
  if (est === null) return { show, share: null, leftS: null, elapsedS, still: false };
  const still = ms >= est;
  return {
    show,
    share: still ? READ_CAP : Math.min(READ_CAP, ms / est),
    leftS: Math.max(0, Math.ceil((est - ms) / 1000)),
    elapsedS,
    still,
  };
}
