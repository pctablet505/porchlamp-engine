// theme-init.js — the reader's theme choice, on every page Porchlamp serves: the app's views, the report, the project
// pages, /check/ and 404 (W-15: the static pages used to ignore it). Each page loads it in <head>, ahead
// of the stylesheets, as a plain script: it runs before the first paint, so a stored choice never shows the other
// theme first, and it is a file of its own because the pages' policy forbids inline script.
//
// It is the one owner of the choice. It reads it (`porchlamp-theme` in localStorage, "light" or "dark"; anything else
// follows the system), applies it as `data-theme` on <html>, which tokens.css themes by, and answers the header's
// toggle (components/chrome.js draws it; components.css draws its icon from the theme in force). After a change it
// fires "porchlamp-themechange" on the document, so the app can redraw what CSS does not restyle (the matrix canvas).
(() => {
  const KEY = "porchlamp-theme";
  //: Where earlier builds kept the choice: moved to KEY on a returning visitor's first page.
  const FORMER_KEY = "porchlamp-v2-theme";
  const root = document.documentElement;

  /** The stored choice, "light", "dark" or "system". A move that cannot be written still applies to this page. */
  function stored() {
    let value = null;
    try {
      value = localStorage.getItem(KEY);
      if (value === null) {
        value = localStorage.getItem(FORMER_KEY);
        if (value !== null) {
          localStorage.setItem(KEY, value);
          localStorage.removeItem(FORMER_KEY);
        }
      }
    } catch {
      // Storage unavailable (private mode, blocked site data), or the move could not be written.
    }
    return value === "light" || value === "dark" ? value : "system";
  }

  /** The theme in force: the choice, else the system's (tokens.css is dark unless the system asks for light). */
  function current() {
    const chosen = root.dataset.theme;
    if (chosen === "light" || chosen === "dark") return chosen;
    return typeof matchMedia === "function" && matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
  }

  const theme = stored();
  if (theme !== "system") root.dataset.theme = theme;

  document.addEventListener("click", (evt) => {
    const toggle = evt.target && typeof evt.target.closest === "function" ? evt.target.closest("[data-theme-toggle]") : null;
    if (!toggle) return;
    const next = current() === "dark" ? "light" : "dark";
    root.dataset.theme = next;
    try {
      localStorage.setItem(KEY, next);
    } catch {
      // Storage unavailable: the theme changes for this page only.
    }
    document.dispatchEvent(new CustomEvent("porchlamp-themechange", { detail: { theme: next } }));
  });
})();
