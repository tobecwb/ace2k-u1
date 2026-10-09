// Every visible string goes through t(); the catalogue is web/i18n/<lang>.json.
let catalog = {};

export function setCatalog(next) {
  catalog = next || {};
}

export function t(key, vars = {}) {
  const text = Object.hasOwn(catalog, key) ? catalog[key] : key;
  return text.replace(/\{(\w+)\}/g, (m, name) => (Object.hasOwn(vars, name) ? String(vars[name]) : m));
}

export async function loadCatalog(lang = 'en', fetchFn = globalThis.fetch) {
  const res = await fetchFn(`i18n/${lang}.json`);
  setCatalog(await res.json());
}
