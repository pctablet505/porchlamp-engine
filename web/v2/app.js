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
  urlSyncMode,
  PAGE_SIZES,
  DEFAULT_PAGE_SIZE,
} from "./state.js";
import {
  renderShell,
  shellShape,
  patchTableBody,
  patchCardBody,
  syncPager,
} from "./views/leaderboard.js";
import {
  renderShell as renderNotRankedShell,
  shellShape as notRankedShellShape,
  rebuildKey as notRankedRebuildKey,
  patchNotRankedBody,
  flattenGroups,
} from "./views/notranked.js";
import { renderDrawer } from "./views/drawer.js";
import { renderKeymapOverlay, actionForKeyEvent } from "./components/keymap.js";
import { computeVisibleRange, rowKey, ROW_HEIGHT_PX, CARD_HEIGHT_PX, sortDirectionLabel } from "./components/table.js";
import { synthesizeRows } from "./perf-fixtures.js";

const PERSONAS = ["balanced", "first_timer", "career_capital", "senior_systems", "ai_assisted"];

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (ch) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]
  ));
}

export function boot(win = window, doc = document) {
  const store = createStore(win.location.search);

  const header = doc.getElementById("app-header-controls");
  const main = doc.getElementById("app-main");
  const footer = doc.getElementById("app-footer");
  const liveRegion = doc.getElementById("app-live-region");
  const drawerRoot = doc.getElementById("app-drawer-root");
  const keymapRoot = doc.getElementById("app-keymap-root");

  // --- Structural bookkeeping (never re-derived from the DOM: these are
  // the "did the SHAPE change" flags that decide shell-rebuild vs patch). ---
  let currentTab = null;
  let currentShellShape = null;
  let currentNotRankedShape = null;
  let scrollPatchScheduled = false;
  let wasDrawerOpen = false;
  let wasKeymapOpen = false;
  let searchDebounce = null;
  let inFlightSignature = null; // guards against a stale response clobbering a newer one

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
      `<span class="brand">${escapeHtml(copy.BRAND_NAME)}</span>` +
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
    if (snapshotChip && state.snapshot) {
      const text = `${state.snapshot.snapshot_id} · ${state.snapshot.benchmark_version}`;
      if (snapshotChip.textContent !== text) snapshotChip.textContent = text;
    }
  }

  // ---------------------------------------------------------------- Footer
  function buildFooterOnce() {
    footer.innerHTML =
      `<span data-footer-snapshot="true"></span>` +
      `<span data-footer-version="true"></span>` +
      `<span data-footer-hash="true"></span>` +
      `<span data-footer-manifest="true"></span>` +
      `<a href="#methodology" data-footer-verify="true">${escapeHtml(copy.FOOTER_VERIFY_LABEL)}</a>`;
  }

  function syncFooter(state) {
    const snap = state.snapshot;
    const set = (sel, text) => {
      const el2 = footer.querySelector(sel);
      if (el2 && el2.textContent !== text) el2.textContent = text;
    };
    if (!snap) {
      set("[data-footer-snapshot]", copy.LOADING_LABEL);
      set("[data-footer-version]", "");
      set("[data-footer-hash]", "");
      set("[data-footer-manifest]", "");
      return;
    }
    set("[data-footer-snapshot]", `${copy.FOOTER_SNAPSHOT_LABEL}: ${snap.snapshot_id}`);
    set("[data-footer-version]", `${copy.FOOTER_VERSION_LABEL}: ${snap.benchmark_version}`);
    set("[data-footer-hash]", `${copy.FOOTER_CONSTANTS_HASH_LABEL}: ${snap.constants_hash.slice(0, 12)}…`);
    set("[data-footer-manifest]", `${copy.FOOTER_MANIFEST_ROOT_LABEL}: ${snap.manifest_root.slice(0, 12)}…`);
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
    // computeHiddenCount/renderCountsStrip live in views/leaderboard.js;
    // re-rendering just this small, bounded element (never an ancestor of
    // a focused control) keeps U-4's "hidden by this lens (N)" disclosure
    // correct across a persona switch or a late-arriving snapshot without
    // a shell rebuild.
    const strip = main.querySelector("[data-counts-strip]");
    if (!strip || !state.leaderboard) return;
    // Recompute via the same logic renderShell used (kept in one place).
    let hiddenCount;
    if (state.persona !== "balanced" && state.snapshot) {
      const visible = state.snapshot.persona_visible_counts[state.persona];
      if (typeof visible === "number") hiddenCount = state.leaderboard.counts.rated - visible;
    }
    const hiddenText =
      typeof hiddenCount === "number" && hiddenCount > 0 ? copy.personaHiddenDisclosure(hiddenCount) : null;
    const existingHiddenSpan = strip.querySelector("[data-hidden-count]");
    if (hiddenText) {
      if (existingHiddenSpan) {
        if (existingHiddenSpan.textContent !== hiddenText) existingHiddenSpan.textContent = hiddenText;
      } else {
        const span = doc.createElement("span");
        span.dataset.hiddenCount = "true";
        span.textContent = hiddenText;
        strip.appendChild(span);
      }
    } else if (existingHiddenSpan) {
      strip.removeChild(existingHiddenSpan);
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
        errBanner.textContent = copy.errorBannerText(state.leaderboardError.endpoint, state.leaderboardError.status);
      } else {
        errBanner.hidden = true;
      }
    }
    const scrollable = main.querySelector(".leaderboard-table, [data-leaderboard-cards]");
    if (scrollable) scrollable.classList.toggle("stale-table", !!(state.leaderboardRefetching || state.leaderboardError));
  }

  function syncView(state) {
    if (state.tab === "notranked") {
      const key = notRankedRebuildKey(state);
      if (currentTab !== "notranked" || key !== currentNotRankedShape) {
        main.innerHTML = renderNotRankedShell(state);
        currentTab = "notranked";
        currentNotRankedShape = key;
        currentShellShape = null;
      }
      if (notRankedShellShape(state) === "table") patchNotRankedBody(main, state);
      return;
    }

    const viewport = currentViewport();
    const shape = shellShape(state, viewport);
    if (currentTab !== "ranked" || shape !== currentShellShape) {
      main.innerHTML = renderShell(state, viewport);
      currentTab = "ranked";
      currentShellShape = shape;
      currentNotRankedShape = null;
    }
    if (shape.startsWith("table")) patchLeaderboardBody(state);
  }

  // ------------------------------------------------------------- URL sync
  // U-16: persona/eco/sort/page/page_size/tab/repo changes are each a real
  // navigation step (pushState); `q` while TYPING is not — every debounced
  // keystroke uses replaceState, and only an explicit commit (Enter, or
  // blurring the search field) turns the current query into one pushState
  // entry (reviewer-found Blocker B-5: `replaceUrl` was defined but never
  // called, so every debounce window pushed a fresh history entry).
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
      if (urlSyncMode("commit") === "push") pushUrl(store.getState(), { force: true });
    }
  });
  header.addEventListener(
    "blur",
    (evt) => {
      if (evt.target.matches && evt.target.matches("[data-search-input]")) {
        if (urlSyncMode("commit") === "push") pushUrl(store.getState(), { force: true });
      }
    },
    true
  );

  header.addEventListener("click", (evt) => {
    if (evt.target.closest("[data-theme-toggle]")) {
      const order = ["system", "light", "dark"];
      const next = order[(order.indexOf(store.getState().theme) + 1) % order.length];
      store.dispatch({ type: "SET_THEME", theme: next });
      applyTheme(next, doc);
      writePersistedTheme(win.localStorage, next);
    }
  });

  function openRepo(owner, name) {
    const key = `${owner}/${name}`;
    store.dispatch({ type: "SET_CURSOR", key });
    store.dispatch({ type: "OPEN_REPO", repo: key });
    pushUrl(store.getState());
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
    }
  });

  main.addEventListener("click", (evt) => {
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
    { passive: true }
  );

  drawerRoot.addEventListener("click", (evt) => {
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
    }
  });

  keymapRoot.addEventListener("click", (evt) => {
    if (evt.target.closest("[data-keymap-close]") || evt.target.matches("[data-keymap-backdrop]")) {
      store.dispatch({ type: "TOGGLE_KEYMAP", open: false });
    }
  });

  function trapFocus(evt) {
    const container = store.getState().repo ? drawerRoot.querySelector(".drawer") : keymapRoot.querySelector(".keymap-overlay");
    if (!container) return;
    const focusable = container.querySelectorAll('button, [href], input, select, [tabindex]:not([tabindex="-1"])');
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
    const isInputFocused = ["INPUT", "SELECT", "TEXTAREA"].includes(doc.activeElement?.tagName);
    const s = store.getState();

    if (evt.key === "Tab" && (s.repo || s.keymapOpen)) {
      trapFocus(evt);
      return;
    }

    const action = actionForKeyEvent(evt, { isInputFocused });
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
        // just an internal "selected" flag and a CSS class — lead-reported
        // defect: j/k updated state but never moved document.activeElement,
        // so a keyboard user (and any test reading focus) saw nothing move).
        scrollRowIntoView(nextIdx);
        patchLeaderboardBody(store.getState());
        focusRowByKey(nextKey);
        break;
      }
      case "open-selected": {
        if (isInputFocused || s.repo || s.keymapOpen) return;
        // U-18/§5 (lead-reported defect on c52ab45): resolve the target
        // from DOM focus FIRST — a row reached by Tab or a direct
        // `.focus()` call never went through SET_CURSOR, so consulting
        // only the J/K cursor opened nothing for it. The cursor is the
        // fallback for when focus is elsewhere but a row was previously
        // selected.
        const focusedRow = doc.activeElement?.closest?.("[data-owner][data-name]") ?? null;
        const focusedKey = focusedRow ? `${focusedRow.dataset.owner}/${focusedRow.dataset.name}` : null;
        const targetKey = resolveOpenTargetKey(s.selectedRepoKey, focusedKey);
        const rows = s.leaderboard ? s.leaderboard.rows : [];
        const row = rows.find((r) => rowKey(r) === targetKey);
        if (row) openRepo(row.owner, row.name);
        break;
      }
      case "close-overlay":
        if (s.repo) closeDrawer();
        else if (s.keymapOpen) store.dispatch({ type: "TOGGLE_KEYMAP", open: false });
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
  });

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
    const previousState = store.getState();
    // popstate must be able to CLEAR a param that is absent from the new
    // URL (e.g. navigating from ?eco=pypi back to no eco at all), so every
    // URL-owned field is reset to its default before re-applying whatever
    // parseSearch finds in the new location.
    const defaults = {
      persona: "balanced", ecosystem: "", query: "", sort: "rank",
      page: 1, pageSize: DEFAULT_PAGE_SIZE, tab: "ranked", repo: null,
    };
    store.dispatch({ type: "SET_PARAMS", payload: { ...defaults, ...parseSearch(win.location.search) } });
    const s = store.getState();
    const paramsChanged =
      s.persona !== previousState.persona || s.ecosystem !== previousState.ecosystem ||
      s.query !== previousState.query || s.sort !== previousState.sort ||
      s.page !== previousState.page || s.pageSize !== previousState.pageSize;
    if (paramsChanged) loadLeaderboard();
    if (s.repo) {
      const [owner, name] = s.repo.split("/");
      loadEvidence(owner, name);
    }
    if (s.tab === "notranked" && !s.notRanked) loadNotRanked();
  });

  win.addEventListener("resize", () => {
    // A resize can cross the 768px breakpoint (table <-> card list), which
    // is a shell-shape change (shellShape folds isMobile in), so this goes
    // through syncView rather than a bare patch.
    if (store.getState().tab === "ranked") syncView(store.getState());
  });

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

  const persisted = readPersistedTheme(win.localStorage);
  store.dispatch({ type: "SET_THEME", theme: persisted });
  applyTheme(persisted, doc);

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
  } else {
    render();
    loadLeaderboard();
    if (store.getState().tab === "notranked") loadNotRanked();
  }
  loadSnapshot();

  return { store, render, patchLeaderboardBody, loadLeaderboard };
}

if (typeof window !== "undefined" && typeof document !== "undefined") {
  // Dev-only debug hook (never referenced by any user-facing code path):
  // lets a real-browser check force a windowed patch synchronously instead
  // of waiting on requestAnimationFrame, which some automated/headless
  // browser contexts throttle indefinitely while a tab lacks OS focus.
  window.__cviDebug = boot();
}
