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

export const ROUTES = deepFreeze([
  // routes:begin
  {"id": "ranked", "path": "/", "title": "Porchlamp rankings: find the projects that leave the light on", "query": ["persona", "eco", "q", "sort", "page", "page_size"], "shell": true},
  {"id": "notranked", "path": "/not-ranked/", "title": "Not ranked: the gated and unrated projects, with their reasons | Porchlamp", "query": ["persona", "eco", "q", "page", "page_size"], "shell": true},
  {"id": "matrix", "path": "/matrix/", "title": "Leverage vs. friction matrix of open-source projects | Porchlamp", "query": ["eco", "q"], "shell": true},
  {"id": "compare", "path": "/compare/", "title": "Compare open-source projects side by side | Porchlamp", "query": ["persona", "cmp"], "shell": true},
  {"id": "configure", "path": "/weights/", "title": "Rank open-source projects with your own pillar weights | Porchlamp", "query": ["eco", "q", "sort", "page", "page_size", "w"], "shell": true},
  {"id": "methodology", "path": "/methodology/", "title": "Porchlamp methodology: how open-source projects are measured and ranked | Porchlamp", "query": [], "shell": true},
  {"id": "report", "path": "/black-holes/", "title": "Open-source black holes: how many projects ignore outside pull requests | Porchlamp", "query": [], "shell": true},
  {"id": "project", "path": "/projects/{owner}/{name}/", "title": "{owner}/{name}: pull request response time and contributor experience | Porchlamp", "query": ["persona"], "shell": false},
  {"id": "notfound", "path": null, "title": "Page not found | Porchlamp", "query": [], "shell": false}
  // routes:end
]);

function deepFreeze(routes) {
  for (const route of routes) {
    Object.freeze(route.query);
    Object.freeze(route);
  }
  return Object.freeze(routes);
}

const BY_ID = new Map(ROUTES.map((route) => [route.id, route]));

/** The route with this id; `undefined` for an id the table does not hold. */
export function routeById(id) {
  return BY_ID.get(id);
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

//: A route path as a pattern: each "{owner}"/"{name}" matches one path segment.
const PATTERNS = ROUTES.filter((route) => route.path !== null).map((route) => {
  const names = [];
  const source = route.path
    .split(/(\{[a-z]+\})/)
    .map((piece) => {
      const slot = /^\{([a-z]+)\}$/.exec(piece);
      if (slot) {
        names.push(slot[1]);
        return "([^/]+)";
      }
      return piece.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    })
    .join("");
  return { route, names, pattern: new RegExp(`^${source}$`) };
});

/**
 * The route an address path names. `index.html` at the end is the directory itself, and a path without its
 * closing slash is the same address (a static host answers `/matrix` with a redirect to `/matrix/`). Returns
 * `{ route, owner, name }` for a project (each part as written, case kept), `{ route }` otherwise, and the
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
  return route.path.replace(/\{([a-z]+)\}/g, (_slot, key) => addressPart(params[key]));
}

/** A ranked project's own page, and the address of its side panel: `/projects/<owner>/<name>/`, lower case. */
export function projectPath(owner, name) {
  return routePath("project", { owner, name });
}

/** The document title of a route; a project's title carries its real spelling. */
export function routeTitle(id, params = {}) {
  const route = BY_ID.get(id) || BY_ID.get("notfound");
  return route.title.replace(/\{([a-z]+)\}/g, (_slot, key) => String(params[key] ?? ""));
}
