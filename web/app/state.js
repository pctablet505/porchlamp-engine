// state.js — single store with subscribe + URL sync (06 §6, U-9).
// Pagination is real: 20/50/100/500/1000 page sizes, default 20,
// next/prev/first/last controls, with `page`/`pageSize` part of the
// URL-synced state.
//
// The address names the state (routes.js): the path names the view, or a
// project's side panel (`/projects/<owner>/<name>/`), and the query holds
// the fields that view reads, e.g. `/not-ranked/?persona=first_timer&eco=npm`.
// Every view change and persona/eco/sort/page/page_size change uses
// history.pushState (each is a meaningful navigation step); `q` uses
// replaceState while typing and one pushState when committed — app.js owns
// that distinction, state.js only exposes the pure parse/serialize pair.
//
// The pure functions (`defaultState`, `parseLocation`, `serializeLocation`,
// `reduce`, `countRankChanges`) are exported separately from the store
// singleton so they stay testable without a DOM or a running server.

import { parseRepoSlug } from "./livecheck.js";
import * as copy from "./copy.js";
import { matchRoute, projectPath, routeById, routePath, routeTitle, routeView, routesOfView } from "./routes.js";

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

/**
 * Normalise a repository parameter (`"owner/name"`, a `.git`-suffixed name,
 * trailing spaces or a pasted `github.com` URL) to `"owner/name"` when
 * `parseRepoSlug` accepts it, so `?repo=bulk/repo-0133.git` or
 * `?repo=bulk/repo-0133%20` opens the real record instead of 404ing on the raw
 * suffix and then claiming the trimmed name is absent from the snapshot.
 * Malformed strings that fail `parseRepoSlug` stay untouched so the drawer
 * still renders the plain not-found state for them.
 */
export function normalizeRepoParam(value) {
  if (typeof value !== "string" || !value) return null;
  const slug = parseRepoSlug(value);
  return slug ? `${slug.owner}/${slug.name}` : value;
}

export function defaultState() {
  return {
    persona: "balanced",
    ecosystem: "",
    query: "",
    sort: "rank",
    page: 1,
    pageSize: DEFAULT_PAGE_SIZE,
    rankedSort: "rank",
    rankedPage: 1,
    rankedPageSize: DEFAULT_PAGE_SIZE,
    leaderboardSignature: null,
    tab: "ranked", // a routes.js view: "ranked" | "notranked" | "matrix" | "compare" | "configure" | "methodology" | "report" | "notfound"
    repo: null, // "owner/name" | null: the open side panel, at /projects/<owner>/<name>/
    missingPath: null, // the address a "notfound" view answers (kept, so the address stays what was asked for)
    theme: "system", // the theme in force, as theme-init.js applied it: "system" | "light" | "dark"

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
    // The digest of the database file the published site was exported from (api.js fetchManifest): null where there
    // is none (the API's own server) or until it has loaded; `manifestLoad` is "idle" | "loading" | "done".
    sourceDbSha256: null,
    manifestLoad: "idle",

    // ----------------------------------------------------- Report (/black-holes/, api.js fetchReport)
    report: null,
    reportLoading: false,
    reportError: null,

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
    // The reason a list of the not-ranked projects by reason shows (routes.js `fixed.reason`, a bare reason code), at
    // its own address; null on every other view, and on the whole not-ranked list.
    notRankedReason: null,

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
    // What a project's panel prints beside its record (api.fetchProjectExtras): "owner/name" -> { answer, standings,
    // similar, recordFile } (the first three null for a project without a balanced rank), or null where none is served.
    projectExtrasByKey: {},
    // Opt-in live checks for repositories outside the snapshot (web/app/livecheck.js),
    // kept in memory until the page reloads, so reopening a drawer never spends the visitor's
    // GitHub allowance twice: "owner/name" -> { status: "loading" } | { status: "done", outcome, atMs }.
    liveCheckByKey: {},

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
  "first_reply",
]));

//: How each query field of an address reads into state and writes back. A value that does not parse is
//: ignored (a hand-edited address never blanks the screen), and a field at its default is not written.
const QUERY_FIELDS = Object.freeze({
  persona: {
    read: (value) => (VALID_PERSONAS.includes(value) ? { persona: value } : null),
    write: (state) => (state.persona && state.persona !== "balanced" ? state.persona : null),
  },
  eco: { read: (value) => ({ ecosystem: value }), write: (state) => state.ecosystem || null },
  q: { read: (value) => ({ query: normalizeQuery(value) }), write: (state) => state.query || null },
  sort: {
    read: (value) => (VALID_SORT_FIELDS.has(value.startsWith("-") ? value.slice(1) : value) ? { sort: value } : null),
    write: (state) => (state.sort && state.sort !== "rank" ? state.sort : null),
  },
  page: {
    read: (value) => {
      const n = parseInt(value, 10);
      return Number.isFinite(n) && n >= 1 ? { page: n } : null;
    },
    write: (state) => (state.page && state.page !== 1 ? String(state.page) : null),
  },
  page_size: {
    read: (value) => {
      const n = parseInt(value, 10);
      return PAGE_SIZES.includes(n) ? { pageSize: n } : null;
    },
    write: (state) => (state.pageSize && state.pageSize !== DEFAULT_PAGE_SIZE ? String(state.pageSize) : null),
  },
  w: {
    read: (value) => {
      const weights = parseWeightsParam(value);
      return weights ? { weights } : null;
    },
    write: (state) =>
      state.weights && !weightsEqual(state.weights, BALANCED_WEIGHTS) ? serializeWeightsParam(state.weights) : null,
  },
  cmp: {
    read: (value) => ({ compareRepos: parseCompareParam(value).slice(0, COMPARE_MAX) }),
    write: (state) =>
      state.compareRepos && state.compareRepos.length > 0 ? serializeCompareParam(state.compareRepos) : null,
  },
});

function parseQuery(search, keys) {
  const qs = new URLSearchParams(String(search || "").replace(/^\?/, ""));
  const out = {};
  for (const key of keys) {
    if (qs.has(key)) Object.assign(out, QUERY_FIELDS[key].read(qs.get(key)));
  }
  return out;
}

function serializeQuery(state, keys) {
  const qs = new URLSearchParams();
  for (const key of keys) {
    const value = QUERY_FIELDS[key].write(state);
    if (value !== null) qs.set(key, value);
  }
  const s = qs.toString();
  return s ? `?${s}` : "";
}

/** `{ owner, name }` of an "owner/name" key both of whose parts can stand in an address, else null. */
function projectOf(repo) {
  if (typeof repo !== "string") return null;
  const match = matchRoute(`/projects/${repo}/`);
  return match.route.id === "project" ? { owner: match.owner, name: match.name } : null;
}

//: The leaderboard's fields a list's address can fix or set (routes.js `fixed`, `defaults`, `{page}`): each one's
//: state field and its value where the route sets no other default.
const LIST_FIELDS = Object.freeze({
  persona: { state: "persona", value: "balanced" },
  eco: { state: "ecosystem", value: "" },
  q: { state: "query", value: "" },
  sort: { state: "sort", value: "rank" },
  page: { state: "page", value: 1 },
  page_size: { state: "pageSize", value: DEFAULT_PAGE_SIZE },
  reason: { state: "notRankedReason", value: null },
});

const owns = (object, key) => Boolean(object) && Object.prototype.hasOwnProperty.call(object, key);

/** A field's value where `route`'s address does not name it. */
function routeDefault(route, key) {
  return owns(route.defaults, key) ? route.defaults[key] : LIST_FIELDS[key].value;
}

/** The value `route`'s path fixes a field at (null in the table is the field at its default). */
function fixedValue(route, key) {
  return route.fixed[key] === null ? LIST_FIELDS[key].value : route.fixed[key];
}

/** The list fields of a whole state, by query-field name. Only the not-ranked view reads a reason. */
function listFieldsOf(state) {
  const fields = Object.fromEntries(Object.entries(LIST_FIELDS).map(([key, field]) => [key, state[field.state]]));
  if (state.tab !== "notranked" || fields.reason === undefined) fields.reason = null;
  return fields;
}

/**
 * Whether `route` can be the address of `fields` (query-field name -> value; a field left out is unset, and takes
 * the route's own default): every field the path fixes has its fixed value, and every other field that is off the
 * route's default is one the path or the query holds.
 */
function routeHolds(route, fields) {
  for (const key of Object.keys(LIST_FIELDS)) {
    const own = routeDefault(route, key);
    const value = fields[key] === undefined ? own : fields[key];
    if (owns(route.fixed, key)) {
      if (value !== fixedValue(route, key)) return false;
    } else if (!route.path.includes(`{${key}}`) && value !== own && !route.query.includes(key)) {
      return false;
    }
  }
  return true;
}

/**
 * The route of a view that is the address of `fields` (routes.js): among the routes that hold them, the one with the
 * most fixed fields, the first in table order on a tie (so an ecosystem's path beats a lens's); the view's own
 * route when no other holds them. `routes` is the view's routes in table order (routes.js `routesOfView`).
 */
export function routeForFields(view, fields, routes = routesOfView(view)) {
  let best = routeById(view);
  let most = -1;
  for (const route of routes) {
    const count = route.fixed ? Object.keys(route.fixed).length : 0;
    if (count > most && (route.id === view || routeHolds(route, fields))) {
      best = route;
      most = count;
    }
  }
  return best;
}

/** The route a state is drawn at: its view's route for its list fields (`routeForFields`); null for a project's
 *  panel or the not-found view. */
export function routeForState(state) {
  const route = routeById(state.tab);
  if (!route || route.path === null || route.id === "project") return null;
  return routeForFields(state.tab, listFieldsOf(state));
}

/** `route`'s address for `fields` (and `state`, for the fields that are not a list's): its path, then each query
 *  field it reads that is off its default there. */
function addressAt(route, fields, state = {}) {
  const qs = new URLSearchParams();
  for (const key of route.query) {
    if (owns(LIST_FIELDS, key)) {
      const value = fields[key];
      if (value !== undefined && value !== null && value !== routeDefault(route, key)) qs.set(key, String(value));
    } else {
      const value = QUERY_FIELDS[key].write(state);
      if (value !== null) qs.set(key, value);
    }
  }
  const query = qs.toString();
  return routePath(route.id, { page: fields.page ?? 1 }) + (query ? `?${query}` : "");
}

/**
 * The state an address names (routes.js): the route's view as `tab`; for a list's own address, the fields its path
 * fixes and its defaults; for a project's address, its side panel (`repo`, as written) over the leaderboard; for an
 * address no route claims, the "notfound" view and that address (`missingPath`). The query fields the route reads
 * come back too; any other field is ignored. Only what the address names is returned: the caller starts from the
 * defaults.
 */
export function parseLocation(pathname, search = "") {
  const match = matchRoute(pathname);
  const { route } = match;
  if (route.id === "notfound") return { tab: "notfound", missingPath: String(pathname || "/") };
  if (route.id === "project") return { tab: "ranked", repo: `${match.owner}/${match.name}`, ...parseQuery(search, route.query) };
  const named = {};
  for (const key of Object.keys(route.defaults || {})) named[LIST_FIELDS[key].state] = route.defaults[key];
  for (const key of Object.keys(route.fixed || {})) named[LIST_FIELDS[key].state] = fixedValue(route, key);
  if (match.page !== undefined) named.page = match.page;
  return { tab: routeView(route), ...named, ...parseQuery(search, route.query) };
}

/**
 * The address of a state: an open panel's project address (with the persona when the view under it reads one),
 * else the address of its view's route (`routeForState`): the path, then the query fields that route reads, each
 * only when it is off the route's default. The "notfound" view keeps the address it answers.
 */
export function serializeLocation(state) {
  const panel = projectOf(state.repo);
  if (panel) {
    const under = routeById(state.tab) || routeById("ranked");
    const keys = routeById("project").query.filter((key) => under.query.includes(key));
    return projectPath(panel.owner, panel.name) + serializeQuery(state, keys);
  }
  if (state.tab === "notfound") return state.missingPath || "/";
  const route = routeForState(state);
  if (!route) return "/";
  return addressAt(route, listFieldsOf(state), state);
}

/** The list fields an address names itself, by query-field name: those its path fixes, its page, and those its query
 *  sets (a field it leaves out is unset). */
function namedListFields(match, search) {
  const { route } = match;
  const fields = {};
  for (const key of Object.keys(route.fixed || {})) fields[key] = fixedValue(route, key);
  if (match.page !== undefined) fields.page = match.page;
  const read = parseQuery(search, route.query.filter((key) => owns(LIST_FIELDS, key)));
  for (const [key, field] of Object.entries(LIST_FIELDS)) {
    if (read[field.state] !== undefined) fields[key] = read[field.state];
  }
  return fields;
}

/** The address of the leaderboard list `fields` name (`routeForFields`), with no default written. */
function listAddress(fields) {
  return addressAt(routeForFields("ranked", fields), fields);
}

/**
 * The history entry's state for an address (`history.pushState`'s first argument): with a panel open, the
 * address of the view under it (`under`) and the project's own spelling (`repo`; the address is lower case), so
 * Back and Forward bring the panel back over the list it was opened from (`stateFromLocation`). Without a panel
 * there is nothing to keep.
 */
export function historyEntryFor(state) {
  return projectOf(state.repo) ? { under: serializeLocation({ ...state, repo: null }), repo: state.repo } : null;
}

/**
 * `parseLocation`, with what the history entry kept for a project's panel (`historyEntryFor`): the view under it,
 * and the project's spelling when it is the address's project. A direct load of a project's address has no entry
 * state: its panel opens over the leaderboard, under the address's spelling.
 */
export function stateFromLocation(pathname, search = "", historyState = null) {
  const parsed = parseLocation(pathname, search);
  if (!parsed.repo || !historyState) return parsed;
  const kept = historyState.repo;
  const repo = typeof kept === "string" && kept.toLowerCase() === parsed.repo.toLowerCase() ? kept : parsed.repo;
  const under = typeof historyState.under === "string" ? historyState.under : null;
  const cut = under ? under.indexOf("?") : -1;
  const view = under ? parseLocation(cut < 0 ? under : under.slice(0, cut), cut < 0 ? "" : under.slice(cut)) : null;
  if (!view || view.repo || view.tab === "notfound") return { ...parsed, repo };
  return { ...view, repo };
}

//: The values the root's `tab=` query field took before the views had their own paths; each is a routes.js id.
//: Any other value meant the leaderboard.
export const VALID_TABS = Object.freeze(["ranked", "notranked", "matrix", "compare", "configure", "methodology"]);

/**
 * Where an address that is not a view's own address goes; null when it is one.
 *
 * - The root's old query links, from before the views had paths (indexed, shared and linked from READMEs):
 *   `?tab=<view>` and `#methodology` load that view's address (`load: true`, for `location.replace`), keeping
 *   every query field that view reads (a field it cannot use, such as a lens on the matrix, is left out, so
 *   the reader lands on the view's one address). `?repo=owner/name` opens that project's side panel where it
 *   is (`load: false`): over the view `tab=` named, at the panel's address `href`, with `under` the address of
 *   the view below it and `repo` the project as the link spelled it (`historyEntryFor`).
 * - A project address in another letter case loads the lower-case one, where the export writes the page.
 * - A list of the leaderboard named by a path and a query that another route has a path for (routes.js: `/?eco=crates`,
 *   `/?persona=first_timer&eco=go`, `/?page=3&page_size=100`, `/rust/?persona=first_timer`) loads that route's
 *   address, keeping the other fields; a field the address leaves out takes the new route's default.
 *
 * An address that is loaded keeps its fragment (`?tab=methodology#gates` lands on `/methodology/#gates`).
 */
export function redirectTarget(pathname, search = "", hash = "") {
  const match = matchRoute(pathname);
  const fragment = hash && hash !== "#methodology" ? String(hash) : "";
  if (match.route.id === "project") {
    if (match.owner === match.owner.toLowerCase() && match.name === match.name.toLowerCase()) return null;
    return { href: projectPath(match.owner, match.name) + String(search || "") + fragment, load: true };
  }
  if (routeView(match.route) !== "ranked") return null;
  const qs = new URLSearchParams(String(search || "").replace(/^\?/, ""));
  if (match.route.id === "ranked" && (hash === "#methodology" || qs.has("tab") || qs.has("repo"))) {
    if (hash === "#methodology") return { href: routePath("methodology"), load: true };
    const tab = VALID_TABS.includes(qs.get("tab")) ? qs.get("tab") : "ranked";
    const view = { ...defaultState(), tab, ...parseQuery(search, routeById(tab).query) };
    const under = tab === "ranked" ? listAddress(namedListFields(match, search)) : serializeLocation(view);
    const panel = qs.has("repo") ? projectOf(normalizeRepoParam(qs.get("repo"))) : null;
    if (!panel) return { href: under + fragment, load: true };
    const repo = `${panel.owner}/${panel.name}`;
    return { href: serializeLocation({ ...view, repo }), under, repo, load: false };
  }
  // A list of the leaderboard named in another form (`/?eco=crates`, `/?page=3&page_size=100`) loads the list's own
  // path, keeping the other fields; the same path in another form is the app's to tidy (boot's replaceState).
  const fields = namedListFields(match, search);
  if (routeForFields("ranked", fields) === match.route) return null;
  return { href: listAddress(fields) + fragment, load: true };
}

/**
 * The address the app moves to when the reader opens `tab` from where they are (the primary nav, the
 * Ranked/Not ranked links): the state SET_TAB gives, so each view comes back with its own page and sort, with
 * no panel open. A view another page answers (the methodology page) is its plain address.
 */
export function hrefFor(state, tab) {
  const route = routeById(tab);
  if (!route || !route.shell) return routePath(tab) || "/";
  return serializeLocation({ ...reduce(state, { type: "SET_TAB", tab }), repo: null });
}

/** The document title of a state: an open panel's project title (in the record's spelling once it has loaded), the
 *  report's own once it has loaded (it names its month), else its view's. */
export function titleForState(state) {
  const panel = projectOf(state.repo);
  if (panel) {
    const record = state.repoDetailByKey ? state.repoDetailByKey[state.repo] : null;
    return routeTitle("project", record ? { owner: record.owner, name: record.name } : panel);
  }
  if (state.tab === "report" && state.report && state.report.title) return state.report.title;
  const route = routeForState(state);
  const slots = route && route.view ? listTitleSlots(route, state) : null;
  if (slots) return routeTitle(route.id, slots);
  return routeTitle(routeById(state.tab) ? state.tab : "notfound");
}

//: Every page title ends with the site's name (routes.js); a list's heading is its title without it.
export const TITLE_SITE_SUFFIX = " | Porchlamp";

/** A count with commas between the thousands (`4,402`), the same bytes in every locale. */
const groupThousands = (n) => String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ",");

/** The positions a page of the list shows, `{ first, last, total }` (1-based), from the page of rows it has
 *  loaded; null before it has. */
export function listSpan(state) {
  const total = state.leaderboard ? state.leaderboard.total : null;
  if (typeof total !== "number") return null;
  return { first: (state.page - 1) * state.pageSize + 1, last: Math.min(state.page * state.pageSize, total), total };
}

/**
 * The words and numbers a list's title names (routes.js "{ecosystem}", "{lens}", "{first}", "{last}", "{total}"):
 * the ecosystem and the lens its path fixes, in copy.js's words, and for a page of the ranking the positions it
 * shows. Null for a page of the ranking whose rows have not loaded.
 */
export function listTitleSlots(route, state) {
  const fixed = route.fixed || {};
  if (fixed.reason) return reasonListTitleSlots(route, state);
  const slots = {};
  if (fixed.eco) slots.ecosystem = copy.ecosystemName(fixed.eco);
  if (fixed.persona && fixed.persona !== "balanced") slots.lens = copy.LENS_PHRASES[fixed.persona];
  if (route.path.includes("{page}")) {
    const span = listSpan(state);
    if (!span) return null;
    slots.first = groupThousands(span.first);
    slots.last = groupThousands(span.last);
    slots.total = groupThousands(span.total);
  }
  return slots;
}

/**
 * What a person typed or pasted into the search, as the text to look for: the spaces at its ends, its case, a GitHub
 * address's `https://`, `www.` and `github.com/`, and trailing slashes are dropped, so `https://github.com/Pallets/Flask/`
 * reads as `pallets/flask`.
 */
export function normalizeQuery(text) {
  return String(text || "")
    .trim()
    .toLowerCase()
    .replace(/^(?:https?:\/\/)?(?:www\.)?github\.com\//, "")
    .replace(/\/+$/, "");
}

/**
 * Where the header search's Enter takes a reader, from what they typed or pasted (`text`) and where they are
 * (`state`): text that is not an owner/name stays the list's filter (`{ kind: "filter" }`), or opens the rankings
 * filtered by it on a view without one (`{ kind: "address", href }`). An owner/name opens its project:
 * - one the snapshot holds unranked, the not-ranked list filtered to its row, which shows its reason
 *   (`{ kind: "address", href }`, its ecosystem filter dropped);
 * - any other, its panel over the list (`{ kind: "panel", owner, name }`): a ranked project's, in its real spelling,
 *   or the panel of a name the snapshot does not hold, which says so.
 * `lookup(owner, name)` answers `{ owner, name, rated }` for a project the snapshot holds (its real spelling), `null`
 * for one it does not, and `undefined` when it cannot tell.
 */
export async function searchDestination(text, state, lookup) {
  const query = normalizeQuery(text);
  const slug = parseRepoSlug(query);
  if (!slug) {
    if (routeById(state.tab) && routeById(state.tab).query.includes("q")) return { kind: "filter" };
    return { kind: "address", href: hrefFor({ ...state, query, page: 1 }, "ranked") };
  }
  const held = await lookup(slug.owner, slug.name);
  if (held && held.rated === false) {
    const row = normalizeQuery(`${held.owner}/${held.name}`);
    return { kind: "address", href: hrefFor({ ...state, ecosystem: "", query: row, notRankedPage: 1 }, "notranked") };
  }
  const at = held || slug;
  return { kind: "panel", owner: at.owner, name: at.name };
}

// --- The lists of the not-ranked projects by reason (routes.js "notranked-*") ---------------------------------------

/** A not-ranked row's reason: its gate, else its unrated reason, in the bare form ("unrated: insufficient_evidence" is
 *  "insufficient_evidence"), as the routes fix it. */
export function notRankedRowReason(row) {
  return copy.bareUnratedReason(row.gate_reason ?? row.unrated_reason ?? null);
}

/** The not-ranked rows in `ecosystem` (a code) whose "owner/name" holds `query`; all of them without either. */
export function filterNotRankedRows(rows, ecosystem, query) {
  if (!rows) return [];
  const eco = ecosystem ? ecosystem.trim().toLowerCase() : "";
  const q = query ? query.trim().toLowerCase() : "";
  if (!eco && !q) return rows;
  return rows.filter((r) => {
    if (eco && String(r.ecosystem || "").toLowerCase() !== eco) return false;
    if (q && !`${r.owner}/${r.name}`.toLowerCase().includes(q)) return false;
    return true;
  });
}

/**
 * The Not ranked tab's count: the size of the list the tab opens, that lens's not-ranked list filtered by the page's
 * ecosystem and search (what views/notranked.js lists). With the list loaded, its rows; without it and with no
 * ecosystem or search, the snapshot's own numbers (balanced: gated + unrated; another lens: the total less the
 * projects the lens ranks). With an ecosystem or a search and no list loaded: null, never another list's number.
 */
export function notRankedTabCount(state) {
  const searched = Boolean(state.query && state.query.trim());
  const notRanked = state.notRanked;
  if (notRanked && !(notRanked.truncated && !state.ecosystem && !searched)) {
    return ["gated", "unrated", "persona_hidden"].reduce(
      (sum, group) => sum + filterNotRankedRows(notRanked[group], state.ecosystem, state.query).length,
      0
    );
  }
  if (state.ecosystem || searched) return null;
  const counts = state.leaderboard ? state.leaderboard.counts : null;
  if (state.persona === "balanced") return counts ? counts.gated + counts.unrated : null;
  const snapshot = state.snapshot;
  const visible = snapshot && snapshot.persona_visible_counts ? snapshot.persona_visible_counts[state.persona] : undefined;
  const total = snapshot && snapshot.counts ? snapshot.counts.total : undefined;
  return typeof visible === "number" && typeof total === "number" ? total - visible : null;
}

/** Every not-ranked row with `reason`, in the API's order (lower-case owner/name, gated then unrated). */
export function notRankedReasonRows(notRanked, reason) {
  if (!notRanked) return [];
  return [...(notRanked.gated || []), ...(notRanked.unrated || [])].filter((row) => notRankedRowReason(row) === reason);
}

/**
 * The page of a list by reason `state` is at: `{ route, reason, rows, total, page, pages, pageSize, first }`, the rows
 * of that page (`pageSize` a page, the API's order) with `page` held to the list's pages (a list with no rows has one
 * empty page). `rows`, `total` and `pages` are null before the not-ranked list has loaded. Null at any other address.
 */
export function reasonListPage(state) {
  const route = state.tab === "notranked" ? routeForState(state) : null;
  const reason = route && route.fixed ? route.fixed.reason : null;
  if (!reason) return null;
  const pageSize = route.fixed.page_size;
  const all = state.notRanked ? notRankedReasonRows(state.notRanked, reason) : null;
  const pages = all ? Math.max(1, Math.ceil(all.length / pageSize)) : null;
  const page = pages ? Math.min(Math.max(1, state.page || 1), pages) : Math.max(1, state.page || 1);
  const start = (page - 1) * pageSize;
  return {
    route,
    reason,
    rows: all ? all.slice(start, start + pageSize) : null,
    total: all ? all.length : null,
    page,
    pages,
    pageSize,
    first: start + 1,
  };
}

/** A list by reason's title slots: its name, and its page part when it has more than one page; null before the
 *  not-ranked list has loaded (the view's own title stands until then). */
function reasonListTitleSlots(route, state) {
  const at = reasonListPage({ ...state, tab: "notranked" });
  if (!at || at.route !== route || at.pages === null) return null;
  const pageOf =
    at.pages > 1
      ? copy.NOT_RANKED_LIST_PAGE_OF.replace("{page}", groupThousands(at.page)).replace("{pages}", groupThousands(at.pages))
      : "";
  return { list: copy.NOT_RANKED_LIST_NAMES[route.fixed.reason], page_of: pageOf };
}

export function weightsEqual(a, b) {
  return PILLAR_IDS.every((p) => Math.abs((a[p] ?? 0) - (b[p] ?? 0)) < 1e-9);
}

/** The exact key identifying which request a leaderboard payload answers — used to validate the prefetch cache and to detect a stale in-flight response. */
export function requestSignature(state) {
  const sort = state.tab === "ranked" ? state.sort : (state.rankedSort ?? state.sort);
  const page = state.tab === "ranked" ? state.page : (state.rankedPage ?? state.page);
  const pageSize = state.tab === "ranked" ? state.pageSize : (state.rankedPageSize ?? state.pageSize);
  return `${state.persona}|${state.ecosystem}|${state.query}|${sort}|${page}|${pageSize}`;
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
 * U-7/U-15/U-16: a drawer opened from an address (`/projects/<owner>/<name>/`,
 * at boot OR restored by `popstate`) fetches exactly what a click-opened one does
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

function isValidRepoKey(repo) {
  if (typeof repo !== "string" || !repo) return false;
  const parts = repo.split("/");
  return parts.length === 2 && Boolean(parts[0]) && Boolean(parts[1]);
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
      const rawPayload = action.payload || {};
      const { resetTabSlices: payloadReset, ...payload } = rawPayload;
      if (payload.repo !== undefined) payload.repo = normalizeRepoParam(payload.repo);
      if (payload.query !== undefined) payload.query = normalizeQuery(payload.query);
      const shouldResetSlices = Boolean(action.resetTabSlices || payloadReset);
      const nextTab = payload.tab !== undefined ? payload.tab : state.tab;
      const personaChanged = payload.persona && payload.persona !== state.persona;
      const filterChanged =
        (payload.ecosystem !== undefined && payload.ecosystem !== state.ecosystem) ||
        (payload.query !== undefined && payload.query !== state.query);
      const resetSlices = shouldResetSlices
        ? {
            rankedPage: 1,
            rankedPageSize: DEFAULT_PAGE_SIZE,
            rankedSort: "rank",
            notRankedPage: 1,
            notRankedPageSize: DEFAULT_PAGE_SIZE,
            configurePage: 1,
            configurePageSize: DEFAULT_PAGE_SIZE,
            configureSort: "rank",
          }
        : {};
      const tabSync = {};
      if (nextTab === "ranked") {
        if (payload.page !== undefined) tabSync.rankedPage = payload.page;
        if (payload.pageSize !== undefined) tabSync.rankedPageSize = payload.pageSize;
        if (payload.sort !== undefined) tabSync.rankedSort = payload.sort;
      } else if (nextTab === "notranked") {
        if (payload.page !== undefined) tabSync.notRankedPage = payload.page;
        if (payload.pageSize !== undefined) tabSync.notRankedPageSize = payload.pageSize;
      } else if (nextTab === "configure") {
        if (payload.page !== undefined) tabSync.configurePage = payload.page;
        if (payload.pageSize !== undefined) tabSync.configurePageSize = payload.pageSize;
        if (payload.sort !== undefined) tabSync.configureSort = payload.sort;
      }
      // A lens, an ecosystem or a search chosen on a list by reason leaves the list for the whole not-ranked list
      // (`/not-ranked/?…`), at its own page size: the list's address fixes all three.
      const leavesReasonList =
        Boolean(state.notRankedReason) && payload.notRankedReason === undefined && Boolean(personaChanged || filterChanged);
      const reasonSync = leavesReasonList
        ? {
            notRankedReason: null,
            notRankedPage: 1,
            notRankedPageSize: DEFAULT_PAGE_SIZE,
            ...(payload.page === undefined ? { page: 1 } : {}),
            ...(nextTab === "notranked" && payload.pageSize === undefined ? { pageSize: DEFAULT_PAGE_SIZE } : {}),
          }
        : {};
      const repoChanged = payload.repo !== undefined && payload.repo !== state.repo;
      const validRepo = isValidRepoKey(payload.repo);
      const repoSync = repoChanged
        ? validRepo
          ? {
              repoDetailLoading: !(state.repoDetailByKey && state.repoDetailByKey[payload.repo]),
              evidenceLoading: !(state.evidenceByRepo && state.evidenceByRepo[payload.repo]),
              repoDetailError: null,
              evidenceError: null,
            }
          : {
              repoDetailLoading: false,
              evidenceLoading: false,
              repoDetailError: null,
              evidenceError: null,
            }
        : {};
      return {
        ...state,
        ...resetSlices,
        ...payload,
        ...repoSync,
        ...(personaChanged
          ? {
              notRanked: null,
              notRankedError: null,
              notRankedPage: 1,
              rankedPage: 1,
              ...(payload.page === undefined ? { page: 1 } : {}),
            }
          : filterChanged
            ? {
                notRankedPage: 1,
                configurePage: 1,
                rankedPage: 1,
                ...(payload.page === undefined ? { page: 1 } : {}),
              }
            : {}),
        ...reasonSync,
        ...tabSync,
      };
    }
    case "SET_PERSONA":
      return {
        ...state,
        persona: action.persona,
        notRanked: null,
        notRankedError: null,
        notRankedPage: 1,
        rankedPage: 1,
        page: 1,
      };
    case "SET_TAB": {
      if (action.tab === state.tab) return state;
      // A list by reason is a page of the not-ranked view: another view, and the Not ranked tab opened from there,
      // come back to the whole list.
      if (state.notRankedReason) {
        return reduce(
          { ...state, notRankedReason: null, notRankedPage: 1, notRankedPageSize: DEFAULT_PAGE_SIZE },
          action
        );
      }
      if (action.tab === "notranked") {
        return {
          ...state,
          tab: action.tab,
          page: state.notRankedPage,
          pageSize: state.notRankedPageSize,
          sort: "rank",
        };
      }
      if (action.tab === "configure") {
        return {
          ...state,
          tab: action.tab,
          page: state.configurePage,
          pageSize: state.configurePageSize,
          sort: state.configureSort || "rank",
        };
      }
      if (action.tab === "ranked") {
        return {
          ...state,
          tab: action.tab,
          page: state.rankedPage ?? 1,
          pageSize: state.rankedPageSize ?? DEFAULT_PAGE_SIZE,
          sort: state.rankedSort || "rank",
        };
      }
      return {
        ...state,
        tab: action.tab,
        page: 1,
        pageSize: DEFAULT_PAGE_SIZE,
        sort: "rank",
      };
    }
    case "SET_PAGE":
      return { ...state, page: action.page, rankedPage: action.page };
    case "SET_PAGE_SIZE": {
      const nextPage = action.page ?? state.page;
      return {
        ...state,
        pageSize: action.pageSize,
        page: nextPage,
        rankedPageSize: action.pageSize,
        rankedPage: nextPage,
      };
    }
    case "SET_NOTRANKED_PAGE":
      return { ...state, notRankedPage: action.page, page: action.page };
    case "SET_NOTRANKED_PAGE_SIZE": {
      const nextPage = action.page ?? state.notRankedPage;
      return {
        ...state,
        notRankedPageSize: action.pageSize,
        notRankedPage: nextPage,
        pageSize: action.pageSize,
        page: nextPage,
      };
    }
    case "OPEN_REPO": {
      const repo = normalizeRepoParam(action.repo);
      const validRepo = isValidRepoKey(repo);
      const hasDetail = !!(state.repoDetailByKey && state.repoDetailByKey[repo]);
      const hasEvidence = !!(state.evidenceByRepo && state.evidenceByRepo[repo]);
      return {
        ...state,
        repo,
        repoDetailLoading: validRepo && !hasDetail,
        evidenceLoading: validRepo && !hasEvidence,
        evidenceError: null,
        repoDetailError: null,
      };
    }
    case "CLOSE_DRAWER":
      return {
        ...state,
        repo: null,
        repoDetailLoading: false,
        evidenceLoading: false,
        evidenceError: null,
        repoDetailError: null,
      };
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
        leaderboardSignature: action.signature ?? requestSignature(state),
        leaderboardError: null,
      };
    case "LEADERBOARD_ERROR":
      return { ...state, leaderboardLoading: false, leaderboardRefetching: false, leaderboardError: action.error };
    case "SET_NEXT_PAGE_CACHE":
      return { ...state, nextPageCache: action.entry };
    case "NOTRANKED_LOADING":
      return { ...state, notRankedLoading: true, notRankedError: null };
    case "NOTRANKED_OK":
      return { ...state, notRankedLoading: false, notRanked: action.data, notRankedError: null };
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
    case "EVIDENCE_OK": {
      const isCurrentRepo = !action.repo || !state.repo || action.repo === state.repo;
      return {
        ...state,
        evidenceLoading: isCurrentRepo ? false : state.evidenceLoading,
        evidenceError: isCurrentRepo ? null : state.evidenceError,
        evidenceByRepo: { ...state.evidenceByRepo, [action.repo]: action.data },
      };
    }
    case "EVIDENCE_ERROR": {
      const isCurrentRepo = !action.repo || !state.repo || action.repo === state.repo;
      if (!isCurrentRepo) return state;
      return { ...state, evidenceLoading: false, evidenceError: action.error };
    }

    // ------------------------------------------ drawer record fetch (Screen C)
    case "REPO_DETAIL_LOADING":
      return { ...state, repoDetailLoading: true, repoDetailError: null };
    case "REPO_DETAIL_OK": {
      const isCurrentRepo = !action.repo || !state.repo || action.repo === state.repo;
      return {
        ...state,
        repoDetailLoading: isCurrentRepo ? false : state.repoDetailLoading,
        repoDetailError: isCurrentRepo ? null : state.repoDetailError,
        repoDetailByKey: { ...state.repoDetailByKey, [action.repo]: action.data },
      };
    }
    case "REPO_DETAIL_ERROR": {
      const isCurrentRepo = !action.repo || !state.repo || action.repo === state.repo;
      if (!isCurrentRepo) return state;
      return { ...state, repoDetailLoading: false, repoDetailError: action.error };
    }

    case "PROJECT_EXTRAS_OK":
      return { ...state, projectExtrasByKey: { ...state.projectExtrasByKey, [action.repo]: action.data } };

    // ---------------- live check for a repository outside the snapshot (C)
    case "LIVE_CHECK_LOADING":
      return { ...state, liveCheckByKey: { ...state.liveCheckByKey, [action.key]: { status: "loading" } } };
    case "LIVE_CHECK_DONE": {
      const entry = { status: "done", outcome: action.outcome };
      const next = { ...state.liveCheckByKey, [action.key]: entry };
      const fullName =
        action.outcome && action.outcome.ok && action.outcome.result && action.outcome.result.repo
          ? action.outcome.result.repo.fullName
          : "";
      const canonical = fullName ? parseRepoSlug(fullName) : null;
      if (canonical) {
        const canonicalKey = `${canonical.owner}/${canonical.name}`;
        if (!next[canonicalKey]) next[canonicalKey] = entry;
      }
      return { ...state, liveCheckByKey: next };
    }

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
      return { ...state, weights: action.weights, configurePage: 1, page: 1 };
    case "TOGGLE_WEIGHT_LOCK":
      return { ...state, weightLocks: { ...state.weightLocks, [action.pillar]: !state.weightLocks[action.pillar] } };
    case "SET_CONFIGURE_SORT":
      return { ...state, configureSort: action.sort, configurePage: 1, sort: action.sort, page: 1 };
    case "SET_CONFIGURE_PAGE":
      return { ...state, configurePage: action.page, page: action.page };
    case "SET_CONFIGURE_PAGE_SIZE": {
      const nextPage = action.page ?? 1;
      return {
        ...state,
        configurePageSize: action.pageSize,
        configurePage: nextPage,
        pageSize: action.pageSize,
        page: nextPage,
      };
    }
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
    case "REPORT_LOADING":
      return { ...state, reportLoading: true, reportError: null };
    case "REPORT_OK":
      return { ...state, reportLoading: false, report: action.data, reportError: null };
    case "REPORT_ERROR":
      return { ...state, reportLoading: false, reportError: action.error };
    case "MANIFEST_LOADING":
      return { ...state, manifestLoad: "loading" };
    case "MANIFEST_DONE":
      return { ...state, manifestLoad: "done", sourceDbSha256: action.sourceDbSha256 || null };

    default:
      return state;
  }
}

/** Creates the mutable store singleton the app uses, starting from what the address names (`stateFromLocation`). */
export function createStore(initial = {}) {
  let state = { ...defaultState(), ...initial };
  if (state.tab === "ranked") {
    state.rankedPage = state.page;
    state.rankedPageSize = state.pageSize;
    state.rankedSort = state.sort;
  } else if (state.tab === "notranked") {
    state.notRankedPage = state.page;
    state.notRankedPageSize = state.pageSize;
  } else if (state.tab === "configure") {
    state.configurePage = state.page;
    state.configurePageSize = state.pageSize;
    state.configureSort = state.sort;
  }
  if (isValidRepoKey(state.repo)) {
    state.repoDetailLoading = true;
    state.evidenceLoading = true;
  }
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
