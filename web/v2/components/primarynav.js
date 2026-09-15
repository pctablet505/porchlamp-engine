// components/primarynav.js — the PRIMARY tab bar (06 §2: "Primary tabs:
// Leaderboard (A) · Matrix (B) · Compare (E) · Configure weights (D) ·
// Methodology (F)"). Distinct from views/leaderboard.js's `renderTabs`,
// which is the Leaderboard screen's OWN Ranked/Not-ranked sub-tab bar —
// this component owns none of that screen's internals.
//
// Rendered ONCE by app.js into its own persistent DOM node
// (`#app-primary-nav`, never inside `main`, so switching screens never
// rebuilds it) and kept in sync via `syncPrimaryNav` (an `aria-selected`
// attribute flip only — never innerHTML on this node after the first
// render, matching U-12's "never innerHTML on an ancestor of focus").

import * as copy from "../copy.js";

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (ch) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]
  ));
}

//: `state.tab` -> which primary-nav entry is active. "ranked"/"notranked"
//: both belong to the "leaderboard" primary tab (06 §2's sub-tab nesting).
export function primaryScreenForTab(tab) {
  if (tab === "ranked" || tab === "notranked") return "leaderboard";
  return tab; // "matrix" | "compare" | "configure" | "methodology"
}

//: One entry per 06 §2 primary tab: `id` is the SCREEN id (see
//: `primaryScreenForTab`), `tab` is the `state.tab` value clicking it sets.
const ENTRIES = [
  { id: "leaderboard", tab: "ranked", label: () => copy.PRIMARY_NAV_LEADERBOARD },
  { id: "matrix", tab: "matrix", label: () => copy.PRIMARY_NAV_MATRIX },
  { id: "compare", tab: "compare", label: () => copy.PRIMARY_NAV_COMPARE },
  { id: "configure", tab: "configure", label: () => copy.PRIMARY_NAV_CONFIGURE },
  { id: "methodology", tab: "methodology", label: () => copy.PRIMARY_NAV_METHODOLOGY },
];

export function renderPrimaryNav(activeTab) {
  const activeScreen = primaryScreenForTab(activeTab);
  const buttons = ENTRIES.map(
    (entry) =>
      `<button type="button" class="primary-tab" role="tab" aria-selected="${entry.id === activeScreen}" ` +
      `data-primary-tab="${entry.tab}">${escapeHtml(entry.label())}</button>`
  ).join("");
  return `<div class="primary-tabs" role="tablist" aria-label="${escapeHtml(copy.PRIMARY_NAV_LABEL)}">${buttons}</div>`;
}

/** Patches `aria-selected` on the persistent primary-nav buttons — never a re-render. */
export function syncPrimaryNav(container, activeTab) {
  const activeScreen = primaryScreenForTab(activeTab);
  for (const btn of container.querySelectorAll("[data-primary-tab]")) {
    const entry = ENTRIES.find((e) => e.tab === btn.dataset.primaryTab);
    const selected = String(entry.id === activeScreen);
    if (btn.getAttribute("aria-selected") !== selected) btn.setAttribute("aria-selected", selected);
  }
}
