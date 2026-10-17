// prerender.mjs — draws the site's pages at export; scripts/export_static.py runs it once it has written them.
//
// Into every page it draws the one header, nav and footer (app/components/chrome.js). Into the page of a view the app
// can draw without a browser it draws that view too: the methodology (app/views/methodology.js) and the report
// (app/views/report.js). Their data comes through the site's own api.js (web/static/api.js in the export), the same
// calls the app makes, fetchSnapshot, fetchMethodology, fetchManifest and fetchReport, reading the exported files: the
// page holds what the app draws from them, and the app takes it over on load without drawing it again (app.js,
// `data-prerendered`). The report's frozen copy is drawn the same way and loads no app, so nothing redraws it. A
// reader or a crawler without JavaScript gets the whole page. The output depends on nothing but the files: no clock,
// no locale. This file lives outside web/app, so the export never publishes it.
//
//   node web/prerender.mjs <site> <pages.json>   draw the pages pages.json names, in place
//   node web/prerender.mjs --chrome <snapshot.json> <page.json>   print the header, nav and footer of one page
//
// pages.json is {"pages": [...]}. A page of the app names its view (routes.js id): {"path": "methodology/index.html",
// "view": "methodology"}; the report's frozen copy is {"path": "black-holes/<sid>/index.html", "view": "report",
// "frozen": true}; any other page the nav entry it belongs to, or null: {"path": "check/index.html", "nav": null}.
// Each page must still have the empty frame of web/app/404.html.

import { readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { fileURLToPath, pathToFileURL } from "node:url";

import * as copy from "./app/copy.js";
import { navEntryFor, renderAppHeader, renderFooter, renderHeader, renderNav } from "./app/components/chrome.js";
import { escapeHtml } from "./app/components/table.js";
import { defaultState } from "./app/state.js";
import { renderShell as renderMethodology } from "./app/views/methodology.js";
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
  methodology: (data) =>
    renderMethodology({
      ...defaultState(),
      tab: "methodology",
      methodology: data.methodology,
      snapshot: data.snapshot,
      sourceDbSha256: data.sourceDbSha256,
      manifestLoad: "done",
    }),
  report: (data) => {
    if (!data.report) throw new Error("the site has no report.json for the report view (the export writes it with --stats)");
    return renderReport({ ...defaultState(), tab: "report", report: data.report, sourceDbSha256: data.sourceDbSha256, manifestLoad: "done" });
  },
});

/** The header, nav and footer of `page` ({view} or {nav}) as the export draws them from `snapshot`. */
export function chromeFor(page, snapshot) {
  return {
    header: page.view ? renderAppHeader(page.view, snapshot) : renderHeader({ snapshot }),
    nav: renderNav(page.view ? navEntryFor(page.view) : page.nav ?? null),
    footer: renderFooter({ kind: "ok", snapshot }),
  };
}

function replaceOnce(html, old, now, what) {
  const at = html.indexOf(old);
  if (at < 0 || html.indexOf(old, at + 1) >= 0) throw new Error(`${what}: the page has no single empty ${old.slice(0, 30)}…`);
  return html.slice(0, at) + now + html.slice(at + old.length);
}

/** `html` (a page the export wrote) with its chrome drawn, and its view if it is one drawn here. */
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
  const view = page.view ? VIEWS[page.view] : undefined;
  if (view) {
    const mains = [...out.matchAll(MAIN)];
    if (mains.length !== 1) throw new Error(`${page.path}: the page has no single <main> for the ${page.view} view`);
    const [whole, indent] = mains[0];
    // The app takes over a page it loads on (data-prerendered); the frozen copy loads no app.
    const marker = page.frozen ? "" : ` data-prerendered="${page.view}"`;
    const drawn = `<main class="app-main" id="app-main" tabindex="-1"${marker}>\n${view(data)}\n${indent}</main>`;
    out = out.slice(0, mains[0].index) + drawn + out.slice(mains[0].index + whole.length);
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
 * methodology, the report (null where the site has none: an export without the stats file) and the database digest
 * from the manifest (null where the site has none, as the app then has none).
 */
export async function loadData(site) {
  globalThis.fetch = readSiteFile;
  const api = await import(pathToFileURL(path.join(path.resolve(site), "api.js")).href);
  const [snapshot, methodology, manifest, report] = await Promise.all([
    api.fetchSnapshot(), api.fetchMethodology(), api.fetchManifest(), api.fetchReport(),
  ]);
  for (const [what, answer] of [["snapshot", snapshot], ["methodology", methodology]]) {
    if (!answer.ok) throw new Error(`the site's api.js could not load the ${what}: ${answer.error && answer.error.message}`);
  }
  return {
    snapshot: snapshot.data,
    methodology: methodology.data,
    report: report.ok ? report.data : null,
    sourceDbSha256: manifest.ok && manifest.data ? manifest.data.source_db_sha256 || null : null,
  };
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
  for (const page of job.pages) {
    const file = path.join(site, page.path);
    await writeFile(file, drawPage(await readFile(file, "utf8"), page, data), "utf8");
  }
  process.stdout.write(`prerender: drew ${job.pages.length} pages\n`);
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  main(process.argv.slice(2)).catch((error) => {
    process.stderr.write(`prerender: ${error && error.stack ? error.stack : error}\n`);
    process.exit(1);
  });
}
