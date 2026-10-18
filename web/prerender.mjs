// prerender.mjs — draws the site's pages at export; scripts/export_static.py runs it once it has written them.
//
// Into every page it draws the one header, nav and footer (app/components/chrome.js). Into the page of a view the app
// can draw without a browser it draws that view too: the ranked view with its first page of rows
// (app/views/leaderboard.js), the methodology (app/views/methodology.js) and the report (app/views/report.js). Their
// data comes through the site's own api.js (web/static/api.js in the export), the same calls the app makes,
// fetchSnapshot, fetchMethodology, fetchManifest, fetchReport and fetchLeaderboard, reading the exported files: the
// page holds what the app draws from them, and the app takes it over on load without drawing it again (app.js,
// `data-prerendered`). The report's frozen copy is drawn the same way and loads no app, so nothing redraws it. A ranked
// project's page gets the project view (app/views/project.js) in page mode, from the calls the app's side panel makes
// for the same project: fetchRepoDetail, fetchEvidence and fetchProjectExtras. A reader or a crawler without
// JavaScript gets the whole page. The output depends on nothing but the files: no clock, no locale. This file lives
// outside web/app, so the export never publishes it.
//
//   node web/prerender.mjs <site> <pages.json>   draw the pages pages.json names, in place
//   node web/prerender.mjs --chrome <snapshot.json> <page.json>   print the header, nav and footer of one page
//
// pages.json is {"pages": [...]}. A page of the app names its view (routes.js id): {"path": "methodology/index.html",
// "view": "methodology"}; a list of the leaderboard at its own address names that address too, {"path":
// "rust/index.html", "view": "ranked", "address": "/rust/"} (a page of the ranking: "rankings/3/index.html",
// "/rankings/3/"): the address is parsed into the app's state (state.js parseLocation), the page of rows that state
// asks for is fetched, and the list's heading, intro, rows (both layouts) and the page's <title> and description
// (state.js titleForState, views/leaderboard.js descriptionForState) are drawn; on a list with an ecosystem or a search
// the Not ranked tab counts that lens's not-ranked list, filtered the same way (fetchNotRankedFull). A page of the
// not-ranked view is {"path": "not-ranked/archived/2/index.html", "view": "notranked", "address":
// "/not-ranked/archived/2/"} for a list by reason, and {"path": "not-ranked/index.html", "view": "notranked",
// "address": "/not-ranked/"} for the whole list: the balanced not-ranked list (fetchNotRankedFull), the report, the
// methodology (its coverage floor) and the counts draw it (views/notranked.js: a list's heading, intro, lines and rows;
// the whole list's tabs, its "Lists by reason" line and its first page of rows) and its <title> and description, for
// the app to take over; the report's frozen copy is {"path": "black-holes/<sid>/index.html", "view": "report",
// "frozen": true}; any other page the nav entry it belongs to, or null: {"path": "check/index.html", "nav": null}; a
// project's page names the project too: {"path": "projects/<owner>/<name>/index.html", "nav": null, "project":
// "<owner>/<name>"}.
// Each page must still have the empty frame of web/app/404.html.

import { readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { fileURLToPath, pathToFileURL } from "node:url";

import * as copy from "./app/copy.js";
import { navEntryFor, renderAppHeader, renderFooter, renderHeader, renderNav } from "./app/components/chrome.js";
import { escapeHtml } from "./app/components/table.js";
import { defaultState, parseLocation, titleForState } from "./app/state.js";
import { descriptionForState, listRows, renderPrerenderedBoard, renderShell as renderLeaderboard } from "./app/views/leaderboard.js";
import { reasonListDescription, renderPrerenderedNotRanked } from "./app/views/notranked.js";
import { renderShell as renderMethodology } from "./app/views/methodology.js";
import { renderProject } from "./app/views/project.js";
import { renderShell as renderReport } from "./app/views/report.js";

//: The frame every page has, empty, from web/app/404.html: what this fills.
const FRAME = Object.freeze({
  header: '<header class="app-header" id="app-header-controls"></header>',
  nav: '<nav class="app-primary-nav" id="app-primary-nav"></nav>',
  footer: '<footer class="app-footer" id="app-footer"></footer>',
});
const MAIN = /<main class="app-main" id="app-main" tabindex="-1">[\s\S]*?\n( *)<\/main>/g;

//: The views drawn here, from the site's data as the app loads it (`loadData`).
const VIEWS = Object.freeze({
  ranked: (data, page) => {
    if (!data.leaderboard) throw new Error(`the site has no leaderboard page for the ranked view at ${page.address || "/"}`);
    const state = listState(page, data);
    // A list with nothing to show (no ranked project in its cut, or no measured median on a fastest list) is the
    // view's own shell, as the app draws it: there is no board to take over.
    return listRows(state).length ? renderPrerenderedBoard(state) : renderLeaderboard(state);
  },
  notranked: (data, page) => {
    const state = listState(page, data);
    if (!data.notRanked) throw new Error(`the site has no not-ranked list for the page at ${page.address}`);
    return renderPrerenderedNotRanked(state);
  },
  methodology: (data) =>
    renderMethodology({
      ...defaultState(),
      tab: "methodology",
      methodology: data.methodology,
      snapshot: data.snapshot,
      report: data.report,
      sourceDbSha256: data.sourceDbSha256,
      manifestLoad: "done",
    }),
  report: (data) => {
    if (!data.report) throw new Error("the site has no report.json for the report view (the export writes it with --stats)");
    return renderReport({ ...defaultState(), tab: "report", report: data.report, sourceDbSha256: data.sourceDbSha256, manifestLoad: "done" });
  },
});

/** The app's state at a list's address (`page.address`, `/` without one): what it parses into, from the defaults. A
 *  list of the leaderboard, or a page of the not-ranked view: a list by reason, or the whole list. */
export function stateForAddress(address = "/") {
  const url = new URL(address, "https://porchlamp.invalid");
  const parsed = parseLocation(url.pathname, url.search);
  if ((parsed.tab !== "ranked" && parsed.tab !== "notranked") || parsed.repo) {
    throw new Error(`${address} is not the address of a list`);
  }
  return { ...defaultState(), ...parsed };
}

/** Whether `page` is a page of the not-ranked view at its address: a list by reason, or the whole list. */
function isNotRankedPage(page) {
  return page.view === "notranked" && Boolean(page.address);
}

/** Whether `page` is drawn from its address's state: a list of the leaderboard, or a page of the not-ranked view. */
function isListPage(page) {
  return (page.view === "ranked" && Boolean(page.address)) || isNotRankedPage(page);
}

/** The state a list's page is drawn from: its address's, with the page of rows, the snapshot, the report, the
 *  methodology and the not-ranked list (where the page reads it) loaded. */
function listState(page, data) {
  return {
    ...stateForAddress(page.address),
    leaderboard: data.leaderboard,
    snapshot: data.snapshot,
    report: data.report,
    methodology: data.methodology,
    notRanked: data.notRanked || null,
  };
}

/** Whether the page at `state` reads the not-ranked list: a list by reason, and the Not ranked tab's count on a list
 *  with an ecosystem or a search. */
export function needsNotRanked(state) {
  if (state.tab === "notranked") return true;
  return Boolean(state.ecosystem || (state.query && state.query.trim()));
}

/** The leaderboard request a state makes: the parameters app.js paramsForState sends for the ranked view. */
export function leaderboardParams(state) {
  return { persona: state.persona, eco: state.ecosystem, q: state.query, sort: state.sort, page: state.page, page_size: state.pageSize };
}

/** `html` with its one <title> and its one description set to `title` and `description` (left as they are when null). */
function writeHead(html, title, description, what) {
  let out = html;
  for (const [pattern, value, tag] of [
    [/<title>[^<]*<\/title>/g, title, (v) => `<title>${escapeHtml(v)}</title>`],
    [/<meta name="description" content="[^"]*">/g, description, (v) => `<meta name="description" content="${escapeHtml(v)}">`],
  ]) {
    if (value === null) continue;
    const found = out.match(pattern) || [];
    if (found.length !== 1) throw new Error(`${what}: the page has no single ${found.length ? "" : "empty "}${tag("…").slice(0, 20)}`);
    out = out.replace(pattern, () => tag(value));
  }
  return out;
}

/** A project's page: its view in page mode, from what `loadProject` read for it, with the methodology's coverage floor. */
function projectView(project, methodology) {
  return renderProject(project.record, { mode: "page", recordFile: project.recordFile, coverageFloor: copy.unratedCoverageFloor(methodology) });
}

/** The header, nav and footer of `page` ({view} or {nav}) as the export draws them from `snapshot`. A list's page
 *  (`page.address`) draws its header's selects at the list's lens and ecosystem, as the app does for that state. */
export function chromeFor(page, snapshot) {
  const state = isListPage(page) ? stateForAddress(page.address) : null;
  return {
    header: page.view ? renderAppHeader(page.view, snapshot, state) : renderHeader({ snapshot }),
    nav: renderNav(page.view ? navEntryFor(page.view) : page.nav ?? null),
    footer: renderFooter({ kind: "ok", snapshot }),
  };
}

function replaceOnce(html, old, now, what) {
  const at = html.indexOf(old);
  if (at < 0 || html.indexOf(old, at + 1) >= 0) throw new Error(`${what}: the page has no single empty ${old.slice(0, 30)}…`);
  return html.slice(0, at) + now + html.slice(at + old.length);
}

/**
 * `html` (a page the export wrote) with its chrome drawn, and its view if it is one drawn here. A project's page takes
 * its project from `data.project` (`loadProject`).
 */
export function drawPage(html, page, data) {
  const chrome = chromeFor(page, data.snapshot);
  let out = replaceOnce(html, FRAME.header, FRAME.header.replace("></header>", `>${chrome.header}</header>`), page.path);
  out = replaceOnce(
    out,
    FRAME.nav,
    FRAME.nav.replace("></nav>", ` aria-label="${escapeHtml(copy.PRIMARY_NAV_LABEL)}">${chrome.nav}</nav>`),
    page.path
  );
  out = replaceOnce(out, FRAME.footer, FRAME.footer.replace("></footer>", `>${chrome.footer}</footer>`), page.path);
  const view = page.view ? VIEWS[page.view] : page.project ? () => projectView(data.project, data.methodology) : undefined;
  if (view) {
    const mains = [...out.matchAll(MAIN)];
    if (mains.length !== 1) throw new Error(`${page.path}: the page has no single <main> for the ${page.view || "project"} view`);
    const [whole, indent] = mains[0];
    // The app takes over a page of its own it loads on (data-prerendered); the frozen copy and a project's page load
    // no app.
    const marker = page.frozen || !page.view ? "" : ` data-prerendered="${page.view}"`;
    const drawn = `<main class="app-main" id="app-main" tabindex="-1"${marker}>\n${view(data, page)}\n${indent}</main>`;
    out = out.slice(0, mains[0].index) + drawn + out.slice(mains[0].index + whole.length);
  }
  if (page.view === "ranked" && page.address) {
    const state = listState(page, data);
    out = writeHead(out, titleForState(state), descriptionForState(state), page.path);
  }
  if (isNotRankedPage(page)) {
    // the whole list has no description of its own: the page keeps the one the export wrote
    const state = listState(page, data);
    out = writeHead(out, titleForState(state), reasonListDescription(state), page.path);
  }
  return out;
}

async function readJson(file) {
  return JSON.parse(await readFile(file, "utf8"));
}

/** `fetch` for the site's own files: what a browser would get from a static host, read from disk. */
async function readSiteFile(input) {
  const url = new URL(input instanceof Request ? input.url : String(input));
  if (url.protocol !== "file:") throw new TypeError(`prerender: a page's data comes from the site only, not ${url.href}`);
  try {
    return new Response(await readFile(fileURLToPath(url)), { status: 200, headers: { "content-type": "application/json" } });
  } catch (error) {
    if (error && error.code === "ENOENT") return new Response("not found", { status: 404 });
    throw error;
  }
}

/**
 * The data the pages are drawn from, through the site's own api.js, as the app loads it: the snapshot, the
 * methodology, the report (null where the site has none: an export without the stats file), the ranked view's first
 * page (`leaderboard`, null where the site has none) and the database digest
 * from the manifest (null where the site has none, as the app then has none).
 */
/** The site's own api.js, reading the site's files. One module per site: each data file is read once. */
async function siteApi(site) {
  globalThis.fetch = readSiteFile;
  return import(pathToFileURL(path.join(path.resolve(site), "api.js")).href);
}

export async function loadData(site) {
  const api = await siteApi(site);
  // The ranked view's first page: the call the app makes at `/`, with the parameters of its default state.
  const [snapshot, methodology, manifest, report, leaderboard] = await Promise.all([
    api.fetchSnapshot(), api.fetchMethodology(), api.fetchManifest(), api.fetchReport(),
    api.fetchLeaderboard(leaderboardParams(defaultState())),
  ]);
  for (const [what, answer] of [["snapshot", snapshot], ["methodology", methodology]]) {
    if (!answer.ok) throw new Error(`the site's api.js could not load the ${what}: ${answer.error && answer.error.message}`);
  }
  return {
    snapshot: snapshot.data,
    methodology: methodology.data,
    report: report.ok ? report.data : null,
    leaderboard: leaderboard.ok ? leaderboard.data : null,
    sourceDbSha256: manifest.ok && manifest.data ? manifest.data.source_db_sha256 || null : null,
  };
}

/**
 * A ranked project's record, "owner/name" (`key`), as the app's side panel loads it: the API's detail and evidence,
 * and the answer, lens standings and similar projects the export wrote beside them; with its record file.
 */
export async function loadProject(site, key) {
  const api = await siteApi(site);
  const [owner, name] = key.split("/");
  const [detail, evidence, extras] = await Promise.all([
    api.fetchRepoDetail(owner, name), api.fetchEvidence(owner, name), api.fetchProjectExtras(owner, name),
  ]);
  for (const [what, answer] of [["record", detail], ["evidence", evidence], ["answer and standings", extras]]) {
    if (!answer.ok) throw new Error(`the site's api.js could not load ${key}'s ${what}: ${answer.error && answer.error.message}`);
  }
  if (!extras.data || !extras.data.answer) throw new Error(`${key} has a page, but its record has no rank`);
  const { recordFile, ...rest } = extras.data;
  return { record: { detail: detail.data, evidence: evidence.data, ...rest }, recordFile };
}

async function main(argv) {
  if (argv[0] === "--chrome") {
    const [snapshot, page] = await Promise.all([readJson(argv[1]), readJson(argv[2])]);
    process.stdout.write(JSON.stringify(chromeFor(page, snapshot)));
    return;
  }
  const [site, jobFile] = argv;
  if (!site || !jobFile) throw new Error("usage: node web/prerender.mjs <site> <pages.json>");
  const job = await readJson(jobFile);
  const data = await loadData(site);
  const api = await siteApi(site);
  // Each lens's whole not-ranked list, read once (`needsNotRanked`).
  const notRankedByLens = new Map();
  const notRankedFor = async (persona, address) => {
    if (!notRankedByLens.has(persona)) {
      const answer = await api.fetchNotRankedFull({ persona });
      if (!answer.ok) throw new Error(`the site's api.js could not load the not-ranked list for ${address}: ${answer.error && answer.error.message}`);
      notRankedByLens.set(persona, answer.data);
    }
    return notRankedByLens.get(persona);
  };
  for (const page of job.pages) {
    const file = path.join(site, page.path);
    let pageData = page.project ? { ...data, project: await loadProject(site, page.project) } : data;
    if (page.view === "ranked" && page.address) {
      const state = stateForAddress(page.address);
      const answer = await api.fetchLeaderboard(leaderboardParams(state));
      if (!answer.ok) throw new Error(`the site's api.js could not load the list at ${page.address}: ${answer.error && answer.error.message}`);
      pageData = { ...data, leaderboard: answer.data, notRanked: needsNotRanked(state) ? await notRankedFor(state.persona, page.address) : null };
    }
    if (isNotRankedPage(page)) {
      pageData = { ...data, notRanked: await notRankedFor(stateForAddress(page.address).persona, page.address) };
    }
    await writeFile(file, drawPage(await readFile(file, "utf8"), page, pageData), "utf8");
  }
  process.stdout.write(`prerender: drew ${job.pages.length} pages\n`);
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  main(process.argv.slice(2)).catch((error) => {
    process.stderr.write(`prerender: ${error && error.stack ? error.stack : error}\n`);
    process.exit(1);
  });
}
