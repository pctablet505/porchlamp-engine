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
import { renderDrawer } from "./views/drawer.js";
import { renderBrand } from "./components/brand.js";
import { renderKeymapOverlay, actionForKeyEvent } from "./components/keymap.js";
import { computeVisibleRange, rowKey, ROW_HEIGHT_PX, CARD_HEIGHT_PX, sortDirectionLabel } from "./components/table.js";
import { synthesizeRows } from "./perf-fixtures.js";
import { renderPrimaryNav, syncPrimaryNav } from "./components/primarynav.js";
import {
  renderShell as renderMatrixShell,
  shellShape as matrixShellShape,
  syncViewBox as syncMatrixViewBox,
  syncMatrixHover,
  zoomView,
  panView,
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
  let searchDebounce = null;
  let compareSearchDebounce = null;
  let reprojectDebounce = null;
  let inFlightSignature = null; // guards against a stale response clobbering a newer one
  let reprojectInFlightSignature = null;

  function announce(text) {
    store.dispatch({ type: "ANNOUNCE", text });
    if (liveRegion) liveRegion.textContent = text;
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
      `<label class="field-label">${escapeHtml(copy.PERSONA_LABEL)}</label>` +
      `<select class="control" data-persona-select="true">${personaOptions}</select>` +
      `<label class="field-label">${escapeHtml(copy.ECOSYSTEM_LABEL)}</label>` +
      `<select class="control" data-eco-select="true">` +
      `<option value="">${escapeHtml(copy.ECOSYSTEM_ALL)}</option>` +
      ["pypi", "npm", "cargo", "go", "maven"].map((e) => `<option value="${e}">${e}</option>`).join("") +
      `</select>` +
      `<input class="control" type="search" data-search-input="true" placeholder="${escapeHtml(copy.SEARCH_PLACEHOLDER)}" aria-label="${escapeHtml(copy.SEARCH_LABEL)}">` +
      `<button type="button" class="icon-button" data-theme-toggle="true" aria-label="${escapeHtml(copy.THEME_TOGGLE_LABEL)}">◐</button>` +
      `</div>`;
  }

  // ------------------------------------------------------ Primary nav
  function buildPrimaryNavOnce() {
    primaryNav.innerHTML = renderPrimaryNav(store.getState().tab);
  }

  /** Fetches whatever `tab` needs that has not already loaded — called on boot, a primary-nav click, popstate, and the footer "verify" link. Idempotent: never re-fetches data that is already present. */
  function loadDataForTab(tab) {
    const state = store.getState();
    if (tab === "ranked" && !state.leaderboard && !state.leaderboardLoading) loadLeaderboard();
    if (tab === "matrix" && !state.matrix && !state.matrixLoading) loadMatrix();
    if (tab === "configure" && !state.reproject && !state.reprojectLoading) runReproject();
    if (tab === "methodology" && !state.methodology && !state.methodologyLoading) loadMethodology();
    if (tab === "compare") {
      if (!state.methodology && !state.methodologyLoading) loadMethodology();
      for (const key of state.compareRepos) {
        if (!state.compareDataByRepo[key]) loadCompareRepo(key);
      }
    }
    if (tab === "notranked" && !state.notRanked) loadNotRanked();
  }

  function syncHeaderControls(state) {
    const personaSelect = header.querySelector("[data-persona-select]");
    const ecoSelect = header.querySelector("[data-eco-select]");
    const searchInput = header.querySelector("[data-search-input]");
    const snapshotChip = header.querySelector("[data-snapshot-chip]");
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
            ? `${fields.snapshot.snapshot_id} · ${fields.snapshot.benchmark_version}`
            : copy.LOADING_LABEL;
      if (snapshotChip.textContent !== text) snapshotChip.textContent = text;
      snapshotChip.classList.toggle("error-chip", fields.kind === "error");
    }
  }

  // ---------------------------------------------------------------- Footer
  function buildFooterOnce() {
    footer.innerHTML =
      `<span data-footer-snapshot="true"></span>` +
      `<span data-footer-version="true"></span>` +
      `<span data-footer-hash="true"></span>` +
      `<span data-footer-manifest="true"></span>` +
      `<button type="button" class="icon-button" data-footer-retry="true" hidden>${escapeHtml(copy.RETRY_LABEL)}</button>` +
      `<a href="#methodology" data-footer-verify="true">${escapeHtml(copy.FOOTER_VERIFY_LABEL)}</a>`;
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

  /** Updates only the `<th>` `aria-sort` attributes to match `state.sort` — never a shell rebuild. */
  function syncSortHeaders(state) {
    const headers = main.querySelectorAll("[data-sort-field]");
    for (const th of headers) {
      const isActive = state.sort.replace(/^-/, "") === th.dataset.sortField;
      if (!isActive) {
        if (th.hasAttribute("aria-sort")) th.removeAttribute("aria-sort");
        continue;
      }
      // sortDirectionLabel is the single source of truth for the
      // effective direction (see components/table.js) — a field's
      // "best first" convention (descending for scores, ascending for
      // rank/tier) is not simply "no '-' prefix = ascending".
      const dir = sortDirectionLabel(state.sort);
      if (th.getAttribute("aria-sort") !== dir) th.setAttribute("aria-sort", dir);
    }
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
        errBanner.textContent = copy.errorBannerText(state.leaderboardError.endpoint, state.leaderboardError.status, state.leaderboardError.detail);
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
    if (canvas.width !== width) canvas.width = width;
    if (canvas.height !== height) canvas.height = height;
    const styles = win.getComputedStyle(doc.documentElement);
    const colors = {
      "Sweet Spot": styles.getPropertyValue("--quad-sweet-spot").trim(),
      Crucible: styles.getPropertyValue("--quad-crucible").trim(),
      Nursery: styles.getPropertyValue("--quad-nursery").trim(),
      Graveyard: styles.getPropertyValue("--quad-graveyard").trim(),
    };
    const ctx = canvas.getContext("2d");
    drawMatrixCanvas(ctx, state.matrix.points, { quadrantsHidden: state.matrixQuadrantsHidden, colors, width, height });
  }

  function syncView(state) {
    if (state.tab === "notranked") {
      const key = notRankedRebuildKey(state);
      if (currentTab !== "notranked" || key !== currentNotRankedShape) {
        main.innerHTML = renderNotRankedShell(state);
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
    if (currentTab !== "ranked" || shape !== currentShellShape) {
      main.innerHTML = renderShell(state, viewport);
      currentTab = "ranked";
      currentShellShape = shape;
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
    store.dispatch({ type: "NOTRANKED_LOADING" });
    return api.fetchNotRanked({ persona: store.getState().persona }).then((result) => {
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
        store.dispatch({ type: "EVIDENCE_ERROR", error: result.error });
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
        store.dispatch({ type: "REPO_DETAIL_ERROR", error: result.error });
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
    return api.postReproject(body).then((result) => {
      if (reprojectInFlightSignature !== signature) return; // superseded by a newer drag
      if (!result.ok) {
        store.dispatch({ type: "REPROJECT_ERROR", error: result.error });
        return;
      }
      store.dispatch({ type: "REPROJECT_OK", data: result.data });
    });
  }

  // ------------------------------------------------------ Compare (E)
  function searchCompare(query) {
    const trimmed = query.trim();
    if (!trimmed) {
      store.dispatch({ type: "COMPARE_SET_RESULTS", results: [] });
      return Promise.resolve();
    }
    const alreadyAdded = new Set(store.getState().compareRepos);
    return Promise.all([
      api.fetchLeaderboard({ q: trimmed, page_size: 20 }),
      api.fetchNotRanked({ persona: "balanced" }),
    ]).then(([leaderboardResult, notRankedResult]) => {
      const results = [];
      if (leaderboardResult.ok) {
        for (const r of leaderboardResult.data.rows) {
          results.push({ owner: r.owner, name: r.name, ecosystem: r.ecosystem, kind: "rated" });
        }
      }
      if (notRankedResult.ok) {
        const qLower = trimmed.toLowerCase();
        for (const group of ["gated", "unrated"]) {
          for (const r of notRankedResult.data[group]) {
            if (`${r.owner}/${r.name}`.toLowerCase().includes(qLower)) {
              results.push({ owner: r.owner, name: r.name, ecosystem: r.ecosystem, kind: group === "gated" ? "gated" : "unrated" });
            }
          }
        }
      }
      const deduped = results.filter((r) => !alreadyAdded.has(`${r.owner}/${r.name}`));
      store.dispatch({ type: "COMPARE_SET_RESULTS", results: deduped.slice(0, 8) });
    });
  }

  function loadCompareRepo(key) {
    const [owner, name] = key.split("/");
    store.dispatch({ type: "COMPARE_REPO_LOADING", key });

    return Promise.all([
      api.fetchLeaderboard({ q: key, page_size: 1000 }),
      api.fetchNotRanked({ persona: "balanced" }),
    ]).then(([leaderboardResult, notRankedResult]) => {
      if (!store.getState().compareRepos.includes(key)) return; // removed while in flight
      let kind = null;
      let row = null;
      if (leaderboardResult.ok) {
        row = leaderboardResult.data.rows.find((r) => r.owner === owner && r.name === name);
        if (row) kind = "rated";
      }
      if (!row && notRankedResult.ok) {
        row = notRankedResult.data.gated.find((r) => r.owner === owner && r.name === name);
        if (row) kind = "gated";
        if (!row) {
          row = notRankedResult.data.unrated.find((r) => r.owner === owner && r.name === name);
          if (row) kind = "unrated";
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
  }

  function paramsForState(state) {
    return {
      persona: state.persona,
      eco: state.ecosystem,
      q: state.query,
      sort: state.sort,
      page: state.page,
      page_size: state.pageSize,
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
    store.dispatch({ type: "LEADERBOARD_OK", data: result.data });
    if (requestedFromState.persona !== "balanced" && requestedFromState.persona === store.getState().persona) {
      announce(copy.reprojectedAnnouncement(copy.PERSONA_NAMES[requestedFromState.persona], countRankChanges(previousRows, result.data.rows)));
    }
    prefetchNextPage(store.getState());
  }

  // ------------------------------------------------------------- Rendering
  function renderDrawerAndKeymap(state) {
    const drawerHtml = renderDrawer(state);
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
      // before this rebuild, restore it to the close button afterward.
      const focusWasInDrawer = drawerRoot.contains(doc.activeElement);
      drawerRoot.innerHTML = drawerHtml;
      drawerRoot.dataset.lastHtml = drawerHtml;
      if (shouldRestoreFocusAfterRebuild(state.repo, focusWasInDrawer)) {
        drawerRoot.querySelector("[data-drawer-close]")?.focus();
      }
    }
    const keymapHtml = state.keymapOpen ? renderKeymapOverlay() : "";
    if (keymapRoot.dataset.lastHtml !== keymapHtml) {
      const focusWasInKeymap = keymapRoot.contains(doc.activeElement);
      keymapRoot.innerHTML = keymapHtml;
      keymapRoot.dataset.lastHtml = keymapHtml;
      if (shouldRestoreFocusAfterRebuild(state.keymapOpen, focusWasInKeymap)) {
        keymapRoot.querySelector("[data-keymap-close]")?.focus();
      }
    }
    if (state.keymapOpen && !wasKeymapOpen) keymapRoot.querySelector("[data-keymap-close]")?.focus();
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
  function changeParamsAndFetch(payload) {
    store.dispatch({ type: "SET_PARAMS", payload: { ...payload, page: 1 } });
    pushUrl(store.getState());
    loadLeaderboard();
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
        store.dispatch({ type: "SET_PARAMS", payload: { query, page: 1 } });
        urlSyncMode("type") === "replace" ? replaceUrl(store.getState()) : pushUrl(store.getState());
        loadLeaderboard();
      }, 80);
    }
  });
  header.addEventListener("keydown", (evt) => {
    if (evt.target.matches("[data-search-input]") && evt.key === "Enter") {
      if (searchDebounce) {
        clearTimeout(searchDebounce);
        searchDebounce = null;
        store.dispatch({ type: "SET_PARAMS", payload: { query: evt.target.value, page: 1 } });
        loadLeaderboard();
      }
      if (urlSyncMode("commit") === "push") pushUrl(store.getState(), { force: true });
    }
  });
  header.addEventListener(
    "blur",
    (evt) => {
      if (evt.target.matches && evt.target.matches("[data-search-input]")) {
        if (searchDebounce) {
          clearTimeout(searchDebounce);
          searchDebounce = null;
          store.dispatch({ type: "SET_PARAMS", payload: { query: evt.target.value, page: 1 } });
          loadLeaderboard();
        }
        if (urlSyncMode("commit") === "push") pushUrl(store.getState(), { force: true });
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
      store.dispatch({ type: "SET_THEME", theme: next });
      applyTheme(next, doc);
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
    const key = store.getState().selectedRepoKey;
    store.dispatch({ type: "CLOSE_DRAWER" });
    pushUrl(store.getState());
    if (!key) return;
    const [owner, name] = key.split("/");
    main.querySelector(`[data-owner="${CSS.escape(owner)}"][data-name="${CSS.escape(name)}"]`)?.focus();
  }

  /** Ruling: "changing the page size keeps the current first visible row on screen". Anchors on the cursor row if one is selected, else the current page's first loaded row. */
  function goToPage(newPage) {
    if (newPage < 1) return;
    store.dispatch({ type: "SET_PAGE", page: newPage });
    pushUrl(store.getState());
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
    loadLeaderboard();
  }

  main.addEventListener("change", (evt) => {
    if (evt.target.matches("[data-sort-field]")) return; // handled via click on <th>, not a <select>
    if (evt.target.matches("[data-pager-size-select]")) {
      goToPageSize(parseInt(evt.target.value, 10));
    } else if (evt.target.matches("[data-notranked-pager-size-select]")) {
      const state = store.getState();
      store.dispatch({ type: "SET_NOTRANKED_PAGE_SIZE", pageSize: parseInt(evt.target.value, 10), page: 1 });
      patchNotRankedBody(main, store.getState());
      void state;
    } else if (evt.target.matches("[data-configure-pager-size-select]")) {
      store.dispatch({ type: "SET_CONFIGURE_PAGE_SIZE", pageSize: parseInt(evt.target.value, 10), page: 1 });
      patchConfigureTable(main, doc, store.getState());
    }
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
        pushUrl(store.getState());
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
        const domainX = ((evt.clientX - rect.left) / rect.width) * 100;
        const domainY = ((evt.clientY - rect.top) / rect.height) * 100;
        // A click within ~15 real screen pixels of a point's centre counts
        // as hitting it — converted to domain units at THIS canvas's own
        // rendered width, so the tolerance is consistent regardless of
        // plot size (same reasoning as components/scatter.js's radius
        // pixel-anchoring).
        const toleranceDomainUnits = (15 / rect.width) * 100;
        const visiblePoints = s.matrix.points.filter((p) => !s.matrixQuadrantsHidden[p.quadrant]);
        const hit = hitTestPoint(visiblePoints, domainX, domainY, toleranceDomainUnits);
        if (hit) {
          store.dispatch({ type: "MATRIX_SET_FOCUS", key: matrixPointKey(hit) });
          openRepo(hit.owner, hit.name);
        }
      }
      return;
    }
    const tabBtn = evt.target.closest("[data-tab]");
    if (tabBtn) {
      const tab = tabBtn.dataset.tab;
      store.dispatch({ type: "SET_TAB", tab });
      pushUrl(store.getState());
      if (tab === "notranked" && !store.getState().notRanked) loadNotRanked();
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
        return;
      }
      if (currentTabValue !== "ranked") return; // the header is only ever meaningfully sortable on these two tabs
      const current = store.getState().sort;
      const next = current === field ? `-${field}` : field;
      changeParamsAndFetch({ sort: next });
      announce(copy.sortedAnnouncement(field));
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
      const flat = s.notRanked ? flattenGroups(s.notRanked) : [];
      const totalPages = Math.max(1, Math.ceil(flat.length / s.notRankedPageSize));
      return goToNotRankedPage(totalPages);
    }
    // ------------------------------------------------- Configure (D)
    if (evt.target.closest("[data-configure-pager-prev]")) {
      store.dispatch({ type: "SET_CONFIGURE_PAGE", page: Math.max(1, store.getState().configurePage - 1) });
      patchConfigureTable(main, doc, store.getState());
      return;
    }
    if (evt.target.closest("[data-configure-pager-next]")) {
      store.dispatch({ type: "SET_CONFIGURE_PAGE", page: store.getState().configurePage + 1 });
      patchConfigureTable(main, doc, store.getState());
      return;
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
      store.dispatch({ type: "COMPARE_ADD_REPO", key });
      pushUrl(store.getState());
      if (!store.getState().compareDataByRepo[key]) loadCompareRepo(key);
      if (!store.getState().methodology && !store.getState().methodologyLoading) loadMethodology();
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
            const original = btn.textContent;
            btn.textContent = copy.METHODOLOGY_COPIED_LABEL;
            win.setTimeout(() => {
              btn.textContent = original;
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
    store.dispatch({ type: "SET_NOTRANKED_PAGE", page });
    patchNotRankedBody(main, store.getState());
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
    if (marker) store.dispatch({ type: "MATRIX_SET_HOVER", key: marker.dataset.key });
  });
  main.addEventListener("mouseout", (evt) => {
    if (store.getState().tab !== "matrix") return;
    const marker = evt.target.closest(".matrix-marker");
    if (marker && !evt.relatedTarget?.closest(".matrix-marker")) {
      store.dispatch({ type: "MATRIX_SET_HOVER", key: null });
    }
  });
  main.addEventListener("focusin", (evt) => {
    if (store.getState().tab !== "matrix") return;
    const marker = evt.target.closest(".matrix-marker");
    if (marker) store.dispatch({ type: "MATRIX_SET_FOCUS", key: marker.dataset.key });
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
    );
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

    const action = actionForKeyEvent(evt, { isInputFocused, isInteractiveFocused });
    if (!action) return;

    switch (action) {
      case "focus-search":
        evt.preventDefault();
        header.querySelector("[data-search-input]")?.focus();
        break;
      case "cursor-down":
      case "cursor-up": {
        if (s.repo || s.keymapOpen) return;
        if (isInputFocused) return;
        evt.preventDefault();
        if (s.tab === "matrix") {
          const points = s.matrix ? s.matrix.points : [];
          if (points.length === 0) return;
          const curIdx = points.findIndex((p) => matrixPointKey(p) === s.matrixFocusedKey);
          const delta = action === "cursor-down" ? 1 : -1;
          const nextIdx = Math.min(Math.max((curIdx === -1 ? -1 : curIdx) + delta, 0), points.length - 1);
          const key = matrixPointKey(points[nextIdx]);
          store.dispatch({ type: "MATRIX_SET_FOCUS", key });
          main.querySelector(`[data-key="${CSS.escape(key)}"]`)?.focus();
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
        if (s.tab !== "matrix" || !s.matrix) return;
        const direction = action.replace("matrix-focus-", "");
        // Above SVG_MARKER_BUDGET markers there is no per-marker DOM node
        // (the canvas fallback, components/scatter.js) — arrow keys always
        // navigate a virtual marker index in that mode (panning stays
        // available via wheel and +/- buttons). Below the budget, the
        // SVG disambiguation applies: stage focus pans, marker focus navigates.
        const usesCanvas = s.matrix.points.length > SVG_MARKER_BUDGET;
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
        const next = nearestMarkerInDirection(s.matrix.points, s.matrixFocusedKey, direction);
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
        if (s.tab !== "matrix") return;
        const index = Number(action.slice(-1)) - 1;
        store.dispatch({ type: "MATRIX_TOGGLE_QUADRANT", quadrant: MATRIX_QUADRANTS[index] });
        break;
      }
      case "matrix-reset-view":
        if (s.tab !== "matrix") return;
        store.dispatch({ type: "MATRIX_RESET_VIEW" });
        break;
      case "nav-leaderboard":
        if (isInputFocused) return;
        goToTab("ranked");
        break;
      case "nav-matrix":
        if (isInputFocused) return;
        goToTab("matrix");
        break;
      case "nav-compare":
        if (isInputFocused) return;
        goToTab("compare");
        break;
      case "nav-configure":
        if (isInputFocused) return;
        goToTab("configure");
        break;
      case "nav-methodology":
        if (isInputFocused) return;
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
        const targetKey = resolveOpenTargetKey(s.selectedRepoKey, focusedKey);
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
        goToPage(s.page + 1);
        break;
      case "prev-page":
        if (isInputFocused || s.repo || s.keymapOpen) return;
        goToPage(s.page - 1);
        break;
      case "open-persona-menu":
        header.querySelector("[data-persona-select]")?.focus();
        break;
      case "toggle-theme":
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
    const containerTop = container.getBoundingClientRect().top + win.scrollY;
    const top = containerTop + index * ROW_HEIGHT_PX;
    const bottom = top + ROW_HEIGHT_PX;
    if (top < win.scrollY) win.scrollTo(0, top);
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
    store.dispatch({ type: "SET_PARAMS", payload: { ...defaults, ...parseSearch(win.location.search) } });
    const s = store.getState();
    const paramsChanged =
      s.persona !== previousState.persona || s.ecosystem !== previousState.ecosystem ||
      s.query !== previousState.query || s.sort !== previousState.sort ||
      s.page !== previousState.page || s.pageSize !== previousState.pageSize;
    if (paramsChanged) loadLeaderboard();
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
    // A resize can cross the 768px breakpoint (table <-> card list), which
    // is a shell-shape change (shellShape folds isMobile in), so this goes
    // through syncView rather than a bare patch.
    if (store.getState().tab === "ranked") syncView(store.getState());
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
