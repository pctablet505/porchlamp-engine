// components/chrome.js — the one header, primary nav and footer of every page Porchlamp serves (owner, 2026-10-17:
// one view per thing, drawn once). app.js draws them on the app's pages and keeps them in step with the reader;
// web/prerender.mjs draws them at export into every page the site serves, the app's views, the report, the project
// pages, /check/ and 404 alike. The render functions read nothing from the page, so the browser and Node
// get the same bytes from the same data.
//
// The nav is real links (W-07): `aria-current="page"` on the entry of the page shown, never role=tab. A link to a
// view the app draws carries `data-route-tab`, so app.js opens it in place; any other link loads its page. The theme
// toggle's behaviour is theme-init.js's, on every page; the snapshot's fields are hooks (components/fill.js) the
// verifier reads back.

import * as copy from "../copy.js";
import { renderBrand } from "./brand.js";
import { hook } from "./fill.js";
import { escapeHtml } from "../escape.js";
import { routeById, routePath } from "../routes.js";
import { PERSONA_PRESET_WEIGHTS, hrefFor } from "../state.js";

/** The primary nav's entries, in order (routes.js ids): each view's own, then the current report and the lookup, pages
 *  of their own the app does not draw. */
export const NAV = Object.freeze(
  [
    { id: "ranked", label: () => copy.PRIMARY_NAV_LEADERBOARD },
    { id: "matrix", label: () => copy.PRIMARY_NAV_MATRIX },
    { id: "compare", label: () => copy.PRIMARY_NAV_COMPARE },
    { id: "configure", label: () => copy.PRIMARY_NAV_CONFIGURE },
    { id: "methodology", label: () => copy.PRIMARY_NAV_METHODOLOGY },
    { id: "first-pull-requests", label: () => copy.FOOTER_REPORT_LABEL },
    { id: "lookup", label: () => copy.PRIMARY_NAV_LOOKUP },
  ].map((entry) => Object.freeze(entry))
);

/** The nav entry of a page: a view's (`state.tab`; the leaderboard's two lists share one), or none. */
export function navEntryFor(page) {
  if (page === "ranked" || page === "notranked") return "ranked";
  return NAV.some((entry) => entry.id === page) ? page : null;
}

/** Where an entry goes: from the reader's `state` (state.js hrefFor keeps what that view reads), or, on a page
 *  drawn without one, the view's own address. */
export function navHref(id, state = null) {
  return state ? hrefFor(state, id) : routePath(id);
}

/** The nav's links. `current` is the entry of the page shown (`navEntryFor`), or null. */
export function renderNav(current, state = null) {
  const links = NAV.map((entry) => {
    const route = routeById(entry.id);
    return (
      `<a class="primary-tab" href="${escapeHtml(navHref(entry.id, state))}"` +
      (route && route.shell ? ` data-route-tab="${entry.id}"` : "") +
      (entry.id === current ? ` aria-current="page"` : "") +
      `>${escapeHtml(entry.label())}</a>`
    );
  }).join("");
  return `<div class="primary-tabs">${links}</div>`;
}

//: Which of the header's controls a view shows: the lens where the list or the comparison reads it, the ecosystem
//: where the view filters by it, and the search everywhere but Compare, whose own search box adds a repository.
export function headerControlsFor(view) {
  return {
    persona: view === "ranked" || view === "notranked" || view === "compare",
    ecosystem: view === "ranked" || view === "notranked" || view === "matrix" || view === "configure",
    search: view !== "compare",
  };
}

const hidden = (shown) => (shown ? "" : " hidden");

/** The app's lens and ecosystem selects: the header's controls on a page of the app, each hidden where `view` does
 *  not read it, so switching views in place only shows or hides them. With `state` (a list's page: the prerender and
 *  the app draw the same), the state's lens and ecosystem are the selected options; at the defaults (the first
 *  option of each) nothing is marked. */
export function renderHeaderFilters(view, state = null) {
  const shown = headerControlsFor(view);
  const persona = state && state.persona !== "balanced" ? state.persona : null;
  const ecosystem = state && state.ecosystem ? state.ecosystem : null;
  const picked = (value, chosen) => (value === chosen ? " selected" : "");
  const lenses = Object.keys(PERSONA_PRESET_WEIGHTS)
    .map((lens) => `<option value="${escapeHtml(lens)}"${picked(lens, persona)}>${escapeHtml(copy.PERSONA_NAMES[lens])}</option>`)
    .join("");
  const ecosystems = copy.ECOSYSTEMS.map(
    ([value, label]) => `<option value="${escapeHtml(value)}"${picked(value, ecosystem)}>${escapeHtml(label)}</option>`
  ).join("");
  return (
    `<span class="header-filter-group" data-header-persona-group="true"${hidden(shown.persona)}>` +
    `<label class="field-label" for="persona-select">${escapeHtml(copy.PERSONA_LABEL)}</label>` +
    `<select class="control" id="persona-select" data-persona-select="true">${lenses}</select>` +
    `</span>` +
    `<span class="header-filter-group" data-header-eco-group="true"${hidden(shown.ecosystem)}>` +
    `<label class="field-label" for="eco-select">${escapeHtml(copy.ECOSYSTEM_LABEL)}</label>` +
    `<select class="control" id="eco-select" data-eco-select="true">` +
    `<option value="">${escapeHtml(copy.ECOSYSTEM_ALL)}</option>${ecosystems}</select>` +
    `</span>`
  );
}

/**
 * The snapshot chip: the snapshot's id, with the exact benchmark version one hover away; "Loading…" before it has
 * loaded, and the error when it could not. `fields` is state.js footerFieldsFor's answer.
 */
export function renderChip(fields) {
  if (fields.kind === "ok") {
    const { snapshot_id: id, benchmark_version: version } = fields.snapshot;
    return (
      `<span class="snapshot-chip" data-snapshot-chip="true" title="${escapeHtml(copy.snapshotChipTitle(id, version))}">` +
      `${hook("header.snapshot_id", id)}</span>`
    );
  }
  if (fields.kind === "error") {
    return `<span class="snapshot-chip error-chip" data-snapshot-chip="true">${escapeHtml(copy.errorBannerText(fields.endpoint, fields.status, fields.detail))}</span>`;
  }
  return `<span class="snapshot-chip" data-snapshot-chip="true">${escapeHtml(copy.LOADING_LABEL)}</span>`;
}

/**
 * The header: the brand (a link to the rankings), the snapshot chip, the page's own `controls` (the app's selects;
 * none on a page the app does not draw), the search and the theme toggle. The search is a real form to the
 * rankings, so it works before the app has started and on pages without it.
 */
export const THEME_TOGGLE_ICONS_SVG =
  `<svg class="theme-icon theme-icon-dark" viewBox="0 0 24 24" aria-hidden="true" focusable="false" fill="none" ` +
  `stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">` +
  `<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>` +
  `</svg>` +
  `<svg class="theme-icon theme-icon-light" viewBox="0 0 24 24" aria-hidden="true" focusable="false" fill="none" ` +
  `stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">` +
  `<circle cx="12" cy="12" r="4.5"/>` +
  `<path d="M12 2v2.5M12 19.5V22M4.93 4.93l1.77 1.77M17.3 17.3l1.77 1.77M2 12h2.5M19.5 12H22M4.93 19.07l1.77-1.77M17.3 6.7l1.77-1.77"/>` +
  `</svg>`;

export function renderHeader({ snapshot = null, controls = "", search = true } = {}) {
  const home = escapeHtml(routePath("ranked"));
  return (
    `<a class="brand-link" href="${home}" aria-label="${escapeHtml(copy.BRAND_NAME)}">${renderBrand()}</a>` +
    renderChip(snapshot ? { kind: "ok", snapshot } : { kind: "loading" }) +
    `<div class="header-controls">` +
    controls +
    `<form class="header-filter-group" data-header-search-group="true" role="search" action="${home}" method="get"${hidden(search)}>` +
    `<input class="control" type="search" name="q" data-search-input="true" placeholder="${escapeHtml(copy.SEARCH_PLACEHOLDER)}" aria-label="${escapeHtml(copy.SEARCH_LABEL)}">` +
    `</form>` +
    `<button type="button" class="icon-button theme-toggle" data-theme-toggle="true" aria-label="${escapeHtml(copy.THEME_TOGGLE_LABEL)}" title="${escapeHtml(copy.THEME_TOGGLE_LABEL)}">${THEME_TOGGLE_ICONS_SVG}</button>` +
    `</div>`
  );
}

/** The header of a page of the app showing `view`; with `state`, its selects show the state's lens and ecosystem. */
export function renderAppHeader(view, snapshot = null, state = null) {
  return renderHeader({ snapshot, controls: renderHeaderFilters(view, state), search: headerControlsFor(view).search });
}

/** The markup inside the footer's four snapshot fields (keyed as their `data-footer-*` attributes). */
export function footerFieldsHtml(fields) {
  if (fields.kind === "ok") {
    const s = fields.snapshot;
    return {
      snapshot: `${escapeHtml(copy.FOOTER_SNAPSHOT_LABEL)}: ${hook("footer.snapshot_id", s.snapshot_id)}`,
      version: `${escapeHtml(copy.FOOTER_VERSION_LABEL)}: ${hook("footer.benchmark_version", s.benchmark_version)}`,
      hash: `${escapeHtml(copy.FOOTER_CONSTANTS_HASH_LABEL)}: ${hook("footer.constants_hash", copy.shortHashText(s.constants_hash))}…`,
      manifest: `${escapeHtml(copy.FOOTER_MANIFEST_ROOT_LABEL)}: ${hook("footer.manifest_root", copy.shortHashText(s.manifest_root))}…`,
    };
  }
  const first = fields.kind === "error" ? copy.errorBannerText(fields.endpoint, fields.status, fields.detail) : copy.LOADING_LABEL;
  return { snapshot: escapeHtml(first), version: "", hash: "", manifest: "" };
}

/** The footer: the snapshot's identity (with a retry when it could not load), then the methodology, the report and
 *  the data. `fields` is state.js footerFieldsFor's answer. */
export function renderFooter(fields) {
  const f = footerFieldsHtml(fields);
  return (
    `<span data-footer-snapshot="true">${f.snapshot}</span>` +
    `<span data-footer-version="true">${f.version}</span>` +
    `<span data-footer-hash="true">${f.hash}</span>` +
    `<span data-footer-manifest="true">${f.manifest}</span>` +
    `<button type="button" class="icon-button" data-footer-retry="true"${hidden(fields.kind === "error")}>${escapeHtml(copy.RETRY_LABEL)}</button>` +
    `<a href="${escapeHtml(routePath("methodology"))}" data-route-tab="methodology">${escapeHtml(copy.FOOTER_METHODOLOGY_LABEL)}</a>` +
    `<a href="${escapeHtml(copy.REPORT_PAGE_HREF)}">${escapeHtml(copy.FOOTER_REPORT_LABEL)}</a>` +
    `<a href="${escapeHtml(copy.FOOTER_DATA_HREF)}">${escapeHtml(copy.FOOTER_DATA_LABEL)}</a>`
  );
}

// ------------------------------------------------------------ the nav on a phone

/**
 * The strip's `scrollLeft` that puts the current link in the middle of the part you can see, clamped to the strip.
 * On a phone the links are wider than the screen; without this a view opened by its address is off the right edge.
 * (On a page the app does not draw, components.css does the same once, with scroll-initial-target.)
 */
export function centeredScrollLeft(tabLeft, tabWidth, viewWidth, contentWidth) {
  const target = tabLeft + tabWidth / 2 - viewWidth / 2;
  return Math.max(0, Math.min(target, Math.max(0, contentWidth - viewWidth)));
}

/** Scrolls the strip so the current link is visible. A no-op when the strip is not wider than its box. */
export function centerCurrentLink(container) {
  const strip = container.querySelector(".primary-tabs");
  const current = container.querySelector('[aria-current="page"]');
  if (!strip || !current || strip.scrollWidth <= strip.clientWidth) return;
  strip.scrollLeft = centeredScrollLeft(current.offsetLeft, current.offsetWidth, strip.clientWidth, strip.scrollWidth);
}

/** Patches each link's href and `aria-current` on the persistent nav (never a re-render), then shows the current
 *  link if it changed: never fight the reader's own scrolling. */
export function syncNav(container, current, state) {
  let changed = false;
  container.querySelectorAll(".primary-tab").forEach((link, i) => {
    const entry = NAV[i];
    if (!entry) return;
    const href = navHref(entry.id, state);
    if (link.getAttribute("href") !== href) link.setAttribute("href", href);
    const isCurrent = entry.id === current;
    if ((link.getAttribute("aria-current") === "page") !== isCurrent) {
      if (isCurrent) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
      changed = true;
    }
  });
  if (changed) centerCurrentLink(container);
}
