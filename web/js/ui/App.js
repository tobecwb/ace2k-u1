import { html, useEffect, useState } from './html.js';
import { motionMs, presence } from '../motion.js';
import { TopBar } from './TopBar.js';
import { ErrorBoundary } from './ErrorBoundary.js';
import { SidePanel } from './SidePanel.js';
import { Diagram } from './Diagram.js';
import { MobileLists } from './MobileLists.js';
import { FileGrid } from './FileGrid.js';
import { DryerView } from './DryerView.js';
import { HistoryView } from './HistoryView.js';
import { PlanView } from './PlanView.js';
import { Printing } from './Printing.js';
import { otherDevice, planNow, readingOf } from '../state.js';
import { OtherDevice, ReadingView } from './ReadingView.js';
import { printOffered, printing } from '../model.js';

/** @typedef {import('../types.js').AppState} AppState */
/** @typedef {import('../types.js').UiCtx} UiCtx */

// A panel that closed stays for its exit: when leaving, runs done after the motion time (0 with "reduce motion").
// Returns the function that cancels the timer, or nothing.
/** @param {boolean} leaving @param {() => void} done @param {typeof globalThis.matchMedia | undefined} [mm] */
export function exitTimer(leaving, done, mm = globalThis.matchMedia?.bind(globalThis)) {
  if (!leaving) return undefined;
  const id = setTimeout(done, motionMs(mm));
  return () => clearTimeout(id);
}

// The page: the top bar, the active tab, and the side panel when one is open.
// Each part sits in its own boundary, so a part that throws leaves the others working.
// The tab's boundary is keyed by the tab, so a tab that failed starts over when the user comes back to it.
/** @param {{ app: AppState, ctx: UiCtx }} props */
export function App({ app, ctx }) {
  const [pres, setPres] = useState(
    /** @type {import('../motion.js').Presence<Record<string, string>>} */ ({ shown: null, leaving: false }),
  );
  const next = presence(pres, app.ui.panel);
  if (next.shown !== pres.shown || next.leaving !== pres.leaving) setPres(next);
  useEffect(() => exitTimer(next.leaving, () => setPres({ shown: null, leaving: false })), [next.leaving, next.shown]);
  return Shell({ app, ctx, next });
}

// The page's markup for the panel state that App worked out (a pure function, so tests read its tree directly).
/** @param {{ app: AppState, ctx: UiCtx, next: import('../motion.js').Presence<Record<string, string>> }} props */
export function Shell({ app, ctx, next }) {
  const tab = app.ui.tab;
  return html`<div class=${app.ui.panel ? 'shell has-panel' : 'shell'}>
    <${ErrorBoundary}><${TopBar} app=${app} ctx=${ctx} /></${ErrorBoundary}>
    <main class="tab-body" id="tab-body" role="tabpanel" aria-labelledby=${`tab-${tab}`} data-tab=${tab}
      data-enter=${app.ui.enter ?? undefined} key=${tab}>
      <${ErrorBoundary} key=${tab}>${tabBody(tab, app, ctx)}</${ErrorBoundary}>
    </main>
    ${next.shown && app.ui.narrow ? html`<${Scrim} ctx=${ctx} leaving=${next.leaving} />` : null}
    ${next.shown ? html`<${ErrorBoundary}><${SidePanel} app=${app} ctx=${ctx} panel=${next.shown} leaving=${next.leaving} /></${ErrorBoundary}>` : null}
  </div>`;
}

// The shade behind a phone's bottom sheet: it fades in and out with the sheet, and a tap on it closes the sheet.
/** @param {{ ctx: UiCtx, leaving: boolean }} props */
function Scrim({ ctx, leaving }) {
  return html`<div class=${leaving ? 'scrim leaving' : 'scrim'} aria-hidden="true"
    onClick=${() => ctx.run('panel_close', {})}></div>`;
}

// The Print tab with no print running and the adapter offering it: the plan once it has come, else the reading view
// while the host reads the chosen file, else the notice when another device planned another file, else the grid.
/** @param {AppState} app @param {UiCtx} ctx */
function planBody(app, ctx) {
  if (planNow(app)) return html`<${PlanView} app=${app} ctx=${ctx} />`;
  if (readingOf(app)) return html`<${ReadingView} app=${app} ctx=${ctx} />`;
  const theirs = otherDevice(app);
  if (theirs)
    return html`<${OtherDevice} ctx=${ctx} mine=${/** @type {string} */ (app.print.file)} theirs=${theirs} />`;
  return html`<${FileGrid} app=${app} ctx=${ctx} />`;
}

// The body of a tab: the Units tab draws the diagram, the Print tab the running print while one is on, else the
// plan of the chosen file once its plan has come (else planBody's views), the Dryer tab its cards, the History tab the
// chart and the events. While the adapter does not offer Print (printOffered), the Print tab only says why: no file
// grid, so no file is listed or planned.
/** @param {string} tab @param {AppState} app @param {UiCtx} ctx */
function tabBody(tab, app, ctx) {
  if (tab === 'units') return html`<${app.ui.narrow ? MobileLists : Diagram} app=${app} ctx=${ctx} />`;
  if (tab === 'print' && printing(app.status)) return html`<${Printing} app=${app} ctx=${ctx} />`;
  if (tab === 'print' && !printOffered(app.status))
    return html`<p class="placeholder">${ctx.t('print.not_offered')}</p>`;
  if (tab === 'print') return planBody(app, ctx);
  if (tab === 'dryer') return html`<${DryerView} app=${app} ctx=${ctx} />`;
  if (tab === 'history') return html`<${HistoryView} app=${app} ctx=${ctx} />`;
  return html`<p class="placeholder">${ctx.t(`tab.${tab}`)}</p>`;
}
