// The demo of the Print tab: its files, cards and plans, from dev/demo-files.json, and the plan of a file put where
// the adapter's plan goes. Only the demo (?demo) uses it. The live page never does.
import { endReading, livePlan } from './state.js';

/** @typedef {import('./types.js').Ctx} Ctx */

// The demo's file list and cards, from dev/demo-files.json. It is read once. A release has no dev/ folder,
// and then the demo has no files.
/**
 * The demo's files, their cards and the plans of some of them.
 * @typedef {{ files: { path: string, modified: number, size?: number }[], cards: Record<string, any>,
 *   plans?: Record<string, import('./types.js').Plan & { delayMs?: number }> }} DemoFiles
 */
/** @type {Promise<DemoFiles> | null} */
let demoData = null;
// Forgets the demo's data, so the next use reads it again (tests that count the reads).
/** @returns {void} */
export function forgetDemoFiles() {
  demoData = null;
}
/** @returns {Promise<DemoFiles>} */
export function demoFiles() {
  demoData ??= globalThis
    .fetch('dev/demo-files.json')
    .then((res) => res.json())
    .catch(() => ({ files: [], cards: {} }));
  return demoData;
}

// A demo plan as the adapter's plan is: without delayMs, which only the demo's reading uses.
/** @param {import('./types.js').Plan & { delayMs?: number }} plan @returns {import('./types.js').Plan} */
function liveOf(plan) {
  const { delayMs: _, ...live } = plan;
  return live;
}

// The demo's plan of a file, put where the adapter's plan goes (ace2k_u1.plan). A file the demo has no plan
// for leaves the status as it is, so the grid stays.
/** @param {Ctx} ctx @param {string} file */
export async function demoPlan(ctx, file) {
  const { app } = ctx;
  // The reading starts at the click, before the demo's data is read, so no render shows the grid or a notice meanwhile.
  app.print = { ...app.print, reading: { file, at: Math.round(ctx.now() * 1000) } };
  const plan = (await demoFiles()).plans?.[file];
  if (!plan) {
    if (app.print.reading?.file === file) app.print = { ...app.print, reading: null };
    return;
  }
  const { delayMs } = plan;
  // A demo plan with delayMs stands for a big file the host takes long to read: the reading bar shows meanwhile.
  if (delayMs) {
    app.pending.add('print:file');
    ctx.render();
    await ctx.sleep(delayMs);
    app.pending.delete('print:file');
  }
  if (app.print.file !== file) return; // Another file was chosen meanwhile.
  app.status = { ...app.status, ace2k_u1: { ...app.status.ace2k_u1, plan: liveOf(plan) } };
  endReading(app);
  ctx.render();
}

// How long the demo's ?other= waits for the first file's plan to be set: that plan's own delayMs, plus this margin
// (ms) for the file list, the card and the render.
export const DEMO_OTHER_MARGIN_MS = 5000;
const DEMO_OTHER_POLL_MS = 50;

// The demo's ?other=: once file's plan is the live one, another file's becomes the live plan, as when another device
// plans it (the notice, for screenshots). It waits at most file's delayMs plus DEMO_OTHER_MARGIN_MS; with no plan for
// other, or file's plan never set, the status stays as it is.
/** @param {Ctx} ctx @param {string} file @param {string} other */
export async function demoOtherPlan(ctx, file, other) {
  const { app } = ctx;
  const plans = (await demoFiles()).plans;
  const limit = (plans?.[file]?.delayMs ?? 0) + DEMO_OTHER_MARGIN_MS;
  for (let waited = 0; waited < limit && livePlan(app)?.file !== file; waited += DEMO_OTHER_POLL_MS)
    await ctx.sleep(DEMO_OTHER_POLL_MS);
  const plan = plans?.[other];
  if (!plan || livePlan(app)?.file !== file) return;
  app.status = { ...app.status, ace2k_u1: { ...app.status.ace2k_u1, plan: liveOf(plan) } };
  ctx.render();
}
