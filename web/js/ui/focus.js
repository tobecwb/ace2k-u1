// The page's focus handling: into the side panel when it opens, back to what opened it, and through the spool
// editor of a bay card. The panel and the card both move it in layout effects, so they do not fight.

/** @typedef {{ opener: { focus: () => void, isConnected?: boolean } | null }} FocusMemo */
/**
 * What focus handling needs of the page: the focused element, and the key events of the document.
 * @typedef {{ activeElement: Element | null, addEventListener: (type: string, fn: (e: KeyboardEvent) => void) => void,
 *   removeEventListener: (type: string, fn: (e: KeyboardEvent) => void) => void }} FocusDoc
 */

// The fields that keep the focus while the page changes around them: typing there is never interrupted.
const TYPING = ['INPUT', 'SELECT', 'TEXTAREA'];

// Moves the focus into the panel: to the element marked data-autofocus (the dryer of a unit opened on it),
// else to the panel itself. The element that had the focus outside the panel is kept, to get it back later.
// A panel that opened by itself (auto) leaves the focus where it is, and so does a field being typed in.
/** @param {HTMLElement | null} el @param {FocusDoc} doc @param {FocusMemo} memo @param {boolean} [auto] */
export function focusIn(el, doc, memo, auto = false) {
  const active = doc.activeElement;
  if (!el || auto || TYPING.includes(active?.tagName ?? '')) return;
  if (active && !el.contains(active)) memo.opener = /** @type {HTMLElement} */ (active);
  const target = /** @type {HTMLElement | null} */ (el.querySelector('[data-autofocus]')) ?? el;
  target.focus();
}

// Gives the focus back to the element that opened the panel, while it is still on the page.
/** @param {FocusMemo} memo */
export function focusBack(memo) {
  if (memo.opener && memo.opener.isConnected !== false) memo.opener.focus();
}

// Keeps the focus through the spool editor: it opening puts the focus on the form's data-autofocus button (the
// chosen material), else its first button, else the panel; it closing (Cancel, or Save done) puts it on the card's
// Edit spool button, which the card draws again then, else on the panel, as focusIn does. A close moves the focus
// only when it was lost (on the page's body): a live control of the card keeps it. A card that mounts with the form
// open leaves the focus to the panel's own focusIn, which takes the same mark.
// was is what the last call saw; doc is the page's document.
/** @param {HTMLElement | null} root @param {boolean} open @param {{ current: boolean }} was
 *  @param {{ activeElement: Element | null, body?: Element | null }} [doc] */
export function focusEditor(root, open, was, doc = globalThis.document) {
  const before = was.current;
  was.current = open;
  if (!root || open === before) return;
  const active = doc?.activeElement ?? null;
  if (!open && active && active !== doc?.body) return;
  /** @param {string} q */
  const find = (q) => root.querySelector(q);
  const panel = root.closest('[tabindex="-1"]'); // the side panel's own frame
  const target = open
    ? (find('.spool-editor [data-autofocus]') ?? find('.spool-editor button') ?? panel)
    : (find('[data-id="edit_spool"]:not(:disabled)') ?? panel);
  /** @type {HTMLElement | null} */ (target)?.focus();
}
