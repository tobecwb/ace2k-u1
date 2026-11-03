// Sets the chosen theme on <html> before the page's styles load.
// So a theme that differs from the system theme does not flash.
// It is a classic script, without module or defer, so it runs first. main.js applies the same choice again.
// The key and the values are THEME_KEY and readTheme of js/state.js. tests/theme-boot.test.js keeps them in step.
(() => {
  try {
    const stored = localStorage.getItem('ace2k.theme');
    if (stored === 'light' || stored === 'dark') document.documentElement.setAttribute('data-theme', stored);
  } catch {
    /* With storage blocked, the page follows the system. */
  }
})();
