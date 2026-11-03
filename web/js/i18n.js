// Every visible string goes through t(). The catalogue is web/i18n/<lang>.json.
/** @type {Record<string, string>} */
let catalog = {};

/** @param {Record<string, string> | null | undefined} next */
export function setCatalog(next) {
  catalog = next || {};
}

/**
 * @param {string} key
 * @param {Record<string, unknown>} [vars]
 */
export function t(key, vars = {}) {
  const text = Object.hasOwn(catalog, key) ? catalog[key] : key;
  return text.replace(/\{(\w+)\}/g, (/** @type {string} */ m, /** @type {string} */ name) =>
    Object.hasOwn(vars, name) ? String(vars[name]) : m,
  );
}

/**
 * @param {string} [lang]
 * @param {typeof fetch} [fetchFn]
 */
export async function loadCatalog(lang = 'en', fetchFn = globalThis.fetch) {
  const res = await fetchFn(`i18n/${lang}.json`);
  setCatalog(await res.json());
}
