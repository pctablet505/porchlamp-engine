// app.js — bootstrap, router, event wiring (06 §6, U-12..U-19, and the
// owner's 2026-09-13 pagination instruction). The only module that
// touches the DOM directly.
//
// Rendering model (U-12): the header, footer and each list's structural
// shell (tabs/pager/table-skeleton or card-container) are built ONCE per
// structural shape — never on scroll, never on a refetch, never on a
// per-row data change. Every other update calls `patchTableBody`/
// `patchCardBody`/`patchNotRankedBody`, which diff the CURRENT PAGE's row
// list into the persistent container via `patchRows`
// (components/patch.js): existing row nodes are reused (their focus,
// listeners and scroll position survive), only genuinely new/removed/
// moved rows touch the DOM, and a row that stays but changes only gets the
// specific `<td>`s that changed rewritten.
//
// Pagination (owner instruction 2026-09-13, replacing an earlier
// no-pagination ruling): one API page is loaded at a time — default 20,
// selectable 20/50/100/500/1000 (`state.PAGE_SIZES`). Sort, search and
// ecosystem-filter changes now go through the API too (U-14 as originally
// written assumed the whole list was loaded client-side; with only one
// page loaded, only the SERVER can see the full result set to sort/filter
// it — see the handoff's "sort/search/filter are fetches" note). The page
// itself scrolls (no inner scroll box): windowing for the two large page
// sizes (500/1000) is driven by the table/card container's position in the
// document (`getBoundingClientRect()`), not a bounded `overflow` box.

import * as copy from "./copy.js";
import * as api from "./api.js";
import {
  createStore,
  parseSearch,
  serializeSearch,
  requestSignature,
  readPersistedTheme,
  writePersistedTheme,
  countRankChanges,
  shouldRestoreFocusAfterRebuild,
  resolveOpenTargetKey,
  footerFieldsFor,
  repoToLoadFromState,
  urlSyncMode,
  PAGE_SIZES,
  DEFAULT_PAGE_SIZE,
  PILLAR_IDS,
  BALANCED_WEIGHTS,
  PERSONA_PRESET_WEIGHTS,
  MATRIX_QUADRANTS,
  weightsEqual,
} from "./state.js";
import {
  renderShell,
  shellShape,
  patchTableBody,
  patchCardBody,
  syncPager,
  syncMobileSortBar,
  scrollDeltaToReveal,
  personaDeltaForState,
  personaVisibleForState,
} from "./views/leaderboard.js";
import {
  renderShell as renderNotRankedShell,
  shellShape as notRankedShellShape,
  rebuildKey as notRankedRebuildKey,
  patchNotRankedBody,
  flattenGroups,
} from "./views/notranked.js";
import { renderDrawer, classifyKindForLens } from "./views/drawer.js";
import { renderBrand } from "./components/brand.js";
import { renderKeymapOverlay, actionForKeyEvent } from "./components/keymap.js";
import { computeVisibleRange, rowKey, ROW_HEIGHT_PX, CARD_HEIGHT_PX, sortDirectionLabel, sortColumnLabel, syncHeaderSortDom } from "./components/table.js";
import { synthesizeRows } from "./perf-fixtures.js";
import { renderPrimaryNav, syncPrimaryNav, centerActiveTab } from "./components/primarynav.js";
import {
  renderShell as renderMatrixShell,
  shellShape as matrixShellShape,
  syncViewBox as syncMatrixViewBox,
  syncMatrixHover,
  filterMatrixPoints,
  zoomView,
  panView,
  clientToDomainCoords,
  nearestMarkerInDirection,
  hitTestPoint,
  DEFAULT_VIEW as MATRIX_DEFAULT_VIEW,
} from "./views/matrix.js";
import { pointKey as matrixPointKey, drawMatrixCanvas, SVG_MARKER_BUDGET } from "./components/scatter.js";
import {
  renderShell as renderConfigureShell,
  shellShape as configureShellShape,
  patchConfigureTable,
  normalizeWeights,
  applyPresetRespectingLocks,
  filterReprojectRows,
  sortReprojectRows,
  REPROJECT_PAGE_SIZE,
  remainingReprojectPages,
  mergeReprojectPages,
} from "./views/configure.js";
import {
  renderShell as renderCompareShell,
  shellShape as compareShellShape,
  shellKey as compareShellKey,
  patchResults as patchCompareResults,
  patchTable as patchCompareTable,
} from "./views/compare.js";
import { renderShell as renderMethodologyShell, shellShape as methodologyShellShape } from "./views/methodology.js";

const PERSONAS = ["balanced", "first_timer", "career_capital", "senior_systems", "ai_assisted"];

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (ch) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]
  ));
}

export function boot(win = window, doc = document) {
  const controller = typeof AbortController !== "undefined" ? new AbortController() : null;
  const signal = controller ? controller.signal : undefined;
  const listenerOpts = (opts = {}) => (signal ? { ...opts, signal } : opts);

  const store = createStore(win.location.search);

  const header = doc.getElementById("app-header-controls");
  const main = doc.getElementById("app-main");
  const footer = doc.getElementById("app-footer");
  const liveRegion = doc.getElementById("app-live-region");
  const drawerRoot = doc.getElementById("app-drawer-root");
  const keymapRoot = doc.getElementById("app-keymap-root");
  const primaryNav = doc.getElementById("app-primary-nav");

  // --- Structural bookkeeping (never re-derived from the DOM: these are
  // the "did the SHAPE change" flags that decide shell-rebuild vs patch). ---
  let currentTab = null;
  let currentShellShape = null;
  let currentNotRankedShape = null;
  let currentMatrixShape = null;
  let currentConfigureShape = null;
  let currentCompareShape = null;
  let currentMethodologyShape = null;
  let scrollPatchScheduled = false;
  let wasDrawerOpen = false;
  let wasKeymapOpen = false;
  let keymapTriggerEl = null;
  let searchDirty = false;
  let searchDebounce = null;
  let compareSearchDebounce = null;
  let reprojectDebounce = null;
  let methodologyCopyTimer = null;
  let badgeCopyTimer = null;
  let inFlightSignature = null; // guards against a stale response clobbering a newer one
  let reprojectInFlightSignature = null;

  function announce(text) {
    store.dispatch({ type: "ANNOUNCE", text });
    if (liveRegion) liveRegion.textContent = text;
  }

  function syncStickyOffsets() {
    if (!doc || !doc.documentElement || !doc.documentElement.style || typeof win.getComputedStyle !== "function") return;
    const hRect = header && typeof header.getBoundingClientRect === "function" ? header.getBoundingClientRect() : { height: 0 };
    const nRect = primaryNav && typeof primaryNav.getBoundingClientRect === "function" ? primaryNav.getBoundingClientRect() : { height: 0 };
    if (hRect.height <= 0 && nRect.height <= 0) return;
    const headerSticky = header && win.getComputedStyle(header).position === "sticky" ? Math.floor(hRect.height) : 0;
    const navSticky = primaryNav && win.getComputedStyle(primaryNav).position === "sticky" ? Math.floor(nRect.height) : 0;
    doc.documentElement.style.setProperty("--header-height", `${headerSticky}px`);
    doc.documentElement.style.setProperty("--primary-nav-height", `${navSticky}px`);
  }

  // ---------------------------------------------------------------- Header
  function buildHeaderOnce() {
    const personaOptions = PERSONAS.map(
      (p) => `<option value="${p}">${escapeHtml(copy.PERSONA_NAMES[p])}</option>`
    ).join("");
    header.innerHTML =
      renderBrand() +
      `<span class="snapshot-chip" data-snapshot-chip="true">${escapeHtml(copy.LOADING_LABEL)}</span>` +
      `<div class="header-controls">` +
      `<span class="header-filter-group" data-header-persona-group="true">` +
      `<label class="field-label" for="persona-select">${escapeHtml(copy.PERSONA_LABEL)}</label>` +
      `<select class="control" id="persona-select" data-persona-select="true">${personaOptions}</select>` +
      `</span>` +
      `<span class="header-filter-group" data-header-eco-group="true">` +
      `<label class="field-label" for="eco-select">${escapeHtml(copy.ECOSYSTEM_LABEL)}</label>` +
      `<select class="control" id="eco-select" data-eco-select="true">` +
      `<option value="">${escapeHtml(copy.ECOSYSTEM_ALL)}</option>` +
      copy.ECOSYSTEMS.map(([value, label]) => `<option value="${value}">${escapeHtml(label)}</option>`).join("") +
      `</select>` +
      `</span>` +
      `<span class="header-filter-group" data-header-search-group="true">` +
      `<input class="control" type="search" data-search-input="true" placeholder="${escapeHtml(copy.SEARCH_PLACEHOLDER)}" aria-label="${escapeHtml(copy.SEARCH_LABEL)}">` +
      `</span>` +
      `<button type="button" class="icon-button" data-theme-toggle="true" aria-label="${escapeHtml(copy.THEME_TOGGLE_LABEL)}">◐</button>` +
      `</div>`;
  }

  // ------------------------------------------------------ Primary nav
  function buildPrimaryNavOnce() {
    primaryNav.innerHTML = renderPrimaryNav(store.getState().tab);
    centerActiveTab(primaryNav);
  }

  /** Fetches whatever `tab` needs that has not already loaded — called on boot, a primary-nav click, popstate, and the footer "verify" link. Idempotent: never re-fetches data that is already present. */
  function loadDataForTab(tab) {
    const state = store.getState();
    const needsLeaderboard =
      !state.leaderboard || state.leaderboardSignature !== requestSignature(state);
    if ((tab === "ranked" || tab === "notranked") && needsLeaderboard && !state.leaderboardLoading && !state.leaderboardRefetching) {
      loadLeaderboard();
    }
    if (tab === "matrix" && !state.matrix && !state.matrixLoading) loadMatrix();
    if (tab === "configure" && !state.reproject && !state.reprojectLoading) runReproject();
    if (tab === "methodology" && !state.methodology && !state.methodologyLoading) loadMethodology();
    if (tab === "compare") {
      if (!state.methodology && !state.methodologyLoading) loadMethodology();
      for (const key of state.compareRepos) {
        if (!state.compareDataByRepo[key]) loadCompareRepo(key);
      }
    }
    if (tab === "notranked" && !state.notRanked && !state.notRankedLoading) loadNotRanked();
  }

  function syncHeaderControls(state) {
    const personaSelect = header.querySelector("[data-persona-select]");
    const ecoSelect = header.querySelector("[data-eco-select]");
    const searchInput = header.querySelector("[data-search-input]");
    const snapshotChip = header.querySelector("[data-snapshot-chip]");
    const personaGroup = header.querySelector("[data-header-persona-group]");
    const ecoGroup = header.querySelector("[data-header-eco-group]");
    const searchGroup = header.querySelector("[data-header-search-group]");
    const showPersona = state.tab === "ranked" || state.tab === "notranked" || state.tab === "compare";
    const showEcoAndSearch = state.tab === "ranked" || state.tab === "notranked" || state.tab === "matrix" || state.tab === "configure";
    if (personaGroup) personaGroup.hidden = !showPersona;
    if (ecoGroup) ecoGroup.hidden = !showEcoAndSearch;
    if (searchGroup) searchGroup.hidden = !showEcoAndSearch;
    if (personaSelect && personaSelect.value !== state.persona) personaSelect.value = state.persona;
    if (ecoSelect && ecoSelect.value !== state.ecosystem) ecoSelect.value = state.ecosystem;
    if (searchInput && doc.activeElement !== searchInput && searchInput.value !== state.query) {
      searchInput.value = state.query;
    }
    if (snapshotChip) {
      // A snapshot fetch failure must not leave "Loading..." indefinitely on the chip.
      const fields = footerFieldsFor(state);
      const text =
        fields.kind === "error"
          ? copy.errorBannerText(fields.endpoint, fields.status, fields.detail)
          : fields.kind === "ok"
            ? fields.snapshot.snapshot_id
            : copy.LOADING_LABEL;
      if (snapshotChip.textContent !== text) snapshotChip.textContent = text;
      // The header names the snapshot; the exact benchmark version (a release-candidate tag today) is one hover
      // away here, and in the footer and on the Methodology page.
      const hover = fields.kind === "ok" ? `${fields.snapshot.snapshot_id} · benchmark ${fields.snapshot.benchmark_version}` : "";
      if (hover) snapshotChip.setAttribute("title", hover);
      else snapshotChip.removeAttribute("title");
      snapshotChip.classList.toggle("error-chip", fields.kind === "error");
    }
    syncStickyOffsets();
  }

  // ---------------------------------------------------------------- Footer
  function buildFooterOnce() {
    footer.innerHTML =
      `<span data-footer-snapshot="true"></span>` +
      `<span data-footer-version="true"></span>` +
      `<span data-footer-hash="true"></span>` +
      `<span data-footer-manifest="true"></span>` +
      `<button type="button" class="icon-button" data-footer-retry="true" hidden>${escapeHtml(copy.RETRY_LABEL)}</button>` +
      `<a href="#methodology" data-footer-verify="true">${escapeHtml(copy.FOOTER_VERIFY_LABEL)}</a>` +
      `<a href="${escapeHtml(copy.REPORT_PAGE_HREF)}" data-footer-report="true">${escapeHtml(copy.FOOTER_REPORT_LABEL)}</a>` +
      `<a href="${escapeHtml(copy.METHODOLOGY_PAGE_HREF)}" data-footer-methodology-page="true">${escapeHtml(copy.FOOTER_METHODOLOGY_LABEL)}</a>`;
  }

  /**
   * Updates the footer view based on state. If `SNAPSHOT_ERROR` was reduced into
   * state, displays the error details instead of a perpetual loading indicator.
   */
  function syncFooter(state) {
    const fields = footerFieldsFor(state);
    const set = (sel, text) => {
      const el2 = footer.querySelector(sel);
      if (el2 && el2.textContent !== text) el2.textContent = text;
    };
    if (fields.kind === "error") {
      set("[data-footer-snapshot]", copy.errorBannerText(fields.endpoint, fields.status, fields.detail));
      set("[data-footer-version]", "");
      set("[data-footer-hash]", "");
      set("[data-footer-manifest]", "");
    } else if (fields.kind === "ok") {
      const snap = fields.snapshot;
      set("[data-footer-snapshot]", `${copy.FOOTER_SNAPSHOT_LABEL}: ${snap.snapshot_id}`);
      set("[data-footer-version]", `${copy.FOOTER_VERSION_LABEL}: ${snap.benchmark_version}`);
      set("[data-footer-hash]", `${copy.FOOTER_CONSTANTS_HASH_LABEL}: ${snap.constants_hash.slice(0, 12)}…`);
      set("[data-footer-manifest]", `${copy.FOOTER_MANIFEST_ROOT_LABEL}: ${snap.manifest_root.slice(0, 12)}…`);
    } else {
      set("[data-footer-snapshot]", copy.LOADING_LABEL);
      set("[data-footer-version]", "");
      set("[data-footer-hash]", "");
      set("[data-footer-manifest]", "");
    }
    const retryBtn = footer.querySelector("[data-footer-retry]");
    if (retryBtn) retryBtn.hidden = fields.kind !== "error";
  }

  /**
   * After a page change the reader is often at the bottom of a long page (they used the pager below the list):
   * bring the top of the list back in under the sticky header, so the new page starts at its first row.
   */
  function revealListTop(selector = ".pager:not(.pager-bottom)") {
    const anchor = main.querySelector(selector);
    if (!anchor || typeof win.scrollTo !== "function") return;
    const headerSticky = header && win.getComputedStyle(header).position === "sticky" ? header.getBoundingClientRect().height : 0;
    const navSticky = primaryNav && win.getComputedStyle(primaryNav).position === "sticky" ? primaryNav.getBoundingClientRect().height : 0;
    const sticky = headerSticky + navSticky;
    const delta = scrollDeltaToReveal(anchor.getBoundingClientRect().top, sticky);
    if (delta !== null) win.scrollTo(0, Math.max(0, win.scrollY + delta));
  }

  // --------------------------------------------------------------- Viewport
  // U-13 (amended): no inner scroll box — the page itself scrolls. Windowing
  // for a loaded page is driven by where the table/card container actually
  // sits in the document, not a bounded `overflow` container's scrollTop.
  function currentViewport() {
    const container = main.querySelector("[data-leaderboard-tbody], [data-leaderboard-cards]");
    const rect = container ? container.getBoundingClientRect() : { top: 0 };
    return {
      scrollTop: Math.max(0, -rect.top),
      height: win.innerHeight || 600,
      isMobile: win.innerWidth < 768,
    };
  }

  /** Updates only the `<th>` / button `aria-sort` attributes and sort arrows to match `state.sort` — never a shell rebuild. */
  function syncSortHeaders(state) {
    syncHeaderSortDom(main, state.sort, doc);
    syncMobileSortBar(main, state.sort);
  }

  function syncCountsStrip(state) {
    // The lens disclosure is computed by views/leaderboard.js's
    // personaDeltaForState — the ONE implementation. This block used to
    // carry its own copy behind a comment claiming the logic was "kept in
    // one place"; the copy had drifted (it rendered only the `> 0` branch,
    // so a lens that ranks MORE repos than balanced produced a negative
    // number and silently rendered nothing). Re-rendering just this small,
    // bounded element — never an ancestor of a focused control — keeps the
    // disclosure right across a persona switch or a late-arriving snapshot
    // without a shell rebuild.
    const strip = main.querySelector("[data-counts-strip]");
    if (!strip || !state.leaderboard) return;
    const delta = personaDeltaForState(state);
    const legend = strip.querySelector(".corpus-legend") || strip;
    const hiddenText = delta
      ? delta.hidden
        ? copy.personaHiddenDisclosure(delta.hidden)
        : copy.personaExtraDisclosure(delta.extra)
      : null;
    const existingHiddenSpan = strip.querySelector("[data-hidden-count]");
    if (hiddenText) {
      if (existingHiddenSpan) {
        if (existingHiddenSpan.textContent !== hiddenText) existingHiddenSpan.textContent = hiddenText;
      } else {
        const span = doc.createElement("span");
        span.className = "corpus-lens";
        span.dataset.hiddenCount = "true";
        span.textContent = hiddenText;
        legend.appendChild(span);
      }
    } else if (existingHiddenSpan) {
      existingHiddenSpan.remove();
    }

    // The Ranked tab count is lens-dependent too (it must equal the row
    // count the pager reports), so it has to be synced here as well.
    const rankedCount = personaVisibleForState(state);
    const rankedCountEl = main.querySelector('[data-tab="ranked"] .count');
    if (rankedCountEl && typeof rankedCount === "number" && rankedCountEl.textContent !== String(rankedCount)) {
      rankedCountEl.textContent = String(rankedCount);
    }
    const notRankedCountEl = main.querySelector('[data-tab="notranked"] .count');
    if (notRankedCountEl) {
      const notRankedCount = state.notRanked
        ? flattenGroups(state.notRanked, { ecosystem: state.ecosystem, query: state.query }).length
        : (state.leaderboard.counts?.gated ?? 0) + (state.leaderboard.counts?.unrated ?? 0);
      if (notRankedCountEl.textContent !== String(notRankedCount)) {
        notRankedCountEl.textContent = String(notRankedCount);
      }
    }
  }

  // ----------------------------------------------------------- Main render
  function patchLeaderboardBody(state) {
    syncSortHeaders(state);
    syncCountsStrip(state);
    if (state.leaderboard) syncPager(main, state.page, state.pageSize, state.leaderboard.total, "pager");

    const viewport = currentViewport();
    const rows = state.leaderboard ? state.leaderboard.rows : [];
    const opts = { showPersonaDelta: state.persona !== "balanced", selectedRepoKey: state.selectedRepoKey };

    if (viewport.isMobile) {
      const container = main.querySelector("[data-leaderboard-cards]");
      if (container) {
        const range = computeVisibleRange(viewport.scrollTop, viewport.height, rows.length, 8, CARD_HEIGHT_PX);
        patchCardBody(container, doc, rows, range, opts);
      }
    } else {
      const tbody = main.querySelector("[data-leaderboard-tbody]");
      if (tbody) {
        const table = tbody.closest("table");
        if (table) table.classList.toggle("stars-unaudited", !copy.hasAuditedStars(rows));
        const range = computeVisibleRange(viewport.scrollTop, viewport.height, rows.length);
        patchTableBody(tbody, doc, rows, range, opts);
      }
    }

    const progress = main.querySelector("[data-refetch-progress]");
    if (progress) progress.hidden = !state.leaderboardRefetching;
    const errBanner = main.querySelector("[data-leaderboard-error]");
    if (errBanner) {
      if (state.leaderboardError) {
        errBanner.hidden = false;
        const msg = copy.errorBannerText(
          state.leaderboardError.endpoint,
          state.leaderboardError.status,
          state.leaderboardError.detail
        );
        errBanner.innerHTML =
          `${escapeHtml(msg)} ` +
          `<button type="button" class="icon-button" data-leaderboard-retry="true">${escapeHtml(copy.RETRY_LABEL)}</button>`;
      } else {
        errBanner.hidden = true;
      }
    }
    const scrollable = main.querySelector(".leaderboard-table, [data-leaderboard-cards]");
    if (scrollable) scrollable.classList.toggle("stale-table", !!(state.leaderboardRefetching || state.leaderboardError));
  }

  /** Resets every OTHER screen's "current shape" tracker so returning to it later always rebuilds its shell (mirrors the pre-existing ranked/notranked pattern, generalised to 6 tabs). */
  function forgetOtherShapes(keep) {
    if (keep !== "ranked") currentShellShape = null;
    if (keep !== "notranked") currentNotRankedShape = null;
    if (keep !== "matrix") currentMatrixShape = null;
    if (keep !== "configure") currentConfigureShape = null;
    if (keep !== "compare") currentCompareShape = null;
    if (keep !== "methodology") currentMethodologyShape = null;
  }

  /**
   * Above `SVG_MARKER_BUDGET` markers (06 §3 B's documented SVG/canvas
   * switch, `components/scatter.js`), `renderMatrixBody` renders a bare
   * `<canvas>` with nothing drawn on it — the actual paint call needs
   * real resolved colours (canvas cannot read `var(--x)`/`currentColor`
   * itself), which only app.js can resolve via `getComputedStyle`. A
   * no-op when the SVG path is showing (the real fixture's 73 points
   * never cross the budget; this only fires under the `?perfmatrix=`
   * dev harness).
   */
  function drawMatrixCanvasIfPresent(state) {
    const canvas = main.querySelector("[data-matrix-canvas]");
    if (!canvas || !state.matrix) return;
    const rect = canvas.getBoundingClientRect();
    const width = Math.max(1, Math.round(rect.width)) || canvas.width;
    const height = Math.max(1, Math.round(rect.height)) || canvas.height;
    const dpr = Math.max(1, Number(win.devicePixelRatio) || 1);
    const targetW = Math.round(width * dpr);
    const targetH = Math.round(height * dpr);
    if (canvas.width !== targetW) canvas.width = targetW;
    if (canvas.height !== targetH) canvas.height = targetH;
    const styles = win.getComputedStyle(doc.documentElement);
    const colors = {
      "Sweet Spot": styles.getPropertyValue("--quad-sweet-spot").trim(),
      Crucible: styles.getPropertyValue("--quad-crucible").trim(),
      Nursery: styles.getPropertyValue("--quad-nursery").trim(),
      Graveyard: styles.getPropertyValue("--quad-graveyard").trim(),
    };
    const ctx = canvas.getContext("2d");
    if (ctx && typeof ctx.setTransform === "function") {
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }
    const filteredPoints = filterMatrixPoints(state.matrix.points, state);
    drawMatrixCanvas(ctx, filteredPoints, {
      quadrantsHidden: state.matrixQuadrantsHidden,
      colors,
      width,
      height,
      view: state.matrixView,
    });
  }

  function syncView(state) {
    if (state.tab === "notranked") {
      const key = notRankedRebuildKey(state);
      if (currentTab !== "notranked" || key !== currentNotRankedShape) {
        const focusedSubTab =
          doc.activeElement && main.contains(doc.activeElement)
            ? doc.activeElement.getAttribute?.("data-sub-tab")
            : null;
        main.innerHTML = renderNotRankedShell(state);
        if (focusedSubTab) main.querySelector(`[data-sub-tab="${focusedSubTab}"]`)?.focus();
        currentTab = "notranked";
        currentNotRankedShape = key;
        forgetOtherShapes("notranked");
      }
      if (notRankedShellShape(state) === "table") patchNotRankedBody(main, state);
      return;
    }

    if (state.tab === "matrix") {
      const shape = matrixShellShape(state);
      if (currentTab !== "matrix" || shape !== currentMatrixShape) {
        main.innerHTML = renderMatrixShell(state);
        currentTab = "matrix";
        currentMatrixShape = shape;
        forgetOtherShapes("matrix");
      }
      if (shape.startsWith("body")) {
        syncMatrixViewBox(main, state.matrixView);
        syncMatrixHover(main, state);
        drawMatrixCanvasIfPresent(state);
      }
      return;
    }

    if (state.tab === "configure") {
      const shape = configureShellShape(state);
      if (currentTab !== "configure" || shape !== currentConfigureShape) {
        main.innerHTML = renderConfigureShell(state);
        currentTab = "configure";
        currentConfigureShape = shape;
        forgetOtherShapes("configure");
      }
      syncConfigureControls(state);
      if (shape === "body") patchConfigureTable(main, doc, state);
      return;
    }

    if (state.tab === "compare") {
      // The shell (heading/chips/search input) is rebuilt ONLY when `shellKey`
      // changes -- tab entry or the chip set changing -- NEVER on a keystroke.
      // The results list and the comparison table are siblings of the search input,
      // patched into their host containers on state changes without touching the shell.
      const shape = compareShellShape(state);
      const key = compareShellKey(state);
      if (currentTab !== "compare" || key !== currentCompareShape) {
        const searchHadFocus = doc.activeElement && doc.activeElement.matches("[data-compare-search]");
        main.innerHTML = renderCompareShell(state);
        if (searchHadFocus) main.querySelector("[data-compare-search]")?.focus();
        currentTab = "compare";
        currentCompareShape = key;
        forgetOtherShapes("compare");
      }
      patchCompareResults(main, state);
      if (shape === "table") patchCompareTable(main, state);
      return;
    }

    if (state.tab === "methodology") {
      const shape = methodologyShellShape(state);
      if (currentTab !== "methodology" || shape !== currentMethodologyShape) {
        main.innerHTML = renderMethodologyShell(state);
        currentTab = "methodology";
        currentMethodologyShape = shape;
        forgetOtherShapes("methodology");
      }
      return;
    }

    const viewport = currentViewport();
    const shape = shellShape(state, viewport);
    const shellKey = shape === "empty" ? `${shape}:${state.ecosystem}:${state.query}` : shape;
    if (currentTab !== "ranked" || shellKey !== currentShellShape) {
      const focusedSubTab =
        doc.activeElement && main.contains(doc.activeElement)
          ? doc.activeElement.getAttribute?.("data-sub-tab")
          : null;
      main.innerHTML = renderShell(state, viewport);
      if (focusedSubTab) main.querySelector(`[data-sub-tab="${focusedSubTab}"]`)?.focus();
      currentTab = "ranked";
      currentShellShape = shellKey;
      forgetOtherShapes("ranked");
    }
    if (shape.startsWith("table")) patchLeaderboardBody(state);
  }

  /** Configure's slider/preset/sum controls render in EVERY shape (loading/error/empty/body — the controls stay visible while re-projecting), so a weight/lock change never rebuilds the shell; this syncs the five sliders' value/output/lock state plus the sum display. Also called from `syncView`'s non-"body" branch, since the controls exist there too. */
  function syncConfigureControls(state) {
    const unlockedCount = PILLAR_IDS.filter((p) => !state.weightLocks[p]).length;
    for (const pillar of PILLAR_IDS) {
      const slider = main.querySelector(`[data-weight-slider="${pillar}"]`);
      const locked = !!state.weightLocks[pillar];
      const shouldDisable = locked || unlockedCount <= 1;
      if (slider) {
        const value = String(state.weights[pillar]);
        if (slider.value !== value) slider.value = value;
        if (slider.disabled !== shouldDisable) slider.disabled = shouldDisable;
        const pct = `${Math.round(state.weights[pillar] * 1000) / 10}%`;
        // Matches views/configure.js::renderSlider's format — screen readers
        // announce which pillar changed, not just the new percentage.
        const valueText = `${copy.PILLAR_NAMES[pillar]}: ${pct}`;
        if (slider.getAttribute("aria-valuetext") !== valueText) slider.setAttribute("aria-valuetext", valueText);
        const output = slider.nextElementSibling;
        if (output && output.classList.contains("weight-slider-value") && output.textContent !== pct) {
          output.textContent = pct;
        }
      }
      const lockBtn = main.querySelector(`[data-weight-lock="${pillar}"]`);
      if (lockBtn) {
        const pressed = String(locked);
        if (lockBtn.getAttribute("aria-pressed") !== pressed) lockBtn.setAttribute("aria-pressed", pressed);
        const glyph = locked ? "🔒" : "🔓";
        if (lockBtn.textContent !== glyph) lockBtn.textContent = glyph;
      }
    }
    const sumEl = main.querySelector("[data-weight-sum]");
    if (sumEl) {
      const sum = PILLAR_IDS.reduce((s, p) => s + state.weights[p], 0);
      const text = copy.configureSumText(sum);
      if (sumEl.textContent !== text) sumEl.textContent = text;
      sumEl.classList.toggle("weight-sum-warning", Math.abs(sum - 1) > 0.005);
    }
  }

  // ------------------------------------------------------------- URL sync
  // URL sync: persona/eco/sort/page/page_size/tab/repo changes are each a real
  // navigation step (pushState); `q` while typing is not — debounced
  // keystrokes use replaceState, and only an explicit commit (Enter or
  // blur) creates a pushState history entry.
  function pushUrl(state, opts = {}) {
    const search = serializeSearch(state);
    const target = `${win.location.pathname}${search}`;
    const current = `${win.location.pathname}${win.location.search}`;
    if (opts.force || target !== current) {
      win.history.pushState(null, "", target);
    }
  }
  function replaceUrl(state) {
    const search = serializeSearch(state);
    win.history.replaceState(null, "", `${win.location.pathname}${search}`);
  }

  // --------------------------------------------------------------- Loaders
  function loadNotRanked() {
    const requestedPersona = store.getState().persona;
    store.dispatch({ type: "NOTRANKED_LOADING" });
    return api.fetchNotRankedFull({ persona: requestedPersona }).then((result) => {
      if (store.getState().persona !== requestedPersona) return;
      if (!result.ok) {
        store.dispatch({ type: "NOTRANKED_ERROR", error: result.error });
        announce(copy.apiErrorAnnouncement(result.error.endpoint, result.error.status));
        return;
      }
      store.dispatch({ type: "NOTRANKED_OK", data: result.data });
    });
  }

  function loadSnapshot() {
    store.dispatch({ type: "SNAPSHOT_LOADING" });
    return api.fetchSnapshot().then((result) => {
      if (result.ok) store.dispatch({ type: "SNAPSHOT_OK", data: result.data });
      else store.dispatch({ type: "SNAPSHOT_ERROR", error: result.error });
    });
  }

  function loadEvidence(owner, name) {
    const key = `${owner}/${name}`;
    if (store.getState().evidenceByRepo[key]) return Promise.resolve();
    store.dispatch({ type: "EVIDENCE_LOADING" });
    return api.fetchEvidence(owner, name).then((result) => {
      if (!result.ok) {
        if (store.getState().repo === key) {
          store.dispatch({ type: "EVIDENCE_ERROR", error: result.error });
        }
        return;
      }
      store.dispatch({ type: "EVIDENCE_OK", repo: key, data: result.data });
    });
  }

  /**
   * Fetches the repo record (`GET /v1/porchlamp/repos/{o}/{n}`) — called by `openRepo`
   * regardless of which screen it was opened from, ensuring the drawer is
   * independent of any state slice a particular screen keeps in memory (Matrix
   * point list, Compare per-chip cache, etc.). Cached per repo, matching
   * `loadEvidence`.
   */
  function loadRepoDetail(owner, name) {
    const key = `${owner}/${name}`;
    if (store.getState().repoDetailByKey[key]) return Promise.resolve();
    store.dispatch({ type: "REPO_DETAIL_LOADING" });
    return api.fetchRepoDetail(owner, name).then((result) => {
      if (!result.ok) {
        if (store.getState().repo === key) {
          store.dispatch({ type: "REPO_DETAIL_ERROR", error: result.error });
        }
        return;
      }
      store.dispatch({ type: "REPO_DETAIL_OK", repo: key, data: result.data });
    });
  }

  // --------------------------------------------------- Matrix (B)
  function loadMatrix() {
    store.dispatch({ type: "MATRIX_LOADING" });
    return api.fetchMatrix().then((result) => {
      if (!result.ok) {
        store.dispatch({ type: "MATRIX_ERROR", error: result.error });
        return;
      }
      store.dispatch({ type: "MATRIX_OK", data: result.data });
    });
  }

  // ------------------------------------------- Methodology (F)
  function loadMethodology() {
    store.dispatch({ type: "METHODOLOGY_LOADING" });
    return api.fetchMethodology().then((result) => {
      if (!result.ok) {
        store.dispatch({ type: "METHODOLOGY_ERROR", error: result.error });
        return;
      }
      store.dispatch({ type: "METHODOLOGY_OK", data: result.data });
    });
  }

  // ----------------------------------------- Configure weights (D)
  /** Debounced to <= 150ms while dragging a slider (06 §3 D); the greyed-table pattern (U-14) keeps stale rows visible with a progress indicator until the new payload lands. */
  function scheduleReproject() {
    clearTimeout(reprojectDebounce);
    reprojectDebounce = setTimeout(runReproject, 120);
  }

  function runReproject() {
    const state = store.getState();
    const signature = JSON.stringify(state.weights);
    reprojectInFlightSignature = signature;
    store.dispatch({ type: "REPROJECT_LOADING" });
    const body = {};
    for (const pillar of PILLAR_IDS) body[`weight_${pillar}`] = state.weights[pillar];
    // The endpoint answers at most REPROJECT_PAGE_SIZE rows a page: take the first, then the rest, so the table
    // and its rank deltas cover the whole ranking.
    return api.postReproject(body, { page_size: REPROJECT_PAGE_SIZE }).then(async (result) => {
      if (reprojectInFlightSignature !== signature) return; // superseded by a newer drag
      if (!result.ok) {
        store.dispatch({ type: "REPROJECT_ERROR", error: result.error });
        return;
      }
      const later = [];
      for (const page of remainingReprojectPages(result.data.total, REPROJECT_PAGE_SIZE)) {
        const next = await api.postReproject(body, { page, page_size: REPROJECT_PAGE_SIZE });
        if (reprojectInFlightSignature !== signature) return;
        if (!next.ok) {
          store.dispatch({ type: "REPROJECT_ERROR", error: next.error });
          return;
        }
        later.push(next.data);
      }
      store.dispatch({ type: "REPROJECT_OK", data: mergeReprojectPages(result.data, later) });
    });
  }

  // ------------------------------------------------------ Compare (E)
  function searchCompare(query) {
    const trimmed = query.trim();
    if (!trimmed) {
      store.dispatch({ type: "COMPARE_SET_RESULTS", results: [] });
      return Promise.resolve();
    }
    return Promise.all([
      api.fetchLeaderboard({ q: trimmed, page_size: 20 }),
      api.fetchNotRanked({ persona: "balanced", q: trimmed }),
    ]).then(([leaderboardResult, notRankedResult]) => {
      if (store.getState().compareQuery.trim() !== trimmed) return;
      const alreadyAdded = new Set(store.getState().compareRepos);
      const ratedResults = [];
      const unrankedResults = [];
      if (leaderboardResult.ok) {
        for (const r of leaderboardResult.data.rows) {
          if (!alreadyAdded.has(`${r.owner}/${r.name}`)) {
            ratedResults.push({ owner: r.owner, name: r.name, ecosystem: r.ecosystem, kind: "rated" });
          }
        }
      }
      if (notRankedResult.ok) {
        const qLower = trimmed.toLowerCase();
        for (const group of ["gated", "unrated"]) {
          for (const r of notRankedResult.data[group]) {
            const key = `${r.owner}/${r.name}`;
            if (!alreadyAdded.has(key) && key.toLowerCase().includes(qLower)) {
              unrankedResults.push({ owner: r.owner, name: r.name, ecosystem: r.ecosystem, kind: group === "gated" ? "gated" : "unrated" });
            }
          }
        }
      }
      const combined = [];
      const maxLen = Math.max(ratedResults.length, unrankedResults.length);
      for (let i = 0; i < maxLen && combined.length < 8; i++) {
        if (i < ratedResults.length) combined.push(ratedResults[i]);
        if (combined.length < 8 && i < unrankedResults.length) combined.push(unrankedResults[i]);
      }
      store.dispatch({ type: "COMPARE_SET_RESULTS", results: combined });
    });
  }

  function loadCompareRepo(key) {
    const [owner, name] = key.split("/");
    store.dispatch({ type: "COMPARE_REPO_LOADING", key });

    const detailPromise =
      typeof api.fetchRepoDetail === "function"
        ? api.fetchRepoDetail(owner, name)
        : Promise.resolve({ ok: false });

    return detailPromise.then((detailResult) => {
      if (!store.getState().compareRepos.includes(key)) return;
      if (detailResult && detailResult.ok && detailResult.data) {
        const row = detailResult.data;
        const kind = classifyKindForLens(row, "balanced") || "rated";
        return api.fetchEvidence(owner, name).then((evidenceResult) => {
          store.dispatch({
            type: "COMPARE_REPO_OK",
            key,
            data: { kind, row, evidence: evidenceResult.ok ? evidenceResult.data : [] },
          });
        });
      }
      return Promise.all([
        api.fetchLeaderboard({ q: key, page_size: 1000 }),
        api.fetchNotRanked({ persona: "balanced", q: key }),
      ]).then(([leaderboardResult, notRankedResult]) => {
        if (!store.getState().compareRepos.includes(key)) return; // removed while in flight
        let kind = null;
        let row = null;
        if (leaderboardResult.ok) {
          row = leaderboardResult.data.rows.find((r) => r.owner === owner && r.name === name);
          if (row) kind = "rated";
        }
        if (!row && notRankedResult.ok) {
          const foundGated = notRankedResult.data.gated.find((r) => r.owner === owner && r.name === name);
          if (foundGated) {
            row = { ...foundGated, eligible: false, rated: false };
            kind = "gated";
          } else {
            const foundUnrated = notRankedResult.data.unrated.find((r) => r.owner === owner && r.name === name);
            if (foundUnrated) {
              row = { ...foundUnrated, eligible: !foundUnrated.gate_reason, rated: false };
              kind = "unrated";
            }
          }
        }
        if (!row) {
          store.dispatch({ type: "COMPARE_REPO_ERROR", key, error: { endpoint: "/v1/porchlamp/leaderboard", status: 404 } });
          return;
        }
        return api.fetchEvidence(owner, name).then((evidenceResult) => {
          store.dispatch({
            type: "COMPARE_REPO_OK",
            key,
            data: { kind, row, evidence: evidenceResult.ok ? evidenceResult.data : [] },
          });
        });
      });
    });
  }

  function paramsForState(state) {
    return {
      persona: state.persona,
      eco: state.ecosystem,
      q: state.query,
      sort: state.tab === "ranked" ? state.sort : (state.rankedSort || "rank"),
      page: state.tab === "ranked" ? state.page : (state.rankedPage ?? 1),
      page_size: state.tab === "ranked" ? state.pageSize : (state.rankedPageSize ?? 20),
    };
  }

  /** Fetches page+1 in the background and caches it (owner instruction: "prefetch the next page after the current one renders"). Never touches loading/error state — a failed prefetch is silently discarded; the user's own click will just fetch normally. */
  function prefetchNextPage(state) {
    const totalPages = Math.max(1, Math.ceil((state.leaderboard?.total || 0) / state.pageSize));
    if (state.page >= totalPages) return;
    const nextState = { ...state, page: state.page + 1 };
    const signature = requestSignature(nextState);
    api.fetchLeaderboard(paramsForState(nextState)).then((result) => {
      if (!result.ok) return;
      // Only cache if the app hasn't since moved on to a different request context.
      if (requestSignature({ ...store.getState(), page: state.page + 1 }) === signature) {
        store.dispatch({ type: "SET_NEXT_PAGE_CACHE", entry: { signature, data: result.data } });
      }
    });
  }

  /**
   * The single entry point for every leaderboard-affecting change
   * (persona/eco/q/sort/page/pageSize). U-14 (amended): sort/search/eco
   * now fetch (only the server sees the whole result set with one page
   * loaded); U-15: a failure keeps the old page visible with an error
   * banner, never an indefinite spinner.
   */
  function loadLeaderboard() {
    const state = store.getState();
    const signature = requestSignature(state);
    inFlightSignature = signature;

    const cached = state.nextPageCache;
    if (cached && cached.signature === signature) {
      applyLeaderboardResult({ ok: true, data: cached.data }, state, signature);
      store.dispatch({ type: "SET_NEXT_PAGE_CACHE", entry: null });
      return Promise.resolve();
    }

    const isRefetch = !!state.leaderboard;
    store.dispatch({ type: isRefetch ? "LEADERBOARD_REFETCHING" : "LEADERBOARD_LOADING" });
    return api.fetchLeaderboard(paramsForState(state)).then((result) => {
      applyLeaderboardResult(result, state, signature);
    });
  }

  function applyLeaderboardResult(result, requestedFromState, signature) {
    if (inFlightSignature !== signature) return; // a newer request has already superseded this one
    const previousRows = requestedFromState.leaderboard ? requestedFromState.leaderboard.rows : null;
    if (!result.ok) {
      store.dispatch({ type: "LEADERBOARD_ERROR", error: result.error });
      announce(copy.apiErrorAnnouncement(result.error.endpoint, result.error.status));
      return;
    }
    store.dispatch({ type: "LEADERBOARD_OK", data: result.data, signature });
    if (requestedFromState.persona !== "balanced" && requestedFromState.persona === store.getState().persona) {
      announce(copy.reprojectedAnnouncement(copy.PERSONA_NAMES[requestedFromState.persona], countRankChanges(previousRows, result.data.rows)));
    }
    prefetchNextPage(store.getState());
  }

  // ------------------------------------------------------------- Rendering
  // The README badge's Markdown must point at the public site, not at whichever
  // host served this page: index.html names it (og:url, then rel=canonical); this
  // page's own directory is the fallback for a copy that names neither.
  const siteBaseUrl =
    doc.querySelector('meta[property="og:url"]')?.getAttribute("content") ||
    doc.querySelector('link[rel="canonical"]')?.getAttribute("href") ||
    new URL(".", win.location.href).href;

  function renderDrawerAndKeymap(state) {
    const drawerHtml = renderDrawer(state, { siteBaseUrl });
    if (drawerRoot.dataset.lastHtml !== drawerHtml) {
      // A content-only update (e.g. evidence arriving after the drawer is
      // ALREADY open) still replaces drawerRoot's innerHTML wholesale — the
      // drawer isn't on the keyed-patch path components/table.js uses.
      // That destroys whatever element currently holds focus if focus is
      // inside the drawer, and browsers fall back to <body> when a focused
      // node is removed. Bug found by the lead's live probe ("opening via
      // click leaves focus on body"): the OPEN transition itself focused
      // the close button correctly, but the evidence fetch resolving a
      // moment later re-rendered the drawer's innerHTML and silently
      // dropped focus to <body>. Fix: if focus was inside the drawer
      // before this rebuild, restore it to the matching control (or close
      // button fallback) afterward.
      const focusWasInDrawer = drawerRoot.contains(doc.activeElement);
      let restoreSelector = "[data-drawer-close]";
      let restoreSummaryIndex = -1;
      if (focusWasInDrawer && doc.activeElement) {
        if (doc.activeElement.matches?.("[data-badge-copy]")) restoreSelector = "[data-badge-copy]";
        else if (doc.activeElement.matches?.("[data-drawer-compare]")) restoreSelector = "[data-drawer-compare]";
        else if (doc.activeElement.matches?.(".drawer-github-link")) restoreSelector = ".drawer-github-link";
        else if (doc.activeElement.matches?.("[data-repo-detail-retry]")) restoreSelector = "[data-repo-detail-retry]";
        else if (doc.activeElement.matches?.("[data-evidence-retry]")) restoreSelector = "[data-evidence-retry]";
        else if (doc.activeElement.tagName === "SUMMARY") {
          restoreSummaryIndex = Array.from(drawerRoot.querySelectorAll("summary")).indexOf(doc.activeElement);
        }
      }
      drawerRoot.innerHTML = drawerHtml;
      drawerRoot.dataset.lastHtml = drawerHtml;
      if (shouldRestoreFocusAfterRebuild(state.repo, focusWasInDrawer)) {
        const target =
          restoreSummaryIndex >= 0
            ? drawerRoot.querySelectorAll("summary")[restoreSummaryIndex]
            : drawerRoot.querySelector(restoreSelector);
        (target || drawerRoot.querySelector("[data-drawer-close]"))?.focus();
      }
    }
    const keymapHtml = state.keymapOpen ? renderKeymapOverlay() : "";
    if (keymapRoot.dataset.lastHtml !== keymapHtml) {
      const focusWasInKeymap = keymapRoot.contains(doc.activeElement);
      if (state.keymapOpen && !wasKeymapOpen) {
        keymapTriggerEl = doc.activeElement && doc.activeElement !== doc.body ? doc.activeElement : null;
      }
      keymapRoot.innerHTML = keymapHtml;
      keymapRoot.dataset.lastHtml = keymapHtml;
      if (shouldRestoreFocusAfterRebuild(state.keymapOpen, focusWasInKeymap)) {
        keymapRoot.querySelector("[data-keymap-close]")?.focus();
      }
    }
    if (state.keymapOpen && !wasKeymapOpen) keymapRoot.querySelector("[data-keymap-close]")?.focus();
    if (!state.keymapOpen && wasKeymapOpen) {
      if (state.repo) {
        drawerRoot.querySelector("[data-drawer-close]")?.focus();
      } else if (keymapTriggerEl && typeof keymapTriggerEl.focus === "function" && doc.contains(keymapTriggerEl)) {
        keymapTriggerEl.focus();
      }
      keymapTriggerEl = null;
    }
    if (state.repo && !wasDrawerOpen) drawerRoot.querySelector("[data-drawer-close]")?.focus();
    wasDrawerOpen = !!state.repo;
    wasKeymapOpen = !!state.keymapOpen;
  }

  function render() {
    const state = store.getState();
    syncHeaderControls(state);
    syncFooter(state);
    syncPrimaryNav(primaryNav, state.tab);
    syncView(state);
    renderDrawerAndKeymap(state);
  }

  store.subscribe(render);

  // -------------------------------------------------------- Event wiring
  function maybeLoadLeaderboardForCurrentTab() {
    const s = store.getState();
    if (s.tab === "ranked" || s.tab === "notranked") {
      loadLeaderboard();
    }
  }

  function changeParamsAndFetch(payload) {
    store.dispatch({ type: "SET_PARAMS", payload: { ...payload, page: 1 } });
    pushUrl(store.getState());
    maybeLoadLeaderboardForCurrentTab();
  }

  header.addEventListener("change", (evt) => {
    if (evt.target.matches("[data-persona-select]")) {
      changeParamsAndFetch({ persona: evt.target.value });
      if (store.getState().tab === "notranked") loadNotRanked();
    } else if (evt.target.matches("[data-eco-select]")) {
      changeParamsAndFetch({ ecosystem: evt.target.value });
    }
  });

  header.addEventListener("input", (evt) => {
    if (evt.target.matches("[data-search-input]")) {
      // The state update (and the fetch it triggers) is debounced to
      // <= 100ms so a fast typist does not fire a request per keystroke —
      // the input's own displayed text needs no JS to update. U-16: this
      // is a replaceState, not a pushState — typing must not spam history
      // (a single query commits to history separately, see below).
      const query = evt.target.value;
      clearTimeout(searchDebounce);
      searchDebounce = setTimeout(() => {
        searchDebounce = null;
        searchDirty = true;
        store.dispatch({ type: "SET_PARAMS", payload: { query, page: 1 } });
        urlSyncMode("type") === "replace" ? replaceUrl(store.getState()) : pushUrl(store.getState());
        maybeLoadLeaderboardForCurrentTab();
      }, 80);
    }
  });
  header.addEventListener("keydown", (evt) => {
    if (evt.target.matches("[data-search-input]") && evt.key === "Enter") {
      if (searchDebounce) {
        clearTimeout(searchDebounce);
        searchDebounce = null;
        searchDirty = true;
        store.dispatch({ type: "SET_PARAMS", payload: { query: evt.target.value, page: 1 } });
        maybeLoadLeaderboardForCurrentTab();
      }
      if (searchDirty && urlSyncMode("commit") === "push") {
        pushUrl(store.getState(), { force: true });
        searchDirty = false;
      }
    }
  });
  header.addEventListener(
    "blur",
    (evt) => {
      if (evt.target.matches && evt.target.matches("[data-search-input]")) {
        if (searchDebounce) {
          clearTimeout(searchDebounce);
          searchDebounce = null;
          searchDirty = true;
          store.dispatch({ type: "SET_PARAMS", payload: { query: evt.target.value, page: 1 } });
          maybeLoadLeaderboardForCurrentTab();
        }
        if (searchDirty && urlSyncMode("commit") === "push") {
          pushUrl(store.getState(), { force: true });
          searchDirty = false;
        }
      }
    },
    true
  );

  header.addEventListener("click", (evt) => {
    if (evt.target.closest("[data-theme-toggle]")) {
      const currentTheme = store.getState().theme;
      const isSystemDark = win.matchMedia && win.matchMedia("(prefers-color-scheme: dark)").matches;
      const isDark = currentTheme === "dark" || (currentTheme === "system" && isSystemDark);
      const next = isDark ? "light" : "dark";
      applyTheme(next, doc);
      store.dispatch({ type: "SET_THEME", theme: next });
      writePersistedTheme(win.localStorage, next);
      const btn = header.querySelector("[data-theme-toggle]");
      if (btn) {
        btn.textContent = next === "dark" ? "🌙" : "☀";
        btn.setAttribute("title", `Active theme: ${next}`);
      }
      return;
    }
  });

  function goToTab(tab) {
    store.dispatch({ type: "SET_TAB", tab });
    pushUrl(store.getState());
    loadDataForTab(tab);
  }

  primaryNav.addEventListener("click", (evt) => {
    const btn = evt.target.closest("[data-primary-tab]");
    if (btn) goToTab(btn.dataset.primaryTab);
  });

  // The footer's retry button (shown only while snapshotError is set)
  // and the "verify" link, which routes to the Methodology screen
  // instead of a bare same-page anchor jump.
  footer.addEventListener("click", (evt) => {
    if (evt.target.closest("[data-footer-retry]")) {
      loadSnapshot();
      return;
    }
    if (evt.target.closest("[data-footer-verify]")) {
      evt.preventDefault();
      goToTab("methodology");
    }
  });

  function openRepo(owner, name) {
    const key = `${owner}/${name}`;
    store.dispatch({ type: "SET_CURSOR", key });
    store.dispatch({ type: "OPEN_REPO", repo: key });
    pushUrl(store.getState());
    loadRepoDetail(owner, name);
    loadEvidence(owner, name);
  }

  function closeDrawer() {
    const s = store.getState();
    const key = s.selectedRepoKey;
    store.dispatch({ type: "CLOSE_DRAWER" });
    pushUrl(store.getState());
    if (key) {
      const [owner, name] = key.split("/");
      const rowEl = main.querySelector(`[data-owner="${CSS.escape(owner)}"][data-name="${CSS.escape(name)}"]`);
      if (rowEl) {
        rowEl.focus();
        return;
      }
    }
    if (s.tab === "matrix") {
      main.querySelector("[data-matrix-stage]")?.focus();
    } else if (s.tab === "compare") {
      main.querySelector("[data-compare-search]")?.focus();
    }
  }

  /** Ruling: "changing the page size keeps the current first visible row on screen". Anchors on the cursor row if one is selected, else the current page's first loaded row. */
  function goToPage(newPage) {
    if (newPage < 1) return;
    const s = store.getState();
    if (s.leaderboard) {
      const totalPages = Math.max(1, Math.ceil((s.leaderboard.total || 0) / s.pageSize));
      if (newPage > totalPages) return;
    }
    store.dispatch({ type: "SET_PAGE", page: newPage });
    pushUrl(store.getState());
    revealListTop();
    loadLeaderboard();
  }

  function goToPageSize(newPageSize) {
    const state = store.getState();
    if (!state.leaderboard) {
      store.dispatch({ type: "SET_PAGE_SIZE", pageSize: newPageSize, page: 1 });
      pushUrl(store.getState());
      loadLeaderboard();
      return;
    }
    const rows = state.leaderboard.rows;
    const cursorIdxInPage = rows.findIndex((r) => rowKey(r) === state.selectedRepoKey);
    const viewport = currentViewport();
    const range = computeVisibleRange(viewport.scrollTop, viewport.height, rows.length);
    const anchorIdxInPage = cursorIdxInPage !== -1 ? cursorIdxInPage : range.start;
    const anchorAbsoluteIndex = (state.page - 1) * state.pageSize + anchorIdxInPage;
    const newPage = Math.floor(anchorAbsoluteIndex / newPageSize) + 1;

    store.dispatch({ type: "SET_PAGE_SIZE", pageSize: newPageSize, page: newPage });
    pushUrl(store.getState());
    revealListTop();
    loadLeaderboard();
  }

  function goToConfigurePage(newPage) {
    if (newPage < 1) return;
    const s = store.getState();
    if (s.reproject) {
      const filtered = filterReprojectRows(s.reproject.rows, { ecosystem: s.ecosystem, query: s.query });
      const totalPages = Math.max(1, Math.ceil(filtered.length / s.configurePageSize));
      if (newPage > totalPages) return;
    }
    store.dispatch({ type: "SET_CONFIGURE_PAGE", page: newPage });
    pushUrl(store.getState());
    revealListTop(".configure-table-col");
  }

  main.addEventListener("change", (evt) => {
    if (evt.target.matches("[data-weight-slider]")) {
      if (reprojectDebounce) {
        clearTimeout(reprojectDebounce);
        reprojectDebounce = null;
        runReproject();
      }
      pushUrl(store.getState(), { force: true });
      return;
    }
    if (evt.target.matches("[data-mobile-sort-select]")) {
      const nextSort = evt.target.value;
      changeParamsAndFetch({ sort: nextSort });
      announce(copy.sortedAnnouncement(sortColumnLabel(nextSort), sortDirectionLabel(nextSort)));
      return;
    }
    if (evt.target.matches("[data-sort-field]")) return; // handled via click on <th>, not a <select>
    if (evt.target.matches("[data-pager-size-select]")) {
      goToPageSize(parseInt(evt.target.value, 10));
    } else if (evt.target.matches("[data-notranked-pager-size-select]")) {
      store.dispatch({ type: "SET_NOTRANKED_PAGE_SIZE", pageSize: parseInt(evt.target.value, 10), page: 1 });
      pushUrl(store.getState());
      patchNotRankedBody(main, store.getState());
      revealListTop();
    } else if (evt.target.matches("[data-configure-pager-size-select]")) {
      store.dispatch({ type: "SET_CONFIGURE_PAGE_SIZE", pageSize: parseInt(evt.target.value, 10), page: 1 });
      pushUrl(store.getState());
      revealListTop(".configure-table-col");
    }
  });

  // Compare: Enter in the search box adds the first match, as every search-and-add box does;
  // Escape clears the open search results dropdown without opening/closing any other overlay.
  main.addEventListener("keydown", (evt) => {
    if (!evt.target.matches || !evt.target.matches("[data-compare-search]")) return;
    if (evt.key === "Escape") {
      if (store.getState().compareSearchResults.length > 0 || store.getState().compareQuery) {
        evt.preventDefault();
        evt.stopPropagation();
        clearTimeout(compareSearchDebounce);
        compareSearchDebounce = null;
        store.dispatch({ type: "COMPARE_SET_RESULTS", results: [] });
      }
      return;
    }
    if (evt.key !== "Enter") return;
    const first = main.querySelector("[data-compare-add]");
    if (!first) return;
    evt.preventDefault();
    first.click();
  });

  main.addEventListener("input", (evt) => {
    const slider = evt.target.closest("[data-weight-slider]");
    if (slider) {
      const pillar = slider.dataset.weightSlider;
      const state = store.getState();
      const next = normalizeWeights(state.weights, state.weightLocks, pillar, parseFloat(slider.value));
      if (weightsEqual(next, state.weights)) return;
      store.dispatch({ type: "SET_WEIGHTS", weights: next });
      syncConfigureControls(store.getState());
      clearTimeout(reprojectDebounce);
      // 06 §3 D: "debounce reproject calls <= 150ms while dragging".
      reprojectDebounce = setTimeout(() => {
        reprojectDebounce = null;
        replaceUrl(store.getState());
        runReproject();
      }, 120);
      return;
    }
    if (evt.target.matches("[data-compare-search]")) {
      const query = evt.target.value;
      store.dispatch({ type: "COMPARE_SET_QUERY", query });
      clearTimeout(compareSearchDebounce);
      compareSearchDebounce = setTimeout(() => searchCompare(query), 100);
    }
  });

  main.addEventListener("click", (evt) => {
    // Above SVG_MARKER_BUDGET markers the matrix falls back to a single
    // <canvas> with no per-marker DOM node for the browser's own click
    // hit-testing to use — hitTestPoint resolves the nearest point
    // within tolerance.
    const canvas = evt.target.closest("[data-matrix-canvas]");
    if (canvas) {
      const s = store.getState();
      if (s.tab === "matrix" && s.matrix) {
        const rect = canvas.getBoundingClientRect();
        const { domainX, domainY, toleranceDomainUnits } = clientToDomainCoords(evt.clientX, evt.clientY, rect, s.matrixView);
        const visiblePoints = filterMatrixPoints(s.matrix.points, s).filter((p) => !s.matrixQuadrantsHidden[p.quadrant]);
        const hit = hitTestPoint(visiblePoints, domainX, domainY, toleranceDomainUnits);
        if (hit) {
          store.dispatch({ type: "MATRIX_SET_FOCUS", key: matrixPointKey(hit) });
          openRepo(hit.owner, hit.name);
        }
      }
      return;
    }
    if (evt.target.closest("[data-mobile-sort-dir]")) {
      const current = store.getState().sort;
      const next = current.startsWith("-") ? current.slice(1) : `-${current}`;
      changeParamsAndFetch({ sort: next });
      announce(copy.sortedAnnouncement(sortColumnLabel(next), sortDirectionLabel(next)));
      return;
    }
    // An empty list's ways out: drop the search and the ecosystem, or look in the other list.
    if (evt.target.closest("[data-clear-filters]")) {
      clearTimeout(searchDebounce);
      searchDebounce = null;
      searchDirty = false;
      changeParamsAndFetch({ ecosystem: "", query: "" });
      if (store.getState().tab === "notranked") patchNotRankedBody(main, store.getState());
      return;
    }
    if (evt.target.closest("[data-leaderboard-retry]")) {
      loadLeaderboard();
      return;
    }
    if (evt.target.closest("[data-notranked-retry]")) {
      loadNotRanked();
      return;
    }
    if (evt.target.closest("[data-matrix-retry]")) {
      loadMatrix();
      return;
    }
    if (evt.target.closest("[data-methodology-retry]")) {
      loadMethodology();
      return;
    }
    const compareRetryBtn = evt.target.closest("[data-compare-retry]");
    if (compareRetryBtn) {
      const key = compareRetryBtn.dataset.compareRetry;
      if (key) loadCompareRepo(key);
      return;
    }
    if (evt.target.closest("[data-search-notranked]")) return goToTab("notranked");
    if (evt.target.closest("[data-search-ranked]")) return goToTab("ranked");
    if (evt.target.closest("[data-onboarding-methodology]")) {
      evt.preventDefault();
      goToTab("methodology");
      return;
    }
    const tabBtn = evt.target.closest("[data-tab]");
    if (tabBtn) {
      const tab = tabBtn.dataset.tab;
      const tabHadFocus = doc.activeElement === tabBtn;
      store.dispatch({ type: "SET_TAB", tab });
      pushUrl(store.getState());
      loadDataForTab(tab);
      if (tabHadFocus) {
        main.querySelector(`[data-tab="${CSS.escape(tab)}"]`)?.focus();
      }
      return;
    }
    const sortHeader = evt.target.closest("[data-sort-field]");
    if (sortHeader) {
      const field = sortHeader.dataset.sortField;
      const currentTabValue = store.getState().tab;
      if (currentTabValue === "configure") {
        // Configure's table reuses the SAME header component but the whole
        // result set is already in memory (scripts/serve_ui_fixtures.py's
        // /reproject has no server-side sort/paging fields) — sorting it
        // is a pure, instant, client-side re-slice, never a fetch.
        const current = store.getState().configureSort;
        const next = current === field ? `-${field}` : field;
        store.dispatch({ type: "SET_CONFIGURE_SORT", sort: next });
        pushUrl(store.getState());
        announce(copy.sortedAnnouncement(sortColumnLabel(field), sortDirectionLabel(next)));
        return;
      }
      if (currentTabValue !== "ranked") return; // the header is only ever meaningfully sortable on these two tabs
      const current = store.getState().sort;
      const next = current === field ? `-${field}` : field;
      changeParamsAndFetch({ sort: next });
      announce(copy.sortedAnnouncement(sortColumnLabel(field), sortDirectionLabel(next)));
      return;
    }
    if (evt.target.closest("[data-pager-first]")) return goToPage(1);
    if (evt.target.closest("[data-pager-prev]")) return goToPage(store.getState().page - 1);
    if (evt.target.closest("[data-pager-next]")) return goToPage(store.getState().page + 1);
    if (evt.target.closest("[data-pager-last]")) {
      const s = store.getState();
      const totalPages = Math.max(1, Math.ceil((s.leaderboard?.total || 0) / s.pageSize));
      return goToPage(totalPages);
    }
    if (evt.target.closest("[data-notranked-pager-first]")) return goToNotRankedPage(1);
    if (evt.target.closest("[data-notranked-pager-prev]")) return goToNotRankedPage(store.getState().notRankedPage - 1);
    if (evt.target.closest("[data-notranked-pager-next]")) return goToNotRankedPage(store.getState().notRankedPage + 1);
    if (evt.target.closest("[data-notranked-pager-last]")) {
      const s = store.getState();
      const flat = s.notRanked ? flattenGroups(s.notRanked, { ecosystem: s.ecosystem, query: s.query }) : [];
      const totalPages = Math.max(1, Math.ceil(flat.length / s.notRankedPageSize));
      return goToNotRankedPage(totalPages);
    }
    // ------------------------------------------------- Configure (D)
    if (evt.target.closest("[data-configure-pager-first]")) return goToConfigurePage(1);
    if (evt.target.closest("[data-configure-pager-prev]")) return goToConfigurePage(store.getState().configurePage - 1);
    if (evt.target.closest("[data-configure-pager-next]")) return goToConfigurePage(store.getState().configurePage + 1);
    if (evt.target.closest("[data-configure-pager-last]")) {
      const s = store.getState();
      const filtered = s.reproject ? filterReprojectRows(s.reproject.rows, { ecosystem: s.ecosystem, query: s.query }) : [];
      const totalPages = Math.max(1, Math.ceil(filtered.length / s.configurePageSize));
      return goToConfigurePage(totalPages);
    }
    if (evt.target.closest("[data-configure-retry]")) {
      runReproject();
      return;
    }
    const lockBtn = evt.target.closest("[data-weight-lock]");
    if (lockBtn) {
      store.dispatch({ type: "TOGGLE_WEIGHT_LOCK", pillar: lockBtn.dataset.weightLock });
      return;
    }
    const presetBtn = evt.target.closest("[data-weight-preset]");
    if (presetBtn) {
      // U-11: a preset button is a STARTING POINT — the vector it sets is
      // immediately "custom" (Configure never re-labels itself a persona).
      const state = store.getState();
      const preset = PERSONA_PRESET_WEIGHTS[presetBtn.dataset.weightPreset];
      const next = applyPresetRespectingLocks(state.weights, state.weightLocks, preset);
      if (weightsEqual(next, state.weights)) return;
      store.dispatch({ type: "SET_WEIGHTS", weights: next });
      pushUrl(store.getState());
      runReproject();
      return;
    }

    // ---------------------------------------------------- Compare (E)
    const addBtn = evt.target.closest("[data-compare-add]");
    if (addBtn) {
      const key = addBtn.dataset.compareAdd;
      clearTimeout(compareSearchDebounce);
      compareSearchDebounce = null;
      store.dispatch({ type: "COMPARE_ADD_REPO", key });
      pushUrl(store.getState());
      if (!store.getState().compareDataByRepo[key]) loadCompareRepo(key);
      if (!store.getState().methodology && !store.getState().methodologyLoading) loadMethodology();
      return;
    }
    const quickAddBtn = evt.target.closest("[data-compare-quick-add]");
    if (quickAddBtn) {
      const key = quickAddBtn.dataset.compareQuickAdd;
      clearTimeout(compareSearchDebounce);
      compareSearchDebounce = null;
      store.dispatch({ type: "COMPARE_ADD_REPO", key });
      pushUrl(store.getState());
      if (!store.getState().compareDataByRepo[key]) loadCompareRepo(key);
      if (!store.getState().methodology && !store.getState().methodologyLoading) loadMethodology();
      return;
    }
    const comparePresetBtn = evt.target.closest("[data-compare-preset]");
    if (comparePresetBtn) {
      const repos = (comparePresetBtn.dataset.comparePreset || "")
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);
      clearTimeout(compareSearchDebounce);
      compareSearchDebounce = null;
      for (const existing of [...store.getState().compareRepos]) {
        store.dispatch({ type: "COMPARE_REMOVE_REPO", key: existing });
      }
      for (const key of repos) {
        store.dispatch({ type: "COMPARE_ADD_REPO", key });
        if (!store.getState().compareDataByRepo[key]) loadCompareRepo(key);
      }
      pushUrl(store.getState());
      if (!store.getState().methodology && !store.getState().methodologyLoading) loadMethodology();
      return;
    }
    if (evt.target.closest("[data-compare-clear]")) {
      for (const existing of [...store.getState().compareRepos]) {
        store.dispatch({ type: "COMPARE_REMOVE_REPO", key: existing });
      }
      pushUrl(store.getState());
      return;
    }
    const removeBtn = evt.target.closest("[data-compare-remove]");
    if (removeBtn) {
      store.dispatch({ type: "COMPARE_REMOVE_REPO", key: removeBtn.dataset.compareRemove });
      pushUrl(store.getState());
      return;
    }

    // ----------------------------------------------------- Matrix (B)
    const quadrantToggle = evt.target.closest("[data-matrix-quadrant-toggle]");
    if (quadrantToggle) {
      store.dispatch({ type: "MATRIX_TOGGLE_QUADRANT", quadrant: quadrantToggle.dataset.matrixQuadrantToggle });
      return;
    }
    if (evt.target.closest("[data-matrix-reset-view]")) {
      store.dispatch({ type: "MATRIX_RESET_VIEW", view: MATRIX_DEFAULT_VIEW });
      return;
    }
    if (evt.target.closest("[data-matrix-zoom-in]")) {
      store.dispatch({ type: "MATRIX_SET_VIEW", view: zoomView(store.getState().matrixView, 1.5) });
      return;
    }
    if (evt.target.closest("[data-matrix-zoom-out]")) {
      store.dispatch({ type: "MATRIX_SET_VIEW", view: zoomView(store.getState().matrixView, 1 / 1.5) });
      return;
    }

    // ------------------------------------------------ Methodology (F)
    // The "On this page" index (and the snapshot's detector-flag links).
    // Handled here rather than as a plain #fragment jump: routing lives in
    // the query string, and a fragment navigation would push a history
    // entry the router does not own. Focus follows the scroll so keyboard
    // and screen-reader users continue from the section they chose.
    const docJump = evt.target.closest("[data-doc-jump]");
    if (docJump) {
      const target = doc.getElementById(docJump.getAttribute("data-doc-jump"));
      if (target) {
        evt.preventDefault();
        // An instant jump, not a smooth scroll: sections sit up to ~7,000 px
        // apart, and a smooth scroll never advances in a hidden tab (measured).
        target.scrollIntoView({ block: "start" });
        const focusTarget = target.matches("[tabindex]") ? target : target.querySelector("[tabindex='-1']");
        if (focusTarget) focusTarget.focus({ preventScroll: true });
      }
      return;
    }
    if (evt.target.closest("[data-methodology-copy]")) {
      const pre = main.querySelector("[data-reproduce-command]");
      const btn = evt.target.closest("[data-methodology-copy]");
      if (pre && win.navigator.clipboard) {
        win.navigator.clipboard
          .writeText(pre.textContent)
          .then(() => {
            btn.textContent = copy.METHODOLOGY_COPIED_LABEL;
            announce(copy.METHODOLOGY_COPIED_LABEL);
            clearTimeout(methodologyCopyTimer);
            methodologyCopyTimer = win.setTimeout(() => {
              btn.textContent = copy.METHODOLOGY_COPY_LABEL;
              methodologyCopyTimer = null;
            }, 1200);
          })
          .catch(() => {
            // Clipboard permission denied/unavailable (a real possibility,
            // not just this sandbox's own restriction): leave the button
            // text alone rather than an unhandled rejection — the
            // reproduce command is still fully selectable/copyable by hand
            // from the visible <pre> block.
          });
      }
      return;
    }

    const row = evt.target.closest("[data-owner][data-name]");
    if (row) openRepo(row.dataset.owner, row.dataset.name);
  });

  function goToNotRankedPage(page) {
    if (page < 1) return;
    const s = store.getState();
    if (s.notRanked) {
      const flat = flattenGroups(s.notRanked, { ecosystem: s.ecosystem, query: s.query });
      const totalPages = Math.max(1, Math.ceil(flat.length / s.notRankedPageSize));
      if (page > totalPages) return;
    }
    store.dispatch({ type: "SET_NOTRANKED_PAGE", page });
    pushUrl(store.getState());
    patchNotRankedBody(main, store.getState());
    revealListTop();
  }

  // U-13 (amended): "the page itself scrolls" — the window/document scroll
  // drives windowing now, not a bounded container's own scroll event.
  win.addEventListener(
    "scroll",
    () => {
      if (store.getState().tab !== "ranked" || scrollPatchScheduled) return;
      scrollPatchScheduled = true;
      const runPatch = () => {
        if (!scrollPatchScheduled) return;
        scrollPatchScheduled = false;
        patchLeaderboardBody(store.getState());
      };
      win.requestAnimationFrame(runPatch);
      win.setTimeout(runPatch, 32);
    },
    listenerOpts({ passive: true })
  );

  // -------------------------------------------------------- Matrix
  // interactions: wheel zoom, hover card, keyboard focus between markers.
  main.addEventListener(
    "wheel",
    (evt) => {
      if (store.getState().tab !== "matrix") return;
      const stage = evt.target.closest(".matrix-stage");
      if (!stage) return;
      evt.preventDefault();
      const factor = evt.deltaY < 0 ? 1.15 : 1 / 1.15;
      store.dispatch({ type: "MATRIX_SET_VIEW", view: zoomView(store.getState().matrixView, factor) });
    },
    { passive: false }
  );

  main.addEventListener("mouseover", (evt) => {
    if (store.getState().tab !== "matrix") return;
    const marker = evt.target.closest(".matrix-marker");
    if (marker) {
      store.dispatch({ type: "MATRIX_SET_HOVER", key: marker.dataset.key });
      return;
    }
    const spotBtn = evt.target.closest("[data-matrix-spotlight-key]");
    if (spotBtn) store.dispatch({ type: "MATRIX_SET_HOVER", key: spotBtn.dataset.matrixSpotlightKey });
  });
  main.addEventListener("mouseout", (evt) => {
    if (store.getState().tab !== "matrix") return;
    const marker = evt.target.closest(".matrix-marker");
    if (marker && !evt.relatedTarget?.closest(".matrix-marker")) {
      store.dispatch({ type: "MATRIX_SET_HOVER", key: null });
    }
    const spotBtn = evt.target.closest("[data-matrix-spotlight-key]");
    if (spotBtn && !evt.relatedTarget?.closest("[data-matrix-spotlight-key]")) {
      store.dispatch({ type: "MATRIX_SET_HOVER", key: null });
    }
    if (evt.target.matches?.("[data-matrix-canvas]")) {
      store.dispatch({ type: "MATRIX_SET_HOVER", key: null });
    }
  });
  main.addEventListener("mousemove", (evt) => {
    const s = store.getState();
    if (s.tab !== "matrix" || !s.matrix) return;
    const canvas = evt.target.closest?.("[data-matrix-canvas]");
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const { domainX, domainY, toleranceDomainUnits } = clientToDomainCoords(evt.clientX, evt.clientY, rect, s.matrixView);
    const visiblePoints = filterMatrixPoints(s.matrix.points, s).filter((p) => !s.matrixQuadrantsHidden[p.quadrant]);
    const hit = hitTestPoint(visiblePoints, domainX, domainY, toleranceDomainUnits);
    const nextKey = hit ? matrixPointKey(hit) : null;
    if (s.matrixHoveredKey !== nextKey) {
      store.dispatch({ type: "MATRIX_SET_HOVER", key: nextKey });
    }
  });
  main.addEventListener("focusin", (evt) => {
    if (store.getState().tab !== "matrix") return;
    const marker = evt.target.closest(".matrix-marker");
    if (marker) {
      store.dispatch({ type: "MATRIX_SET_FOCUS", key: marker.dataset.key });
      return;
    }
    const spotBtn = evt.target.closest("[data-matrix-spotlight-key]");
    if (spotBtn) store.dispatch({ type: "MATRIX_SET_FOCUS", key: spotBtn.dataset.matrixSpotlightKey });
  });

  drawerRoot.addEventListener("click", (evt) => {
    const compareBtn = evt.target.closest("[data-drawer-compare]");
    if (compareBtn) {
      const key = compareBtn.dataset.drawerCompare;
      if (key) {
        store.dispatch({ type: "COMPARE_ADD_REPO", key });
        pushUrl(store.getState());
        if (!store.getState().compareDataByRepo[key]) loadCompareRepo(key);
        if (!store.getState().methodology && !store.getState().methodologyLoading) loadMethodology();
        closeDrawer();
        goToTab("compare");
      }
      return;
    }
    if (evt.target.closest("[data-drawer-close]") || evt.target.matches("[data-drawer-backdrop]")) {
      closeDrawer();
      return;
    }
    if (evt.target.closest("[data-evidence-retry]")) {
      const repo = store.getState().repo;
      if (!repo) return;
      const [owner, name] = repo.split("/");
      store.dispatch({ type: "EVIDENCE_LOADING" });
      loadEvidence(owner, name);
      return;
    }
    const badgeCopyBtn = evt.target.closest("[data-badge-copy]");
    if (badgeCopyBtn) {
      const pre = drawerRoot.querySelector("[data-badge-markdown]");
      if (pre && win.navigator.clipboard) {
        win.navigator.clipboard
          .writeText(pre.textContent)
          .then(() => {
            badgeCopyBtn.textContent = copy.MAINTAINER_BADGE_COPIED_LABEL;
            announce(copy.MAINTAINER_BADGE_COPIED_LABEL);
            clearTimeout(badgeCopyTimer);
            badgeCopyTimer = win.setTimeout(() => {
              badgeCopyBtn.textContent = copy.MAINTAINER_BADGE_COPY_LABEL;
              badgeCopyTimer = null;
            }, 1200);
          })
          .catch(() => {
            // Clipboard refused (permission or an insecure origin): the snippet stays
            // selectable, and one click selects all of it (user-select: all).
          });
      }
      return;
    }
    if (evt.target.closest("[data-repo-detail-retry]")) {
      const repo = store.getState().repo;
      if (!repo) return;
      const [owner, name] = repo.split("/");
      store.dispatch({ type: "REPO_DETAIL_LOADING" });
      loadRepoDetail(owner, name);
    }
  });

  keymapRoot.addEventListener("click", (evt) => {
    if (evt.target.closest("[data-keymap-close]") || evt.target.matches("[data-keymap-backdrop]")) {
      store.dispatch({ type: "TOGGLE_KEYMAP", open: false });
    }
  });

  function trapFocus(evt) {
    const s = store.getState();
    const container = s.keymapOpen
      ? keymapRoot.querySelector(".keymap-overlay")
      : s.repo
      ? drawerRoot.querySelector(".drawer")
      : null;
    if (!container) return;
    const focusable = Array.from(
      container.querySelectorAll(
        'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), summary, [tabindex]:not([tabindex="-1"]):not([disabled])'
      )
    ).filter((el) => {
      if (el.closest?.("[hidden]")) return false;
      const closedDetails = el.closest?.("details:not([open])");
      if (closedDetails && !(el.tagName === "SUMMARY" && el.parentElement === closedDetails)) return false;
      return true;
    });
    if (focusable.length === 0) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (evt.shiftKey && doc.activeElement === first) {
      evt.preventDefault();
      last.focus();
    } else if (!evt.shiftKey && doc.activeElement === last) {
      evt.preventDefault();
      first.focus();
    }
  }

  doc.addEventListener("keydown", (evt) => {
    const activeTag = doc.activeElement?.tagName;
    const isInputFocused = ["INPUT", "SELECT", "TEXTAREA"].includes(activeTag);
    const isInteractiveFocused = ["BUTTON", "A", "SUMMARY"].includes(activeTag);
    const s = store.getState();

    if (evt.key === "Tab" && (s.repo || s.keymapOpen)) {
      trapFocus(evt);
      return;
    }

    if ((evt.key === "Enter" || evt.key === " ") && doc.activeElement?.matches?.("th[data-sort-field]")) {
      evt.preventDefault();
      doc.activeElement.click();
      return;
    }

    if (
      evt.key === " " &&
      !isInputFocused &&
      !isInteractiveFocused &&
      !s.repo &&
      !s.keymapOpen &&
      doc.activeElement?.matches?.("[data-owner][data-name]")
    ) {
      evt.preventDefault();
      openRepo(doc.activeElement.dataset.owner, doc.activeElement.dataset.name);
      return;
    }

    if (
      !s.repo &&
      !s.keymapOpen &&
      (evt.key === "ArrowLeft" || evt.key === "ArrowRight" || evt.key === "Home" || evt.key === "End") &&
      doc.activeElement?.getAttribute?.("role") === "tab"
    ) {
      const tablist = doc.activeElement.closest?.('[role="tablist"]');
      if (tablist) {
        const tabs = Array.from(tablist.querySelectorAll('[role="tab"]'));
        const idx = tabs.indexOf(doc.activeElement);
        if (idx !== -1 && tabs.length > 0) {
          evt.preventDefault();
          let nextIdx = idx;
          if (evt.key === "ArrowRight") nextIdx = (idx + 1) % tabs.length;
          else if (evt.key === "ArrowLeft") nextIdx = (idx - 1 + tabs.length) % tabs.length;
          else if (evt.key === "Home") nextIdx = 0;
          else if (evt.key === "End") nextIdx = tabs.length - 1;
          const nextTabEl = tabs[nextIdx];
          nextTabEl.focus();
          nextTabEl.click();
          return;
        }
      }
    }

    const action = actionForKeyEvent(evt, { isInputFocused, isInteractiveFocused });
    if (!action) return;

    switch (action) {
      case "focus-search": {
        if (s.repo || s.keymapOpen) return;
        if (s.tab === "compare") {
          const compareSearch = main.querySelector("[data-compare-search]");
          if (compareSearch) {
            evt.preventDefault();
            compareSearch.focus();
          }
          break;
        }
        const searchGroup = header.querySelector("[data-header-search-group]");
        if (searchGroup && searchGroup.hidden) return;
        evt.preventDefault();
        header.querySelector("[data-search-input]")?.focus();
        break;
      }
      case "cursor-down":
      case "cursor-up": {
        if (s.repo || s.keymapOpen) return;
        if (isInputFocused) return;
        evt.preventDefault();
        if (s.tab === "matrix") {
          const points = s.matrix
            ? filterMatrixPoints(s.matrix.points, s).filter((p) => !s.matrixQuadrantsHidden[p.quadrant])
            : [];
          if (points.length === 0) return;
          const curIdx = points.findIndex((p) => matrixPointKey(p) === s.matrixFocusedKey);
          const delta = action === "cursor-down" ? 1 : -1;
          const nextIdx = Math.min(Math.max((curIdx === -1 ? -1 : curIdx) + delta, 0), points.length - 1);
          const key = matrixPointKey(points[nextIdx]);
          store.dispatch({ type: "MATRIX_SET_FOCUS", key });
          main.querySelector(`[data-key="${CSS.escape(key)}"]`)?.focus();
          return;
        }
        if (s.tab === "notranked") {
          const flat = s.notRanked ? flattenGroups(s.notRanked, { ecosystem: s.ecosystem, query: s.query }) : [];
          if (flat.length === 0) return;
          const totalPages = Math.max(1, Math.ceil(flat.length / s.notRankedPageSize));
          const page = Math.min(s.notRankedPage, totalPages);
          const start = (page - 1) * s.notRankedPageSize;
          const pageRows = flat.slice(start, start + s.notRankedPageSize).map((item) => item.row);
          if (pageRows.length === 0) return;
          const curIdx = pageRows.findIndex((r) => rowKey(r) === s.selectedRepoKey);
          const delta = action === "cursor-down" ? 1 : -1;
          const nextIdx = Math.min(Math.max((curIdx === -1 ? -1 : curIdx) + delta, 0), pageRows.length - 1);
          const nextKey = rowKey(pageRows[nextIdx]);
          store.dispatch({ type: "SET_CURSOR", key: nextKey });
          focusRowByKey(nextKey);
          return;
        }
        if (s.tab === "configure") {
          const filtered = s.reproject ? filterReprojectRows(s.reproject.rows, { ecosystem: s.ecosystem, query: s.query }) : [];
          const sorted = sortReprojectRows(filtered, s.configureSort || "rank");
          const totalPages = Math.max(1, Math.ceil(sorted.length / s.configurePageSize));
          const page = Math.min(s.configurePage, totalPages);
          const start = (page - 1) * s.configurePageSize;
          const pageRows = sorted.slice(start, start + s.configurePageSize);
          if (pageRows.length === 0) return;
          const curIdx = pageRows.findIndex((r) => rowKey(r) === s.selectedRepoKey);
          const delta = action === "cursor-down" ? 1 : -1;
          const nextIdx = Math.min(Math.max((curIdx === -1 ? -1 : curIdx) + delta, 0), pageRows.length - 1);
          const nextKey = rowKey(pageRows[nextIdx]);
          store.dispatch({ type: "SET_CURSOR", key: nextKey });
          focusRowByKey(nextKey);
          return;
        }
        const rows = s.leaderboard ? s.leaderboard.rows : [];
        if (rows.length === 0) return;
        const curIdx = rows.findIndex((r) => rowKey(r) === s.selectedRepoKey);
        const delta = action === "cursor-down" ? 1 : -1;
        const nextIdx = Math.min(Math.max((curIdx === -1 ? -1 : curIdx) + delta, 0), rows.length - 1);
        const nextKey = rowKey(rows[nextIdx]);
        store.dispatch({ type: "SET_CURSOR", key: nextKey });
        // Scroll BEFORE the second patch so a row that was outside the
        // windowed buffer actually exists in the DOM by the time we try to
        // focus it (U-18: keyboard focus must move with the cursor, not
        // just an internal "selected" flag and a CSS class, ensuring
        // document.activeElement moves so keyboard users see the focus move).
        scrollRowIntoView(nextIdx);
        patchLeaderboardBody(store.getState());
        focusRowByKey(nextKey);
        break;
      }
      case "matrix-focus-up":
      case "matrix-focus-down":
      case "matrix-focus-left":
      case "matrix-focus-right": {
        if (s.tab !== "matrix" || !s.matrix || s.repo || s.keymapOpen) return;
        const stage = main.querySelector(".matrix-stage");
        if (!stage || !stage.contains(doc.activeElement)) return;
        const direction = action.replace("matrix-focus-", "");
        // Above SVG_MARKER_BUDGET markers there is no per-marker DOM node
        // (the canvas fallback, components/scatter.js) — arrow keys always
        // navigate a virtual marker index in that mode (panning stays
        // available via wheel and +/- buttons). Below the budget, the
        // SVG disambiguation applies: stage focus pans, marker focus navigates.
        const allPoints = filterMatrixPoints(s.matrix.points, s);
        const visiblePoints = allPoints.filter((p) => !s.matrixQuadrantsHidden[p.quadrant]);
        const usesCanvas = visiblePoints.length > SVG_MARKER_BUDGET;
        const markerFocused = !usesCanvas && doc.activeElement?.classList.contains("matrix-marker");
        if (!usesCanvas && !markerFocused) {
          // The stage itself (not a marker) has focus: arrow keys PAN
          // (module doc's reconciliation of "zoom/pan by wheel and keys"
          // vs. "focus moves between markers (Tab/arrow keys)").
          evt.preventDefault();
          const pan = { up: [0, -8], down: [0, 8], left: [-8, 0], right: [8, 0] }[direction];
          store.dispatch({ type: "MATRIX_SET_VIEW", view: panView(s.matrixView, pan[0], pan[1]) });
          return;
        }
        const next = nearestMarkerInDirection(visiblePoints, s.matrixFocusedKey, direction);
        if (!next) return;
        evt.preventDefault();
        const key = matrixPointKey(next);
        store.dispatch({ type: "MATRIX_SET_FOCUS", key });
        // Only the SVG path has a real DOM node per marker to move real
        // focus onto; the canvas path's "focus" is purely the state key
        // driving the SVG overlay ring (renderFocusOverlaySvg).
        if (!usesCanvas) main.querySelector(`[data-key="${CSS.escape(key)}"]`)?.focus();
        break;
      }
      case "matrix-toggle-1":
      case "matrix-toggle-2":
      case "matrix-toggle-3":
      case "matrix-toggle-4": {
        if (s.tab !== "matrix" || s.repo || s.keymapOpen) return;
        const index = Number(action.slice(-1)) - 1;
        store.dispatch({ type: "MATRIX_TOGGLE_QUADRANT", quadrant: MATRIX_QUADRANTS[index] });
        break;
      }
      case "matrix-reset-view":
        if (s.tab !== "matrix" || s.repo || s.keymapOpen) return;
        store.dispatch({ type: "MATRIX_RESET_VIEW" });
        break;
      case "nav-leaderboard":
        if (isInputFocused || s.repo || s.keymapOpen) return;
        goToTab("ranked");
        break;
      case "nav-matrix":
        if (isInputFocused || s.repo || s.keymapOpen) return;
        goToTab("matrix");
        break;
      case "nav-compare":
        if (isInputFocused || s.repo || s.keymapOpen) return;
        goToTab("compare");
        break;
      case "nav-configure":
        if (isInputFocused || s.repo || s.keymapOpen) return;
        goToTab("configure");
        break;
      case "nav-methodology":
        if (isInputFocused || s.repo || s.keymapOpen) return;
        goToTab("methodology");
        break;
      case "open-selected": {
        if (isInputFocused || s.repo || s.keymapOpen) return;
        if (["BUTTON", "A", "SUMMARY"].includes(doc.activeElement?.tagName)) return;
        // U-18/§5: resolve the target from DOM focus FIRST — a row reached
        // by Tab or a direct `.focus()` call never went through SET_CURSOR,
        // so consulting only the J/K cursor opened nothing for it. The cursor
        // is the fallback for when focus is elsewhere but a row was previously
        // selected. `[data-owner][data-name]` matches both a leaderboard
        // `<tr>` AND a Matrix `.matrix-marker` `<g>` (both carry the same
        // two data attributes) — resolving the key is enough to call
        // `openRepo` directly without requiring a pre-loaded `row` object
        // (since the matrix tab never loads `state.leaderboard`).
        const focusedRow = doc.activeElement?.closest?.("[data-owner][data-name]") ?? null;
        // Above the canvas budget a marker has no DOM node at all to
        // resolve via `[data-owner][data-name]` — `matrixFocusedKey`
        // (the virtual index arrow keys move) is the fallback specifically
        // for that case ("click AND Enter open the drawer", 06 §3 B).
        const matrixVirtualKey = s.tab === "matrix" ? s.matrixFocusedKey : null;
        const focusedKey = focusedRow
          ? `${focusedRow.dataset.owner}/${focusedRow.dataset.name}`
          : matrixVirtualKey;
        const cursorKey =
          !s.tab || s.tab === "ranked" || s.tab === "notranked" || s.tab === "configure"
            ? s.selectedRepoKey
            : null;
        const targetKey = resolveOpenTargetKey(cursorKey, focusedKey);
        if (targetKey) {
          const [owner, name] = targetKey.split("/");
          openRepo(owner, name);
        }
        break;
      }
      case "close-overlay":
        if (s.keymapOpen) store.dispatch({ type: "TOGGLE_KEYMAP", open: false });
        else if (s.repo) closeDrawer();
        break;
      case "next-page":
        if (isInputFocused || s.repo || s.keymapOpen) return;
        if (s.tab === "notranked") goToNotRankedPage(s.notRankedPage + 1);
        else if (s.tab === "configure") goToConfigurePage(s.configurePage + 1);
        else goToPage(s.page + 1);
        break;
      case "prev-page":
        if (isInputFocused || s.repo || s.keymapOpen) return;
        if (s.tab === "notranked") goToNotRankedPage(s.notRankedPage - 1);
        else if (s.tab === "configure") goToConfigurePage(s.configurePage - 1);
        else goToPage(s.page - 1);
        break;
      case "open-persona-menu": {
        if (isInputFocused || s.repo || s.keymapOpen) return;
        const personaGroup = header.querySelector("[data-header-persona-group]");
        if (personaGroup && personaGroup.hidden) return;
        header.querySelector("[data-persona-select]")?.focus();
        break;
      }
      case "toggle-theme":
        if (isInputFocused || s.repo || s.keymapOpen) return;
        header.querySelector("[data-theme-toggle]")?.click();
        break;
      case "toggle-keymap":
        store.dispatch({ type: "TOGGLE_KEYMAP" });
        break;
      default:
        break;
    }
  }, listenerOpts());

  function scrollRowIntoView(index) {
    const container = main.querySelector("[data-leaderboard-tbody], [data-leaderboard-cards]");
    if (!container) return;
    const vp = currentViewport();
    const itemHeight = vp.isMobile ? CARD_HEIGHT_PX : ROW_HEIGHT_PX;
    const headerSticky =
      header && typeof win.getComputedStyle === "function" && win.getComputedStyle(header).position === "sticky"
        ? header.getBoundingClientRect().height
        : 0;
    const navSticky =
      primaryNav && typeof win.getComputedStyle === "function" && win.getComputedStyle(primaryNav).position === "sticky"
        ? primaryNav.getBoundingClientRect().height
        : 0;
    const thead = !vp.isMobile ? main.querySelector(".leaderboard-table thead") : null;
    const theadSticky = thead && typeof thead.getBoundingClientRect === "function" ? thead.getBoundingClientRect().height : 0;
    const stickyTop = headerSticky + navSticky + theadSticky;
    const containerTop = container.getBoundingClientRect().top + win.scrollY;
    const top = containerTop + index * itemHeight;
    const bottom = top + itemHeight;
    if (top < win.scrollY + stickyTop) win.scrollTo(0, Math.max(0, top - stickyTop));
    else if (bottom > win.scrollY + win.innerHeight) win.scrollTo(0, bottom - win.innerHeight);
  }

  /** Moves DOM focus to the row/card for `key` (U-18: focus must track the J/K cursor, not just internal state + a CSS class). No-op if the row is not currently rendered (should not happen: callers patch the DOM for the target row's index first). */
  function focusRowByKey(key) {
    if (!key) return;
    const [owner, name] = key.split("/");
    main.querySelector(`[data-owner="${CSS.escape(owner)}"][data-name="${CSS.escape(name)}"]`)?.focus();
  }

  win.addEventListener("popstate", () => {
    clearTimeout(searchDebounce);
    clearTimeout(reprojectDebounce);
    clearTimeout(compareSearchDebounce);
    searchDebounce = null;
    reprojectDebounce = null;
    compareSearchDebounce = null;
    searchDirty = false;
    const previousState = store.getState();
    // popstate must be able to CLEAR a param that is absent from the new
    // URL (e.g. navigating from ?eco=pypi back to no eco at all), so every
    // URL-owned field is reset to its default before re-applying whatever
    // parseSearch finds in the new location.
    const defaults = {
      persona: "balanced", ecosystem: "", query: "", sort: "rank",
      page: 1, pageSize: DEFAULT_PAGE_SIZE, tab: "ranked", repo: null,
      weights: { ...BALANCED_WEIGHTS }, compareRepos: [],
    };
    store.dispatch({
      type: "SET_PARAMS",
      resetTabSlices: true,
      payload: { ...defaults, ...parseSearch(win.location.search) },
    });
    const s = store.getState();
    const paramsChanged =
      s.persona !== previousState.persona || s.ecosystem !== previousState.ecosystem ||
      s.query !== previousState.query || s.sort !== previousState.sort ||
      s.page !== previousState.page || s.pageSize !== previousState.pageSize;
    if (paramsChanged) maybeLoadLeaderboardForCurrentTab();
    if (s.tab === "notranked" && s.persona !== previousState.persona) loadNotRanked();
    const repoToLoad = repoToLoadFromState(s);
    if (repoToLoad) {
      loadRepoDetail(repoToLoad.owner, repoToLoad.name);
      loadEvidence(repoToLoad.owner, repoToLoad.name);
    }
    const weightsChanged = PILLAR_IDS.some((p) => s.weights[p] !== previousState.weights[p]);
    if (weightsChanged || (s.tab === "configure" && !s.reproject)) runReproject();
    loadDataForTab(s.tab);
  }, listenerOpts());

  win.addEventListener("resize", () => {
    syncStickyOffsets();
    // A resize can cross the 768px breakpoint (table <-> card list), which
    // is a shell-shape change (shellShape folds isMobile in), so this goes
    // through syncView rather than a bare patch.
    if (store.getState().tab === "ranked") syncView(store.getState());
    if (store.getState().tab === "matrix") drawMatrixCanvasIfPresent(store.getState());
  }, listenerOpts());

  // ------------------------------------------------------------ Boot
  function applyTheme(theme, document_ = doc) {
    const root = document_.documentElement;
    if (theme === "system") delete root.dataset.theme;
    else root.dataset.theme = theme;
  }

  const skipLink = doc.querySelector(".skip-link");
  if (skipLink) skipLink.textContent = copy.SKIP_LINK_LABEL;
  main.setAttribute("aria-label", copy.MAIN_LANDMARK_LABEL);

  buildHeaderOnce();
  buildFooterOnce();
  buildPrimaryNavOnce();
  syncStickyOffsets();

  const persisted = readPersistedTheme(win.localStorage);
  store.dispatch({ type: "SET_THEME", theme: persisted });
  applyTheme(persisted, doc);
  const initialThemeBtn = header.querySelector("[data-theme-toggle]");
  if (initialThemeBtn) {
    const isSystemDark = win.matchMedia && win.matchMedia("(prefers-color-scheme: dark)").matches;
    const isDark = persisted === "dark" || (persisted === "system" && isSystemDark);
    initialThemeBtn.textContent = isDark ? "🌙" : "☀";
    initialThemeBtn.setAttribute("title", `Active theme: ${isDark ? "dark" : "light"}`);
  }

  // Dev-only perf harness (U-19: "measured with the 1000-row page as the
  // perf case"): `?perf=1000` synthesizes a page-shaped payload client-side
  // instead of fetching, so windowing can be timed with `performance.now()`
  // in a real browser without needing a 1000-row fixture on the server.
  const perfParam = new URLSearchParams(win.location.search).get("perf");
  if (perfParam) {
    const n = parseInt(perfParam, 10) || 1000;
    const t0 = win.performance.now();
    const rows = synthesizeRows(n);
    store.dispatch({
      type: "LEADERBOARD_OK",
      data: {
        rows, total: rows.length, counts: { rated: rows.length, gated: 0, unrated: 0, total: rows.length },
        page: 1, page_size: rows.length, persona: "balanced", ecosystem: null,
      },
    });
    const t1 = win.performance.now();
    // Dev-only timing readout (06 §7 budgets): never user-facing, exempt
    // from copy.js.
    // eslint-disable-next-line no-console
    console.log(`[perf] synthesized ${n} rows in ${(t1 - t0).toFixed(1)}ms`);
    render();
    win.requestAnimationFrame(() => {
      const t2 = win.performance.now();
      patchLeaderboardBody(store.getState());
      const t3 = win.performance.now();
      // eslint-disable-next-line no-console
      console.log(`[perf] initial windowed patch of ${n} rows: ${(t3 - t2).toFixed(1)}ms`);
    });
  } else if (new URLSearchParams(win.location.search).get("perfmatrix")) {
    // Dev-only harness (06 §7: "matrix render < 150ms for 10k markers"):
    // reuses `synthesizeRows` (its rows already carry leverage/friction/
    // quadrant/stars_audited — a superset of `MatrixPoint`) so no second
    // synthetic-data generator is needed for this budget.
    const n = parseInt(new URLSearchParams(win.location.search).get("perfmatrix"), 10) || 10000;
    const t0 = win.performance.now();
    const points = synthesizeRows(n);
    store.dispatch({ type: "SET_TAB", tab: "matrix" });
    store.dispatch({ type: "MATRIX_OK", data: { points, excluded_count: 0, axis_coverage: {} } });
    const t1 = win.performance.now();
    // eslint-disable-next-line no-console
    console.log(`[perf] synthesized ${n} matrix points in ${(t1 - t0).toFixed(1)}ms`);
    const t2 = win.performance.now();
    render();
    const t3 = win.performance.now();
    // eslint-disable-next-line no-console
    console.log(`[perf] matrix render of ${n} points: ${(t3 - t2).toFixed(1)}ms`);
  } else {
    replaceUrl(store.getState());
    render();
    const initialState = store.getState();
    const initialTab = initialState.tab;
    if (initialTab === "ranked" || initialTab === "notranked") loadLeaderboard();
    loadDataForTab(initialTab);
    // U-7/U-15/U-16: a drawer opened straight from a URL (?repo=owner/name,
    // no click ever happened) must fetch the same record+evidence a
    // click-opened one does — see repoToLoadFromState's own docstring.
    // `openRepo` is not reused here because the URL is already authoritative
    // (repo/cursor/history are already correct); only the two fetches are missing.
    // `renderDrawerAndKeymap` (called inside the `render()` just above)
    // already focuses the drawer's close button for a URL-hydrated
    // `state.repo` on this very first render (`wasDrawerOpen` starts
    // `false`) — only the two fetches were missing.
    const bootRepoToLoad = repoToLoadFromState(initialState);
    if (bootRepoToLoad) {
      store.dispatch({ type: "SET_CURSOR", key: initialState.repo });
      loadRepoDetail(bootRepoToLoad.owner, bootRepoToLoad.name);
      loadEvidence(bootRepoToLoad.owner, bootRepoToLoad.name);
    }
  }
  loadSnapshot();

  return {
    store,
    render,
    patchLeaderboardBody,
    loadLeaderboard,
    loadMatrix,
    loadMethodology,
    runReproject,
    goToTab,
    destroy: () => {
      if (controller) controller.abort();
      clearTimeout(searchDebounce);
      clearTimeout(reprojectDebounce);
      clearTimeout(compareSearchDebounce);
      clearTimeout(methodologyCopyTimer);
      clearTimeout(badgeCopyTimer);
    },
  };
}

if (typeof window !== "undefined" && typeof document !== "undefined") {
  // Dev-only debug hook (never referenced by any user-facing code path):
  // lets a real-browser check force a windowed patch synchronously instead
  // of waiting on requestAnimationFrame, which some automated/headless
  // browser contexts throttle indefinitely while a tab lacks OS focus.
  window.__porchlampDebug = boot();
}
