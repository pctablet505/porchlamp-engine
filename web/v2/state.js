// state.js — single store with subscribe + URL sync (06 §6, U-9, amended
// twice by the lead on 2026-09-13: first the "single virtualized list, no
// pagination" ruling, then the owner's verbatim instruction that REPLACED
// it with real pagination — 20/50/100/500/1000 page sizes, default 20,
// next/prev/first/last controls. This file reflects the OWNER'S final
// ruling: `page`/`pageSize` are back in the URL-synced state).
//
// URL shape (the real query string, ruling U-16):
//   ?persona=&eco=&q=&sort=&page=&page_size=&repo=owner%2Fname&tab=
// persona/eco/sort/page/page_size/tab/repo changes use history.pushState
// (each is a meaningful navigation step); `q` uses replaceState while
// typing and one pushState when committed — app.js owns that distinction,
// state.js only exposes the pure parse/serialize pair.
//
// The pure functions (`defaultState`, `parseSearch`, `serializeSearch`,
// `reduce`, `countRankChanges`) are exported separately from the store
// singleton so they stay testable without a DOM or a running server.

const PERSISTED_THEME_KEY = "cvi-v2-theme";

//: Owner instruction 2026-09-13 (verbatim): "keep value of pagesize like
//: 20, 50, 100, 500,1000". Mirrors `cvi.serve.contract.LEADERBOARD_PAGE_SIZES`.
export const PAGE_SIZES = Object.freeze([20, 50, 100, 500, 1000]);
export const DEFAULT_PAGE_SIZE = 20;

export function defaultState() {
  return {
    persona: "balanced",
    ecosystem: "",
    query: "",
    sort: "rank",
    page: 1,
    pageSize: DEFAULT_PAGE_SIZE,
    tab: "ranked", // "ranked" | "notranked"
    repo: null, // "owner/name" | null
    theme: "system", // "system" | "light" | "dark"

    leaderboard: null, // the CURRENT page's LeaderboardResponse
    leaderboardLoading: false, // true only when there is NO data yet (skeleton)
    leaderboardRefetching: false, // true while re-fetching (persona/eco/q/sort/page/pageSize change) with a page already showing (grey + progress bar, old rows stay visible)
    leaderboardError: null, // ApiError | null; stale-but-shown data stays in `leaderboard`

    // Single-slot "prefetch the next page" cache (ruling: "prefetch the
    // next page after the current one renders"). `signature` identifies
    // exactly which (persona, eco, query, sort, page, pageSize) the cached
    // `data` answers; a mismatch means it must be discarded, not served.
    nextPageCache: null, // { signature: string, data: LeaderboardResponse } | null

    notRanked: null, // the full NotRankedResponse (fetched once per persona; paginated CLIENT-SIDE, see the handoff)
    notRankedLoading: false,
    notRankedError: null,
    notRankedPage: 1,
    notRankedPageSize: DEFAULT_PAGE_SIZE,

    snapshot: null,
    snapshotLoading: false,
    snapshotError: null,

    evidenceByRepo: {}, // "owner/name" -> EvidenceRow[]
    evidenceLoading: false,
    evidenceError: null, // { endpoint, status } for the CURRENTLY open repo's evidence, if it failed

    selectedRepoKey: null, // "owner/name" — J/K cursor; survives patches (keyed, not index-based)
    announcement: "", // aria-live polite text
    keymapOpen: false,
  };
}

/** Parse `location.search` (with or without the leading "?") into a partial state. */
export function parseSearch(search) {
  const qs = new URLSearchParams(search.replace(/^\?/, ""));
  const out = {};
  if (qs.has("persona")) out.persona = qs.get("persona");
  if (qs.has("eco")) out.ecosystem = qs.get("eco");
  if (qs.has("q")) out.query = qs.get("q");
  if (qs.has("sort")) out.sort = qs.get("sort");
  if (qs.has("page")) {
    const n = parseInt(qs.get("page"), 10);
    if (Number.isFinite(n) && n >= 1) out.page = n;
  }
  if (qs.has("page_size")) {
    const n = parseInt(qs.get("page_size"), 10);
    if (PAGE_SIZES.includes(n)) out.pageSize = n;
  }
  if (qs.has("repo")) out.repo = qs.get("repo") || null;
  if (qs.has("tab")) out.tab = qs.get("tab") === "notranked" ? "notranked" : "ranked";
  return out;
}

/** Serialize the URL-relevant slice of state back into a "?..." string ("" when every param is default). */
export function serializeSearch(state) {
  const qs = new URLSearchParams();
  if (state.persona && state.persona !== "balanced") qs.set("persona", state.persona);
  if (state.ecosystem) qs.set("eco", state.ecosystem);
  if (state.query) qs.set("q", state.query);
  if (state.sort && state.sort !== "rank") qs.set("sort", state.sort);
  if (state.page && state.page !== 1) qs.set("page", String(state.page));
  if (state.pageSize && state.pageSize !== DEFAULT_PAGE_SIZE) qs.set("page_size", String(state.pageSize));
  if (state.repo) qs.set("repo", state.repo);
  if (state.tab && state.tab !== "ranked") qs.set("tab", state.tab);
  const s = qs.toString();
  return s ? `?${s}` : "";
}

/** The exact key identifying which request a leaderboard payload answers — used to validate the prefetch cache and to detect a stale in-flight response. */
export function requestSignature(state) {
  return `${state.persona}|${state.ecosystem}|${state.query}|${state.sort}|${state.page}|${state.pageSize}`;
}

/**
 * Ruling U-17: the aria-live "N rows moved" count is the number of rows
 * (matched by owner/name) whose `rank` differs between the previous and
 * next payload — never a raw row-count delta (a persona that only hides
 * rows, with every surviving rank unchanged, correctly announces 0).
 */
export function countRankChanges(previousRows, nextRows) {
  if (!previousRows || !nextRows) return 0;
  const previousRankByKey = new Map(previousRows.map((r) => [`${r.owner}/${r.name}`, r.rank]));
  let moved = 0;
  for (const row of nextRows) {
    const key = `${row.owner}/${row.name}`;
    if (previousRankByKey.has(key) && previousRankByKey.get(key) !== row.rank) moved++;
  }
  return moved;
}

/**
 * Pure reducer: given the current state and an action, returns the next
 * state. Kept separate from the store so it is unit-testable without a
 * fetch/DOM environment.
 */
export function reduce(state, action) {
  switch (action.type) {
    case "SET_PARAMS":
      return { ...state, ...action.payload };
    case "SET_TAB":
      return { ...state, tab: action.tab };
    case "SET_PAGE":
      return { ...state, page: action.page };
    case "SET_PAGE_SIZE":
      return { ...state, pageSize: action.pageSize, page: action.page ?? state.page };
    case "SET_NOTRANKED_PAGE":
      return { ...state, notRankedPage: action.page };
    case "SET_NOTRANKED_PAGE_SIZE":
      return { ...state, notRankedPageSize: action.pageSize, notRankedPage: action.page ?? state.notRankedPage };
    case "OPEN_REPO":
      return { ...state, repo: action.repo, evidenceError: null };
    case "CLOSE_DRAWER":
      return { ...state, repo: null, evidenceError: null };
    case "SET_THEME":
      return { ...state, theme: action.theme };
    case "SET_CURSOR":
      return { ...state, selectedRepoKey: action.key };
    case "TOGGLE_KEYMAP":
      return { ...state, keymapOpen: action.open ?? !state.keymapOpen };
    case "ANNOUNCE":
      return { ...state, announcement: action.text };
    case "LEADERBOARD_LOADING":
      return { ...state, leaderboardLoading: true, leaderboardError: null };
    case "LEADERBOARD_REFETCHING":
      return { ...state, leaderboardRefetching: true, leaderboardError: null };
    case "LEADERBOARD_OK":
      return {
        ...state,
        leaderboardLoading: false,
        leaderboardRefetching: false,
        leaderboard: action.data,
        leaderboardError: null,
      };
    case "LEADERBOARD_ERROR":
      return { ...state, leaderboardLoading: false, leaderboardRefetching: false, leaderboardError: action.error };
    case "SET_NEXT_PAGE_CACHE":
      return { ...state, nextPageCache: action.entry };
    case "NOTRANKED_LOADING":
      return { ...state, notRankedLoading: true, notRankedError: null };
    case "NOTRANKED_OK":
      return { ...state, notRankedLoading: false, notRanked: action.data, notRankedError: null, notRankedPage: 1 };
    case "NOTRANKED_ERROR":
      return { ...state, notRankedLoading: false, notRankedError: action.error };
    case "SNAPSHOT_LOADING":
      return { ...state, snapshotLoading: true, snapshotError: null };
    case "SNAPSHOT_OK":
      return { ...state, snapshotLoading: false, snapshot: action.data, snapshotError: null };
    case "SNAPSHOT_ERROR":
      return { ...state, snapshotLoading: false, snapshotError: action.error };
    case "EVIDENCE_LOADING":
      return { ...state, evidenceLoading: true, evidenceError: null };
    case "EVIDENCE_OK":
      return {
        ...state,
        evidenceLoading: false,
        evidenceError: null,
        evidenceByRepo: { ...state.evidenceByRepo, [action.repo]: action.data },
      };
    case "EVIDENCE_ERROR":
      return { ...state, evidenceLoading: false, evidenceError: action.error };
    default:
      return state;
  }
}

export function readPersistedTheme(storage) {
  try {
    const value = storage.getItem(PERSISTED_THEME_KEY);
    return value === "light" || value === "dark" ? value : "system";
  } catch {
    return "system";
  }
}

export function writePersistedTheme(storage, theme) {
  try {
    if (theme === "system") storage.removeItem(PERSISTED_THEME_KEY);
    else storage.setItem(PERSISTED_THEME_KEY, theme);
  } catch {
    // Storage unavailable (private mode, disabled site data): theme just
    // does not persist across reloads for this viewer.
  }
}

/** Creates the mutable store singleton the app uses. */
export function createStore(initialSearch = "") {
  let state = { ...defaultState(), ...parseSearch(initialSearch) };
  const subscribers = new Set();

  function notify(action) {
    for (const fn of subscribers) fn(state, action);
  }

  function dispatch(action) {
    state = reduce(state, action);
    notify(action);
    return state;
  }

  return {
    getState: () => state,
    subscribe(fn) {
      subscribers.add(fn);
      return () => subscribers.delete(fn);
    },
    dispatch,
  };
}
