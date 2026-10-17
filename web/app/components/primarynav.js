// components/primarynav.js — the PRIMARY nav (06 §2: "Primary tabs:
// Leaderboard (A) · Matrix (B) · Compare (E) · Configure weights (D) ·
// Methodology (F)"): one real link per view, to that view's address
// (routes.js), with `aria-current="page"` on the view shown. Distinct from
// views/leaderboard.js's `renderTabs`, the Leaderboard's own Ranked/Not
// ranked links — this component owns none of that screen's internals.
//
// Rendered ONCE by app.js into its own persistent DOM node
// (`#app-primary-nav`, never inside `main`, so switching screens never
// rebuilds it) and kept in sync via `syncPrimaryNav` (attribute writes
// only — never innerHTML on this node after the first render, matching
// U-12's "never innerHTML on an ancestor of focus").

import * as copy from "../copy.js";
import { routeById } from "../routes.js";
import { hrefFor } from "../state.js";

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (ch) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]
  ));
}

//: `state.tab` -> which primary-nav entry is current. "ranked"/"notranked"
//: both belong to the Leaderboard entry (06 §2's sub-tab nesting).
export function primaryScreenForTab(tab) {
  if (tab === "ranked" || tab === "notranked") return "ranked";
  return tab; // "matrix" | "compare" | "configure" | "methodology" | "notfound" (no entry)
}

//: One entry per 06 §2 primary tab: `tab` is the routes.js view it links to.
const ENTRIES = [
  { tab: "ranked", label: () => copy.PRIMARY_NAV_LEADERBOARD },
  { tab: "matrix", label: () => copy.PRIMARY_NAV_MATRIX },
  { tab: "compare", label: () => copy.PRIMARY_NAV_COMPARE },
  { tab: "configure", label: () => copy.PRIMARY_NAV_CONFIGURE },
  { tab: "methodology", label: () => copy.PRIMARY_NAV_METHODOLOGY },
];

/**
 * The nav's links for a state. A view the app draws carries `data-route-tab`, so app.js opens it in place (its
 * href is where that goes: `hrefFor`); the methodology page is a plain link, loaded like any other page.
 */
export function renderPrimaryNav(state) {
  const current = primaryScreenForTab(state.tab);
  const links = ENTRIES.map((entry) => {
    const inApp = routeById(entry.tab).shell;
    return (
      `<a class="primary-tab" href="${escapeHtml(hrefFor(state, entry.tab))}"` +
      (inApp ? ` data-route-tab="${entry.tab}"` : "") +
      (entry.tab === current ? ` aria-current="page"` : "") +
      `>${escapeHtml(entry.label())}</a>`
    );
  }).join("");
  return `<div class="primary-tabs">${links}</div>`;
}

/**
 * The strip's `scrollLeft` that puts the current link in the middle of the part you can see, clamped to the strip.
 * On a phone the five links are wider than the screen; without this a view opened by its address (Configure
 * weights) is off the right edge.
 */
export function centeredScrollLeft(tabLeft, tabWidth, viewWidth, contentWidth) {
  const target = tabLeft + tabWidth / 2 - viewWidth / 2;
  return Math.max(0, Math.min(target, Math.max(0, contentWidth - viewWidth)));
}

/** Scrolls the strip so the current link is visible. A no-op when the strip is not wider than its box. */
export function centerActiveTab(container) {
  const strip = container.querySelector(".primary-tabs");
  const active = container.querySelector('[aria-current="page"]');
  if (!strip || !active || strip.scrollWidth <= strip.clientWidth) return;
  strip.scrollLeft = centeredScrollLeft(active.offsetLeft, active.offsetWidth, strip.clientWidth, strip.scrollWidth);
}

/** Patches each link's href and `aria-current` on the persistent nav — never a re-render. */
export function syncPrimaryNav(container, state) {
  const current = primaryScreenForTab(state.tab);
  let changed = false;
  container.querySelectorAll(".primary-tab").forEach((link, i) => {
    const entry = ENTRIES[i];
    if (!entry) return;
    const href = hrefFor(state, entry.tab);
    if (link.getAttribute("href") !== href) link.setAttribute("href", href);
    const isCurrent = entry.tab === current;
    if ((link.getAttribute("aria-current") === "page") !== isCurrent) {
      if (isCurrent) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
      changed = true;
    }
  });
  if (changed) centerActiveTab(container); // only when the view changed: never fight the reader's own scrolling
}
