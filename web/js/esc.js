// Every dynamic text goes through esc() before it enters the HTML.
// The module sits below the views, so the pure modules can use it too.
/** @param {unknown} value */
export function esc(value) {
  return String(value ?? '').replace(
    /[&<>"']/g,
    (/** @type {string} */ c) =>
      /** @type {Record<string, string>} */ ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[
        c
      ],
  );
}
