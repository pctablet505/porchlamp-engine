// prerender.mjs — draws the site's pages at export; scripts/export_static.py runs it once it has written them.
//
// Into every page it draws the one header, nav and footer (app/components/chrome.js), from the snapshot the export
// wrote. Into the page of a view the app can draw without a browser it draws that view too, from the same exported
// files the app loads: today the methodology (app/views/methodology.js over methodology.json, snapshot.json and the
// manifest's database digest). A reader or a crawler without JavaScript then gets the whole page, and the app takes
// it over on load without drawing it again (app.js, `data-prerendered`). The output depends on nothing but the
// files: no clock, no locale. It lives outside web/app, so the export never publishes it.
//
//   node web/prerender.mjs <site> <pages.json>   draw the pages pages.json names, in place
//   node web/prerender.mjs --chrome <snapshot.json> <page.json>   print the header, nav and footer of one page
//
// pages.json is {"data": "data/<snapshot_id>", "pages": [...]}. A page of the app names its view (routes.js id):
// {"path": "methodology/index.html", "view": "methodology"}; any other page the nav entry it belongs to, or null:
// {"path": "black-holes/index.html", "nav": "report"}. Each page must still have the empty frame of web/app/404.html.

import { readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { pathToFileURL } from "node:url";

import * as copy from "./app/copy.js";
import { navEntryFor, renderAppHeader, renderFooter, renderHeader, renderNav } from "./app/components/chrome.js";
import { escapeHtml } from "./app/components/table.js";
import { defaultState } from "./app/state.js";
import { renderShell as renderMethodology } from "./app/views/methodology.js";

//: The frame every page has, empty, from web/app/404.html: what this fills.
const FRAME = Object.freeze({
  header: '<header class="app-header" id="app-header-controls"></header>',
  nav: '<nav class="app-primary-nav" id="app-primary-nav"></nav>',
  footer: '<footer class="app-footer" id="app-footer"></footer>',
});
const MAIN = /<main class="app-main" id="app-main" tabindex="-1">[\s\S]*?\n( *)<\/main>/g;

//: The views drawn here, from the exported data (`data`: snapshot, methodology, manifest).
const VIEWS = Object.freeze({
  methodology: (data) =>
    renderMethodology({
      ...defaultState(),
      tab: "methodology",
      methodology: data.methodology,
      snapshot: data.snapshot,
      sourceDbSha256: data.manifest.source_db_sha256 || null,
      exportManifest: "done",
    }),
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
    const drawn = `<main class="app-main" id="app-main" tabindex="-1" data-prerendered="${page.view}">\n${view(data)}\n${indent}</main>`;
    out = out.slice(0, mains[0].index) + drawn + out.slice(mains[0].index + whole.length);
  }
  return out;
}

async function readJson(file) {
  return JSON.parse(await readFile(file, "utf8"));
}

/** The exported data a page is drawn from: the snapshot, the methodology and the export's manifest. */
export async function loadData(dir) {
  const [snapshot, methodology, manifest] = await Promise.all(
    ["snapshot.json", "methodology.json", "manifest.json"].map((name) => readJson(path.join(dir, name)))
  );
  return { snapshot, methodology, manifest };
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
  const data = await loadData(path.join(site, job.data));
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
