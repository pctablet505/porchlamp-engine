// routes.js — every address the app draws, in one table (the owner's rule, 2026-10-17: each thing has one view
// at one real address). state.js reads and writes the address with it (parseLocation, serializeLocation), the
// primary nav links to it, app.js takes the document title from it, and scripts/export_static.py writes the app
// shell at each of its app paths.
//
// The table between the two marker lines is plain JSON (double quotes, no comments, no trailing comma), so the
// export reads it with json.loads; tests/test_app_routes.py checks that Python and Node read the same table.
//
// A route's fields:
//   id     the view, and the `state.tab` value that shows it; "project" is a project's side panel over a list,
//          and "notfound" answers any address no other route claims;
//   path   its address; "{owner}" and "{name}" stand for a project's name parts, lower-cased; null for "notfound";
//   title  the document title; "{owner}" and "{name}" are filled with the project's real spelling;
//   query  the query fields the view reads, in the order the address writes them;
//   shell  true when the export writes the app's own page at `path`, so the app moves there without a page load;
//          false when another page answers there: a ranked project's page, or 404.html.
//
// A list of the leaderboard at its own address (a preset) adds:
//   view      the view that draws it, its `state.tab` ("ranked"); without it the view is the route's id;
//   fixed     the state fields the path fixes, by query-field name (persona, eco, q, sort, page_size); null is the
//             field unset, at its default;
//   defaults  the query fields whose default differs on this path ({"page_size": 50});
//   top       true on the fastest-reply lists: the list stops before the first row without a measured median
//             first reply, and has no pager;
//   "{page}"  in the path: one address per page, `state.page`.
// state.js serializeLocation picks, among the routes of a view, the one whose fixed fields the state has and whose
// query holds every other field the state sets off its default; the most fixed fields win, then table order.
// "{ecosystem}", "{lens}", "{first}", "{last}" and "{total}" in a title are filled from copy.js and the data.
//
// A list of the not-ranked projects by reason ("view": "notranked") fixes `reason` too: the bare reason code its rows
// carry (state.notRankedReason), and pages its rows `fixed.page_size` a page. "{list}" in its title is the list's name, and
// "{page_of}" its page part, empty when the list has one page (copy.js NOT_RANKED_LIST_NAMES, NOT_RANKED_LIST_PAGE_OF).

export const ROUTES = deepFreeze([
  // routes:begin
  {"id": "ranked", "path": "/", "title": "Porchlamp: do open-source projects answer pull requests? Rankings", "query": ["persona", "eco", "q", "sort", "page", "page_size"], "shell": true},
  {"id": "rankings", "view": "ranked", "path": "/rankings/{page}/", "title": "Open-source projects ranked {first} to {last} of {total} | Porchlamp", "query": [], "fixed": {"persona": "balanced", "eco": null, "q": null, "sort": "rank", "page_size": 100}, "shell": true},
  {"id": "rust", "view": "ranked", "path": "/rust/", "title": "{ecosystem} projects that answer outside pull requests | Porchlamp", "query": ["persona", "sort", "page", "page_size", "q"], "fixed": {"eco": "crates"}, "defaults": {"page_size": 50}, "shell": true},
  {"id": "python", "view": "ranked", "path": "/python/", "title": "{ecosystem} projects that answer outside pull requests | Porchlamp", "query": ["persona", "sort", "page", "page_size", "q"], "fixed": {"eco": "pypi"}, "defaults": {"page_size": 50}, "shell": true},
  {"id": "javascript", "view": "ranked", "path": "/javascript/", "title": "{ecosystem} projects that answer outside pull requests | Porchlamp", "query": ["persona", "sort", "page", "page_size", "q"], "fixed": {"eco": "npm"}, "defaults": {"page_size": 50}, "shell": true},
  {"id": "go", "view": "ranked", "path": "/go/", "title": "{ecosystem} projects that answer outside pull requests | Porchlamp", "query": ["persona", "sort", "page", "page_size", "q"], "fixed": {"eco": "go"}, "defaults": {"page_size": 50}, "shell": true},
  {"id": "java", "view": "ranked", "path": "/java/", "title": "{ecosystem} projects that answer outside pull requests | Porchlamp", "query": ["persona", "sort", "page", "page_size", "q"], "fixed": {"eco": "maven"}, "defaults": {"page_size": 50}, "shell": true},
  {"id": "c-cpp", "view": "ranked", "path": "/c-cpp/", "title": "{ecosystem} projects that answer outside pull requests | Porchlamp", "query": ["persona", "sort", "page", "page_size", "q"], "fixed": {"eco": "systems"}, "defaults": {"page_size": 50}, "shell": true},
  {"id": "rust-first-timers", "view": "ranked", "path": "/rust/first-timers/", "title": "{ecosystem} projects for {lens} | Porchlamp", "query": ["sort", "page", "page_size", "q"], "fixed": {"eco": "crates", "persona": "first_timer"}, "defaults": {"page_size": 50}, "shell": true},
  {"id": "python-first-timers", "view": "ranked", "path": "/python/first-timers/", "title": "{ecosystem} projects for {lens} | Porchlamp", "query": ["sort", "page", "page_size", "q"], "fixed": {"eco": "pypi", "persona": "first_timer"}, "defaults": {"page_size": 50}, "shell": true},
  {"id": "javascript-first-timers", "view": "ranked", "path": "/javascript/first-timers/", "title": "{ecosystem} projects for {lens} | Porchlamp", "query": ["sort", "page", "page_size", "q"], "fixed": {"eco": "npm", "persona": "first_timer"}, "defaults": {"page_size": 50}, "shell": true},
  {"id": "go-first-timers", "view": "ranked", "path": "/go/first-timers/", "title": "{ecosystem} projects for {lens} | Porchlamp", "query": ["sort", "page", "page_size", "q"], "fixed": {"eco": "go", "persona": "first_timer"}, "defaults": {"page_size": 50}, "shell": true},
  {"id": "java-first-timers", "view": "ranked", "path": "/java/first-timers/", "title": "{ecosystem} projects for {lens} | Porchlamp", "query": ["sort", "page", "page_size", "q"], "fixed": {"eco": "maven", "persona": "first_timer"}, "defaults": {"page_size": 50}, "shell": true},
  {"id": "c-cpp-first-timers", "view": "ranked", "path": "/c-cpp/first-timers/", "title": "{ecosystem} projects for {lens} | Porchlamp", "query": ["sort", "page", "page_size", "q"], "fixed": {"eco": "systems", "persona": "first_timer"}, "defaults": {"page_size": 50}, "shell": true},
  {"id": "first-timers", "view": "ranked", "path": "/first-timers/", "title": "Open-source projects for {lens} | Porchlamp", "query": ["sort", "page", "page_size", "q"], "fixed": {"persona": "first_timer"}, "defaults": {"page_size": 50}, "shell": true},
  {"id": "career", "view": "ranked", "path": "/career/", "title": "Open-source projects for {lens} | Porchlamp", "query": ["sort", "page", "page_size", "q"], "fixed": {"persona": "career_capital"}, "defaults": {"page_size": 50}, "shell": true},
  {"id": "senior-systems", "view": "ranked", "path": "/senior-systems/", "title": "Open-source projects for {lens} | Porchlamp", "query": ["sort", "page", "page_size", "q"], "fixed": {"persona": "senior_systems"}, "defaults": {"page_size": 50}, "shell": true},
  {"id": "ai-assisted", "view": "ranked", "path": "/ai-assisted/", "title": "Open-source projects for {lens} | Porchlamp", "query": ["sort", "page", "page_size", "q"], "fixed": {"persona": "ai_assisted"}, "defaults": {"page_size": 50}, "shell": true},
  {"id": "fastest-replies", "view": "ranked", "path": "/fastest-replies/", "title": "Fastest first reply to pull requests | Porchlamp", "query": [], "fixed": {"sort": "first_reply", "persona": "balanced", "eco": null}, "defaults": {"page_size": 50}, "top": true, "shell": true},
  {"id": "rust-fastest-replies", "view": "ranked", "path": "/rust/fastest-replies/", "title": "Fastest first reply to pull requests: {ecosystem} projects | Porchlamp", "query": [], "fixed": {"sort": "first_reply", "persona": "balanced", "eco": "crates"}, "defaults": {"page_size": 50}, "top": true, "shell": true},
  {"id": "python-fastest-replies", "view": "ranked", "path": "/python/fastest-replies/", "title": "Fastest first reply to pull requests: {ecosystem} projects | Porchlamp", "query": [], "fixed": {"sort": "first_reply", "persona": "balanced", "eco": "pypi"}, "defaults": {"page_size": 50}, "top": true, "shell": true},
  {"id": "javascript-fastest-replies", "view": "ranked", "path": "/javascript/fastest-replies/", "title": "Fastest first reply to pull requests: {ecosystem} projects | Porchlamp", "query": [], "fixed": {"sort": "first_reply", "persona": "balanced", "eco": "npm"}, "defaults": {"page_size": 50}, "top": true, "shell": true},
  {"id": "go-fastest-replies", "view": "ranked", "path": "/go/fastest-replies/", "title": "Fastest first reply to pull requests: {ecosystem} projects | Porchlamp", "query": [], "fixed": {"sort": "first_reply", "persona": "balanced", "eco": "go"}, "defaults": {"page_size": 50}, "top": true, "shell": true},
  {"id": "java-fastest-replies", "view": "ranked", "path": "/java/fastest-replies/", "title": "Fastest first reply to pull requests: {ecosystem} projects | Porchlamp", "query": [], "fixed": {"sort": "first_reply", "persona": "balanced", "eco": "maven"}, "defaults": {"page_size": 50}, "top": true, "shell": true},
  {"id": "c-cpp-fastest-replies", "view": "ranked", "path": "/c-cpp/fastest-replies/", "title": "Fastest first reply to pull requests: {ecosystem} projects | Porchlamp", "query": [], "fixed": {"sort": "first_reply", "persona": "balanced", "eco": "systems"}, "defaults": {"page_size": 50}, "top": true, "shell": true},
  {"id": "notranked", "path": "/not-ranked/", "title": "Not ranked: the gated and unrated projects, with their reasons | Porchlamp", "query": ["persona", "eco", "q", "page", "page_size"], "shell": true},
  {"id": "notranked-stale", "view": "notranked", "path": "/not-ranked/stale/{page}/", "title": "Not ranked: {list}{page_of} | Porchlamp", "query": [], "fixed": {"reason": "stale", "persona": "balanced", "eco": null, "q": null, "page_size": 200}, "shell": true},
  {"id": "notranked-not-software", "view": "notranked", "path": "/not-ranked/not-software/{page}/", "title": "Not ranked: {list}{page_of} | Porchlamp", "query": [], "fixed": {"reason": "non_software", "persona": "balanced", "eco": null, "q": null, "page_size": 200}, "shell": true},
  {"id": "notranked-archived", "view": "notranked", "path": "/not-ranked/archived/{page}/", "title": "Not ranked: {list}{page_of} | Porchlamp", "query": [], "fixed": {"reason": "archived", "persona": "balanced", "eco": null, "q": null, "page_size": 200}, "shell": true},
  {"id": "notranked-unlicensed", "view": "notranked", "path": "/not-ranked/unlicensed/{page}/", "title": "Not ranked: {list}{page_of} | Porchlamp", "query": [], "fixed": {"reason": "unlicensed", "persona": "balanced", "eco": null, "q": null, "page_size": 200}, "shell": true},
  {"id": "notranked-unanswered", "view": "notranked", "path": "/not-ranked/unanswered/{page}/", "title": "Not ranked: {list}{page_of} | Porchlamp", "query": [], "fixed": {"reason": "black_hole", "persona": "balanced", "eco": null, "q": null, "page_size": 200}, "shell": true},
  {"id": "notranked-fork", "view": "notranked", "path": "/not-ranked/fork/{page}/", "title": "Not ranked: {list}{page_of} | Porchlamp", "query": [], "fixed": {"reason": "fork", "persona": "balanced", "eco": null, "q": null, "page_size": 200}, "shell": true},
  {"id": "notranked-mirror", "view": "notranked", "path": "/not-ranked/mirror/{page}/", "title": "Not ranked: {list}{page_of} | Porchlamp", "query": [], "fixed": {"reason": "mirror", "persona": "balanced", "eco": null, "q": null, "page_size": 200}, "shell": true},
  {"id": "notranked-insufficient-evidence", "view": "notranked", "path": "/not-ranked/insufficient-evidence/{page}/", "title": "Not ranked: {list}{page_of} | Porchlamp", "query": [], "fixed": {"reason": "insufficient_evidence", "persona": "balanced", "eco": null, "q": null, "page_size": 200}, "shell": true},
  {"id": "notranked-no-contribution-workflow", "view": "notranked", "path": "/not-ranked/no-contribution-workflow/{page}/", "title": "Not ranked: {list}{page_of} | Porchlamp", "query": [], "fixed": {"reason": "no_external_workflow", "persona": "balanced", "eco": null, "q": null, "page_size": 200}, "shell": true},
  {"id": "notranked-off-platform", "view": "notranked", "path": "/not-ranked/off-platform/{page}/", "title": "Not ranked: {list}{page_of} | Porchlamp", "query": [], "fixed": {"reason": "off_platform_routed", "persona": "balanced", "eco": null, "q": null, "page_size": 200}, "shell": true},
  {"id": "matrix", "path": "/matrix/", "title": "Leverage vs. friction matrix of open-source projects | Porchlamp", "query": ["eco", "q"], "shell": true},
  {"id": "compare", "path": "/compare/", "title": "Compare open-source projects side by side | Porchlamp", "query": ["persona", "cmp"], "shell": true},
  {"id": "configure", "path": "/weights/", "title": "Rank open-source projects with your own pillar weights | Porchlamp", "query": ["eco", "q", "sort", "page", "page_size", "w"], "shell": true},
  {"id": "methodology", "path": "/methodology/", "title": "Porchlamp methodology: how open-source projects are measured and ranked | Porchlamp", "query": [], "shell": true},
  {"id": "report", "path": "/black-holes/", "title": "Open-source black holes: do projects answer pull requests? | Porchlamp", "query": [], "shell": true},
  {"id": "project", "path": "/projects/{owner}/{name}/", "title": "{owner}/{name}: pull request response time and contributor experience | Porchlamp", "query": ["persona"], "shell": false},
  {"id": "notfound", "path": null, "title": "Page not found | Porchlamp", "query": [], "shell": false}
  // routes:end
]);

function deepFreeze(routes) {
  for (const route of routes) {
    Object.freeze(route.query);
    if (route.fixed) Object.freeze(route.fixed);
    if (route.defaults) Object.freeze(route.defaults);
    Object.freeze(route);
  }
  return Object.freeze(routes);
}

const BY_ID = new Map(ROUTES.map((route) => [route.id, route]));

/** The route with this id; `undefined` for an id the table does not hold. */
export function routeById(id) {
  return BY_ID.get(id);
}

/** The view that draws a route: its `state.tab`. */
export function routeView(route) {
  return route.view || route.id;
}

/** The routes a view is drawn at, in table order (the view's own route first). */
export function routesOfView(view) {
  return ROUTES.filter((route) => route.path !== null && route.id !== "project" && routeView(route) === view);
}

//: A project name part as GitHub spells one: letters, digits, `-`, `_` and `.`, never `.` or `..` alone.
const NAME_PART = /^[A-Za-z0-9_.-]+$/;

function namePart(raw) {
  let part;
  try {
    part = decodeURIComponent(raw);
  } catch {
    return null;
  }
  return NAME_PART.test(part) && part !== "." && part !== ".." ? part : null;
}

/** One project name part as it stands in an address: lower case, and encoded (a guard: GitHub names need none). */
function addressPart(value) {
  return encodeURIComponent(String(value).toLowerCase()).replace(/[()]/g, (ch) => (ch === "(" ? "%28" : "%29"));
}

//: A route path as a pattern: each "{owner}"/"{name}" matches one path segment, "{page}" a page number (1 and up,
//: no leading zero, so each page has one address).
const PATTERNS = ROUTES.filter((route) => route.path !== null).map((route) => {
  const names = [];
  const source = route.path
    .split(/(\{[a-z]+\})/)
    .map((piece) => {
      const slot = /^\{([a-z]+)\}$/.exec(piece);
      if (slot) {
        names.push(slot[1]);
        return slot[1] === "page" ? "([1-9][0-9]*)" : "([^/]+)";
      }
      return piece.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    })
    .join("");
  return { route, names, pattern: new RegExp(`^${source}$`) };
});

/**
 * The route an address path names. `index.html` at the end is the directory itself, and a path without its
 * closing slash is the same address (a static host answers `/matrix` with a redirect to `/matrix/`). Returns
 * `{ route, owner, name }` for a project (each part as written, case kept), `{ route, page }` for a route with a page
 * in its path, `{ route }` otherwise, and the
 * "notfound" route for a path no route claims or a project part that is not a GitHub name.
 */
export function matchRoute(pathname) {
  let path = String(pathname || "/");
  if (path.endsWith("/index.html")) path = path.slice(0, -"index.html".length);
  if (!path.endsWith("/")) path += "/";
  for (const { route, names, pattern } of PATTERNS) {
    const found = pattern.exec(path);
    if (!found) continue;
    const params = {};
    for (let i = 0; i < names.length; i++) {
      if (names[i] === "page") {
        params.page = Number(found[i + 1]);
        continue;
      }
      const part = namePart(found[i + 1]);
      if (part === null) return { route: BY_ID.get("notfound") };
      params[names[i]] = part;
    }
    return { route, ...params };
  }
  return { route: BY_ID.get("notfound") };
}

/** The address of a route; a project's name parts lower-cased, as the export writes its page. */
export function routePath(id, params = {}) {
  const route = BY_ID.get(id);
  if (!route || route.path === null) return null;
  return route.path.replace(/\{([a-z]+)\}/g, (_slot, key) => (key === "page" ? String(params.page ?? 1) : addressPart(params[key])));
}

/** A ranked project's own page, and the address of its side panel: `/projects/<owner>/<name>/`, lower case. */
export function projectPath(owner, name) {
  return routePath("project", { owner, name });
}

/** The document title of a route; a project's title carries its real spelling. */
export function routeTitle(id, params = {}) {
  const route = BY_ID.get(id) || BY_ID.get("notfound");
  return route.title.replace(/\{([a-z_]+)\}/g, (_slot, key) => String(params[key] ?? ""));
}
