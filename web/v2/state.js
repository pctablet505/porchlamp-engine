// state.js — single store with subscribe + URL sync (06 §6, U-9).
// Pagination is real: 20/50/100/500/1000 page sizes, default 20,
// next/prev/first/last controls, with `page`/`pageSize` part of the
// URL-synced state.
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

const PERSISTED_THEME_KEY = "porchlamp-v2-theme";

//: The page sizes the pagination control offers. Mirrors
//: `porchlamp.serve.contract.LEADERBOARD_PAGE_SIZES`.
export const PAGE_SIZES = Object.freeze([20, 50, 100, 500, 1000]);
export const DEFAULT_PAGE_SIZE = 20;

// --------------------------------------------------------------------- //
// `tab` holds four primary-screen values ("matrix" | "compare" |
// "configure" | "methodology") alongside the "ranked" | "notranked"
// Leaderboard sub-tabs — the "Primary tabs" bar (06 §2) treats
// "ranked"/"notranked" as both belonging to one "Leaderboard" tab. `w=`
// and `cmp=` are the two extra URL-owned fields.
// --------------------------------------------------------------------- //

//: The five pillars, in the fixed D-AGG-1 order — also the order `w=` uses.
export const PILLAR_IDS = Object.freeze(["responsiveness", "criticality", "governance", "rigor", "accessibility"]);
//: D-AGG-1's balanced weights — Configure's own starting point/reset target.
export const BALANCED_WEIGHTS = Object.freeze({
  responsiveness: 0.25,
  criticality: 0.25,
  governance: 0.2,
  rigor: 0.15,
  accessibility: 0.15,
});
//: The five persona presets (`porchlamp.core.constants.PERSONA_PRESETS`,
//: transcribed — Configure's preset BUTTONS start a custom vector from one
//: of these, but the result is immediately relabelled "custom" (U-11): a
//: preset button is a starting point, not a mode switch back to a persona).
export const PERSONA_PRESET_WEIGHTS = Object.freeze({
  balanced: BALANCED_WEIGHTS,
  first_timer: Object.freeze({ responsiveness: 0.35, criticality: 0.1, governance: 0.15, rigor: 0.1, accessibility: 0.3 }),
  career_capital: Object.freeze({ responsiveness: 0.2, criticality: 0.35, governance: 0.15, rigor: 0.1, accessibility: 0.2 }),
  senior_systems: Object.freeze({ responsiveness: 0.15, criticality: 0.35, governance: 0.25, rigor: 0.2, accessibility: 0.05 }),
  ai_assisted: Object.freeze({ responsiveness: 0.2, criticality: 0.2, governance: 0.15, rigor: 0.35, accessibility: 0.1 }),
});
//: The four quadrant ids Matrix's `1`-`4` keys toggle, in that key order.
export const MATRIX_QUADRANTS = Object.freeze(["Sweet Spot", "Crucible", "Nursery", "Graveyard"]);
//: Screen E: 2..4 repositories may be compared at once (06 §3 E).
export const COMPARE_MIN = 2;
export const COMPARE_MAX = 4;

/**
 * `w=` URL serialisation (06 §3 D leaves the exact shape to the
 * frontend): five comma-separated fractions in
 * `PILLAR_IDS` order, e.g. `w=0.3,0.2,0.2,0.2,0.1`. Returns `null` for a
 * malformed or wrong-length value (the caller falls back to
 * `BALANCED_WEIGHTS`) — never throws on a hand-edited URL.
 */
export function parseWeightsParam(value) {
  if (!value) return null;
  const rawParts = value.split(",");
  if (rawParts.some((p) => p.trim() === "")) return null;
  const parts = rawParts.map(Number);
  if (parts.length !== PILLAR_IDS.length || parts.some((n) => !Number.isFinite(n) || n < 0)) return null;
  const sum = parts.reduce((a, b) => a + b, 0);
  if (Math.abs(sum - 1) > 1e-6) return null;
  const weights = {};
  PILLAR_IDS.forEach((pillar, i) => {
    weights[pillar] = parts[i];
  });
  return weights;
}

export function serializeWeightsParam(weights) {
  return PILLAR_IDS.map((p) => {
    const n = weights[p];
    // Trim float noise (e.g. 0.30000000000000004) without losing precision
    // real proportional normalisation needs — 8 significant decimals is
    // far below where two DIFFERENT weight vectors could ever collide.
    return Math.round(n * 1e8) / 1e8;
  }).join(",");
}

/**
 * `cmp=` URL serialisation: comma-separated `owner/name` pairs (06 §3 E:
 * "URL `cmp=a/b,c/d`").
 */
export function parseCompareParam(value) {
  if (!value) return [];
  const validRepoRegex = /^[a-zA-Z0-9_.-]+\/[a-zA-Z0-9_.-]+$/;
  const seen = new Set();
  const result = [];
  for (const s of value.split(",")) {
    const trimmed = s.trim();
    if (trimmed && validRepoRegex.test(trimmed) && !seen.has(trimmed)) {
      seen.add(trimmed);
      result.push(trimmed);
      if (result.length >= COMPARE_MAX) break;
    }
  }
  return result;
}

export function serializeCompareParam(repos) {
  return repos.join(",");
}

export function defaultState() {
  return {
    persona: "balanced",
    ecosystem: "",
    query: "",
    sort: "rank",
    page: 1,
    pageSize: DEFAULT_PAGE_SIZE,
    tab: "ranked", // "ranked" | "notranked" | "matrix" | "compare" | "configure" | "methodology"
    repo: null, // "owner/name" | null
    theme: "system", // "system" | "light" | "dark"

    // ----------------------------------------------------------- Matrix (B)
    matrix: null, // the MatrixResponse
    matrixLoading: false,
    matrixError: null,
    // All four quadrants visible by default; `1`-`4` toggle one off/on.
    matrixQuadrantsHidden: {}, // { [quadrantName]: true } for a HIDDEN quadrant
    matrixView: { x: 0, y: 0, scale: 1 }, // pan/zoom transform (SVG viewBox offset + scale)
    matrixFocusedKey: null, // "owner/name" — keyboard focus / hover, drives the hover card
    matrixHoveredKey: null,

    // ------------------------------------------------ Configure weights (D)
    weights: { ...BALANCED_WEIGHTS },
    weightLocks: { responsiveness: false, criticality: false, governance: false, rigor: false, accessibility: false },
    reproject: null, // the ReprojectResponse for the CURRENT `weights`
    reprojectLoading: false, // true only before the FIRST reproject response for this weight vector lands
    reprojectRefetching: false, // true while a slider drag is debounced/in-flight and stale rows are shown greyed
    reprojectError: null,
    configurePage: 1,
    configurePageSize: DEFAULT_PAGE_SIZE,
    configureSort: "rank", // client-side only (the whole reproject result is already in memory) — not URL-persisted

    // --------------------------------------------------------- Compare (E)
    compareRepos: [], // ["owner/name", ...], 2..COMPARE_MAX, order = display/chip order
    compareQuery: "", // the picker's search text
    compareSearchResults: [], // [{ owner, name, ecosystem, kind: "rated"|"gated"|"unrated" }]
    compareDataByRepo: {}, // "owner/name" -> { kind, row, evidence, loading, error }

    // ----------------------------------------------------- Methodology (F)
    methodology: null,
    methodologyLoading: false,
    methodologyError: null,

    leaderboard: null, // the CURRENT page's LeaderboardResponse
    leaderboardLoading: false, // true only when there is NO data yet (skeleton)
    leaderboardRefetching: false, // true while re-fetching (persona/eco/q/sort/page/pageSize change) with a page already showing (grey + progress bar, old rows stay visible)
    leaderboardError: null, // ApiError | null; stale-but-shown data stays in `leaderboard`

    // Single-slot "prefetch the next page" cache (ruling: "prefetch the
    // next page after the current one renders"). `signature` identifies
    // exactly which (persona, eco, query, sort, page, pageSize) the cached
    // `data` answers; a mismatch means it must be discarded, not served.
    nextPageCache: null, // { signature: string, data: LeaderboardResponse } | null

    notRanked: null, // the full NotRankedResponse (fetched once per persona; paginated CLIENT-SIDE)
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

    // The drawer's record fetch (GET /v1/porchlamp/repos/{o}/{n}), independent
    // of which screen (Leaderboard/Matrix/Compare/Configure) opened it --
    // the single source `views/drawer.js::findRow` consults.
    repoDetailByKey: {}, // "owner/name" -> the flat RepoDetailResponse (kind is derived client-side, views/drawer.js::classifyKind)
    repoDetailLoading: false,
    repoDetailError: null, // { endpoint, status, detail } for the CURRENTLY open repo, if it failed

    selectedRepoKey: null, // "owner/name" — J/K cursor; survives patches (keyed, not index-based)
    announcement: "", // aria-live polite text
    keymapOpen: false,
  };
}

export const VALID_PERSONAS = Object.freeze(["balanced", "first_timer", "career_capital", "senior_systems", "ai_assisted"]);

export const VALID_SORT_FIELDS = Object.freeze(new Set([
  "rank",
  "porchlamp",
  "coverage",
  "tier",
  "p_responsiveness",
  "p_criticality",
  "p_governance",
  "p_rigor",
  "p_accessibility",
]));

/** Parse `location.search` (with or without the leading "?") into a partial state. */
export function parseSearch(search) {
  const qs = new URLSearchParams(search.replace(/^\?/, ""));
  const out = {};
  if (qs.has("persona")) {
    const p = qs.get("persona");
    if (VALID_PERSONAS.includes(p)) out.persona = p;
  }
  if (qs.has("eco")) out.ecosystem = qs.get("eco");
  if (qs.has("q")) out.query = qs.get("q");
  if (qs.has("sort")) {
    const s = qs.get("sort");
    const field = s.startsWith("-") ? s.slice(1) : s;
    if (VALID_SORT_FIELDS.has(field)) out.sort = s;
  }
  if (qs.has("page")) {
    const n = parseInt(qs.get("page"), 10);
    if (Number.isFinite(n) && n >= 1) out.page = n;
  }
  if (qs.has("page_size")) {
    const n = parseInt(qs.get("page_size"), 10);
    if (PAGE_SIZES.includes(n)) out.pageSize = n;
  }
  if (qs.has("repo")) out.repo = qs.get("repo") || null;
  if (qs.has("tab")) {
    const tab = qs.get("tab");
    out.tab = VALID_TABS.includes(tab) ? tab : "ranked";
  }
  if (qs.has("w")) {
    const weights = parseWeightsParam(qs.get("w"));
    if (weights) out.weights = weights;
  }
  if (qs.has("cmp")) {
    out.compareRepos = parseCompareParam(qs.get("cmp")).slice(0, COMPARE_MAX);
  }
  return out;
}

//: Every value `tab=` may legitimately hold (06 §2's primary tabs, plus the
//: Leaderboard screen's own Ranked/Not-ranked sub-tabs) — an unrecognised
//: value in a hand-edited URL falls back to "ranked", never a blank screen.
export const VALID_TABS = Object.freeze(["ranked", "notranked", "matrix", "compare", "configure", "methodology"]);

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
  if (state.weights && !weightsEqual(state.weights, BALANCED_WEIGHTS)) {
    qs.set("w", serializeWeightsParam(state.weights));
  }
  if (state.compareRepos && state.compareRepos.length > 0) {
    qs.set("cmp", serializeCompareParam(state.compareRepos));
  }
  const s = qs.toString();
  return s ? `?${s}` : "";
}

export function weightsEqual(a, b) {
  return PILLAR_IDS.every((p) => Math.abs((a[p] ?? 0) - (b[p] ?? 0)) < 1e-9);
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
 * A content-only rebuild of the drawer/keymap dialog (e.g. the evidence
 * fetch resolving a moment after the drawer opened) replaces the dialog's
 * innerHTML wholesale, destroying whatever element held focus — and
 * browsers fall back to `<body>` when a focused node is removed from the
 * document. Focus is restored into the dialog after such a rebuild only
 * when focus was ALREADY inside it before the rebuild (never stolen from
 * elsewhere, e.g. the header) AND the dialog is still meant to be open.
 * A pure predicate, so the decision itself — not the DOM plumbing around
 * it — is unit-testable.
 */
export function shouldRestoreFocusAfterRebuild(isOpen, focusWasInside) {
  return !!isOpen && !!focusWasInside;
}

/**
 * U-18/§5: "Enter opens the FOCUSED row, whether focus came from J/K, Tab,
 * or a click". DOM focus is the ground truth whenever it is actually on a
 * row; the J/K cursor is the fallback for when focus is elsewhere (e.g. a
 * header control) but a row was previously selected.
 */
export function resolveOpenTargetKey(cursorKey, focusedRowKey) {
  return focusedRowKey || cursorKey || null;
}

/**
 * U-7/U-15/U-16: a drawer opened from a URL (`?repo=owner/name`, at boot
 * OR restored by `popstate`) fetches exactly what a click-opened one does
 * (the record via `GET /v1/porchlamp/repos/{o}/{n}` and its evidence).
 * `owner`/`name` are `null` when there is nothing to load (`state.repo`
 * is falsy or malformed), so the caller can `if (parsed)` without a
 * second check.
 */
export function repoToLoadFromState(state) {
  if (!state.repo) return null;
  const parts = state.repo.split("/");
  if (parts.length !== 2 || !parts[0] || !parts[1]) return null;
  return { owner: parts[0], name: parts[1] };
}

/**
 * U-15/U-7: the footer/snapshot-chip strip carries U-6's reproducibility
 * claim, so it must never rest on a fabricated "Loading..." forever when
 * the snapshot fetch actually failed. Returns which of the three states
 * applies (never DOES the string formatting itself — that stays in
 * app.js/copy.js, same separation as
 * `computeHiddenCount`/`renderCountsStrip` in views/leaderboard.js) so
 * the state-selection decision is unit-testable without importing copy.js
 * into this otherwise copy-free module.
 */
export function footerFieldsFor(state) {
  if (state.snapshotError) {
    return {
      kind: "error",
      endpoint: state.snapshotError.endpoint,
      status: state.snapshotError.status,
      detail: state.snapshotError.detail,
    };
  }
  if (state.snapshot) {
    return { kind: "ok", snapshot: state.snapshot };
  }
  return { kind: "loading" };
}

/**
 * U-16's push-vs-replace policy, as one pure decision function app.js
 * actually calls. Every debounced `q` keystroke ("type") must not spam
 * history; every other leaderboard-affecting change, and a `q` COMMIT
 * (Enter or blur), is a real navigation step.
 */
export function urlSyncMode(trigger) {
  return trigger === "type" ? "replace" : "push";
}

/**
 * Pure reducer: given the current state and an action, returns the next
 * state. Kept separate from the store so it is unit-testable without a
 * fetch/DOM environment.
 */
export function reduce(state, action) {
  switch (action.type) {
    case "SET_PARAMS": {
      const personaChanged = action.payload && action.payload.persona && action.payload.persona !== state.persona;
      return {
        ...state,
        ...action.payload,
        ...(personaChanged ? { notRanked: null, notRankedError: null, notRankedPage: 1 } : {}),
      };
    }
    case "SET_PERSONA":
      return {
        ...state,
        persona: action.persona,
        notRanked: null,
        notRankedError: null,
        notRankedPage: 1,
      };
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
      return { ...state, repo: action.repo, evidenceError: null, repoDetailError: null };
    case "CLOSE_DRAWER":
      return { ...state, repo: null, evidenceError: null, repoDetailError: null };
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

    // ------------------------------------------ drawer record fetch (Screen C)
    case "REPO_DETAIL_LOADING":
      return { ...state, repoDetailLoading: true, repoDetailError: null };
    case "REPO_DETAIL_OK":
      return {
        ...state,
        repoDetailLoading: false,
        repoDetailError: null,
        repoDetailByKey: { ...state.repoDetailByKey, [action.repo]: action.data },
      };
    case "REPO_DETAIL_ERROR":
      return { ...state, repoDetailLoading: false, repoDetailError: action.error };

    // ------------------------------------------------------------ Matrix (B)
    case "MATRIX_LOADING":
      return { ...state, matrixLoading: true, matrixError: null };
    case "MATRIX_OK":
      return { ...state, matrixLoading: false, matrix: action.data, matrixError: null };
    case "MATRIX_ERROR":
      return { ...state, matrixLoading: false, matrixError: action.error };
    case "MATRIX_TOGGLE_QUADRANT": {
      const hidden = { ...state.matrixQuadrantsHidden };
      if (hidden[action.quadrant]) delete hidden[action.quadrant];
      else hidden[action.quadrant] = true;
      return { ...state, matrixQuadrantsHidden: hidden };
    }
    case "MATRIX_SET_VIEW":
      return { ...state, matrixView: action.view };
    case "MATRIX_RESET_VIEW":
      return { ...state, matrixView: { x: 0, y: 0, scale: 1 } };
    case "MATRIX_SET_FOCUS":
      return { ...state, matrixFocusedKey: action.key };
    case "MATRIX_SET_HOVER":
      return { ...state, matrixHoveredKey: action.key };

    // -------------------------------------------------- Configure weights (D)
    case "SET_WEIGHTS":
      return { ...state, weights: action.weights, configurePage: 1 };
    case "TOGGLE_WEIGHT_LOCK":
      return { ...state, weightLocks: { ...state.weightLocks, [action.pillar]: !state.weightLocks[action.pillar] } };
    case "SET_CONFIGURE_SORT":
      return { ...state, configureSort: action.sort, configurePage: 1 };
    case "SET_CONFIGURE_PAGE":
      return { ...state, configurePage: action.page };
    case "SET_CONFIGURE_PAGE_SIZE":
      return { ...state, configurePageSize: action.pageSize, configurePage: action.page ?? 1 };
    case "REPROJECT_LOADING":
      return { ...state, reprojectLoading: !state.reproject, reprojectRefetching: !!state.reproject, reprojectError: null };
    case "REPROJECT_OK":
      return { ...state, reprojectLoading: false, reprojectRefetching: false, reproject: action.data, reprojectError: null };
    case "REPROJECT_ERROR":
      return { ...state, reprojectLoading: false, reprojectRefetching: false, reprojectError: action.error };

    // ----------------------------------------------------------- Compare (E)
    case "COMPARE_SET_QUERY":
      return { ...state, compareQuery: action.query };
    case "COMPARE_SET_RESULTS":
      return { ...state, compareSearchResults: action.results };
    case "COMPARE_ADD_REPO":
      if (state.compareRepos.includes(action.key) || state.compareRepos.length >= COMPARE_MAX) return state;
      return { ...state, compareRepos: [...state.compareRepos, action.key], compareQuery: "", compareSearchResults: [] };
    case "COMPARE_REMOVE_REPO":
      return {
        ...state,
        compareRepos: state.compareRepos.filter((k) => k !== action.key),
        compareDataByRepo: Object.fromEntries(Object.entries(state.compareDataByRepo).filter(([k]) => k !== action.key)),
      };
    case "COMPARE_REPO_LOADING":
      return {
        ...state,
        compareDataByRepo: { ...state.compareDataByRepo, [action.key]: { ...(state.compareDataByRepo[action.key] || {}), loading: true, error: null } },
      };
    case "COMPARE_REPO_OK":
      return {
        ...state,
        compareDataByRepo: { ...state.compareDataByRepo, [action.key]: { ...action.data, loading: false, error: null } },
      };
    case "COMPARE_REPO_ERROR":
      return {
        ...state,
        compareDataByRepo: { ...state.compareDataByRepo, [action.key]: { ...(state.compareDataByRepo[action.key] || {}), loading: false, error: action.error } },
      };

    // ------------------------------------------------------- Methodology (F)
    case "METHODOLOGY_LOADING":
      return { ...state, methodologyLoading: true, methodologyError: null };
    case "METHODOLOGY_OK":
      return { ...state, methodologyLoading: false, methodology: action.data, methodologyError: null };
    case "METHODOLOGY_ERROR":
      return { ...state, methodologyLoading: false, methodologyError: action.error };

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
