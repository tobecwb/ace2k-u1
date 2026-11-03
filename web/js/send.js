// Sends a G-code script, and records a refusal.
// ctx holds the page state, the effects and sendScript itself, so a test can stand in for each of them.
import { gcodeFor, RefusedError } from './model.js';
import { appendEvent } from './history.js';
import { pkey, unitNames } from './state.js';

/** @typedef {import('./types.js').Ctx} Ctx */
/** @typedef {import('./types.js').SendOpts} SendOpts */
/** @typedef {import('./conn.js').Connection} Connection */

// Returns the script that build() returns.
// When build() throws, it shows the refusal toast with the reason, and returns null.
/** @template T @param {Ctx} ctx @param {() => T} build @returns {T | null} */
export function built(ctx, build) {
  try {
    return build();
  } catch (e) {
    const err = /** @type {Error} */ (e);
    ctx.toast(ctx.t('toast.refused', { msg: err instanceof RefusedError ? ctx.t(err.key, err.vars) : err.message }));
    return null;
  }
}

// Builds the G-code of the action id and sends it.
// Returns true when the command was handed over, or shown in the demo. Returns false when refused or failed.
// key is the pending key, by default `id:n`.
// When quiet(msg) returns true, the caller handles the refusal itself, and nothing shows it.
// unit is the unit's name, by default the first unit, and null for [ace2k].
// A unit with a name is carried as UNIT=.
/**
 * @param {Ctx} ctx
 * @param {string} id
 * @param {number | null} n
 * @param {import('./model.js').GcodeArgs} args
 * @param {SendOpts} [opts]
 */
export async function send(
  ctx,
  id,
  n,
  args,
  { key = `${id}:${n ?? ''}`, quiet = null, unit = unitNames(ctx.app)[0] } = {},
) {
  const script = built(ctx, () => gcodeFor(id, n, args, unit));
  if (script === null) return false;
  return ctx.sendScript(script, n, { key: pkey(ctx.app, unit, key), quiet, unit });
}

// Sends script as it is, under the pending key. send builds the script, and Stop all decides it before its dialog.
// unit is lane n's unit. A refusal is filed under the lane only for the first unit, the page's main unit.
// With once, no second request goes out under key while one is in flight. The start of a print uses it.
/** @param {Ctx} ctx @param {string} script @param {number | null} n @param {SendOpts & { key: string }} opts */
export async function sendScript(ctx, script, n, { key, quiet = null, unit = unitNames(ctx.app)[0], once = false }) {
  const { app } = ctx;
  if (ctx.demo) {
    ctx.toast(`${ctx.t('conn.demo')}: ${script}`);
    return true;
  }
  app.pending.add(key);
  ctx.render();
  try {
    // app.conn is null only before the first connect.
    // Reading it then throws, and the catch below treats that as a refusal.
    const conn = /** @type {Connection} */ (app.conn);
    await (once ? conn.gcodeOnce(key, script) : conn.gcode(script));
    return true;
  } catch (e) {
    const { message } = /** @type {Error} */ (e);
    if (quiet?.(message)) return false;
    ctx.toast(ctx.t('toast.refused', { msg: message }));
    app.events = appendEvent(app.events, {
      t: ctx.now(),
      k: 'e',
      lvl: 'err',
      src: n && unit === unitNames(app)[0] ? `lane${n}` : 'unit',
      msg: message,
    });
    return false;
  } finally {
    app.pending.delete(key);
    ctx.render();
  }
}
